// Companion client and transports for Apple platforms.
//
// The codec in CompanionProtocol.swift turns payloads into frames; this turns
// frames into bytes on a link, and owns command/reply correlation.
//
// Two transports, and which is primary is not a preference:
//
//   WebSocketTransport (the device's SoftAP) is PRIMARY. It is the transport
//   the protocol was designed around because it is the only one a web client
//   on iOS can use, and using the same one natively means the native app and
//   the PWA exercise the same device code path.
//
//   BluetoothTransport is the reason a native app exists at all: iOS Safari has
//   no Web Bluetooth, so BLE and background operation are what native buys.
//
// See wiki/design/companion-protocol.md.

import Foundation

public enum TransportState: Equatable, Sendable {
    case idle
    case connecting
    case connected
    case closed(String?)
    case failed(String)
}

public protocol CompanionTransport: AnyObject {
    /// Usable payload bytes per frame, after the link's own overhead.
    var maxFrameLength: Int { get }
    var onFrame: (([UInt8]) -> Void)? { get set }
    var onStateChange: ((TransportState) -> Void)? { get set }
    func connect() throws
    func send(_ frame: [UInt8]) throws
    func close()
}

/// WebSocket to the device's own access point.
public final class WebSocketTransport: NSObject, CompanionTransport, URLSessionWebSocketDelegate {
    public var maxFrameLength: Int = 4096
    public var onFrame: (([UInt8]) -> Void)?
    public var onStateChange: ((TransportState) -> Void)?

    private let url: URL
    private let token: String?
    private var session: URLSession?
    private var task: URLSessionWebSocketTask?

    public init(url: URL, token: String? = nil) {
        self.url = url
        self.token = token
        super.init()
    }

    public func connect() throws {
        onStateChange?(.connecting)
        let configuration = URLSessionConfiguration.ephemeral
        // The sensor's AP has no internet. Without this the request can sit
        // waiting for a route that will never exist.
        configuration.waitsForConnectivity = false
        configuration.timeoutIntervalForRequest = 10

        let session = URLSession(configuration: configuration, delegate: self, delegateQueue: nil)
        self.session = session

        var request = URLRequest(url: url)
        // The token travels as a subprotocol rather than a query parameter:
        // query strings land in logs and history, and this one authorises
        // control of a radio.
        request.setValue(token.map { "medusa.v1.\($0)" } ?? "medusa.v1",
                         forHTTPHeaderField: "Sec-WebSocket-Protocol")

        let task = session.webSocketTask(with: request)
        self.task = task
        task.resume()
        receiveLoop()
    }

    private func receiveLoop() {
        task?.receive { [weak self] result in
            guard let self else { return }
            switch result {
            case .success(let message):
                if case .data(let data) = message { self.onFrame?([UInt8](data)) }
                // Text frames are not part of this protocol; ignoring them is
                // deliberate rather than an oversight.
                self.receiveLoop()
            case .failure(let error):
                self.onStateChange?(.failed(error.localizedDescription))
            }
        }
    }

    public func send(_ frame: [UInt8]) throws {
        guard let task else { throw ProtocolError("transport.closed", "not connected") }
        task.send(.data(Data(frame))) { [weak self] error in
            if let error { self?.onStateChange?(.failed(error.localizedDescription)) }
        }
    }

    public func close() {
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
        session?.invalidateAndCancel()
        session = nil
        onStateChange?(.closed(nil))
    }

    public func urlSession(_ session: URLSession, webSocketTask: URLSessionWebSocketTask,
                          didOpenWithProtocol proto: String?) {
        onStateChange?(.connected)
    }

    public func urlSession(_ session: URLSession, webSocketTask: URLSessionWebSocketTask,
                           didCloseWith closeCode: URLSessionWebSocketTask.CloseCode,
                           reason: Data?) {
        onStateChange?(.closed(reason.flatMap { String(data: $0, encoding: .utf8) }))
    }
}

