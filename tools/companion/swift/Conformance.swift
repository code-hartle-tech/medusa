// Runs the shared vector files against the Swift codec.
//
// A plain main rather than XCTest, for the same reason the Java one is a plain
// main: this must run with nothing but a Swift compiler. XCTest on macOS drags
// in a test host, and the whole point of keeping the codec Foundation-only is
// that its conformance can be checked before Xcode exists on the machine.
//
// Build and run:
//     swiftc -O CompanionProtocol.swift Conformance.swift -o /tmp/conformance
//     /tmp/conformance ../vectors

import Foundation

var passed = 0
var failures: [String] = []

func check(_ ok: Bool, _ what: String, _ detail: @autoclosure () -> String) {
    if ok { passed += 1 } else { failures.append("\(what) — \(detail())") }
}

func fields(_ line: String) -> [String] {
    // components(separatedBy:) keeps empty trailing fields, which matters: the
    // empty-payload case has an empty fourth column and dropping it would
    // silently shift every later field by one.
    line.components(separatedBy: "\t")
}

func encodeVectors(_ path: String) throws {
    let text = try String(contentsOfFile: path, encoding: .utf8)
    for line in text.split(separator: "\n", omittingEmptySubsequences: true) {
        if line.hasPrefix("#") { continue }
        let f = fields(String(line))
        let name = f[0]
        let msgId = UInt16(f[1]) ?? 0
        let maxFrameLen = Int(f[2]) ?? 0
        let payload = CompanionProtocol.fromHex(f[3])
        let expected = f[4].isEmpty ? [] : f[4].components(separatedBy: ",")

        let frames = try CompanionProtocol.encodeFrames(payload: payload, msgId: msgId, maxFrameLen: maxFrameLen)
        check(frames.count == expected.count, "encode/\(name)",
              "expected \(expected.count) frames, produced \(frames.count)")
        for i in 0..<min(frames.count, expected.count) {
            let got = CompanionProtocol.toHex(frames[i])
            check(got == expected[i], "encode/\(name)[\(i)]", "expected \(expected[i]) got \(got)")
        }

        // Round trip: the frames this codec produced must reassemble back into
        // the payload it started from.
        let re = CompanionProtocol.Reassembler()
        var out: [UInt8]?
        for frame in frames { out = try re.push(frame) }
        check(out != nil && CompanionProtocol.toHex(out!) == f[3], "roundtrip/\(name)",
              "reassembled \(out.map(CompanionProtocol.toHex) ?? "nil")")
    }
}

func decodeErrorVectors(_ path: String) throws {
    let text = try String(contentsOfFile: path, encoding: .utf8)
    for line in text.split(separator: "\n", omittingEmptySubsequences: true) {
        if line.hasPrefix("#") { continue }
        let f = fields(String(line))
        let name = f[0]
        let expectedCode = f[1]
        let framesHex = f[2].components(separatedBy: ",")

        let re = CompanionProtocol.Reassembler()
        var seen: String?
        for hex in framesHex {
            do {
                _ = try re.push(CompanionProtocol.fromHex(hex))
            } catch let error as CompanionProtocol.ProtocolError {
                seen = error.code
                break
            }
        }
        // Refusing for the wrong REASON is its own bug: the codes are what the
        // app shows the operator, so "bad CRC" reported as "out of order" sends
        // them to debug the wrong thing.
        check(expectedCode == seen, "decode/\(name)",
              "expected \(expectedCode) got \(seen ?? "no error")")
    }
}

// @main rather than renaming this to main.swift: Swift only permits top-level
// statements in a file with that exact name, and "Conformance.swift" says what
// this is where "main.swift" would not.
@main
struct Conformance {
    static func main() {
        let dir = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "../vectors"
        do {
            try encodeVectors("\(dir)/frames.tsv")
            try decodeErrorVectors("\(dir)/decode_errors.tsv")
        } catch {
            print("swift codec: harness error — \(error)")
            exit(2)
        }

        print("swift codec: \(passed) checks passed, \(failures.count) failed")
        for failure in failures { print("  FAIL \(failure)") }
        exit(failures.isEmpty ? 0 : 1)
    }
}
