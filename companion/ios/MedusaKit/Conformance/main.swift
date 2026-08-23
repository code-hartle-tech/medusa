// Conformance runner for MedusaKit's Companion Protocol codec.
//
// The XCTest suite in Tests/ is the normal way to run these checks, but it
// needs SwiftPM, and SwiftPM's manifest compilation is broken on a machine that
// has only Command Line Tools rather than full Xcode: PackageDescription fails
// to link. Rather than leave the Swift codec unverified on such a machine, this
// runner compiles the same source directly and asserts the same things.
//
//   swiftc -O Sources/MedusaKit/CompanionProtocol.swift Conformance/main.swift \
//       -o /tmp/medusakit-conformance && /tmp/medusakit-conformance <vectors.json>
//
// Exits non-zero on the first failure, so it works as a build gate.

import Foundation

var failures = 0
var checks = 0

func check(_ condition: Bool, _ label: String) {
    checks += 1
    if !condition {
        failures += 1
        FileHandle.standardError.write(Data("FAIL: \(label)\n".utf8))
    }
}

func checkEqual<T: Equatable>(_ actual: T, _ expected: T, _ label: String) {
    checks += 1
    if actual != expected {
        failures += 1
        FileHandle.standardError.write(Data("FAIL: \(label)\n  actual:   \(actual)\n  expected: \(expected)\n".utf8))
    }
}

func unhex(_ string: String) -> [UInt8] {
    stride(from: 0, to: string.count, by: 2).map { offset in
        let start = string.index(string.startIndex, offsetBy: offset)
        let end = string.index(start, offsetBy: 2)
        return UInt8(string[start..<end], radix: 16)!
    }
}

func hex(_ bytes: [UInt8]) -> String {
    bytes.map { String(format: "%02x", $0) }.joined()
}

// MARK: - independent checks (not derived from the vector file)

checkEqual(CRC32.compute(Array("hi".utf8)), 0xD893_2AAC, "crc32(\"hi\")")
checkEqual(CRC32.compute([]), 0, "crc32(empty)")
checkEqual(CRC32.compute(Array("123456789".utf8)), 0xCBF4_3926, "crc32(check vector)")
checkEqual(CompanionProtocol.headerLength, 12, "header length")

do {
    let frames = try Frames.encode(payload: Array("hi".utf8), msgId: 1, maxFrameLength: 64)
    checkEqual(frames.count, 1, "single frame for a 2-byte payload")
    checkEqual(
        hex(frames[0]),
        "01" + "01" + "0100" + "0000" + "0200" + "ac2a93d8" + "6869",
        "hand-computed header bytes"
    )
} catch {
    failures += 1
    FileHandle.standardError.write(Data("FAIL: hand-computed header threw \(error)\n".utf8))
}

checkEqual(
    String(decoding: Envelopes.encode(type: .cmd, op: "status.get", id: "1"), as: UTF8.self),
    #"{"id":"1","op":"status.get","t":"cmd","v":1}"#,
    "envelope bytes match the reference encoder"
)

// Determinism: key order must not change the bytes.
checkEqual(
    hex(Envelopes.encode(type: .cmd, op: "s", id: "1", args: ["b": .number(2), "a": .number(1)])),
    hex(Envelopes.encode(type: .cmd, op: "s", id: "1", args: ["a": .number(1), "b": .number(2)])),
    "envelope encoding is deterministic"
)

// A new message must abandon an incomplete one rather than splice.
do {
    let first = try Frames.encode(payload: [UInt8](repeating: 0, count: 600), msgId: 9, maxFrameLength: 244)
    let second = try Frames.encode(payload: Array("second".utf8), msgId: 10, maxFrameLength: 244)
    let reassembler = Reassembler()
    check(try reassembler.push(first[0]) == nil, "first fragment is incomplete")
    let out = try reassembler.push(second[0])
    checkEqual(out.map { String(decoding: $0, as: UTF8.self) }, "second", "new msg_id abandons the partial message")
} catch {
    failures += 1
    FileHandle.standardError.write(Data("FAIL: abandon-partial threw \(error)\n".utf8))
}

// Malformed envelopes must produce stable codes.
for (raw, code) in [
    ("not json", "proto.json"),
    ("[]", "proto.json"),
    (#"{"v":2,"id":"1","t":"cmd","op":"x"}"#, "proto.version"),
    (#"{"v":1,"id":"1","t":"nope","op":"x"}"#, "proto.type"),
    (#"{"v":1,"id":"1","t":"cmd"}"#, "proto.op"),
    (#"{"v":1,"t":"cmd","op":"x"}"#, "proto.id"),
] {
    var caught: String?
    do { _ = try Envelopes.decode(Array(raw.utf8)) } catch let error as ProtocolError { caught = error.code } catch {}
    checkEqual(caught, code, "envelope rejection for \(raw)")
}

// MARK: - the cross-language contract

struct Vectors: Decodable {
    struct EncodeCase: Decodable {
        let name: String
        let why: String
        let payload_hex: String
        let msg_id: Int
        let max_frame_len: Int
        let frames_hex: [String]
    }

    struct DecodeErrorCase: Decodable {
        let name: String
        let why: String
        let frames_hex: [String]
        let code: String
    }

    let header_len: Int
    let encode: [EncodeCase]
    let decode_errors: [DecodeErrorCase]
}

let vectorPath = CommandLine.arguments.count > 1
    ? CommandLine.arguments[1]
    : "../../../tools/companion/vectors/frames.json"

guard let data = FileManager.default.contents(atPath: vectorPath) else {
    FileHandle.standardError.write(Data("FATAL: cannot read vectors at \(vectorPath)\n".utf8))
    exit(2)
}
let vectors = try JSONDecoder().decode(Vectors.self, from: data)
checkEqual(vectors.header_len, CompanionProtocol.headerLength, "vector file header length agrees")
check(!vectors.encode.isEmpty, "encode vectors present")
check(!vectors.decode_errors.isEmpty, "decode-failure vectors present")

for testCase in vectors.encode {
    do {
        let frames = try Frames.encode(
            payload: unhex(testCase.payload_hex),
            msgId: UInt16(testCase.msg_id),
            maxFrameLength: testCase.max_frame_len
        )
        checkEqual(frames.map(hex), testCase.frames_hex, "encode vector '\(testCase.name)' — \(testCase.why)")
    } catch {
        failures += 1
        FileHandle.standardError.write(Data("FAIL: encode vector '\(testCase.name)' threw \(error)\n".utf8))
    }
}

for testCase in vectors.decode_errors {
    let reassembler = Reassembler()
    var caught: String?
    do {
        for frameHex in testCase.frames_hex { _ = try reassembler.push(unhex(frameHex)) }
    } catch let error as ProtocolError {
        caught = error.code
    } catch {}
    checkEqual(caught, testCase.code, "failure vector '\(testCase.name)' — \(testCase.why)")
}

if failures == 0 {
    print("PASS: \(checks) MedusaKit conformance checks, including \(vectors.encode.count) encode and \(vectors.decode_errors.count) failure vectors")
    exit(0)
}
FileHandle.standardError.write(Data("\(failures) of \(checks) checks failed\n".utf8))
exit(1)
