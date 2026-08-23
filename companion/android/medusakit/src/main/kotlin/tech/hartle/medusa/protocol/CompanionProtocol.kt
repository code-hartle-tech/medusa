package tech.hartle.medusa.protocol

/**
 * Medusa Companion Protocol v1 — Kotlin codec.
 *
 * Specification: wiki/design/companion-protocol.md
 * Conformance:   tools/companion/vectors/frames.json
 *
 * A port of the Python reference in tools/companion/python/medusa_companion.py,
 * matching the TypeScript and Swift codecs byte for byte.
 *
 * No Android imports here. This turns payloads into frames and back; whether
 * those frames travel over a BluetoothGattCharacteristic or an OkHttp WebSocket
 * to the device's SoftAP is the transport's business, not the codec's.
 *
 * **JVM hazard, handled deliberately throughout:** `Byte` is signed on the JVM,
 * so `byte.toInt()` sign-extends and 0xAC becomes -84. Every byte-to-int
 * conversion below masks with `and 0xFF`, and every CRC shift uses `ushr`
 * rather than `shr`. Those two mistakes are the entire reason a JVM port of a
 * checksum silently disagrees with a C or Python one.
 */
object CompanionProtocol {
    const val VERSION: Int = 1
    const val HEADER_LEN: Int = 12
    const val FLAG_LAST: Int = 0x01
    const val MAX_PAYLOAD: Int = 0xFFFF
}

class ProtocolException(val code: String, override val message: String) : Exception("$code: $message")

/** CRC-32/ISO-HDLC — the same polynomial zlib.crc32 uses. */
object Crc32 {
    private val TABLE = IntArray(256) { index ->
        var c = index
        repeat(8) { c = if (c and 1 != 0) 0xEDB88320.toInt() xor (c ushr 1) else c ushr 1 }
        c
    }

    /** Returns the CRC as a Long so callers never see a negative checksum. */
    fun compute(payload: ByteArray): Long {
        var crc = -1 // 0xFFFFFFFF as a signed Int
        for (b in payload) {
            crc = TABLE[(crc xor (b.toInt() and 0xFF)) and 0xFF] xor (crc ushr 8)
        }
        return (crc.inv()).toLong() and 0xFFFFFFFFL
    }
}

data class FrameHeader(
    val version: Int,
    val isLast: Boolean,
    val msgId: Int,
    val fragIndex: Int,
    val totalLength: Int,
    val crc32: Long,
    val body: ByteArray,
) {
    // ByteArray gives identity equals; the data class needs these to be useful.
    override fun equals(other: Any?): Boolean {
        if (this === other) return true
        if (other !is FrameHeader) return false
        return version == other.version && isLast == other.isLast && msgId == other.msgId &&
            fragIndex == other.fragIndex && totalLength == other.totalLength &&
            crc32 == other.crc32 && body.contentEquals(other.body)
    }

    override fun hashCode(): Int {
        var result = version
        result = 31 * result + isLast.hashCode()
        result = 31 * result + msgId
        result = 31 * result + fragIndex
        result = 31 * result + totalLength
        result = 31 * result + crc32.hashCode()
        result = 31 * result + body.contentHashCode()
        return result
    }
}

