// Medusa Companion Protocol v1 — Swift codec.
//
// Specification: wiki/design/companion-protocol.md
// Conformance:   tools/companion/vectors/frames.json
//
// A port of the Python reference in tools/companion/python/medusa_companion.py.
// Both run the same vector file, so a divergence between any two
// implementations is a test failure rather than a field report.
//
// No CoreBluetooth and no URLSession in here. This turns payloads into frames
// and back; whether those frames travel over a BLE characteristic or a
// WebSocket to the device's SoftAP is the transport's business.

import Foundation

public enum CompanionProtocol {
    public static let version: UInt8 = 1
    public static let headerLength = 12
    public static let flagLast: UInt8 = 0x01
    public static let maxPayload = 0xFFFF
}

public struct ProtocolError: Error, Equatable, CustomStringConvertible {
    public let code: String
    public let message: String

    public init(_ code: String, _ message: String) {
        self.code = code
        self.message = message
    }

    public var description: String { "\(code): \(message)" }
}

// MARK: - CRC-32/ISO-HDLC

public enum CRC32 {
    // Reflected polynomial, the same one zlib.crc32 uses.
    private static let table: [UInt32] = {
        (0..<256).map { index -> UInt32 in
            var c = UInt32(index)
            for _ in 0..<8 {
                c = (c & 1) != 0 ? 0xEDB8_8320 ^ (c >> 1) : c >> 1
            }
            return c
        }
    }()

    public static func compute(_ payload: [UInt8]) -> UInt32 {
        var crc: UInt32 = 0xFFFF_FFFF
        for byte in payload {
            crc = table[Int((crc ^ UInt32(byte)) & 0xFF)] ^ (crc >> 8)
        }
        return crc ^ 0xFFFF_FFFF
    }
}

// MARK: - Frames

public struct FrameHeader: Equatable {
    public let version: UInt8
    public let isLast: Bool
    public let msgId: UInt16
    public let fragIndex: UInt16
    public let totalLength: UInt16
    public let crc32: UInt32
    public let body: [UInt8]
}

public enum Frames {
    /// Split one payload into wire frames.
    ///
    /// `maxFrameLength` is the transport's usable bytes per frame. For BLE that
    /// is ATT_MTU minus 3, not the MTU itself — passing the raw MTU produces
    /// frames the peer silently truncates.
    public static func encode(payload: [UInt8], msgId: UInt16, maxFrameLength: Int) throws -> [[UInt8]] {
        guard payload.count <= CompanionProtocol.maxPayload else {
            throw ProtocolError("proto.length", "payload \(payload.count) exceeds \(CompanionProtocol.maxPayload)")
        }
        guard maxFrameLength > CompanionProtocol.headerLength else {
            throw ProtocolError("proto.mtu", "frame length \(maxFrameLength) leaves no room for payload")
        }

        let bodySize = maxFrameLength - CompanionProtocol.headerLength
        var chunks: [[UInt8]] = stride(from: 0, to: payload.count, by: bodySize).map {
            Array(payload[$0..<min($0 + bodySize, payload.count)])
        }
        // An empty payload is still one frame: the peer must see the message.
        if chunks.isEmpty { chunks = [[]] }

        let checksum = CRC32.compute(payload)
        return chunks.enumerated().map { index, chunk in
            var frame: [UInt8] = []
            frame.reserveCapacity(CompanionProtocol.headerLength + chunk.count)
            frame.append(CompanionProtocol.version)
            frame.append(index == chunks.count - 1 ? CompanionProtocol.flagLast : 0)
            frame.append(contentsOf: littleEndian(msgId))
            frame.append(contentsOf: littleEndian(UInt16(index)))
            frame.append(contentsOf: littleEndian(UInt16(payload.count)))
            frame.append(contentsOf: littleEndian(checksum))
            frame.append(contentsOf: chunk)
            return frame
        }
    }

