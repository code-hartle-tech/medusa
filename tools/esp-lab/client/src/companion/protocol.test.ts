// Runs the same conformance vectors as the Python reference codec. If these
// two disagree about a single byte, one of them is wrong and this fails.

import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import {
  HEADER_LEN,
  ProtocolError,
  Reassembler,
  crc32,
  decodeEnvelope,
  decodeHeader,
  encodeEnvelope,
  encodeFrames,
} from './protocol.ts';

const here = dirname(fileURLToPath(import.meta.url));
const vectors = JSON.parse(
  readFileSync(join(here, '../../../../companion/vectors/frames.json'), 'utf8'),
);

const hex = (bytes: Uint8Array) => Buffer.from(bytes).toString('hex');
const unhex = (s: string) => new Uint8Array(Buffer.from(s, 'hex'));

test('crc32 matches the reference implementation on known values', () => {
  // Independently known CRC-32/ISO-HDLC results.
  assert.equal(crc32(new TextEncoder().encode('hi')), 0xd8932aac);
  assert.equal(crc32(new Uint8Array(0)), 0);
  assert.equal(crc32(new TextEncoder().encode('123456789')), 0xcbf43926);
});

test('header layout matches the hand-computed bytes', () => {
  const frames = encodeFrames(new TextEncoder().encode('hi'), 1, 64);
  assert.equal(frames.length, 1);
  assert.equal(
    hex(frames[0]),
    // ver flags msg_id frag_idx total_len crc32(LE)  payload
    '01' + '01' + '0100' + '0000' + '0200' + 'ac2a93d8' + '6869',
  );
});

test('encode vectors match the Python reference byte for byte', () => {
  for (const c of vectors.encode) {
    const frames = encodeFrames(unhex(c.payload_hex), c.msg_id, c.max_frame_len);
    assert.deepEqual(frames.map(hex), c.frames_hex, `vector ${c.name}: ${c.why}`);
  }
});

test('decode-failure vectors produce the same stable codes', () => {
  for (const c of vectors.decode_errors) {
    const r = new Reassembler();
    assert.throws(
      () => {
        for (const f of c.frames_hex) r.push(unhex(f));
      },
      (error: unknown) => {
        assert.ok(error instanceof ProtocolError, `vector ${c.name} threw a non-protocol error`);
        assert.equal(error.code, c.code, `vector ${c.name}: ${c.why}`);
        return true;
      },
    );
  }
});

test('a full round trip survives fragmentation at the default BLE MTU', () => {
  const payload = encodeEnvelope('cmd', 'scan.wifi.start', 'c0ffee', { channels: [1, 6, 11] });
  const frames = encodeFrames(payload, 3, 20);
  assert.ok(frames.length > 1, 'this payload must fragment at a 20-byte frame');

  const r = new Reassembler();
  let out: Uint8Array | null = null;
  for (const f of frames) out = r.push(f);
  assert.ok(out);
  assert.deepEqual(decodeEnvelope(out).op, 'scan.wifi.start');
});

test('envelope encoding is deterministic regardless of key order', () => {
  const a = encodeEnvelope('cmd', 'status.get', '1', { b: 2, a: 1 });
  const b = encodeEnvelope('cmd', 'status.get', '1', { a: 1, b: 2 });
  assert.deepEqual(hex(a), hex(b));
});

test('the encoder emits no incidental whitespace', () => {
  const raw = new TextDecoder().decode(encodeEnvelope('cmd', 'status.get', '1', { a: 1 }));
  assert.ok(!raw.includes(' '), raw);
});

test('malformed envelopes are refused with stable codes', () => {
  const cases: Array<[string, string]> = [
    ['not json', 'proto.json'],
    ['[]', 'proto.json'],
    ['{"v":2,"id":"1","t":"cmd","op":"x"}', 'proto.version'],
    ['{"v":1,"id":"1","t":"nope","op":"x"}', 'proto.type'],
    ['{"v":1,"id":"1","t":"cmd"}', 'proto.op'],
    ['{"v":1,"t":"cmd","op":"x"}', 'proto.id'],
  ];
  for (const [raw, code] of cases) {
    assert.throws(
      () => decodeEnvelope(new TextEncoder().encode(raw)),
      (error: unknown) => {
        assert.ok(error instanceof ProtocolError);
        assert.equal(error.code, code, `input ${raw}`);
        return true;
      },
    );
  }
});

test('a frame length with no room for payload is refused', () => {
  assert.throws(
    () => encodeFrames(new Uint8Array([1]), 1, HEADER_LEN),
    (error: unknown) => (error as ProtocolError).code === 'proto.mtu',
  );
});

test('a new message abandons an incomplete one instead of splicing', () => {
  const first = encodeFrames(new Uint8Array(600), 9, 244);
  const second = encodeFrames(new TextEncoder().encode('second'), 10, 244);
  const r = new Reassembler();
  assert.equal(r.push(first[0]), null);
  const out = r.push(second[0]);
  assert.ok(out);
  assert.equal(new TextDecoder().decode(out), 'second');
});

test('decodeHeader reports last-fragment state correctly', () => {
  const frames = encodeFrames(new Uint8Array(600), 1, 244);
  assert.equal(decodeHeader(frames[0]).last, false);
  assert.equal(decodeHeader(frames[frames.length - 1]).last, true);
});
