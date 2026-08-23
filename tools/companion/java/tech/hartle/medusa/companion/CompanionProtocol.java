// Medusa Companion Protocol v1 — Java codec, for the Android companion.
//
// Specification: wiki/design/companion-protocol.md
// Conformance:   tools/companion/vectors/frames.tsv, decode_errors.tsv
//
// A port of the Python reference in tools/companion/python/medusa_companion.py
// and the TypeScript one in tools/esp-lab/client/src/companion/protocol.ts. All
// three run the same vector files, so a divergence is a test failure here
// rather than a field report from someone whose phone will not talk to their
// device.
//
// PLAIN JAVA ON PURPOSE. No Android imports, no Kotlin, no third-party JSON.
// The codec is the piece most likely to be reused — by a desktop tool, by a
// test harness, eventually by a different app — and every dependency it takes
// is one the reuser inherits. It also means this compiles and its conformance
// runs with nothing but a JDK, years before or after any particular Android
// Gradle plugin works.
//
// Frames only. The envelope layer is JSON, and JSON belongs to whatever
// facility the host already has — org.json on Android, JSONSerialization on
// iOS — rather than to a hand-rolled parser living in here.
package tech.hartle.medusa.companion;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.ArrayList;
import java.util.List;
import java.util.zip.CRC32;

public final class CompanionProtocol {

    public static final int VERSION = 1;
    public static final int HEADER_LEN = 12;
    public static final int FLAG_LAST = 0x01;
    public static final int MAX_PAYLOAD = 0xFFFF;

    private CompanionProtocol() {
    }

    /** Carries the stable error code, not just a message, so callers can branch on it. */
    public static final class ProtocolException extends RuntimeException {
        private final String code;

        public ProtocolException(String code, String message) {
            super(code + ": " + message);
            this.code = code;
        }

        public String getCode() {
            return code;
        }
    }

    /**
     * CRC-32/ISO-HDLC over the whole payload.
     *
     * java.util.zip.CRC32 already implements exactly this polynomial — the same
     * one zlib uses, which is what the Python reference calls. Reimplementing
     * the table here would be more code and one more thing to get subtly wrong;
     * the vectors prove the equivalence rather than a comment asserting it.
     */
    public static long crc32(byte[] payload) {
        CRC32 crc = new CRC32();
        crc.update(payload, 0, payload.length);
        return crc.getValue();
    }

    public static final class FrameHeader {
        public final int version;
        public final boolean last;
        public final int msgId;
        public final int fragIndex;
        public final int totalLen;
        public final long crc32;
        public final byte[] body;

        FrameHeader(int version, boolean last, int msgId, int fragIndex, int totalLen, long crc32, byte[] body) {
            this.version = version;
            this.last = last;
            this.msgId = msgId;
            this.fragIndex = fragIndex;
            this.totalLen = totalLen;
            this.crc32 = crc32;
            this.body = body;
        }
    }

    /**
     * Split one payload into wire frames.
     *
     * maxFrameLen is the transport's USABLE bytes per frame. For BLE that is
     * ATT_MTU minus 3, not the MTU itself — passing the raw MTU produces frames
     * the peer silently truncates, which presents as sporadic CRC failures
     * rather than as an obvious size error.
     */
    public static List<byte[]> encodeFrames(byte[] payload, int msgId, int maxFrameLen) {
        if (payload.length > MAX_PAYLOAD) {
            throw new ProtocolException("proto.length",
                    "payload " + payload.length + " exceeds " + MAX_PAYLOAD);
        }
        if (maxFrameLen <= HEADER_LEN) {
            throw new ProtocolException("proto.mtu",
                    "frame length " + maxFrameLen + " leaves no room for payload");
        }
        if (msgId < 0 || msgId > 0xFFFF) {
            throw new ProtocolException("proto.msgid", "msg_id must fit in u16");
        }

        int body = maxFrameLen - HEADER_LEN;
        List<int[]> spans = new ArrayList<>();
        for (int i = 0; i < payload.length; i += body) {
            spans.add(new int[]{i, Math.min(i + body, payload.length)});
        }
        // An empty payload is still one frame: the peer must be able to tell
        // "no data" from silence.
        if (spans.isEmpty()) {
            spans.add(new int[]{0, 0});
        }

        long checksum = crc32(payload);
        List<byte[]> frames = new ArrayList<>(spans.size());
        for (int index = 0; index < spans.size(); index++) {
            int[] span = spans.get(index);
            int len = span[1] - span[0];
            byte[] frame = new byte[HEADER_LEN + len];
            ByteBuffer buf = ByteBuffer.wrap(frame).order(ByteOrder.LITTLE_ENDIAN);
            buf.put((byte) VERSION);
            buf.put((byte) (index == spans.size() - 1 ? FLAG_LAST : 0));
            buf.putShort((short) msgId);
            buf.putShort((short) index);
            buf.putShort((short) payload.length);
            buf.putInt((int) checksum);
            System.arraycopy(payload, span[0], frame, HEADER_LEN, len);
            frames.add(frame);
        }
        return frames;
    }

