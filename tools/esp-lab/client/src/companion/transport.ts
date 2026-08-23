// Companion transports.
//
// The codec in protocol.ts turns payloads into frames; these turn frames into
// bytes on a link. Both implement the same interface, so the client above them
// does not know or care which one it has.
//
// Two exist for a reason that is not preference:
//
//   Wi-Fi (device SoftAP + WebSocket) is the PRIMARY transport, because it is
//   the only one that works on iOS. Web Bluetooth is unsupported in Safari on
//   both desktop and iOS, and every browser on iOS is WebKit — so a BLE-only
//   design can never be reached from a store-free web client on iPhone.
//
//   BLE is the OPTIMISATION, available in Chrome and Edge (Android 151+), and
//   in the native apps via CoreBluetooth and the Android BLE stack. Lower
//   power, and it does not require joining the device's access point.
//
// See wiki/design/companion-protocol.md.

import { Reassembler, encodeFrames, decodeEnvelope, encodeEnvelope, type Envelope, type EnvelopeType } from './protocol.ts';

export type TransportKind = 'wifi' | 'ble';

export type TransportState = 'idle' | 'connecting' | 'connected' | 'closed' | 'error';

export interface CompanionTransport {
  readonly kind: TransportKind;
  /** Usable payload bytes per frame, after the link's own overhead. */
  readonly maxFrameLength: number;
  readonly state: TransportState;
  connect(): Promise<void>;
  send(frame: Uint8Array): Promise<void>;
  close(): void;
  onFrame(handler: (frame: Uint8Array) => void): void;
  onStateChange(handler: (state: TransportState, detail?: string) => void): void;
}

abstract class BaseTransport implements CompanionTransport {
  abstract readonly kind: TransportKind;
  abstract readonly maxFrameLength: number;
  protected _state: TransportState = 'idle';
  private frameHandler: ((frame: Uint8Array) => void) | null = null;
  private stateHandler: ((state: TransportState, detail?: string) => void) | null = null;

  get state(): TransportState {
    return this._state;
  }

  onFrame(handler: (frame: Uint8Array) => void): void {
    this.frameHandler = handler;
  }

  onStateChange(handler: (state: TransportState, detail?: string) => void): void {
    this.stateHandler = handler;
  }

  protected emitFrame(frame: Uint8Array): void {
    this.frameHandler?.(frame);
  }

  protected setState(state: TransportState, detail?: string): void {
    this._state = state;
    this.stateHandler?.(state, detail);
  }

  abstract connect(): Promise<void>;
  abstract send(frame: Uint8Array): Promise<void>;
  abstract close(): void;
}

/**
 * Wi-Fi transport: a WebSocket to the device's own SoftAP.
 *
 * WebSocket already frames messages, so this does not need the protocol's
 * fragmentation — but it uses it anyway. One framing across both transports
 * means one codec, one test suite, and one set of bugs. The frame size here is
 * generous because there is no ATT_MTU to respect.
 */
export class WifiTransport extends BaseTransport {
  readonly kind = 'wifi' as const;
  readonly maxFrameLength = 4096;
  private socket: WebSocket | null = null;
  // Explicit fields rather than TypeScript parameter properties: parameter
  // properties need code generation, so they break any strip-only transpiler
  // (Node's own type stripping among them).
  private readonly url: string;
  private readonly token: string | null;

  constructor(url: string, token: string | null = null) {
    super();
    this.url = url;
    this.token = token;
  }

  connect(): Promise<void> {
    return new Promise((resolve, reject) => {
      this.setState('connecting');
      // The token is a subprotocol rather than a query parameter: query
      // strings land in logs and history, and this one authorises control of
      // a radio.
      const socket = this.token
        ? new WebSocket(this.url, [`medusa.v1.${this.token}`])
        : new WebSocket(this.url, ['medusa.v1']);
      socket.binaryType = 'arraybuffer';
      this.socket = socket;

      socket.onopen = () => {
        this.setState('connected');
        resolve();
      };
      socket.onmessage = (event) => {
        if (event.data instanceof ArrayBuffer) this.emitFrame(new Uint8Array(event.data));
      };
      socket.onerror = () => {
        this.setState('error', 'websocket error');
        reject(new Error('websocket error'));
      };
      socket.onclose = (event) => {
        this.setState('closed', event.reason || undefined);
      };
    });
  }

