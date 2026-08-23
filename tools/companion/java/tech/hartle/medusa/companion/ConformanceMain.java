// Runs the shared vector files against the Java codec.
//
// Deliberately a plain main() rather than JUnit: this must be runnable with a
// bare JDK and no dependency resolution, so that "does the Android codec still
// agree with the firmware" is one javac and one java away rather than a Gradle
// sync. The Android app's own tests can wrap the same class later.
//
// Both files are checked. The encode vectors prove bytes go out correctly; the
// decode-error vectors prove the wrong bytes are REFUSED, which is where codecs
// actually diverge — an implementation that accepts a truncated header or a bad
// CRC passes every encode case and still fails in the field.
package tech.hartle.medusa.companion;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

public final class ConformanceMain {

    private static int passed = 0;
    private static final List<String> failures = new ArrayList<>();

    private static void check(boolean ok, String what, String detail) {
        if (ok) {
            passed++;
        } else {
            failures.add(what + " — " + detail);
        }
    }

    private static void encodeVectors(Path tsv) throws IOException {
        for (String line : Files.readAllLines(tsv, StandardCharsets.UTF_8)) {
            if (line.isEmpty() || line.startsWith("#")) {
                continue;
            }
            String[] f = line.split("\t", -1);
            String name = f[0];
            int msgId = Integer.parseInt(f[1]);
            int maxFrameLen = Integer.parseInt(f[2]);
            byte[] payload = CompanionProtocol.fromHex(f[3]);
            String[] expected = f[4].isEmpty() ? new String[0] : f[4].split(",");

            List<byte[]> frames = CompanionProtocol.encodeFrames(payload, msgId, maxFrameLen);
            check(frames.size() == expected.length, "encode/" + name,
                    "expected " + expected.length + " frames, produced " + frames.size());
            for (int i = 0; i < Math.min(frames.size(), expected.length); i++) {
                String got = CompanionProtocol.toHex(frames.get(i));
                check(got.equals(expected[i]), "encode/" + name + "[" + i + "]",
                        "expected " + expected[i] + " got " + got);
            }

            // Round trip: the frames this codec produced must reassemble back
            // into the payload it started from.
            CompanionProtocol.Reassembler re = new CompanionProtocol.Reassembler();
            byte[] out = null;
            for (byte[] frame : frames) {
                out = re.push(frame);
            }
            check(out != null && CompanionProtocol.toHex(out).equals(f[3]),
                    "roundtrip/" + name,
                    "reassembled " + (out == null ? "null" : CompanionProtocol.toHex(out)));
        }
    }

    private static void decodeErrorVectors(Path tsv) throws IOException {
        for (String line : Files.readAllLines(tsv, StandardCharsets.UTF_8)) {
            if (line.isEmpty() || line.startsWith("#")) {
                continue;
            }
            String[] f = line.split("\t", -1);
            String name = f[0];
            String expectedCode = f[1];
            String[] framesHex = f[2].split(",");

            CompanionProtocol.Reassembler re = new CompanionProtocol.Reassembler();
            String seen = null;
            for (String hex : framesHex) {
                try {
                    re.push(CompanionProtocol.fromHex(hex));
                } catch (CompanionProtocol.ProtocolException e) {
                    seen = e.getCode();
                    break;
                }
            }
            // Refusing for the wrong REASON is its own bug: the codes are what
            // the app shows the operator, so "bad CRC" reported as "out of
            // order" sends them to debug the wrong thing.
            check(expectedCode.equals(seen), "decode/" + name,
                    "expected " + expectedCode + " got " + (seen == null ? "no error" : seen));
        }
    }

    public static void main(String[] args) throws IOException {
        Path vectors = Path.of(args.length > 0 ? args[0] : "../vectors");
        encodeVectors(vectors.resolve("frames.tsv"));
        decodeErrorVectors(vectors.resolve("decode_errors.tsv"));

        System.out.println("java codec: " + passed + " checks passed, " + failures.size() + " failed");
        for (String failure : failures) {
            System.out.println("  FAIL " + failure);
        }
        if (!failures.isEmpty()) {
            System.exit(1);
        }
    }
}
