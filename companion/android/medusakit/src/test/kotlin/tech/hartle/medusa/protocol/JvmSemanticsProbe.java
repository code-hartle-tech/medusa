// JVM semantics probe for the Companion Protocol codec.
//
// WHAT THIS IS: a line-for-line transliteration of the arithmetic in
// CompanionProtocol.kt, run on the JVM against the shared conformance vectors.
//
// WHAT THIS IS NOT: a test of the Kotlin source. It cannot be — this machine
// has a JDK but no Kotlin compiler, and installing one was not worth the disk
// on a host already short of it. Compiling the Kotlin and running the real
// KotlinConformanceTest remains the outstanding verification step.
//
// WHY IT IS STILL WORTH HAVING: the way a JVM port of this codec breaks is
// almost always one of two things, and both are arithmetic, not language:
//
//   1. `byte` is SIGNED on the JVM. Widening 0xAC to int gives -84, so a CRC
//      table lookup indexes negatively and the checksum silently disagrees
//      with the C, Python, and Swift implementations.
//   2. `>>` sign-extends. The CRC loop needs `>>>`, and using `>>` corrupts
//      the high bits after the first byte with the top bit set.
//
// Both hazards live in arithmetic that Kotlin and Java compile identically, so
// proving it here proves the algorithm is right for the JVM. The remaining
// risk after this passes is a Kotlin transcription slip, not a semantics trap.
//
//   javac JvmSemanticsProbe.java -d /tmp/probe
//   java -cp /tmp/probe JvmSemanticsProbe <path-to-frames.json>

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.List;

public class JvmSemanticsProbe {
    static final int HEADER_LEN = 12;
    static final int VERSION = 1;
    static final int FLAG_LAST = 0x01;

    static int checks = 0;
    static int failures = 0;

    // ---- transliterated from Crc32.compute in CompanionProtocol.kt ----
    static final int[] TABLE = new int[256];
    static {
        for (int index = 0; index < 256; index++) {
            int c = index;
            for (int k = 0; k < 8; k++) {
                c = (c & 1) != 0 ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1);
            }
            TABLE[index] = c;
        }
    }

    static long crc32(byte[] payload) {
        int crc = -1;
        for (byte b : payload) {
            // The two hazards, both handled: mask the sign-extended byte, and
            // shift with >>> rather than >>.
            crc = TABLE[(crc ^ (b & 0xFF)) & 0xFF] ^ (crc >>> 8);
        }
        return (~crc) & 0xFFFFFFFFL;
    }

    // ---- transliterated from Frames.encode ----
    static List<byte[]> encode(byte[] payload, int msgId, int maxFrameLength) {
        int bodySize = maxFrameLength - HEADER_LEN;
        List<byte[]> chunks = new ArrayList<>();
        if (payload.length == 0) {
            chunks.add(new byte[0]);
        } else {
            for (int start = 0; start < payload.length; start += bodySize) {
                int end = Math.min(start + bodySize, payload.length);
                byte[] chunk = new byte[end - start];
                System.arraycopy(payload, start, chunk, 0, end - start);
                chunks.add(chunk);
            }
        }
        long checksum = crc32(payload);
        List<byte[]> frames = new ArrayList<>();
        for (int index = 0; index < chunks.size(); index++) {
            byte[] chunk = chunks.get(index);
            byte[] frame = new byte[HEADER_LEN + chunk.length];
            frame[0] = (byte) VERSION;
            frame[1] = (byte) (index == chunks.size() - 1 ? FLAG_LAST : 0);
            writeU16(frame, 2, msgId);
            writeU16(frame, 4, index);
            writeU16(frame, 6, payload.length);
            writeU32(frame, 8, checksum);
            System.arraycopy(chunk, 0, frame, HEADER_LEN, chunk.length);
            frames.add(frame);
        }
        return frames;
    }

    static void writeU16(byte[] target, int offset, int value) {
        target[offset] = (byte) (value & 0xFF);
        target[offset + 1] = (byte) ((value >>> 8) & 0xFF);
    }

    static void writeU32(byte[] target, int offset, long value) {
        target[offset] = (byte) (value & 0xFF);
        target[offset + 1] = (byte) ((value >>> 8) & 0xFF);
        target[offset + 2] = (byte) ((value >>> 16) & 0xFF);
        target[offset + 3] = (byte) ((value >>> 24) & 0xFF);
    }

    // ---- harness ----
    static void checkEqual(Object actual, Object expected, String label) {
        checks++;
        if (!String.valueOf(actual).equals(String.valueOf(expected))) {
            failures++;
            System.err.println("FAIL: " + label + "\n  actual:   " + actual + "\n  expected: " + expected);
        }
    }

    static String hex(byte[] bytes) {
        StringBuilder out = new StringBuilder();
        for (byte b : bytes) out.append(String.format("%02x", b));
        return out.toString();
    }

    static byte[] unhex(String s) {
        byte[] out = new byte[s.length() / 2];
        for (int i = 0; i < out.length; i++) {
            out[i] = (byte) Integer.parseInt(s.substring(i * 2, i * 2 + 2), 16);
        }
        return out;
    }

    public static void main(String[] args) throws Exception {
        // The two signed-byte hazards, pinned directly.
        checkEqual(crc32("hi".getBytes(StandardCharsets.UTF_8)), 0xD8932AACL, "crc32(\"hi\") — high bit set in the result");
        checkEqual(crc32(new byte[0]), 0L, "crc32(empty)");
        checkEqual(crc32("123456789".getBytes(StandardCharsets.UTF_8)), 0xCBF43926L, "crc32 check vector");
        // 0xFF is the byte most likely to expose sign extension.
        checkEqual(crc32(new byte[] {(byte) 0xFF}), 0xFF000000L, "crc32(0xFF) — the sign-extension trap");

        checkEqual(hex(encode("hi".getBytes(StandardCharsets.UTF_8), 1, 64).get(0)),
                "010101000000" + "0200" + "ac2a93d8" + "6869", "hand-computed header bytes");

        // frames.tsv is the flat view of the same vectors, generated alongside
        // frames.json precisely so this probe (and the C firmware check) need
        // no JSON dependency. One case per line, tab separated.
        String path = args.length > 0 ? args[0] : "../../../../../../../tools/companion/vectors/frames.tsv";
        int vectors = 0;
        for (String line : Files.readAllLines(Paths.get(path), StandardCharsets.UTF_8)) {
            if (line.isEmpty() || line.startsWith("#")) continue;
            String[] field = line.split("\t", -1);
            if (field.length != 5) {
                failures++;
                System.err.println("FAIL: malformed vector line: " + line);
                continue;
            }
            String name = field[0];
            int msgId = Integer.parseInt(field[1]);
            int maxFrameLen = Integer.parseInt(field[2]);
            byte[] payload = unhex(field[3]);

            List<String> expected = new ArrayList<>();
            for (String piece : field[4].split(",")) if (!piece.isEmpty()) expected.add(piece);

            List<String> actual = new ArrayList<>();
            for (byte[] f : encode(payload, msgId, maxFrameLen)) actual.add(hex(f));
            checkEqual(actual, expected, "encode vector '" + name + "'");
            vectors++;
        }

        checkEqual(vectors > 0, true, "vector file yielded encode cases");

        if (failures == 0) {
            System.out.println("PASS: " + checks + " JVM semantics checks, including " + vectors + " encode vectors");
            System.exit(0);
        }
        System.err.println(failures + " of " + checks + " checks failed");
        System.exit(1);
    }
}
