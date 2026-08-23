// Medusa Companion Protocol v1 — Swift codec, for the iOS companion.
//
// Specification: wiki/design/companion-protocol.md
// Conformance:   tools/companion/vectors/frames.tsv, decode_errors.tsv
//
// A port of the Python reference and the TypeScript and Java codecs. All four
// run the same vector files, so a divergence is a test failure here rather than
// a field report from someone whose phone will not talk to their device.
//
// FOUNDATION ONLY, NO SWIFTPM MANIFEST. This builds with swiftc directly, which
// matters because a SwiftPM package will not link on a machine without Xcode —
// and the codec is exactly the piece that should be verifiable before anyone
// installs fifteen gigabytes of toolchain. Drop these files into an Xcode
// target later and nothing changes.
//
// Frames only. The envelope layer is JSON and belongs to JSONSerialization,
// not to a hand-rolled parser living in here.

import Foundation

public enum CompanionProtocol {

    public static let version: UInt8 = 1
    public static let headerLen = 12
    public static let flagLast: UInt8 = 0x01
    public static let maxPayload = 0xFFFF

    /// Carries the stable error code, not just a message, so callers can branch
    /// on it and the app can show the operator the same code the firmware and
    /// every other codec would.
    public struct ProtocolError: Error, CustomStringConvertible {
        public let code: String
        public let message: String
        public var description: String { "\(code): \(message)" }
        public init(_ code: String, _ message: String) {
            self.code = code
            self.message = message
        }
    }

    // MARK: - CRC

    /// CRC-32/ISO-HDLC, the same polynomial zlib uses.
    ///
    /// Written out rather than borrowed because Foundation has no CRC-32 and
    /// zlib's is only reachable through a bridging header — which would drag a
    /// module map into a file whose whole point is that it builds standalone.
    /// The reflected form means shifting right, not left.
    private static let table: [UInt32] = {
        (0..<256).map { n -> UInt32 in
            var c = UInt32(n)
            for _ in 0..<8 {
                c = (c & 1) != 0 ? 0xEDB8_8320 ^ (c >> 1) : c >> 1
            }
            return c
        }
    }()

    public static func crc32(_ payload: [UInt8]) -> UInt32 {
        var crc: UInt32 = 0xFFFF_FFFF
        for byte in payload {
            crc = table[Int((crc ^ UInt32(byte)) & 0xFF)] ^ (crc >> 8)
        }
        return crc ^ 0xFFFF_FFFF
    }

    // MARK: - Frames

    public struct FrameHeader {
        public let version: UInt8
        public let last: Bool
        public let msgId: UInt16
        public let fragIndex: UInt16
        public let totalLen: UInt16
        public let crc32: UInt32
        public let body: [UInt8]
    }

    /// Split one payload into wire frames.
    ///
    /// `maxFrameLen` is the transport's USABLE bytes per frame. For BLE that is
    /// ATT_MTU minus 3, not the MTU itself — passing the raw MTU produces
    /// frames the peer silently truncates, which shows up as sporadic CRC
    /// failures rather than as an obvious size error.
    public static func encodeFrames(payload: [UInt8], msgId: UInt16, maxFrameLen: Int) throws -> [[UInt8]] {
        if payload.count > maxPayload {
            throw ProtocolError("proto.length", "payload \(payload.count) exceeds \(maxPayload)")
        }
        if maxFrameLen <= headerLen {
            throw ProtocolError("proto.mtu", "frame length \(maxFrameLen) leaves no room for payload")
        }

        let bodyLen = maxFrameLen - headerLen
        var spans: [(Int, Int)] = []
        var i = 0
        while i < payload.count {
            spans.append((i, min(i + bodyLen, payload.count)))
            i += bodyLen
        }
        // An empty payload is still one frame: the peer must be able to tell
        // "no data" from silence.
        if spans.isEmpty { spans.append((0, 0)) }

        let checksum = crc32(payload)
        return spans.enumerated().map { index, span in
            var frame = [UInt8]()
            frame.reserveCapacity(headerLen + (span.1 - span.0))
            frame.append(version)
            frame.append(index == spans.count - 1 ? flagLast : 0)
            appendLE16(&frame, msgId)
            appendLE16(&frame, UInt16(index))
            appendLE16(&frame, UInt16(payload.count))
            appendLE32(&frame, checksum)
            frame.append(contentsOf: payload[span.0..<span.1])
            return frame
        }
    }

