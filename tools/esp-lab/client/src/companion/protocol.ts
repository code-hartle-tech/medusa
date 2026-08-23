// Medusa Companion Protocol v1 — TypeScript codec.
//
// Specification: wiki/design/companion-protocol.md
// Conformance:   tools/companion/vectors/frames.json
//
// A byte-for-byte port of the Python reference in
// tools/companion/python/medusa_companion.py. Both run the same vector file,
// so a divergence between them is a test failure rather than a field report.
//
// No DOM and no transport in here: this module turns payloads into frames and
// back. Whether those frames travel over a WebSocket to the device's SoftAP or
// over a BLE characteristic is the transport's business, not the codec's.

export const VERSION = 1;
export const HEADER_LEN = 12;
export const FLAG_LAST = 0x01;
export const MAX_PAYLOAD = 0xffff;

export type EnvelopeType = 'cmd' | 'evt' | 'err' | 'ack';
const ENVELOPE_TYPES: readonly string[] = ['cmd', 'evt', 'err', 'ack'];

export class ProtocolError extends Error {
  readonly code: string;
  constructor(code: string, message: string) {
    super(`${code}: ${message}`);
    this.name = 'ProtocolError';
    this.code = code;
  }
}

// CRC-32/ISO-HDLC, the same polynomial zlib.crc32 uses. Table built once at
// module load; the reflected form means we shift right, not left.
const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n += 1) {
    let c = n;
    for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c >>> 0;
  }
  return table;
})();

export function crc32(payload: Uint8Array): number {
  let crc = 0xffffffff;
  for (let i = 0; i < payload.length; i += 1) {
    crc = CRC_TABLE[(crc ^ payload[i]) & 0xff] ^ (crc >>> 8);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

export type FrameHeader = {
  version: number;
  last: boolean;
  msgId: number;
  fragIndex: number;
  totalLen: number;
  crc32: number;
  body: Uint8Array;
};

/**
 * Split one payload into wire frames.
 *
 * `maxFrameLen` is the transport's usable bytes per frame. For BLE that is
 * ATT_MTU minus 3, not the MTU itself — passing the raw MTU produces frames
 * the peer silently truncates.
 */
export function encodeFrames(payload: Uint8Array, msgId: number, maxFrameLen: number): Uint8Array[] {
  if (payload.length > MAX_PAYLOAD) {
    throw new ProtocolError('proto.length', `payload ${payload.length} exceeds ${MAX_PAYLOAD}`);
  }
  if (maxFrameLen <= HEADER_LEN) {
    throw new ProtocolError('proto.mtu', `frame length ${maxFrameLen} leaves no room for payload`);
  }
  if (!Number.isInteger(msgId) || msgId < 0 || msgId > 0xffff) {
    throw new ProtocolError('proto.msgid', 'msg_id must fit in u16');
  }

  const body = maxFrameLen - HEADER_LEN;
  const chunks: Uint8Array[] = [];
  for (let i = 0; i < payload.length; i += body) chunks.push(payload.subarray(i, i + body));
  // An empty payload is still one frame: the peer must see the message.
  if (chunks.length === 0) chunks.push(new Uint8Array(0));

  const checksum = crc32(payload);
  return chunks.map((chunk, index) => {
    const frame = new Uint8Array(HEADER_LEN + chunk.length);
    const view = new DataView(frame.buffer);
    frame[0] = VERSION;
    frame[1] = index === chunks.length - 1 ? FLAG_LAST : 0;
    view.setUint16(2, msgId, true);
    view.setUint16(4, index, true);
    view.setUint16(6, payload.length, true);
    view.setUint32(8, checksum, true);
    frame.set(chunk, HEADER_LEN);
    return frame;
  });
}

export function decodeHeader(frame: Uint8Array): FrameHeader {
  if (frame.length < HEADER_LEN) {
    throw new ProtocolError('proto.short', `frame is ${frame.length} bytes, need at least ${HEADER_LEN}`);
  }
  if (frame[0] !== VERSION) {
    throw new ProtocolError('proto.version', `unsupported protocol version ${frame[0]}`);
  }
  const view = new DataView(frame.buffer, frame.byteOffset, frame.byteLength);
  return {
    version: frame[0],
    last: (frame[1] & FLAG_LAST) !== 0,
    msgId: view.getUint16(2, true),
    fragIndex: view.getUint16(4, true),
    totalLen: view.getUint16(6, true),
    crc32: view.getUint32(8, true),
    body: frame.subarray(HEADER_LEN),
  };
}

/**
 * Collects fragments into payloads.
 *
 * Holds at most one incomplete message. A fragment carrying a new msgId
 * abandons whatever was in progress, which bounds memory to a single
 * reassembly buffer and makes a lost tail self-healing rather than a leak.
 */
export class Reassembler {
  private msgId: number | null = null;
  private parts: Uint8Array[] = [];
  private expectIndex = 0;
  private totalLen = 0;
  private checksum = 0;

  reset(): void {
    this.msgId = null;
    this.parts = [];
    this.expectIndex = 0;
  }

  /** Feed one frame. Returns a complete payload, or null if more are needed. */
  push(frame: Uint8Array): Uint8Array | null {
    const head = decodeHeader(frame);

    if (head.msgId !== this.msgId) {
      if (head.fragIndex !== 0) {
        this.reset();
        throw new ProtocolError('proto.orphan', 'first fragment of a message must have index 0');
      }
      this.msgId = head.msgId;
      this.parts = [];
      this.expectIndex = 0;
      this.totalLen = head.totalLen;
      this.checksum = head.crc32;
    }

    if (head.fragIndex !== this.expectIndex) {
      this.reset();
      throw new ProtocolError('proto.order', 'fragments must arrive in order');
    }
    if (head.totalLen !== this.totalLen || head.crc32 !== this.checksum) {
      this.reset();
      throw new ProtocolError('proto.mismatch', 'fragment disagrees about total length or checksum');
    }

    this.parts.push(head.body);
    this.expectIndex += 1;

    if (!head.last) {
      const seen = this.parts.reduce((n, p) => n + p.length, 0);
      if (seen > this.totalLen) {
        this.reset();
        throw new ProtocolError('proto.length', 'fragments exceed declared total length');
      }
      return null;
    }

    const total = this.parts.reduce((n, p) => n + p.length, 0);
    const payload = new Uint8Array(total);
    let offset = 0;
    for (const part of this.parts) {
      payload.set(part, offset);
      offset += part.length;
    }
    const expectedLen = this.totalLen;
    const expectedCrc = this.checksum;
    this.reset();

    if (payload.length !== expectedLen) {
      throw new ProtocolError('proto.length', `reassembled ${payload.length} bytes, header declared ${expectedLen}`);
    }
    if (crc32(payload) !== expectedCrc) {
      throw new ProtocolError('proto.crc', 'checksum mismatch on reassembled payload');
    }
    return payload;
  }
}

// --------------------------------------------------------------------------- //
// envelope
// --------------------------------------------------------------------------- //
export type Envelope = { v: number; id: string; t: EnvelopeType; op: string; a?: unknown };

/** Stable key order so two encoders produce identical bytes for identical content. */
function stableStringify(value: unknown): string {
  if (value === null || typeof value !== 'object') return JSON.stringify(value) ?? 'null';
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(',')}]`;
  const entries = Object.keys(value as Record<string, unknown>)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${stableStringify((value as Record<string, unknown>)[key])}`);
  return `{${entries.join(',')}}`;
}