  async send(frame: Uint8Array): Promise<void> {
    if (this.socket?.readyState !== WebSocket.OPEN) throw new Error('transport is not connected');
    // Copy into a fresh buffer: a subarray view would send the whole backing
    // store, which on a shared buffer leaks neighbouring frames.
    this.socket.send(frame.slice().buffer);
  }

  close(): void {
    this.socket?.close();
    this.socket = null;
  }
}

// Minimal structural types for the Web Bluetooth surface we touch. The DOM lib
// does not ship them, and pulling a dependency for six members is not worth it.
type BluetoothCharacteristic = {
  writeValueWithoutResponse?(value: BufferSource): Promise<void>;
  writeValue(value: BufferSource): Promise<void>;
  startNotifications(): Promise<BluetoothCharacteristic>;
  addEventListener(type: 'characteristicvaluechanged', listener: (event: Event) => void): void;
  value?: DataView;
};

/**
 * BLE transport over Web Bluetooth.
 *
 * Present for Chrome and Edge on Android and desktop. It is deliberately NOT
 * the default: `isAvailable()` returning false is the normal case on iOS and
 * Firefox, and the caller is expected to fall back to Wi-Fi rather than treat
 * it as an error.
 */
export class BleTransport extends BaseTransport {
  readonly kind = 'ble' as const;
  // ATT_MTU 247 minus 3 bytes of ATT overhead. Before negotiation a peer may
  // only accept 20; the device reports its negotiated value in status.get and
  // the client may lower this.
  maxFrameLength = 244;

  private writer: BluetoothCharacteristic | null = null;
  private device: { gatt?: { disconnect(): void } } | null = null;
  private readonly serviceUuid: string;
  private readonly writeUuid: string;
  private readonly notifyUuid: string;

  constructor(serviceUuid: string, writeUuid: string, notifyUuid: string) {
    super();
    this.serviceUuid = serviceUuid;
    this.writeUuid = writeUuid;
    this.notifyUuid = notifyUuid;
  }

  /** Web Bluetooth is absent on iOS and Firefox; that is expected, not an error. */
  static isAvailable(): boolean {
    return typeof navigator !== 'undefined' && 'bluetooth' in navigator;
  }

  async connect(): Promise<void> {
    if (!BleTransport.isAvailable()) {
      throw new Error('Web Bluetooth is unavailable in this browser; use the Wi-Fi transport');
    }
    this.setState('connecting');
    // Must be called from a user gesture: the browser shows its own chooser
    // and there is deliberately no way to enumerate devices silently.
    const bluetooth = (navigator as unknown as { bluetooth: { requestDevice(o: unknown): Promise<any> } }).bluetooth;
    const device = await bluetooth.requestDevice({ filters: [{ services: [this.serviceUuid] }] });
    this.device = device;

    const server = await device.gatt.connect();
    const service = await server.getPrimaryService(this.serviceUuid);
    this.writer = await service.getCharacteristic(this.writeUuid);
    const notifier: BluetoothCharacteristic = await service.getCharacteristic(this.notifyUuid);

    await notifier.startNotifications();
    notifier.addEventListener('characteristicvaluechanged', (event: Event) => {
      const value = (event.target as unknown as { value?: DataView }).value;
      if (value) this.emitFrame(new Uint8Array(value.buffer, value.byteOffset, value.byteLength));
    });

    device.addEventListener?.('gattserverdisconnected', () => this.setState('closed', 'gatt disconnected'));
    this.setState('connected');
  }