    public static func decodeHeader(_ frame: [UInt8]) throws -> FrameHeader {
        if frame.count < headerLen {
            throw ProtocolError("proto.short", "frame is \(frame.count) bytes, need at least \(headerLen)")
        }
        if frame[0] != version {
            throw ProtocolError("proto.version", "unsupported protocol version \(frame[0])")
        }
        return FrameHeader(
            version: frame[0],
            last: (frame[1] & flagLast) != 0,
            msgId: readLE16(frame, 2),
            fragIndex: readLE16(frame, 4),
            totalLen: readLE16(frame, 6),
            crc32: readLE32(frame, 8),
            body: Array(frame[headerLen...])
        )
    }

    // MARK: - Reassembly

    /// Collects fragments into payloads.
    ///
    /// Holds at most one incomplete message. A fragment carrying a new msgId
    /// abandons whatever was in progress, which bounds memory to a single
    /// reassembly buffer and makes a lost tail self-healing rather than a leak.
    public final class Reassembler {
        private var msgId: UInt16?
        private var parts: [[UInt8]] = []
        private var expectIndex: UInt16 = 0
        private var totalLen: UInt16 = 0
        private var checksum: UInt32 = 0

        public init() {}

        public func reset() {
            msgId = nil
            parts = []
            expectIndex = 0
        }

        /// Feed one frame. Returns a complete payload, or nil if more are needed.
        public func push(_ frame: [UInt8]) throws -> [UInt8]? {
            let head = try decodeHeader(frame)

            if msgId != head.msgId {
                if head.fragIndex != 0 {
                    reset()
                    throw ProtocolError("proto.orphan", "first fragment of a message must have index 0")
                }
                msgId = head.msgId
                parts = []
                expectIndex = 0
                totalLen = head.totalLen
                checksum = head.crc32
            }

            if head.fragIndex != expectIndex {
                reset()
                throw ProtocolError("proto.order", "fragments must arrive in order")
            }
            if head.totalLen != totalLen || head.crc32 != checksum {
                reset()
                throw ProtocolError("proto.mismatch", "fragment disagrees about total length or checksum")
            }

            parts.append(head.body)
            expectIndex += 1

            if !head.last {
                let seen = parts.reduce(0) { $0 + $1.count }
                if seen > Int(totalLen) {
                    reset()
                    throw ProtocolError("proto.length", "fragments exceed declared total length")
                }
                return nil
            }

            let payload = parts.flatMap { $0 }
            let expectedLen = Int(totalLen)
            let expectedCrc = checksum
            reset()

            if payload.count != expectedLen {
                throw ProtocolError("proto.length",
                                    "reassembled \(payload.count) bytes, header declared \(expectedLen)")
            }
            if crc32(payload) != expectedCrc {
                throw ProtocolError("proto.crc", "checksum mismatch on reassembled payload")
            }
            return payload
        }
    }

    // MARK: - Little-endian helpers
    //
    // Written by hand rather than through withUnsafeBytes: the wire is
    // little-endian regardless of what the host is, and code that happens to be
    // correct because Apple silicon is little-endian is code that is wrong for
    // a reason nobody wrote down.

    private static func appendLE16(_ out: inout [UInt8], _ value: UInt16) {
        out.append(UInt8(value & 0xFF))
        out.append(UInt8((value >> 8) & 0xFF))
    }

    private static func appendLE32(_ out: inout [UInt8], _ value: UInt32) {
        out.append(UInt8(value & 0xFF))
        out.append(UInt8((value >> 8) & 0xFF))
        out.append(UInt8((value >> 16) & 0xFF))
        out.append(UInt8((value >> 24) & 0xFF))
    }

    private static func readLE16(_ b: [UInt8], _ at: Int) -> UInt16 {
        UInt16(b[at]) | (UInt16(b[at + 1]) << 8)
    }

    private static func readLE32(_ b: [UInt8], _ at: Int) -> UInt32 {
        UInt32(b[at]) | (UInt32(b[at + 1]) << 8) | (UInt32(b[at + 2]) << 16) | (UInt32(b[at + 3]) << 24)
    }

    // MARK: - Hex

    public static func fromHex(_ hex: String) -> [UInt8] {
        var out: [UInt8] = []
        var index = hex.startIndex
        while index < hex.endIndex, let next = hex.index(index, offsetBy: 2, limitedBy: hex.endIndex) {
            out.append(UInt8(hex[index..<next], radix: 16) ?? 0)
            index = next
        }
        return out
    }

    public static func toHex(_ bytes: [UInt8]) -> String {
        bytes.map { String(format: "%02x", $0) }.joined()
    }
}
