// CompanionClient behaviour, driven through a fake transport.
//
// The point of these is the failure paths. A companion that leaves a promise
// pending when the link drops leaves the UI showing a spinner forever, and a
// companion that silently swallows an undecodable frame hides a firmware bug.

import assert from 'node:assert/strict';
import test from 'node:test';

import { CompanionClient, type CompanionTransport, type TransportKind, type TransportState } from './transport.ts';
import { Reassembler, decodeEnvelope, encodeEnvelope, encodeFrames } from './protocol.ts';

class FakeTransport implements CompanionTransport {
  readonly kind: TransportKind = 'wifi';
  maxFrameLength = 64; // small on purpose, so commands fragment
  state: TransportState = 'connected';
  sent: Uint8Array[] = [];
  private frameHandler: ((f: Uint8Array) => void) | null = null;
  private stateHandler: ((s: TransportState, d?: string) => void) | null = null;
  private readonly reassembler = new Reassembler();

  async connect(): Promise<void> {}
  async send(frame: Uint8Array): Promise<void> {
    this.sent.push(frame);
  }
  close(): void {}
  onFrame(h: (f: Uint8Array) => void): void {
    this.frameHandler = h;
  }
  onStateChange(h: (s: TransportState, d?: string) => void): void {
    this.stateHandler = h;
  }

  /** Reassemble whatever the client sent us, so a fake device can reply to it. */
  lastRequest() {
    let payload: Uint8Array | null = null;
    for (const frame of this.sent) payload = this.reassembler.push(frame);
    this.sent = [];
    assert.ok(payload, 'the client did not finish sending a message');
    return decodeEnvelope(payload);
  }

  deliver(payload: Uint8Array, msgId = 1): void {
    for (const frame of encodeFrames(payload, msgId, this.maxFrameLength)) this.frameHandler?.(frame);
  }

  drop(reason: string): void {
    this.state = 'closed';
    this.stateHandler?.('closed', reason);
  }
}

test('a command resolves with the reply that echoes its correlation id', async () => {
  const transport = new FakeTransport();
  const client = new CompanionClient(transport);

  const inflight = client.command('status.get');
  const request = transport.lastRequest();
  assert.equal(request.op, 'status.get');
  assert.equal(request.t, 'cmd');

  transport.deliver(encodeEnvelope('evt', 'status.get', request.id, { uptime_ms: 4321 }));
  const reply = await inflight;
  assert.equal((reply.a as { uptime_ms: number }).uptime_ms, 4321);
});

test('a command fragments and still correlates', async () => {
  const transport = new FakeTransport();
  const client = new CompanionClient(transport);

  const inflight = client.command('scan.wifi.start', { channels: [1, 6, 11], note: 'x'.repeat(200) });
  // Only the first frame is written synchronously; the rest go out across
  // await boundaries, so let the microtask queue drain before inspecting.
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.ok(transport.sent.length > 1, 'this payload must fragment at a 64-byte frame');

  const request = transport.lastRequest();
  transport.deliver(encodeEnvelope('ack', 'scan.wifi.start', request.id));
  await inflight;
});

test('an err reply rejects with its stable code', async () => {
  const transport = new FakeTransport();
  const client = new CompanionClient(transport);

  const inflight = client.command('tx.session.open');
  const request = transport.lastRequest();
  transport.deliver(encodeEnvelope('err', 'tx.session.open', request.id, { code: 'tx.denied', message: 'no allow-list' }));

  await assert.rejects(inflight, /tx\.denied/);
});

test('a dropped link fails every in-flight command instead of hanging', async () => {
  const transport = new FakeTransport();
  const client = new CompanionClient(transport);

  const first = client.command('status.get');
  transport.lastRequest();
  const second = client.command('caps.get');
  transport.lastRequest();

  transport.drop('device went away');

  await assert.rejects(first, /device went away/);
  await assert.rejects(second, /device went away/);
});

test('a command that is never answered rejects on its timeout', async () => {
  const transport = new FakeTransport();
  const client = new CompanionClient(transport, 20);
  const inflight = client.command('status.get');
  transport.lastRequest();
  await assert.rejects(inflight, /no reply to status\.get/);
});

test('unsolicited envelopes reach the event handler, not a waiter', async () => {
  const transport = new FakeTransport();
  const client = new CompanionClient(transport);
  const seen: string[] = [];
  client.onEvent((envelope) => seen.push(envelope.op));

  transport.deliver(encodeEnvelope('evt', 'scan.wifi.ap', 'unsolicited-1', { bssid: 'AA:BB:CC:00:00:01' }));
  assert.deepEqual(seen, ['scan.wifi.ap']);
});

test('an undecodable frame is surfaced rather than silently dropped', () => {
  const transport = new FakeTransport();
  const client = new CompanionClient(transport);
  const errors: string[] = [];
  client.onDecodeError((error) => errors.push(error.message));

  // Valid framing, payload that is not a valid envelope.
  transport.deliver(new TextEncoder().encode('{"not":"an envelope"}'), 7);
  assert.equal(errors.length, 1);
  assert.match(errors[0], /proto\.version/);
});

test('a corrupt frame is surfaced and does not wedge the reassembler', async () => {
  const transport = new FakeTransport();
  const client = new CompanionClient(transport);
  const errors: string[] = [];
  client.onDecodeError((error) => errors.push(error.message));

  const junk = new Uint8Array(16);
  junk[0] = 0x09; // wrong protocol version
  transport.deliver(junk, 3);
  assert.ok(errors.length >= 1);

  // The client must still work afterwards.
  const inflight = client.command('status.get');
  const request = transport.lastRequest();
  transport.deliver(encodeEnvelope('evt', 'status.get', request.id, { ok: true }));
  await inflight;
});

test('correlation ids are not reused between commands', () => {
  const transport = new FakeTransport();
  const client = new CompanionClient(transport, 50);
  const ids = new Set<string>();
  for (let i = 0; i < 25; i += 1) {
    client.command('status.get').catch(() => {});
    ids.add(transport.lastRequest().id);
  }
  assert.equal(ids.size, 25);
});