    public static FrameHeader decodeHeader(byte[] frame) {
        if (frame.length < HEADER_LEN) {
            throw new ProtocolException("proto.short",
                    "frame is " + frame.length + " bytes, need at least " + HEADER_LEN);
        }
        int version = frame[0] & 0xFF;
        if (version != VERSION) {
            throw new ProtocolException("proto.version", "unsupported protocol version " + version);
        }
        ByteBuffer buf = ByteBuffer.wrap(frame).order(ByteOrder.LITTLE_ENDIAN);
        buf.position(1);
        boolean last = (buf.get() & FLAG_LAST) != 0;
        // Java has no unsigned short, so widen by hand. Forgetting the mask is
        // how a msgId above 0x7FFF becomes negative and stops matching itself.
        int msgId = buf.getShort() & 0xFFFF;
        int fragIndex = buf.getShort() & 0xFFFF;
        int totalLen = buf.getShort() & 0xFFFF;
        long crc = buf.getInt() & 0xFFFFFFFFL;
        byte[] body = new byte[frame.length - HEADER_LEN];
        System.arraycopy(frame, HEADER_LEN, body, 0, body.length);
        return new FrameHeader(version, last, msgId, fragIndex, totalLen, crc, body);
    }

    /**
     * Collects fragments into payloads.
     *
     * Holds at most one incomplete message. A fragment carrying a new msgId
     * abandons whatever was in progress, which bounds memory to a single
     * reassembly buffer and makes a lost tail self-healing rather than a leak.
     */
    public static final class Reassembler {
        private Integer msgId = null;
        private final List<byte[]> parts = new ArrayList<>();
        private int expectIndex = 0;
        private int totalLen = 0;
        private long checksum = 0;

        public void reset() {
            msgId = null;
            parts.clear();
            expectIndex = 0;
        }

        /** Feed one frame. Returns a complete payload, or null if more are needed. */
        public byte[] push(byte[] frame) {
            FrameHeader head = decodeHeader(frame);

            if (msgId == null || head.msgId != msgId) {
                if (head.fragIndex != 0) {
                    reset();
                    throw new ProtocolException("proto.orphan",
                            "first fragment of a message must have index 0");
                }
                msgId = head.msgId;
                parts.clear();
                expectIndex = 0;
                totalLen = head.totalLen;
                checksum = head.crc32;
            }

            if (head.fragIndex != expectIndex) {
                reset();
                throw new ProtocolException("proto.order", "fragments must arrive in order");
            }
            if (head.totalLen != totalLen || head.crc32 != checksum) {
                reset();
                throw new ProtocolException("proto.mismatch",
                        "fragment disagrees about total length or checksum");
            }

            parts.add(head.body);
            expectIndex += 1;

            if (!head.last) {
                int seen = 0;
                for (byte[] part : parts) {
                    seen += part.length;
                }
                if (seen > totalLen) {
                    reset();
                    throw new ProtocolException("proto.length",
                            "fragments exceed declared total length");
                }
                return null;
            }

            int total = 0;
            for (byte[] part : parts) {
                total += part.length;
            }
            byte[] payload = new byte[total];
            int offset = 0;
            for (byte[] part : parts) {
                System.arraycopy(part, 0, payload, offset, part.length);
                offset += part.length;
            }
            int expectedLen = totalLen;
            long expectedCrc = checksum;
            reset();

            if (payload.length != expectedLen) {
                throw new ProtocolException("proto.length",
                        "reassembled " + payload.length + " bytes, header declared " + expectedLen);
            }
            if (crc32(payload) != expectedCrc) {
                throw new ProtocolException("proto.crc", "checksum mismatch on reassembled payload");
            }
            return payload;
        }
    }

    // ------------------------------------------------------------------ hex
    public static byte[] fromHex(String hex) {
        int n = hex.length() / 2;
        byte[] out = new byte[n];
        for (int i = 0; i < n; i++) {
            out[i] = (byte) Integer.parseInt(hex.substring(i * 2, i * 2 + 2), 16);
        }
        return out;
    }

    public static String toHex(byte[] bytes) {
        StringBuilder sb = new StringBuilder(bytes.length * 2);
        for (byte b : bytes) {
            sb.append(String.format("%02x", b & 0xFF));
        }
        return sb.toString();
    }
}