    public static func decodeHeader(_ frame: [UInt8]) throws -> FrameHeader {
        guard frame.count >= CompanionProtocol.headerLength else {
            throw ProtocolError("proto.short", "frame is \(frame.count) bytes, need at least \(CompanionProtocol.headerLength)")
        }
        guard frame[0] == CompanionProtocol.version else {
            throw ProtocolError("proto.version", "unsupported protocol version \(frame[0])")
        }
        return FrameHeader(
            version: frame[0],
            isLast: (frame[1] & CompanionProtocol.flagLast) != 0,
            msgId: readUInt16(frame, 2),
            fragIndex: readUInt16(frame, 4),
            totalLength: readUInt16(frame, 6),
            crc32: readUInt32(frame, 8),
            body: Array(frame[CompanionProtocol.headerLength...])
        )
    }

    private static func littleEndian(_ value: UInt16) -> [UInt8] {
        [UInt8(value & 0xFF), UInt8((value >> 8) & 0xFF)]
    }

    private static func littleEndian(_ value: UInt32) -> [UInt8] {
        [
            UInt8(value & 0xFF),
            UInt8((value >> 8) & 0xFF),
            UInt8((value >> 16) & 0xFF),
            UInt8((value >> 24) & 0xFF),
        ]
    }

    private static func readUInt16(_ bytes: [UInt8], _ offset: Int) -> UInt16 {
        UInt16(bytes[offset]) | (UInt16(bytes[offset + 1]) << 8)
    }

    private static func readUInt32(_ bytes: [UInt8], _ offset: Int) -> UInt32 {
        UInt32(bytes[offset])
            | (UInt32(bytes[offset + 1]) << 8)
            | (UInt32(bytes[offset + 2]) << 16)
            | (UInt32(bytes[offset + 3]) << 24)
    }
}

/// Collects fragments into payloads.
///
/// Holds at most one incomplete message. A fragment carrying a new `msgId`
/// abandons whatever was in progress, which bounds memory to a single
/// reassembly buffer and makes a lost tail self-healing rather than a leak.
public final class Reassembler {
    private var msgId: UInt16?
    private var parts: [[UInt8]] = []
    private var expectIndex: UInt16 = 0
    private var totalLength: UInt16 = 0
    private var checksum: UInt32 = 0

    public init() {}

    public func reset() {
        msgId = nil
        parts = []
        expectIndex = 0
    }

    /// Feed one frame. Returns a complete payload, or nil if more are needed.
    public func push(_ frame: [UInt8]) throws -> [UInt8]? {
        let head = try Frames.decodeHeader(frame)

        if head.msgId != msgId {
            guard head.fragIndex == 0 else {
                reset()
                throw ProtocolError("proto.orphan", "first fragment of a message must have index 0")
            }
            msgId = head.msgId
            parts = []
            expectIndex = 0
            totalLength = head.totalLength
            checksum = head.crc32
        }

        guard head.fragIndex == expectIndex else {
            reset()
            throw ProtocolError("proto.order", "fragments must arrive in order")
        }
        guard head.totalLength == totalLength, head.crc32 == checksum else {
            reset()
            throw ProtocolError("proto.mismatch", "fragment disagrees about total length or checksum")
        }

        parts.append(head.body)
        expectIndex += 1

        guard head.isLast else {
            if parts.reduce(0, { $0 + $1.count }) > Int(totalLength) {
                reset()
                throw ProtocolError("proto.length", "fragments exceed declared total length")
            }
            return nil
        }

        let payload = parts.flatMap { $0 }
        let expectedLength = Int(totalLength)
        let expectedCrc = checksum
        reset()

        guard payload.count == expectedLength else {
            throw ProtocolError("proto.length", "reassembled \(payload.count) bytes, header declared \(expectedLength)")
        }
        guard CRC32.compute(payload) == expectedCrc else {
            throw ProtocolError("proto.crc", "checksum mismatch on reassembled payload")
        }
        return payload
    }
}