export function encodeEnvelope(kind: EnvelopeType, op: string, id: string, args?: unknown): Uint8Array {
  if (!ENVELOPE_TYPES.includes(kind)) throw new ProtocolError('proto.type', `unknown envelope type ${kind}`);
  const body: Record<string, unknown> = { v: VERSION, id, t: kind, op };
  if (args !== undefined) body.a = args;
  return new TextEncoder().encode(stableStringify(body));
}

export function decodeEnvelope(payload: Uint8Array): Envelope {
  let body: unknown;
  try {
    body = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(payload));
  } catch (error) {
    throw new ProtocolError('proto.json', `payload is not valid JSON: ${(error as Error).message}`);
  }
  if (typeof body !== 'object' || body === null || Array.isArray(body)) {
    throw new ProtocolError('proto.json', 'envelope must be a JSON object');
  }
  const env = body as Record<string, unknown>;
  if (env.v !== VERSION) throw new ProtocolError('proto.version', `unsupported envelope version ${String(env.v)}`);
  if (typeof env.t !== 'string' || !ENVELOPE_TYPES.includes(env.t)) {
    throw new ProtocolError('proto.type', `unknown envelope type ${String(env.t)}`);
  }
  if (typeof env.op !== 'string' || !env.op) throw new ProtocolError('proto.op', 'envelope needs a non-empty op');
  if (typeof env.id !== 'string' || !env.id) throw new ProtocolError('proto.id', 'envelope needs a non-empty correlation id');
  return env as unknown as Envelope;
}
