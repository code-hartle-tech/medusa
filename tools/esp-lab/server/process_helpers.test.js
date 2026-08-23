import assert from 'node:assert/strict';
import test from 'node:test';

import {
  SERIAL_STOP_TIMEOUT_MESSAGE,
  bindUsbDescriptor,
  completeSerialStop,
  consumeBuildTicket,
  createLineAccumulator,
  detectedBoardError,
  exportArtifactPlan,
  isProductReadyChip,
  requestSerialStop,
  selectUsbDescriptor,
  usbDescriptorCandidates,
  usbDescriptorDiscoveryCommand,
  usbDescriptorIdentityError,
  usbSerialPorts,
} from './process_helpers.js';

test('line accumulators keep stdout and stderr fragments independent', () => {
  const stdout = [];
  const stderr = [];
  const out = createLineAccumulator((line) => stdout.push(line), (line) => line.replace(/\x1b\[[0-9;]*m/g, ''));
  const err = createLineAccumulator((line) => stderr.push(line), (line) => line.replace(/\x1b\[[0-9;]*m/g, ''));

  out.write(Buffer.from('UNATTENDED_DUMP_'));
  err.write(Buffer.from('warning fra'));
  out.write(Buffer.from('JSON {"rows":2}\r\nnext\ntrail'));
  err.write(Buffer.from('gment\n'));
  out.flush();
  err.flush();
  out.flush();

  assert.deepEqual(stdout, ['UNATTENDED_DUMP_JSON {"rows":2}', 'next', 'trail']);
  assert.deepEqual(stderr, ['warning fragment']);
});

test('line accumulator preserves split UTF-8 and split ANSI sequences', () => {
  const lines = [];
  const parser = createLineAccumulator((line) => lines.push(line), (line) => line.replace(/\x1b\[[0-9;]*m/g, ''));
  const bytes = Buffer.from('\x1b[32mMedusa é\x1b[0m\n');
  parser.write(bytes.subarray(0, 2));
  parser.write(bytes.subarray(2, bytes.length - 4));
  parser.write(bytes.subarray(bytes.length - 4));
  parser.flush();
  assert.deepEqual(lines, ['Medusa é']);
});

test('forced serial-stop timeout rejects queued work and retains ownership', () => {
  const timers = [];
  const kills = [];
  const session = {
    child: { kill: (signal) => kills.push(signal || 'SIGTERM') },
    stopping: false,
    stopError: null,
    afterClose: [],
    stopTimer: null,
    forceTimer: null,
  };
  let owner = session;
  let retrievalStarted = false;
  let callbackError;
  const options = {
    isCurrent: () => owner === session,
    afterClose: (error) => {
      callbackError = error;
      if (!error) retrievalStarted = true;
    },
    setTimer: (callback) => { timers.push(callback); return timers.length; },
    queueTask: (callback) => callback(),
  };

  requestSerialStop(session, options);
  assert.deepEqual(kills, ['SIGTERM']);
  timers.shift()();
  assert.deepEqual(kills, ['SIGTERM', 'SIGKILL']);
  timers.shift()();

  assert.equal(owner, session);
  assert.equal(retrievalStarted, false);
  assert.equal(callbackError?.message, SERIAL_STOP_TIMEOUT_MESSAGE);
  let retryError;
  requestSerialStop(session, { ...options, afterClose: (error) => { retryError = error; } });
  assert.equal(retryError?.message, SERIAL_STOP_TIMEOUT_MESSAGE);
});

test('normal serial close releases queued work without an error', () => {
  const session = { afterClose: [] };
  let called = 0;
  session.afterClose.push((error) => { assert.equal(error, undefined); called += 1; });
  completeSerialStop(session, (callback) => callback());
  assert.equal(called, 1);
});

test('build tickets are socket-local, one-time, chip-bound, and default-safe', () => {
  const passiveId = 'a'.repeat(48);
  const activeId = 'b'.repeat(48);
  const wrongChipId = 'c'.repeat(48);
  const passive = { chip: 'esp32-c3', variant: 'target', attack: 'unattended', dir: '/tmp/passive' };
  const active = { chip: 'esp32-c3', variant: 'target', attack: 'deauth', dir: '/tmp/active' };
  const socketBuilds = new Map([[passiveId, passive], [activeId, active], [wrongChipId, passive]]);

  assert.deepEqual(
    consumeBuildTicket(socketBuilds, { buildId: undefined, chip: 'esp32-c3', activeLabEnabled: false }),
    { ok: false, error: 'unknown or expired build' },
  );
  assert.deepEqual(
    consumeBuildTicket(new Map(), { buildId: passiveId, chip: 'esp32-c3', activeLabEnabled: false }),
    { ok: false, error: 'unknown or expired build' },
  );
  assert.deepEqual(
    consumeBuildTicket(socketBuilds, { buildId: activeId, chip: 'esp32-c3', activeLabEnabled: false }),
    { ok: false, error: 'only a recorded passive unattended build may be flashed in the default product' },
  );
  assert.equal(socketBuilds.has(activeId), false);
  assert.deepEqual(
    consumeBuildTicket(socketBuilds, { buildId: wrongChipId, chip: 'esp32-s3', activeLabEnabled: false }),
    { ok: false, error: 'build does not match detected chip' },
  );
  const allowed = consumeBuildTicket(socketBuilds, { buildId: passiveId, chip: 'esp32-c3', activeLabEnabled: false });
  assert.equal(allowed.ok, true);
  assert.equal(allowed.record, passive);
  assert.deepEqual(
    consumeBuildTicket(socketBuilds, { buildId: passiveId, chip: 'esp32-c3', activeLabEnabled: false }),
    { ok: false, error: 'unknown or expired build' },
  );
});

test('privileged hardware actions are bound to the socket detected board', () => {
  const identity = bindUsbDescriptor({
    port: '/dev/cu.usbmodem-test', ports: ['/dev/cu.usbmodem-test'],
    stableId: 'usb:1234:abcd:02:00:00:00:00:01:02%3A00%3A00%3A00%3A00%3A01', usbSerial: '02:00:00:00:00:01',
    vid: '0x1234', pid: '0xabcd', hardwareId: '02:00:00:00:00:01', ambiguousPorts: false,
  }, 'esp32-c3');
  const detected = { ...identity, deviceIdentity: identity };
  assert.equal(
    detectedBoardError(detected, { port: detected.port, chip: detected.chipKey }),
    null,
  );
  assert.equal(
    detectedBoardError({}, { port: detected.port, chip: detected.chipKey }),
    'detect a supported device before using the hardware bridge',
  );
  assert.equal(
    detectedBoardError(detected, { port: '/dev/cu.other', chip: detected.chipKey }),
    'port does not match the detected device',
  );
  assert.equal(
    detectedBoardError(detected, { port: detected.port, chip: 'esp32-s3' }),
    'chip does not match the detected device',
  );
});

const descriptorListing = (...ports) => ({ detected_ports: ports });
const descriptorPort = ({
  address = '/dev/cu.usbmodem-test', serialNumber = '02:00:00:00:00:01',
  vid = '0x1234', pid = '0xABCD', hardwareId = serialNumber,
} = {}) => ({
  port: {
    address, protocol: 'serial', protocol_label: 'Serial Port (USB)',
    properties: { serialNumber, vid, pid },
    hardware_id: hardwareId,
  },
});

test('Product descriptor discovery is non-resetting and never invokes esptool', () => {
  const command = usbDescriptorDiscoveryCommand('/opt/bin/arduino-cli');
  assert.deepEqual(command, {
    executable: '/opt/bin/arduino-cli',
    args: ['board', 'list', '--format', 'json'],
  });
  assert.equal(`${command.executable} ${command.args.join(' ')}`.includes('esptool'), false);
  assert.equal(command.args.includes('--probe'), false);
  assert.equal(command.args.includes('flash-id'), false);
});

test('USB descriptor parsing deduplicates macOS tty/callout aliases', () => {
  const listing = descriptorListing(
    descriptorPort(),
    descriptorPort({ address: '/dev/tty.usbmodem-test' }),
  );
  const candidates = usbDescriptorCandidates(listing);
  assert.equal(candidates.length, 1);
  assert.equal(candidates[0].port, '/dev/cu.usbmodem-test');
  assert.deepEqual(candidates[0].ports, ['/dev/cu.usbmodem-test', '/dev/tty.usbmodem-test']);
  assert.equal(candidates[0].ambiguousPorts, false);
});

test('stable USB identity accepts only the same descriptor on the exact port', () => {
  const candidate = usbDescriptorCandidates(descriptorListing(descriptorPort()))[0];
  const expected = bindUsbDescriptor(candidate, 'esp32-c3');
  const matching = usbDescriptorCandidates(descriptorListing(descriptorPort()));
  const changedBoard = usbDescriptorCandidates(descriptorListing(descriptorPort({
    serialNumber: '02:00:00:00:00:02',
  })));
  const changedHardwareId = usbDescriptorCandidates(descriptorListing(descriptorPort({
    hardwareId: 'different-usb-hardware-id',
  })));

  assert.equal(usbDescriptorIdentityError(expected, matching), null);
  assert.equal(
    usbDescriptorIdentityError(expected, changedBoard),
    'USB device identity changed on the detected port; detect the board again',
  );
  assert.equal(
    usbDescriptorIdentityError(expected, changedHardwareId),
    'USB device identity changed on the detected port; detect the board again',
  );
});

test('stable USB identity rejects missing descriptors and a relocated path', () => {
  const expected = bindUsbDescriptor(
    usbDescriptorCandidates(descriptorListing(descriptorPort()))[0],
    'esp32-c3',
  );
  const relocated = usbDescriptorCandidates(descriptorListing(descriptorPort({
    address: '/dev/cu.usbmodem-other',
  })));

  assert.equal(
    usbDescriptorIdentityError(expected, []),
    'USB device identity could not be re-verified on its original port',
  );
  assert.equal(
    usbDescriptorIdentityError(expected, relocated),
    'USB device identity could not be re-verified on its original port',
  );
});

test('Product readiness rejects missing serials, ambiguous boards, and unproven chips', () => {
  const missingSerial = descriptorListing(
    descriptorPort({ serialNumber: '', hardwareId: '' }),
    { port: { address: '/dev/cu.Bluetooth-Incoming-Port', protocol: 'serial', properties: {} } },
  );
  assert.deepEqual(usbDescriptorCandidates(missingSerial), []);
  assert.deepEqual(usbSerialPorts(missingSerial), ['/dev/cu.usbmodem-test']);

  const candidates = usbDescriptorCandidates(descriptorListing(
    descriptorPort(),
    descriptorPort({
      address: '/dev/cu.usbmodem-other',
      serialNumber: '02:00:00:00:00:02',
    }),
  ));
  assert.equal(candidates.length, 2);
  assert.equal(selectUsbDescriptor(candidates), null);
  assert.equal(selectUsbDescriptor(candidates, '/dev/cu.usbmodem-other')?.usbSerial, '02:00:00:00:00:02');

  assert.equal(isProductReadyChip('esp32-c3'), true);
  assert.equal(isProductReadyChip('esp32-s3'), true);
  assert.equal(isProductReadyChip('esp32-c5'), false);
  assert.equal(isProductReadyChip('esp32-c6'), false);
  assert.equal(bindUsbDescriptor(candidates[0], 'esp32-c6'), null);

  const duplicatedIdentity = usbDescriptorCandidates(descriptorListing(
    descriptorPort(),
    descriptorPort({ address: '/dev/cu.usbmodem-other' }),
  ));
  assert.equal(duplicatedIdentity.length, 1);
  assert.equal(duplicatedIdentity[0].ambiguousPorts, true);
  assert.equal(selectUsbDescriptor(duplicatedIdentity, '/dev/cu.usbmodem-test'), null);
});

test('export plans redact only on an explicit opt-in', () => {
  const base = { format: 'posture', exportId: 'abc', inPath: '/tmp/in.json', outDir: '/tmp', inventoryDb: '/tmp/inv.sqlite' };

  const plain = exportArtifactPlan(base);
  assert.equal(plain.pseudonymized, false);
  assert.ok(!plain.args.includes('--pseudonymize'));
  assert.ok(!plain.name.includes('pseudonymized'));

  const redacted = exportArtifactPlan({ ...base, pseudonymize: true });
  assert.equal(redacted.pseudonymized, true);
  assert.ok(redacted.args.includes('--pseudonymize'));
  assert.match(redacted.name, /^medusa_posture_pseudonymized_abc\.html$/);
});

test('a truthy-but-not-true opt-in never redacts silently', () => {
  const base = { format: 'wigle', exportId: 'abc', inPath: '/tmp/in.json' };

  for (const sloppy of ['true', 1, 'yes', {}, []]) {
    const plan = exportArtifactPlan({ ...base, pseudonymize: sloppy });
    assert.equal(plan.pseudonymized, false, `pseudonymize=${JSON.stringify(sloppy)} must not redact`);
  }
});

test('the local inventory always receives real rows, redacted export or not', () => {
  const base = { format: 'airodump', exportId: 'abc', inPath: '/tmp/in.json', inventoryDb: '/tmp/inv.sqlite' };

  for (const pseudonymize of [false, true]) {
    const plan = exportArtifactPlan({ ...base, pseudonymize });
    const dbIndex = plan.args.indexOf('--db');
    assert.ok(dbIndex !== -1, 'inventory must still be written');
    assert.equal(plan.args[dbIndex + 1], '/tmp/inv.sqlite');
  }
});

test('an unknown export format yields no plan at all', () => {
  assert.equal(exportArtifactPlan({ format: 'exfil', exportId: 'abc' }), null);
});

// ---- Kismet remote-capture datasource -------------------------------------
//
// Verified against a conforming mock, not a real Kismet server: Kismet does not
// install on this workstation. That is a real limit and the capability note
// says so — this proves the client behaves as designed, not that a live server
// accepts it.

import { WebSocketServer } from 'ws';
import {
  KISMET_ERRORS,
  KismetDatasource,
  kismetUrl,
  validateKismetConfig,
} from './kismet_source.js';

/** ws keeps the process alive until every client socket is gone. */
async function shutdown(server) {
  for (const client of server.clients) client.terminate();
  await new Promise((resolve) => server.close(resolve));
}

const goodConfig = {
  host: '127.0.0.1',
  port: 2501,
  apiKey: 'a-datasource-role-key',
  sourceName: 'medusa-lab',
};

test('config validation refuses what a server would reject anyway', () => {
  assert.equal(validateKismetConfig(goodConfig).ok, true);

  assert.equal(validateKismetConfig({ ...goodConfig, host: '' }).error, KISMET_ERRORS.CONFIG);
  assert.equal(validateKismetConfig({ ...goodConfig, port: 0 }).error, KISMET_ERRORS.CONFIG);
  assert.equal(validateKismetConfig({ ...goodConfig, apiKey: 'short' }).error, KISMET_ERRORS.AUTH);
  assert.equal(validateKismetConfig({ ...goodConfig, sourceName: 'no/slashes' }).error, KISMET_ERRORS.CONFIG);
});

test('an API key is never sent in plaintext to a remote host by accident', () => {
  // The authenticated transport was chosen over the unauthenticated legacy one
  // precisely so the key means something; leaking it in cleartext to a remote
  // host would undo that.
  const remote = { ...goodConfig, host: 'kismet.example.org' };
  const refused = validateKismetConfig(remote);
  assert.equal(refused.ok, false);
  assert.equal(refused.error, KISMET_ERRORS.CONFIG);

  assert.equal(validateKismetConfig({ ...remote, tls: true }).ok, true, 'TLS is fine');
  assert.equal(validateKismetConfig({ ...remote, insecure: true }).ok, true, 'explicit opt-out is fine');
  assert.equal(validateKismetConfig(goodConfig).ok, true, 'loopback needs neither');
});

test('the URL targets the documented remote-capture endpoint', () => {
  assert.equal(kismetUrl(goodConfig), 'ws://127.0.0.1:2501/datasource/remote/remotesource.ws');
  assert.equal(
    kismetUrl({ ...goodConfig, tls: true, host: 'k.example', port: 443 }),
    'wss://k.example:443/datasource/remote/remotesource.ws',
  );
});

test('a source is only usable once the server has ACCEPTED it', async () => {
  const server = new WebSocketServer({ port: 0, host: '127.0.0.1' });
  const port = await new Promise((r) => server.on('listening', () => r(server.address().port)));

  let sawAuth = false;
  let announced = null;
  const packets = [];
  server.on('connection', (socket, req) => {
    sawAuth = req.headers.authorization === `Bearer ${goodConfig.apiKey}`;
    socket.on('message', (raw) => {
      const msg = JSON.parse(String(raw));
      if (msg.type === 'source.announce') {
        announced = msg;
        socket.send(JSON.stringify({ type: 'source.accepted', uuid: 'mock-uuid' }));
      } else if (msg.type === 'source.packet') {
        packets.push(msg.packet);
      }
    });
  });

  const source = new KismetDatasource({ ...goodConfig, port });
  await source.open();

  assert.ok(sawAuth, 'the API key travelled as an Authorization header');
  assert.equal(announced.name, 'medusa-lab', 'the source announced itself before sending anything');

  assert.equal(source.sendPacket({ bssid: '02:00:00:00:00:01' }), true);
  await new Promise((r) => setTimeout(r, 60));
  assert.equal(packets.length, 1, 'the observation reached the server');

  source.close();
  await shutdown(server);
});

test('a rejected source fails rather than looking connected', async () => {
  const server = new WebSocketServer({ port: 0, host: '127.0.0.1' });
  const port = await new Promise((r) => server.on('listening', () => r(server.address().port)));
  server.on('connection', (socket) => {
    socket.on('message', () => socket.send(JSON.stringify({ type: 'source.rejected', reason: 'unknown source type' })));
  });

  const source = new KismetDatasource({ ...goodConfig, port });
  await assert.rejects(source.open(), (error) => {
    assert.equal(error.code, KISMET_ERRORS.PROTOCOL);
    assert.match(error.message, /unknown source type/);
    return true;
  });
  source.close();
  await shutdown(server);
});

test('packets are refused before acceptance rather than queued', () => {
  // Buffering against a source the server never accepted eventually delivers a
  // burst of stale observations with misleading timestamps.
  const source = new KismetDatasource(goodConfig);
  assert.equal(source.sendPacket({ bssid: '02:00:00:00:00:01' }), false);
  assert.equal(source.sent, 0);
});
