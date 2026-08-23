package tech.hartle.medusa.protocol

import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicInteger
import kotlin.coroutines.Continuation
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException
import kotlin.coroutines.suspendCoroutine

/**
 * Companion client and transport interface for Android.
 *
 * The codec in CompanionProtocol.kt turns payloads into frames; this owns
 * command/reply correlation and the link lifecycle.
 *
 * NOT COMPILED IN CI ON THE AUTHORING MACHINE. There is a JDK here but no
 * Kotlin toolchain, so this file is unverified Kotlin over a codec whose
 * arithmetic *is* verified — see JvmSemanticsProbe.java, which runs the shared
 * conformance vectors on the JVM and pins the signed-byte and shift traps that
 * are the real hazard in a JVM port. Compiling this module is the outstanding
 * step; see wiki/design/companion-distribution.md.
 *
 * Deliberately free of Android imports so it can be unit-tested on a plain JVM
 * once a toolchain exists. The BLE and OkHttp implementations of
 * [CompanionTransport] belong in the app module, which does need the SDK.
 */

sealed interface TransportState {
    data object Idle : TransportState
    data object Connecting : TransportState
    data object Connected : TransportState
    data class Closed(val reason: String?) : TransportState
    data class Failed(val reason: String) : TransportState
}

interface CompanionTransport {
    /** Usable payload bytes per frame, after the link's own overhead. */
    val maxFrameLength: Int
    var onFrame: ((ByteArray) -> Unit)?
    var onStateChange: ((TransportState) -> Unit)?
    fun connect()
    fun send(frame: ByteArray)
    fun close()
}

/**
 * Speaks the Companion Protocol over any transport.
 *
 * The failure paths are the point. A dropped link must fail every in-flight
 * command rather than leave the UI on a spinner forever, and a frame that will
 * not decode must be surfaced rather than swallowed — a silently dropped frame
 * hides a firmware bug.
 */
class CompanionClient(
    private val transport: CompanionTransport,
    private val timeoutMs: Long = 10_000,
) {
    private val reassembler = Reassembler()
    private val msgId = AtomicInteger(0)
    private val correlation = AtomicInteger(0)
    private val pending = ConcurrentHashMap<String, Continuation<Envelope>>()

    var onEvent: ((Envelope) -> Unit)? = null
    var onDecodeError: ((ProtocolException) -> Unit)? = null

    init {
        transport.onFrame = ::ingest
        transport.onStateChange = { state ->
            when (state) {
                is TransportState.Closed -> failAll(ProtocolException("transport.closed", state.reason ?: "closed"))
                is TransportState.Failed -> failAll(ProtocolException("transport.failed", state.reason))
                else -> Unit
            }
        }
    }

    private fun ingest(frame: ByteArray) {
        val payload = try {
            reassembler.push(frame)
        } catch (error: ProtocolException) {
            onDecodeError?.invoke(error)
            return
        } ?: return

        val envelope = try {
            decodeEnvelope(payload)
        } catch (error: ProtocolException) {
            onDecodeError?.invoke(error)
            return
        }

        val waiter = pending.remove(envelope.id)
        if (waiter != null) {
            if (envelope.type == EnvelopeType.ERR) {
                waiter.resumeWithException(ProtocolException("remote", envelope.op))
            } else {
                waiter.resume(envelope)
            }
            return
        }
        onEvent?.invoke(envelope)
    }

    private fun failAll(error: ProtocolException) {
        val waiters = pending.toMap()
        pending.clear()
        waiters.values.forEach { it.resumeWithException(error) }
    }

    suspend fun command(op: String, args: Map<String, JsonValue>? = null): Envelope {
        val id = "c${correlation.incrementAndGet()}"
        val payload = Envelopes.encode(EnvelopeType.CMD, op, id, args)
        val frames = Frames.encode(payload, msgId.incrementAndGet() and 0xFFFF, transport.maxFrameLength)

        return suspendCoroutine { continuation ->
            pending[id] = continuation
            // A command that is never answered must not wait forever.
            val timer = java.util.Timer(true)
            timer.schedule(
                object : java.util.TimerTask() {
                    override fun run() {
                        pending.remove(id)?.resumeWithException(
                            ProtocolException("transport.timeout", "no reply to $op within ${timeoutMs}ms"),
                        )
                    }
                },
                timeoutMs,
            )
            try {
                frames.forEach(transport::send)
            } catch (error: Exception) {
                pending.remove(id)?.resumeWithException(error)
            }
        }
    }
}

/** Envelope decoding, kept next to the client that consumes it. */
data class Envelope(
    val version: Int,
    val id: String,
    val type: EnvelopeType,
    val op: String,
)

fun decodeEnvelope(payload: ByteArray): Envelope {
    val text = payload.toString(Charsets.UTF_8)
    // Deliberately minimal: the fields the envelope contract guarantees are
    // flat and known, and the open `a` payload is handed to callers as raw
    // text rather than modelled here. A real app substitutes kotlinx.
    fun field(name: String): String? =
        Regex("\"$name\"\\s*:\\s*\"([^\"]*)\"").find(text)?.groupValues?.get(1)

    val version = Regex("\"v\"\\s*:\\s*(\\d+)").find(text)?.groupValues?.get(1)?.toIntOrNull()
        ?: throw ProtocolException("proto.version", "missing envelope version")
    if (version != CompanionProtocol.VERSION) {
        throw ProtocolException("proto.version", "unsupported envelope version $version")
    }
    val type = EnvelopeType.from(field("t"))
        ?: throw ProtocolException("proto.type", "unknown envelope type")
    val op = field("op")?.takeIf { it.isNotEmpty() }
        ?: throw ProtocolException("proto.op", "envelope needs a non-empty op")
    val id = field("id")?.takeIf { it.isNotEmpty() }
        ?: throw ProtocolException("proto.id", "envelope needs a non-empty correlation id")
    return Envelope(version, id, type, op)
}