/// Speaks the Companion Protocol over any transport.
///
/// Owns correlation: a command's continuation is resumed by the reply carrying
/// the same id, or by a timeout, or by the transport dropping. All three must
/// be handled or the UI waits forever on a reply that cannot arrive.
public final class CompanionClient {
    private let transport: CompanionTransport
    private let timeout: TimeInterval
    private let reassembler = Reassembler()
    private var nextMsgId: UInt16 = 0
    private var nextCorrelation: UInt32 = 0
    private var pending: [String: CheckedContinuation<Envelope, Error>] = [:]
    private let lock = NSLock()

    public var onEvent: ((Envelope) -> Void)?
    public var onDecodeError: ((ProtocolError) -> Void)?

    public init(transport: CompanionTransport, timeout: TimeInterval = 10) {
        self.transport = transport
        self.timeout = timeout
        transport.onFrame = { [weak self] frame in self?.ingest(frame) }
        transport.onStateChange = { [weak self] state in
            switch state {
            case .closed(let reason): self?.failAll(ProtocolError("transport.closed", reason ?? "closed"))
            case .failed(let reason): self?.failAll(ProtocolError("transport.failed", reason))
            default: break
            }
        }
    }

    private func ingest(_ frame: [UInt8]) {
        let payload: [UInt8]?
        do {
            payload = try reassembler.push(frame)
        } catch let error as ProtocolError {
            onDecodeError?(error)
            return
        } catch {
            onDecodeError?(ProtocolError("proto.unknown", error.localizedDescription))
            return
        }
        guard let payload else { return }

        let envelope: Envelope
        do {
            envelope = try Envelopes.decode(payload)
        } catch let error as ProtocolError {
            onDecodeError?(error)
            return
        } catch {
            return
        }

        if let waiter = take(envelope.id) {
            if envelope.type == .err {
                waiter.resume(throwing: ProtocolError("remote", envelope.op))
            } else {
                waiter.resume(returning: envelope)
            }
            return
        }
        onEvent?(envelope)
    }

    private func failAll(_ error: ProtocolError) {
        lock.lock()
        let waiters = pending
        pending.removeAll()
        lock.unlock()
        for (_, waiter) in waiters { waiter.resume(throwing: error) }
    }

    // Locking helpers are deliberately non-async. Taking an NSLock directly in
    // an async function is unavailable in Swift 6 language mode — a task can
    // suspend while holding it and resume on another thread — so every critical
    // section is confined to a synchronous call.
    private func nextIdentifiers() -> (id: String, msgId: UInt16) {
        lock.lock()
        defer { lock.unlock() }
        nextCorrelation &+= 1
        nextMsgId &+= 1
        return ("c\(nextCorrelation)", nextMsgId)
    }

    private func store(_ continuation: CheckedContinuation<Envelope, Error>, for id: String) {
        lock.lock()
        defer { lock.unlock() }
        pending[id] = continuation
    }

    private func take(_ id: String) -> CheckedContinuation<Envelope, Error>? {
        lock.lock()
        defer { lock.unlock() }
        return pending.removeValue(forKey: id)
    }

    public func command(_ op: String, args: [String: JSONValue]? = nil) async throws -> Envelope {
        let (id, msgId) = nextIdentifiers()

        let payload = Envelopes.encode(type: .cmd, op: op, id: id, args: args)
        let frames = try Frames.encode(payload: payload, msgId: msgId, maxFrameLength: transport.maxFrameLength)

        return try await withThrowingTaskGroup(of: Envelope.self) { group in
            group.addTask {
                try await withCheckedThrowingContinuation { continuation in
                    self.store(continuation, for: id)
                    do {
                        for frame in frames { try self.transport.send(frame) }
                    } catch {
                        self.take(id)?.resume(throwing: error)
                    }
                }
            }
            group.addTask {
                try await Task.sleep(nanoseconds: UInt64(self.timeout * 1_000_000_000))
                let error = ProtocolError("transport.timeout", "no reply to \(op)")
                self.take(id)?.resume(throwing: error)
                throw error
            }
            defer { group.cancelAll() }
            return try await group.next()!
        }
    }
}