  async send(frame: Uint8Array): Promise<void> {
    if (!this.writer) throw new Error('transport is not connected');
    const buffer = frame.slice();
    // Write-without-response is far faster and the protocol's own CRC and
    // ordering rules already detect loss, but not every stack exposes it.
    if (this.writer.writeValueWithoutResponse) await this.writer.writeValueWithoutResponse(buffer);
    else await this.writer.writeValue(buffer);
  }

  close(): void {
    this.device?.gatt?.disconnect();
    this.device = null;
    this.writer = null;
  }
}

/**
 * Speaks the Companion Protocol over any transport.
 *
 * Owns correlation: a command returns a promise that settles when the reply
 * carrying the same id arrives, or when the timeout expires. Unsolicited
 * events go to the event handler instead.
 */
export class CompanionClient {
  private readonly reassembler = new Reassembler();
  private nextMsgId = 0;
  private nextCorrelation = 0;
  private readonly pending = new Map<string, { resolve: (e: Envelope) => void; reject: (error: Error) => void; timer: ReturnType<typeof setTimeout> }>();
  private eventHandler: ((envelope: Envelope) => void) | null = null;
  private decodeErrorHandler: ((error: Error) => void) | null = null;
  private readonly transport: CompanionTransport;
  private readonly timeoutMs: number;

  constructor(transport: CompanionTransport, timeoutMs = 10_000) {
    this.transport = transport;
    this.timeoutMs = timeoutMs;
    this.transport.onFrame((frame) => this.ingest(frame));
    this.transport.onStateChange((state, detail) => {
      // A dropped link must fail every in-flight command rather than leave the
      // UI waiting on a reply that can no longer arrive.
      if (state === 'closed' || state === 'error') this.failAll(new Error(detail || `transport ${state}`));
    });
  }

  onEvent(handler: (envelope: Envelope) => void): void {
    this.eventHandler = handler;
  }

  /** Frames that fail to decode are surfaced, never silently dropped. */
  onDecodeError(handler: (error: Error) => void): void {
    this.decodeErrorHandler = handler;
  }

  private ingest(frame: Uint8Array): void {
    let payload: Uint8Array | null;
    try {
      payload = this.reassembler.push(frame);
    } catch (error) {
      this.decodeErrorHandler?.(error as Error);
      return;
    }
    if (!payload) return;

    let envelope: Envelope;
    try {
      envelope = decodeEnvelope(payload);
    } catch (error) {
      this.decodeErrorHandler?.(error as Error);
      return;
    }

    const waiter = this.pending.get(envelope.id);
    if (waiter) {
      this.pending.delete(envelope.id);
      clearTimeout(waiter.timer);
      if (envelope.t === 'err') {
        const args = envelope.a as { code?: string; message?: string } | undefined;
        waiter.reject(new Error(`${args?.code ?? 'err'}: ${args?.message ?? envelope.op}`));
      } else {
        waiter.resolve(envelope);
      }
      return;
    }
    this.eventHandler?.(envelope);
  }

  private failAll(error: Error): void {
    for (const [, waiter] of this.pending) {
      clearTimeout(waiter.timer);
      waiter.reject(error);
    }
    this.pending.clear();
  }

  async send(type: EnvelopeType, op: string, args?: unknown): Promise<Envelope> {
    const id = `c${(this.nextCorrelation = (this.nextCorrelation + 1) & 0xffffff).toString(36)}`;
    const payload = encodeEnvelope(type, op, id, args);
    const msgId = (this.nextMsgId = (this.nextMsgId + 1) & 0xffff);
    const frames = encodeFrames(payload, msgId, this.transport.maxFrameLength);

    const reply = new Promise<Envelope>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`no reply to ${op} within ${this.timeoutMs}ms`));
      }, this.timeoutMs);
      this.pending.set(id, { resolve, reject, timer });
    });

    for (const frame of frames) await this.transport.send(frame);
    return reply;
  }

  command(op: string, args?: unknown): Promise<Envelope> {
    return this.send('cmd', op, args);
  }
}