object Frames {
    /**
     * Split one payload into wire frames.
     *
     * [maxFrameLength] is the transport's usable bytes per frame. For BLE that
     * is ATT_MTU minus 3, not the MTU itself — passing the raw MTU produces
     * frames the peer silently truncates.
     */
    fun encode(payload: ByteArray, msgId: Int, maxFrameLength: Int): List<ByteArray> {
        if (payload.size > CompanionProtocol.MAX_PAYLOAD) {
            throw ProtocolException("proto.length", "payload ${payload.size} exceeds ${CompanionProtocol.MAX_PAYLOAD}")
        }
        if (maxFrameLength <= CompanionProtocol.HEADER_LEN) {
            throw ProtocolException("proto.mtu", "frame length $maxFrameLength leaves no room for payload")
        }
        if (msgId !in 0..0xFFFF) {
            throw ProtocolException("proto.msgid", "msg_id must fit in u16")
        }

        val bodySize = maxFrameLength - CompanionProtocol.HEADER_LEN
        val chunks = if (payload.isEmpty()) {
            // An empty payload is still one frame: the peer must see the message.
            listOf(ByteArray(0))
        } else {
            (payload.indices step bodySize).map { start ->
                payload.copyOfRange(start, minOf(start + bodySize, payload.size))
            }
        }

        val checksum = Crc32.compute(payload)
        return chunks.mapIndexed { index, chunk ->
            val frame = ByteArray(CompanionProtocol.HEADER_LEN + chunk.size)
            frame[0] = CompanionProtocol.VERSION.toByte()
            frame[1] = (if (index == chunks.size - 1) CompanionProtocol.FLAG_LAST else 0).toByte()
            writeU16(frame, 2, msgId)
            writeU16(frame, 4, index)
            writeU16(frame, 6, payload.size)
            writeU32(frame, 8, checksum)
            chunk.copyInto(frame, CompanionProtocol.HEADER_LEN)
            frame
        }
    }

    fun decodeHeader(frame: ByteArray): FrameHeader {
        if (frame.size < CompanionProtocol.HEADER_LEN) {
            throw ProtocolException(
                "proto.short",
                "frame is ${frame.size} bytes, need at least ${CompanionProtocol.HEADER_LEN}",
            )
        }
        val version = frame[0].toInt() and 0xFF
        if (version != CompanionProtocol.VERSION) {
            throw ProtocolException("proto.version", "unsupported protocol version $version")
        }
        return FrameHeader(
            version = version,
            isLast = (frame[1].toInt() and CompanionProtocol.FLAG_LAST) != 0,
            msgId = readU16(frame, 2),
            fragIndex = readU16(frame, 4),
            totalLength = readU16(frame, 6),
            crc32 = readU32(frame, 8),
            body = frame.copyOfRange(CompanionProtocol.HEADER_LEN, frame.size),
        )
    }

    private fun writeU16(target: ByteArray, offset: Int, value: Int) {
        target[offset] = (value and 0xFF).toByte()
        target[offset + 1] = ((value ushr 8) and 0xFF).toByte()
    }

    private fun writeU32(target: ByteArray, offset: Int, value: Long) {
        target[offset] = (value and 0xFF).toByte()
        target[offset + 1] = ((value ushr 8) and 0xFF).toByte()
        target[offset + 2] = ((value ushr 16) and 0xFF).toByte()
        target[offset + 3] = ((value ushr 24) and 0xFF).toByte()
    }

    private fun readU16(source: ByteArray, offset: Int): Int =
        (source[offset].toInt() and 0xFF) or ((source[offset + 1].toInt() and 0xFF) shl 8)

    private fun readU32(source: ByteArray, offset: Int): Long =
        ((source[offset].toLong() and 0xFF)) or
            ((source[offset + 1].toLong() and 0xFF) shl 8) or
            ((source[offset + 2].toLong() and 0xFF) shl 16) or
            ((source[offset + 3].toLong() and 0xFF) shl 24)
}

/**
 * Collects fragments into payloads.
 *
 * Holds at most one incomplete message. A fragment carrying a new msgId
 * abandons whatever was in progress, which bounds memory to a single
 * reassembly buffer and makes a lost tail self-healing rather than a leak.
 */
class Reassembler {
    private var msgId: Int? = null
    private var parts = mutableListOf<ByteArray>()
    private var expectIndex = 0
    private var totalLength = 0
    private var checksum = 0L

    fun reset() {
        msgId = null
        parts = mutableListOf()
        expectIndex = 0
    }