// MARK: - Envelope

public enum EnvelopeType: String, Codable, CaseIterable {
    case cmd, evt, err, ack
}

public struct Envelope: Equatable {
    public let version: Int
    public let id: String
    public let type: EnvelopeType
    public let op: String
    public let args: [String: JSONValue]?

    public init(version: Int = 1, id: String, type: EnvelopeType, op: String, args: [String: JSONValue]? = nil) {
        self.version = version
        self.id = id
        self.type = type
        self.op = op
        self.args = args
    }
}

/// A minimal JSON tree. The protocol's `a` field is deliberately open so
/// additive changes do not require a version bump, which means the client
/// cannot model it as a fixed struct.
public indirect enum JSONValue: Equatable {
    case string(String)
    case number(Double)
    case bool(Bool)
    case null
    case array([JSONValue])
    case object([String: JSONValue])

    var serialized: String {
        switch self {
        case .string(let s): return Self.quote(s)
        case .number(let d):
            // Emit integral values without a decimal point so the bytes match
            // the other implementations.
            if d == d.rounded(), abs(d) < 1e15 { return String(Int64(d)) }
            return String(d)
        case .bool(let b): return b ? "true" : "false"
        case .null: return "null"
        case .array(let items): return "[" + items.map(\.serialized).joined(separator: ",") + "]"
        case .object(let map):
            let body = map.keys.sorted().map { "\(Self.quote($0)):\(map[$0]!.serialized)" }
            return "{" + body.joined(separator: ",") + "}"
        }
    }

    static func quote(_ value: String) -> String {
        var out = "\""
        for scalar in value.unicodeScalars {
            switch scalar {
            case "\"": out += "\\\""
            case "\\": out += "\\\\"
            case "\n": out += "\\n"
            case "\r": out += "\\r"
            case "\t": out += "\\t"
            default:
                if scalar.value < 0x20 {
                    out += String(format: "\\u%04x", scalar.value)
                } else {
                    out.unicodeScalars.append(scalar)
                }
            }
        }
        return out + "\""
    }
}

public enum Envelopes {
    /// Stable key order so two encoders produce identical bytes for identical content.
    public static func encode(type: EnvelopeType, op: String, id: String, args: [String: JSONValue]? = nil) -> [UInt8] {
        var fields: [String: JSONValue] = [
            "v": .number(Double(CompanionProtocol.version)),
            "id": .string(id),
            "t": .string(type.rawValue),
            "op": .string(op),
        ]
        if let args { fields["a"] = .object(args) }
        return Array(JSONValue.object(fields).serialized.utf8)
    }

    public static func decode(_ payload: [UInt8]) throws -> Envelope {
        let data = Data(payload)
        let parsed: Any
        do {
            parsed = try JSONSerialization.jsonObject(with: data, options: [])
        } catch {
            throw ProtocolError("proto.json", "payload is not valid JSON: \(error.localizedDescription)")
        }
        guard let object = parsed as? [String: Any] else {
            throw ProtocolError("proto.json", "envelope must be a JSON object")
        }
        guard let v = object["v"] as? Int, v == Int(CompanionProtocol.version) else {
            throw ProtocolError("proto.version", "unsupported envelope version \(object["v"] ?? "nil")")
        }
        guard let rawType = object["t"] as? String, let type = EnvelopeType(rawValue: rawType) else {
            throw ProtocolError("proto.type", "unknown envelope type \(object["t"] ?? "nil")")
        }
        guard let op = object["op"] as? String, !op.isEmpty else {
            throw ProtocolError("proto.op", "envelope needs a non-empty op")
        }
        guard let id = object["id"] as? String, !id.isEmpty else {
            throw ProtocolError("proto.id", "envelope needs a non-empty correlation id")
        }
        return Envelope(version: v, id: id, type: type, op: op, args: nil)
    }
}
