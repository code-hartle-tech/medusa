// Runs the same conformance vectors as the Python reference and the
// TypeScript codec. If any two disagree about a single byte, this fails.

import XCTest
@testable import MedusaKit

private struct Vectors: Decodable {
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

    let version: Int
    let header_len: Int
    let encode: [EncodeCase]
    let decode_errors: [DecodeErrorCase]
}

private func unhex(_ string: String) -> [UInt8] {
    stride(from: 0, to: string.count, by: 2).map { offset in
        let start = string.index(string.startIndex, offsetBy: offset)
        let end = string.index(start, offsetBy: 2)
        return UInt8(string[start..<end], radix: 16)!
    }
}

private func hex(_ bytes: [UInt8]) -> String {
    bytes.map { String(format: "%02x", $0) }.joined()
}

final class CompanionProtocolTests: XCTestCase {
    private func loadVectors() throws -> Vectors {
        guard let url = Bundle.module.url(forResource: "frames", withExtension: "json") else {
            XCTFail("conformance vectors missing from the test bundle")
            fatalError("unreachable")
        }
        return try JSONDecoder().decode(Vectors.self, from: Data(contentsOf: url))
    }

    // MARK: independent checks

    func testCRC32MatchesKnownValues() {
        XCTAssertEqual(CRC32.compute(Array("hi".utf8)), 0xD893_2AAC)
        XCTAssertEqual(CRC32.compute([]), 0)
        XCTAssertEqual(CRC32.compute(Array("123456789".utf8)), 0xCBF4_3926)
    }

    func testHeaderLayoutMatchesHandComputedBytes() throws {
        let frames = try Frames.encode(payload: Array("hi".utf8), msgId: 1, maxFrameLength: 64)
        XCTAssertEqual(frames.count, 1)
        //                   ver flags msg_id frag  total crc32(LE)  payload
        XCTAssertEqual(hex(frames[0]), "01" + "01" + "0100" + "0000" + "0200" + "ac2a93d8" + "6869")
    }

    func testHeaderIsTwelveBytes() throws {
        let frames = try Frames.encode(payload: [], msgId: 0, maxFrameLength: 32)
        XCTAssertEqual(frames[0].count, CompanionProtocol.headerLength)
        XCTAssertEqual(CompanionProtocol.headerLength, 12)
    }

    // MARK: the cross-language contract

    func testEncodeVectorsMatchTheReferenceByteForByte() throws {
        let vectors = try loadVectors()
        XCTAssertEqual(vectors.header_len, CompanionProtocol.headerLength)
        XCTAssertFalse(vectors.encode.isEmpty)

        for testCase in vectors.encode {
            let frames = try Frames.encode(
                payload: unhex(testCase.payload_hex),
                msgId: UInt16(testCase.msg_id),
                maxFrameLength: testCase.max_frame_len
            )
            XCTAssertEqual(frames.map(hex), testCase.frames_hex, "vector \(testCase.name): \(testCase.why)")
        }
    }

    func testDecodeFailureVectorsProduceTheSameStableCodes() throws {
        let vectors = try loadVectors()
        XCTAssertFalse(vectors.decode_errors.isEmpty)

        for testCase in vectors.decode_errors {
            let reassembler = Reassembler()
            var caught: ProtocolError?
            do {
                for frameHex in testCase.frames_hex {
                    _ = try reassembler.push(unhex(frameHex))
                }
            } catch let error as ProtocolError {
                caught = error
            }
            XCTAssertEqual(caught?.code, testCase.code, "vector \(testCase.name): \(testCase.why)")
        }
    }

    // MARK: behaviour

    func testRoundTripSurvivesFragmentationAtDefaultBLEMTU() throws {
        let payload = Envelopes.encode(
            type: .cmd, op: "scan.wifi.start", id: "c0ffee",
            args: ["channels": .array([.number(1), .number(6), .number(11)])]
        )
        let frames = try Frames.encode(payload: payload, msgId: 3, maxFrameLength: 20)
        XCTAssertGreaterThan(frames.count, 1, "this payload must fragment at a 20-byte frame")

        let reassembler = Reassembler()
        var out: [UInt8]?
        for frame in frames { out = try reassembler.push(frame) }
        let decoded = try Envelopes.decode(try XCTUnwrap(out))
        XCTAssertEqual(decoded.op, "scan.wifi.start")
        XCTAssertEqual(decoded.type, .cmd)
    }

    func testEnvelopeEncodingIsDeterministic() {
        let a = Envelopes.encode(type: .cmd, op: "status.get", id: "1", args: ["b": .number(2), "a": .number(1)])
        let b = Envelopes.encode(type: .cmd, op: "status.get", id: "1", args: ["a": .number(1), "b": .number(2)])
        XCTAssertEqual(hex(a), hex(b))
        XCTAssertFalse(String(decoding: a, as: UTF8.self).contains(" "))
    }

    func testEnvelopeBytesMatchTheReferenceEncoder() {
        // The exact bytes the Python and TypeScript encoders produce.
        let raw = Envelopes.encode(type: .cmd, op: "status.get", id: "1")
        XCTAssertEqual(String(decoding: raw, as: UTF8.self), #"{"id":"1","op":"status.get","t":"cmd","v":1}"#)
    }

    func testMalformedEnvelopesAreRefusedWithStableCodes() {
        let cases: [(String, String)] = [
            ("not json", "proto.json"),
            ("[]", "proto.json"),
            (#"{"v":2,"id":"1","t":"cmd","op":"x"}"#, "proto.version"),
            (#"{"v":1,"id":"1","t":"nope","op":"x"}"#, "proto.type"),
            (#"{"v":1,"id":"1","t":"cmd"}"#, "proto.op"),
            (#"{"v":1,"t":"cmd","op":"x"}"#, "proto.id"),
        ]
        for (raw, code) in cases {
            var caught: ProtocolError?
            do { _ = try Envelopes.decode(Array(raw.utf8)) } catch let error as ProtocolError { caught = error } catch {}
            XCTAssertEqual(caught?.code, code, "input \(raw)")
        }
    }

    func testAFrameLengthWithNoRoomForPayloadIsRefused() {
        var caught: ProtocolError?
        do {
            _ = try Frames.encode(payload: [1], msgId: 1, maxFrameLength: CompanionProtocol.headerLength)
        } catch let error as ProtocolError { caught = error } catch {}
        XCTAssertEqual(caught?.code, "proto.mtu")
    }

    func testANewMessageAbandonsAnIncompleteOne() throws {
        let first = try Frames.encode(payload: [UInt8](repeating: 0, count: 600), msgId: 9, maxFrameLength: 244)
        let second = try Frames.encode(payload: Array("second".utf8), msgId: 10, maxFrameLength: 244)

        let reassembler = Reassembler()
        XCTAssertNil(try reassembler.push(first[0]))
        let out = try XCTUnwrap(try reassembler.push(second[0]))
        XCTAssertEqual(String(decoding: out, as: UTF8.self), "second")
    }
}