    /** Feed one frame. Returns a complete payload, or null if more are needed. */
    fun push(frame: ByteArray): ByteArray? {
        val head = Frames.decodeHeader(frame)

        if (head.msgId != msgId) {
            if (head.fragIndex != 0) {
                reset()
                throw ProtocolException("proto.orphan", "first fragment of a message must have index 0")
            }
            msgId = head.msgId
            parts = mutableListOf()
            expectIndex = 0
            totalLength = head.totalLength
            checksum = head.crc32
        }

        if (head.fragIndex != expectIndex) {
            reset()
            throw ProtocolException("proto.order", "fragments must arrive in order")
        }
        if (head.totalLength != totalLength || head.crc32 != checksum) {
            reset()
            throw ProtocolException("proto.mismatch", "fragment disagrees about total length or checksum")
        }

        parts.add(head.body)
        expectIndex += 1

        if (!head.isLast) {
            if (parts.sumOf { it.size } > totalLength) {
                reset()
                throw ProtocolException("proto.length", "fragments exceed declared total length")
            }
            return null
        }

        val payload = ByteArray(parts.sumOf { it.size })
        var offset = 0
        for (part in parts) {
            part.copyInto(payload, offset)
            offset += part.size
        }
        val expectedLength = totalLength
        val expectedCrc = checksum
        reset()

        if (payload.size != expectedLength) {
            throw ProtocolException("proto.length", "reassembled ${payload.size} bytes, header declared $expectedLength")
        }
        if (Crc32.compute(payload) != expectedCrc) {
            throw ProtocolException("proto.crc", "checksum mismatch on reassembled payload")
        }
        return payload
    }
}

enum class EnvelopeType(val wire: String) {
    CMD("cmd"), EVT("evt"), ERR("err"), ACK("ack");

    companion object {
        fun from(wire: String?): EnvelopeType? = entries.firstOrNull { it.wire == wire }
    }
}

/**
 * A minimal JSON tree. The protocol's `a` field is deliberately open so
 * additive changes do not require a version bump, which means the client
 * cannot model it as a fixed class.
 */
sealed interface JsonValue {
    data class Str(val value: String) : JsonValue
    data class Num(val value: Double) : JsonValue
    data class Bool(val value: Boolean) : JsonValue
    data object Null : JsonValue
    data class Arr(val items: List<JsonValue>) : JsonValue
    data class Obj(val fields: Map<String, JsonValue>) : JsonValue
}

object Envelopes {
    /** Stable key order so two encoders produce identical bytes for identical content. */
    fun encode(type: EnvelopeType, op: String, id: String, args: Map<String, JsonValue>? = null): ByteArray {
        val fields = linkedMapOf<String, JsonValue>(
            "v" to JsonValue.Num(CompanionProtocol.VERSION.toDouble()),
            "id" to JsonValue.Str(id),
            "t" to JsonValue.Str(type.wire),
            "op" to JsonValue.Str(op),
        )
        if (args != null) fields["a"] = JsonValue.Obj(args)
        return serialize(JsonValue.Obj(fields)).toByteArray(Charsets.UTF_8)
    }

    internal fun serialize(value: JsonValue): String = when (value) {
        is JsonValue.Str -> quote(value.value)
        is JsonValue.Num ->
            // Emit integral values without a decimal point so the bytes match
            // the other implementations.
            if (value.value == Math.rint(value.value) && Math.abs(value.value) < 1e15) {
                value.value.toLong().toString()
            } else {
                value.value.toString()
            }
        is JsonValue.Bool -> if (value.value) "true" else "false"
        JsonValue.Null -> "null"
        is JsonValue.Arr -> value.items.joinToString(",", "[", "]") { serialize(it) }
        is JsonValue.Obj -> value.fields.keys.sorted()
            .joinToString(",", "{", "}") { key -> "${quote(key)}:${serialize(value.fields.getValue(key))}" }
    }

    private fun quote(value: String): String {
        val out = StringBuilder("\"")
        for (ch in value) {
            when {
                ch == '"' -> out.append("\\\"")
                ch == '\\' -> out.append("\\\\")
                ch == '\n' -> out.append("\\n")
                ch == '\r' -> out.append("\\r")
                ch == '\t' -> out.append("\\t")
                ch.code < 0x20 -> out.append(String.format("\\u%04x", ch.code))
                else -> out.append(ch)
            }
        }
        return out.append("\"").toString()
    }
}
