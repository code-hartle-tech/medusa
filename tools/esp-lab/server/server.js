// Medusa Wi-Fi Lab — backend.
// A thin, hardened driver over ../esp-hw/esp_hw.py: detect / build / flash /
// stream serial, over socket.io. Same loopback + token + Origin discipline as
// the rest of the workspace: this is a privileged LOCAL control surface (it
// flashes firmware), so we never expose it off-host and never let a foreign
// page drive it. Fixed argv, no shell, validated port arg.
import express from 'express';
import cors from 'cors';
import http from 'http';
import crypto from 'crypto';
import path from 'path';
import fs from 'fs';
import os from 'os';
import { fileURLToPath } from 'url';
import { execFile, spawn } from 'child_process';
import { Server } from 'socket.io';
import {
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

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PORT = 4300;
const HOST = '127.0.0.1';

const ESP_HW_DIR = process.env.MEDUSA_ESP_HW_DIR || path.resolve(__dirname, '..', '..', 'esp-hw');
const ESP_HW = path.join(ESP_HW_DIR, 'esp_hw.py');
const ESP_REPORT = path.join(ESP_HW_DIR, 'esp_report.py');
const READ_SERIAL = path.join(ESP_HW_DIR, 'read_serial.py');
const ARDUINO_CLI = process.env.MEDUSA_ARDUINO_CLI || 'arduino-cli';
const INVENTORY_DB = path.join('/tmp', 'medusa_inventory.sqlite');

// Plugins. Bundled examples ship read-only with the repo; anything the
// operator writes lands beside them in a separate directory so an update
// never overwrites their work.
const DEVKIT_DIR = process.env.MEDUSA_DEVKIT_DIR || path.resolve(__dirname, '..', '..', '..', 'devkit');
const PLUGIN_EXAMPLES_DIR = path.join(DEVKIT_DIR, 'examples');
const PLUGIN_USER_DIR = process.env.MEDUSA_PLUGIN_DIR
  || path.join(os.homedir(), 'Library', 'Application Support', 'Medusa', 'plugins');
const PLUGIN_LINT = path.join(DEVKIT_DIR, 'medusa-plugin');
const JOURNEY_FILE = path.join(DEVKIT_DIR, 'journey', 'journey.json');

const ALLOWED_ORIGINS = (
  process.env.ESP_LAB_ORIGINS ||
  'http://localhost:5250,http://127.0.0.1:5250,http://localhost:4173,http://127.0.0.1:4173'
).split(',').map((o) => o.trim()).filter(Boolean);

const TOKEN = process.env.ESP_LAB_TOKEN || crypto.randomBytes(24).toString('hex');
const isAllowedOrigin = (o) => ALLOWED_ORIGINS.includes(o);

const DEV_PORT_RE = /^\/dev\/[A-Za-z0-9._-]+$/;
const MAC_RE = /^([0-9a-f]{2}:){5}[0-9a-f]{2}$/i;
const CHIPS = new Set(['esp32', 'esp32-s3', 'esp32-c3', 'esp32-c6', 'esp32-c5']);
const RISCV = new Set(['esp32-c3', 'esp32-c6', 'esp32-c5']);
const BUILD_VARIANTS = new Set(['stock', 'patched', 'target']);
const BUILD_ATTACKS = new Set(['deauth', 'csa', 'authflood', 'beaconspam', 'probeflood', 'blespam', 'unattended']);
const TARGETLESS_ATTACKS = new Set(['beaconspam', 'probeflood', 'blespam']);
// The public/default product exposes only the passive unattended build. The
// historical research lab can be enabled explicitly on a private, authorized
// workstation, but is never activated merely by reaching this local server.
const ACTIVE_LAB_ENABLED = process.env.MEDUSA_ACTIVE_LAB === '1';
const ANSI = /\x1b\[[0-9;]*m/g;
const HARDWARE_ENV = {
  ...process.env,
  PYTHONUNBUFFERED: '1',
  MEDUSA_EXACT_PORT: '1',
};
let hardwareChild = null;
let serialSession = null;

function stopSerial(socket, afterClose) {
  const session = serialSession;
  if (!session || session.socket !== socket) return false;
  requestSerialStop(session, {
    isCurrent: () => serialSession === session,
    afterClose,
    onFailure: (error) => socket.emit('serial:line', { raw: error.message, rc: null, state: 'error' }),
  });
  return true;
}

// User-selected or tool-reported chip label -> esp_hw.py --chip value.
function chipToKey(s) {
  if (!s) return null;
  const n = String(s).trim().toLowerCase().replace(/[()]/g, '').replace(/\s+/g, '-');
  if (n.startsWith('esp32-c3')) return 'esp32-c3';
  if (n.startsWith('esp32-c6')) return 'esp32-c6';
  if (n.startsWith('esp32-c5')) return 'esp32-c5';
  if (n.startsWith('esp32-s3')) return 'esp32-s3';
  if (n === 'esp32' || n.startsWith('esp32-d') || n.startsWith('esp32-pico')) return 'esp32';
  if (CHIPS.has(n)) return n;
  return null;
}
const archOf = (key) => (RISCV.has(key) ? 'RISC-V' : 'Xtensa');

const app = express();
app.use(cors({ origin: (origin, cb) => cb(null, !origin || isAllowedOrigin(origin)) }));
app.get('/auth-token', (req, res) => {
  const origin = req.headers.origin;
  if (origin && !isAllowedOrigin(origin)) return res.status(403).json({ error: 'origin' });
  res.json({ token: TOKEN });
});
app.get('/health', (_req, res) => res.json({ ok: true, espHw: fs.existsSync(ESP_HW) }));
// download a capture PCAP (fixed prefix, no traversal; origin-checked)
app.get('/capture/:name', (req, res) => {
  const origin = req.headers.origin;
  if (origin && !isAllowedOrigin(origin)) return res.status(403).end();
  const name = req.params.name;
  if (!/^medusa_[A-Za-z0-9._-]+\.(pcap|hc22000|csv|html)$/.test(name)) return res.status(400).end();
  const fp = path.join('/tmp', name);
  if (!fs.existsSync(fp)) return res.status(404).end();
  res.download(fp, name);
});

const server = http.createServer(app);
const io = new Server(server, {
  cors: { origin: ALLOWED_ORIGINS, methods: ['GET', 'POST'] },
  allowRequest: (req, cb) => {
    const o = req.headers.origin;
    cb(null, !o || isAllowedOrigin(o));
  },
});
io.use((socket, next) => {
  const o = socket.handshake.headers.origin;
  if (o && !isAllowedOrigin(o)) return next(new Error('origin'));
  if ((socket.handshake.auth && socket.handshake.auth.token) !== TOKEN) return next(new Error('token'));
  next();
});

function runScript(socket, script, args, onDone, onLine) {
  if (hardwareChild || serialSession) {
    socket.emit('log', { line: 'The hardware or serial port is already in use.', level: 'error' });
    if (onDone) onDone(-1);
    return;
  }
  socket.emit('log', { line: `$ ${path.basename(script)} ${args.join(' ')}`, level: 'cmd' });
  const child = execFile('python3', [script, ...args], {
    cwd: ESP_HW_DIR, env: HARDWARE_ENV, shell: false, maxBuffer: 32 * 1024 * 1024,
  });
  hardwareChild = child;
  socket.data.hwChild = child;
  let settled = false;
  const deliver = (line) => {
    if (onLine) onLine(line);
    socket.emit('log', { line });
  };
  // stdout and stderr are unrelated byte streams. A separate accumulator for
  // each prevents a split structured record from being emitted as fragments
  // or accidentally joined to a fragment from the other stream.
  const stdoutLines = createLineAccumulator(deliver, (line) => line.replace(ANSI, ''));
  const stderrLines = createLineAccumulator(deliver, (line) => line.replace(ANSI, ''));
  const finish = (code) => {
    if (settled) return;
    stdoutLines.flush();
    stderrLines.flush();
    settled = true;
    if (hardwareChild === child) hardwareChild = null;
    if (socket.data.hwChild === child) socket.data.hwChild = null;
    if (onDone) onDone(code);
  };
  child.stdout.on('data', (buf) => stdoutLines.write(buf));
  child.stderr.on('data', (buf) => stderrLines.write(buf));
  child.on('close', (code) => finish(code));
  child.on('error', (err) => { socket.emit('log', { line: err.message, level: 'error' }); finish(-1); });
}

function runPy(socket, args, onDone, onLine) {
  return runScript(socket, ESP_HW, args, onDone, onLine);
}

function discoverUsbDescriptors(socket, onDone) {
  if (hardwareChild || serialSession) {
    onDone('hardware busy');
    return;
  }

  const command = usbDescriptorDiscoveryCommand(ARDUINO_CLI);
  const child = execFile(command.executable, command.args, {
    cwd: ESP_HW_DIR,
    env: HARDWARE_ENV,
    timeout: 30000,
    maxBuffer: 4 * 1024 * 1024,
  }, (error, stdout) => {
    if (hardwareChild === child) hardwareChild = null;
    if (socket.data.hwChild === child) socket.data.hwChild = null;
    if (error) {
      onDone('non-invasive USB descriptor discovery failed');
      return;
    }
    let listing;
    try { listing = JSON.parse(stdout || '{}'); } catch {
      onDone('non-invasive USB descriptor discovery returned invalid data');
      return;
    }
    onDone(null, {
      candidates: usbDescriptorCandidates(listing),
      ports: usbSerialPorts(listing),
    });
  });
  hardwareChild = child;
  socket.data.hwChild = child;
}

function reverifyDetectedBoard(socket, onDone) {
  const expected = socket.data.deviceIdentity;
  if (!expected) {
    onDone('no verified USB device identity is available; detect the board again');
    return;
  }
  socket.emit('log', { line: `Re-verifying USB identity ${expected.stableId} on ${expected.port} without opening the serial port…` });
  discoverUsbDescriptors(socket, (discoveryError, discovery) => {
    if (!socket.connected) {
      onDone('hardware client disconnected during identity check');
      return;
    }
    onDone(discoveryError || usbDescriptorIdentityError(expected, discovery?.candidates));
  });
}

function runVerifiedPy(socket, args, onDone, onLine) {
  reverifyDetectedBoard(socket, (identityError) => {
    if (identityError) {
      onDone(-1, identityError);
      return;
    }
    runPy(socket, args, (code) => onDone(code, null), onLine);
  });
}

io.on('connection', (socket) => {
  socket.data = {};
  socket.data.builds = new Map();
  socket.data.chipSelections = new Map();
  socket.data.serialStartGeneration = 0;

  // ---- detect ----
  socket.on('detect', ({ requestId, port, chip } = {}) => {
    if (hardwareChild || serialSession)
      return socket.emit('detect:result', { found: false, error: 'hardware busy', requestId });
    socket.data.port = null;
    socket.data.chipKey = null;
    socket.data.usbSerial = null;
    socket.data.deviceIdentity = null;
    discoverUsbDescriptors(socket, (discoveryError, discovery) => {
      const candidates = discovery?.candidates || [];
      const ports = discovery?.ports || [];
      const publicCandidates = candidates.map((candidate) => ({
        port: candidate.port,
        ports: candidate.ports,
        usbSerial: candidate.usbSerial,
        vid: candidate.vid,
        pid: candidate.pid,
        hardwareId: candidate.hardwareId,
        stableId: candidate.stableId,
        ambiguousPorts: candidate.ambiguousPorts,
      }));
      if (discoveryError) return socket.emit('detect:result', {
        found: false, verified: false, ports, candidates: publicCandidates,
        error: discoveryError, requestId,
      });
      if (port !== undefined && !DEV_PORT_RE.test(port || '')) return socket.emit('detect:result', {
        found: false, verified: false, ports, candidates: publicCandidates,
        error: 'invalid USB device selection', requestId,
      });

      const candidate = selectUsbDescriptor(candidates, port);
      if (!candidate) {
        const eligible = candidates.filter((entry) => entry.ambiguousPorts !== true);
        const ambiguousDevices = eligible.length > 1 && port === undefined;
        const selectedIdentityIsAmbiguous = port !== undefined && candidates.some(
          (entry) => entry.ambiguousPorts === true && entry.ports?.includes(port),
        );
        return socket.emit('detect:result', {
          found: false,
          verified: false,
          ports,
          candidates: publicCandidates,
          needsDeviceSelection: ambiguousDevices,
          error: ambiguousDevices
            ? 'Multiple verified USB devices are attached; select the exact board before continuing.'
            : selectedIdentityIsAmbiguous || (port === undefined && candidates.some((entry) => entry.ambiguousPorts))
              ? 'A USB descriptor identity appeared on multiple unrelated ports and was rejected.'
              : port !== undefined
                ? 'The selected USB device is no longer present with a verified stable descriptor.'
              : ports.length
                ? 'A serial device was found, but a stable USB serial number and VID/PID were not available.'
                : 'No USB serial device was found.',
          requestId,
        });
      }

      let explicitChip = null;
      if (chip !== undefined) {
        explicitChip = chipToKey(chip) || (CHIPS.has(chip) ? chip : null);
        if (!isProductReadyChip(explicitChip)) return socket.emit('detect:result', {
          found: false, verified: false, port: candidate.port,
          usbSerial: candidate.usbSerial, stableId: candidate.stableId,
          ports, candidates: publicCandidates, needsChipSelection: true,
          error: 'Select the board model explicitly; Product readiness is limited to proven ESP32-C3 and ESP32-S3 hardware.',
          requestId,
        });
      }
      const key = explicitChip || socket.data.chipSelections.get(candidate.stableId);
      if (!isProductReadyChip(key)) return socket.emit('detect:result', {
        found: false, verified: false, port: candidate.port,
        usbSerial: candidate.usbSerial, stableId: candidate.stableId,
        vid: candidate.vid, pid: candidate.pid, hardwareId: candidate.hardwareId,
        ports, candidates: publicCandidates, needsChipSelection: true,
        error: 'USB identity verified without resetting the board. Select ESP32-C3 or ESP32-S3 to confirm its model.',
        requestId,
      });

      const identity = bindUsbDescriptor(candidate, key);
      if (!identity) return socket.emit('detect:result', {
        found: false, verified: false, ports, candidates: publicCandidates,
        error: 'The selected USB device could not be bound safely.', requestId,
      });
      if (explicitChip) socket.data.chipSelections.set(identity.stableId, key);
      socket.data.port = identity.port;
      socket.data.chipKey = identity.chipKey;
      socket.data.usbSerial = identity.usbSerial;
      socket.data.deviceIdentity = identity;
      socket.emit('detect:result', {
        found: true, verified: true, port: identity.port,
        chip: identity.chipKey.toUpperCase(), chipKey: identity.chipKey,
        arch: archOf(identity.chipKey), usbSerial: identity.usbSerial,
        vid: identity.vid, pid: identity.pid, hardwareId: identity.hardwareId,
        stableId: identity.stableId, chipSource: explicitChip ? 'operator-selection' : 'session-cache',
        requestId,
      });
    });
  });

  // ---- build ----
  socket.on('build', ({ variant, chip, bssid, channel, client, attack, ssid, requestId } = {}) => {
    const key = chipToKey(chip) || (CHIPS.has(chip) ? chip : null);
    if (!key) return socket.emit('build:done', { ok: false, error: 'unknown chip', requestId });
    if (!BUILD_VARIANTS.has(variant)) return socket.emit('build:done', { ok: false, error: 'bad variant', requestId });
    if (!ACTIVE_LAB_ENABLED && (variant !== 'target' || attack !== 'unattended'))
      return socket.emit('build:done', { ok: false, error: 'active research builds are disabled in the default product', requestId });
    if (variant === 'target' && !BUILD_ATTACKS.has(attack))
      return socket.emit('build:done', { ok: false, error: 'bad attack', requestId });
    const atk = BUILD_ATTACKS.has(attack) ? attack : 'deauth';
    const tag = variant === 'stock' ? 'stock' : variant === 'target' ? atk : 'patched';
    const buildId = crypto.randomBytes(24).toString('hex');
    const dir = path.join(os.tmpdir(), `esplab_${tag}_${key}_${Date.now().toString(36)}_${crypto.randomBytes(3).toString('hex')}`);
    const args = ['build', '--chip', key, '--out', dir];
    if (variant === 'stock') args.push('--no-patch');
    if (variant === 'target') {
      const ch = Number(channel);
      if (atk === 'unattended') {
        // Passive/local-storage firmware: always stock and deliberately
        // provisioned with no target or network credentials.
        args.push('--attack', atk, '--no-patch');
      } else if (TARGETLESS_ATTACKS.has(atk)) {
        args.push('--attack', atk);
        if (ch >= 1 && ch <= 14) args.push('--channel', String(ch)); // optional; else the fw hops 1/6/11
        if (atk === 'probeflood' && typeof ssid === 'string' && ssid.length > 0 && ssid.length <= 32) args.push('--ssid', ssid);
      } else {
        if (!MAC_RE.test(bssid || '') || !(ch >= 1 && ch <= 14))
          return socket.emit('build:done', { ok: false, error: 'bad target', requestId });
        args.push('--bssid', bssid, '--channel', String(ch), '--attack', atk);
        if (atk === 'deauth' && MAC_RE.test(client || '')) args.push('--client', client);
        if (atk === 'csa' && typeof ssid === 'string' && ssid.length <= 32) args.push('--ssid', ssid);
      }
    }
    runPy(socket, args, (code) => {
      if (code === 0) {
        // Tickets are held only on this socket and expose no filesystem path.
        // The oldest unconsumed ticket is forgotten if a client hoards them.
        while (socket.data.builds.size >= 8) socket.data.builds.delete(socket.data.builds.keys().next().value);
        socket.data.builds.set(buildId, { dir, chip: key, variant, attack: variant === 'target' ? atk : null });
      }
      socket.emit('build:done', { ok: code === 0, variant, buildId: code === 0 ? buildId : undefined, chip: key, requestId });
    });
  });

  // ---- flash ----
  socket.on('flash', ({ chip, buildId, port, requestId } = {}) => {
    const key = chipToKey(chip) || (CHIPS.has(chip) ? chip : null);
    if (!key) return socket.emit('flash:done', { ok: false, error: 'unknown chip', requestId });
    if (!DEV_PORT_RE.test(port || '')) return socket.emit('flash:done', { ok: false, error: 'bad port', requestId });
    const boardError = detectedBoardError(socket.data, { port, chip: key });
    if (boardError) return socket.emit('flash:done', { ok: false, error: boardError, requestId });
    // A transient owner must not burn the one-time ticket before a retry can
    // actually start. The event handler is synchronous through runPy's lock.
    if (hardwareChild || serialSession)
      return socket.emit('flash:done', { ok: false, error: 'hardware busy', requestId });
    reverifyDetectedBoard(socket, (identityError) => {
      if (identityError)
        return socket.emit('flash:done', { ok: false, error: identityError, requestId });
      // Consume only after identity verification. Mismatch, unprobeable, and
      // busy failures leave the ticket available for an explicit retry.
      const ticket = consumeBuildTicket(socket.data.builds, { buildId, chip: key, activeLabEnabled: ACTIVE_LAB_ENABLED });
      if (!ticket.ok) return socket.emit('flash:done', { ok: false, error: ticket.error, requestId });
      if (!fs.existsSync(ticket.record.dir))
        return socket.emit('flash:done', { ok: false, error: 'recorded build is no longer available', requestId });
      runPy(socket, ['flash', '--port', port, '--chip', key, '--build-dir', ticket.record.dir], (code) =>
        socket.emit('flash:done', { ok: code === 0, requestId }));
    });
  });

  // ---- scan nearby 2.4GHz APs with the ESP itself ----
  socket.on('scanAps', ({ chip, port, requestId } = {}) => {
    const key = chipToKey(chip) || (CHIPS.has(chip) ? chip : null);
    if (!key) return socket.emit('apscan:done', { ok: false, flashed: false, complete: false, error: 'unknown chip', requestId });
    if (!DEV_PORT_RE.test(port || '')) return socket.emit('apscan:done', { ok: false, flashed: false, complete: false, error: 'bad port', requestId });
    const boardError = detectedBoardError(socket.data, { port, chip: key });
    if (boardError) return socket.emit('apscan:done', { ok: false, flashed: false, complete: false, error: boardError, requestId });
    let aps = [];
    let flashed = false;
    let complete = false;
    let scanError;
    reverifyDetectedBoard(socket, (identityError) => {
      if (identityError) return socket.emit('apscan:done', {
        ok: false, aps, flashed: false, complete: false,
        error: identityError, requestId,
      });
      runPy(socket, ['scan-aps', '--chip', key, '--port', port],
        (code) => socket.emit('apscan:done', {
          ok: code === 0 && complete,
          aps, flashed, complete,
          error: code === 0 && complete ? undefined : scanError || 'Wi-Fi survey did not complete',
          requestId,
        }),
        (line) => {
          const m = line.match(/^APSCAN_JSON\s+(\{.*\})$/);
          if (m) {
            try {
              const result = JSON.parse(m[1]);
              aps = Array.isArray(result.aps) ? result.aps : [];
              flashed = result.flashed === true;
              complete = result.complete === true;
              scanError = typeof result.error === 'string' ? result.error : undefined;
              socket.emit('apscan:result', { aps, flashed, complete, error: scanError, requestId });
            } catch {}
          }
        });
    });
  });

  // ---- recon: profile advertised AP security configuration ----
  socket.on('recon', ({ chip, channel, requestId } = {}) => {
    const key = chipToKey(chip) || (CHIPS.has(chip) ? chip : null);
    if (!key) return socket.emit('recon:done', { ok: false, error: 'unknown chip', requestId });
    const boardError = detectedBoardError(socket.data, { chip: key });
    if (boardError) return socket.emit('recon:done', { ok: false, error: boardError, requestId });
    const args = ['recon', '--chip', key, '--port', socket.data.port];
    const ch = Number(channel);
    if (ch >= 1 && ch <= 14) args.push('--channel', String(ch));
    let result = { aps: [], flashed: false, complete: false };
    runVerifiedPy(socket, args, (code, identityError) => socket.emit('recon:done', {
      ok: code === 0 && result.complete === true,
      ...result, error: identityError || result.error, requestId,
    }), (line) => {
      const m = line.match(/^RECON_JSON\s+(\{.*\})$/);
      if (m) { try { result = JSON.parse(m[1]); socket.emit('recon:result', { ...result, requestId }); } catch {} }
    });
  });

  // ---- scan clients on a target AP ----
  socket.on('scanClients', ({ chip, bssid, channel, requestId } = {}) => {
    const key = chipToKey(chip) || (CHIPS.has(chip) ? chip : null);
    if (!key) return socket.emit('clients:done', { ok: false, error: 'unknown chip', requestId });
    const boardError = detectedBoardError(socket.data, { chip: key });
    if (boardError) return socket.emit('clients:done', { ok: false, error: boardError, requestId });
    const ch = Number(channel);
    if (!MAC_RE.test(bssid || '') || !(ch >= 1 && ch <= 14))
      return socket.emit('clients:done', { ok: false, error: 'bad target', requestId });
    let result = { clients: [], flashed: false, complete: false };
    runVerifiedPy(socket, ['scan-clients', '--chip', key, '--port', socket.data.port,
      '--bssid', bssid, '--channel', String(ch)],
      (code, identityError) => socket.emit('clients:done', {
        ok: code === 0 && result.complete === true,
        ...result, error: identityError || result.error, requestId,
      }), (line) => {
        const m = line.match(/^CLIENTS_JSON\s+(\{.*\})$/);
        if (m) { try { result = JSON.parse(m[1]); socket.emit('clients:result', { ...result, requestId }); } catch {} }
      });
  });

  // ---- passive traffic monitor + signal meter ----
  socket.on('monitor', ({ chip, channel, bssid, seconds, requestId } = {}) => {
    const key = chipToKey(chip) || (CHIPS.has(chip) ? chip : null);
    if (!key) return socket.emit('monitor:done', { ok: false, error: 'unknown chip', requestId });
    const boardError = detectedBoardError(socket.data, { chip: key });
    if (boardError) return socket.emit('monitor:done', { ok: false, error: boardError, requestId });
    const ch = Number(channel);
    if (!(ch >= 1 && ch <= 14)) return socket.emit('monitor:done', { ok: false, error: 'bad channel', requestId });
    const args = ['monitor', '--chip', key, '--port', socket.data.port,
      '--channel', String(ch), '--seconds', String(Math.min(60, Math.max(5, Number(seconds) || 12)))];
    if (MAC_RE.test(bssid || '')) args.push('--bssid', bssid);
    let res = { flashed: false, complete: false };
    runVerifiedPy(socket, args, (code, identityError) => socket.emit('monitor:done', {
      ok: code === 0 && res.complete === true,
      res, ...res, error: identityError || res.error, requestId,
    }), (line) => {
      const m = line.match(/^MONITOR_JSON\s+(\{.*\})$/);
      if (m) { try { res = JSON.parse(m[1]); socket.emit('monitor:result', { ...res, requestId }); } catch {} }
    });
  });

  // ---- retrieve unattended LittleFS inventory over local USB serial ----
  socket.on('unattendedDump', ({ port, requestId } = {}) => {
    if (!DEV_PORT_RE.test(port || ''))
      return socket.emit('unattended:done', { ok: false, error: 'bad port', requestId });
    const boardError = detectedBoardError(socket.data, { port });
    if (boardError)
      return socket.emit('unattended:done', { ok: false, error: boardError, requestId });
    if (hardwareChild)
      return socket.emit('unattended:done', { ok: false, error: 'hardware busy', requestId });
    if (serialSession && serialSession.socket !== socket)
      return socket.emit('unattended:done', { ok: false, error: 'serial busy in another tab', requestId });
    const name = 'medusa_unattended.csv';
    const outPath = path.join('/tmp', name);
    let result = null;
    let retrievalError = null;
    const retrieve = () => reverifyDetectedBoard(socket, (identityError) => {
      if (identityError) {
        socket.emit('unattended:done', { ok: false, error: identityError, requestId });
        return;
      }
      runPy(socket,
        ['unattended-dump', '--port', port, '--out', outPath, '--timeout', '20'],
        (code) => socket.emit('unattended:done', {
          ok: code === 0 && result !== null,
          name: code === 0 && result ? name : undefined,
          rows: result?.rows,
          partial: result?.partial,
          capacityDrops: result?.capacityDrops,
          recovery: result?.recovery,
          source: result?.source,
          error: code === 0 && result ? undefined : retrievalError || 'USB readback failed',
          requestId,
        }),
        (line) => {
          const match = line.match(/^UNATTENDED_DUMP_JSON\s+(\{.*\})$/);
          if (match) { try { result = JSON.parse(match[1]); } catch {} }
          if (/unattended retrieval failed:/i.test(line)) retrievalError = line;
        });
    });
    if (serialSession && serialSession.socket === socket) {
      stopSerial(socket, (error) => {
        if (error) {
          socket.emit('unattended:done', { ok: false, error: error.message, requestId });
          return;
        }
        retrieve();
      });
    } else {
      retrieve();
    }
  });

  // ---- build a deployable BadUSB firmware (S3, USB-OTG) — build only, never flashed here ----
  socket.on('buildBadusb', ({ payload, requestId } = {}) => {
    if (!ACTIVE_LAB_ENABLED)
      return socket.emit('badusb:done', { ok: false, error: 'USB HID research builds are disabled in the default product', requestId });
    if (typeof payload !== 'string' || !payload.length || payload.length > 4096)
      return socket.emit('badusb:done', { ok: false, error: 'bad payload', requestId });
    const dir = path.join(os.tmpdir(), 'esplab_badusb_s3');
    runPy(socket, ['build', '--chip', 'esp32-s3', '--attack', 'badusb', '--no-patch', '--payload', payload, '--out', dir],
      (code) => socket.emit('badusb:done', { ok: code === 0, dir, requestId }));
  });

  // ---- scan nearby BLE devices ----
  socket.on('bleScan', ({ chip, seconds, requestId } = {}) => {
    const key = chipToKey(chip) || (CHIPS.has(chip) ? chip : null);
    if (!key) return socket.emit('blescan:done', { ok: false, flashed: false, complete: false, error: 'unknown chip', requestId });
    const boardError = detectedBoardError(socket.data, { chip: key });
    if (boardError) return socket.emit('blescan:done', { ok: false, flashed: false, complete: false, error: boardError, requestId });
    const secs = Math.min(60, Math.max(4, Number(seconds) || 10));
    let devices = [];
    let flashed = false;
    let complete = false;
    let scanError;
    runVerifiedPy(socket, ['ble-scan', '--chip', key, '--port', socket.data.port, '--seconds', String(secs)],
      (code, identityError) => socket.emit('blescan:done', {
        ok: code === 0 && complete,
        devices, flashed, complete,
        error: identityError || (code === 0 && complete ? undefined : scanError || 'BLE survey did not complete'),
        requestId,
      }),
      (line) => {
        const m = line.match(/^BLESCAN_JSON\s+(\{.*\})$/);
        if (m) {
          try {
            const result = JSON.parse(m[1]);
            devices = Array.isArray(result.devices) ? result.devices : [];
            flashed = result.flashed === true;
            complete = result.complete === true;
            scanError = typeof result.error === 'string' ? result.error : undefined;
            socket.emit('blescan:result', { devices, flashed, complete, error: scanError, requestId });
          } catch {}
        }
      });
  });


  // ---- plugins: list, validate, save ----------------------------------
  //
  // Deliberately NOT routed through runScript(): validating a plugin is pure
  // host work with no radio involved, so it must not contend for the hardware
  // lock or be blocked while a survey is running. An author editing a plugin
  // while a capture runs is the normal case, not an edge case.
  socket.on('plugin:list', () => {
    const listing = [];
    for (const [source, dir] of [['bundled', PLUGIN_EXAMPLES_DIR], ['saved', PLUGIN_USER_DIR]]) {
      let names = [];
      try { names = fs.readdirSync(dir).filter((n) => n.endsWith('.medusa')); } catch { continue; }
      for (const name of names) {
        try {
          const text = fs.readFileSync(path.join(dir, name), 'utf8');
          const field = (key) => (text.match(new RegExp(`^${key} (.*)$`, 'm')) || [])[1] || '';
          listing.push({
            file: name,
            source,
            id: field('id'),
            name: field('name') || field('id'),
            author: field('author'),
            needs: field('needs'),
            steps: (text.match(/^>/gm) || []).length,
          });
        } catch { /* an unreadable file is skipped, not fatal for the listing */ }
      }
    }
    socket.emit('plugin:list:done', { ok: true, plugins: listing });
  });

  // The guided path is data, not code: an ordered set of lessons that each
  // name something to look at. Served from the same devkit directory as the
  // plugins they reference, so a lesson cannot drift from its example.
  socket.on('journey:get', () => {
    try {
      const journey = JSON.parse(fs.readFileSync(JOURNEY_FILE, 'utf8'));
      socket.emit('journey:get:done', { ok: true, journey });
    } catch {
      socket.emit('journey:get:done', { ok: false, error: 'journey.missing' });
    }
  });

  socket.on('plugin:validate', ({ source, requestId } = {}) => {
    if (typeof source !== 'string' || source.length === 0 || source.length > 65536) {
      return socket.emit('plugin:validate:done', { ok: false, error: 'plugin.size', requestId });
    }
    const tmp = path.join(os.tmpdir(), `medusa_plugin_${crypto.randomBytes(8).toString('hex')}.medusa`);
    try { fs.writeFileSync(tmp, source); } catch {
      return socket.emit('plugin:validate:done', { ok: false, error: 'write', requestId });
    }
    // The linter is the firmware's own parser compiled for this host, so what
    // it accepts here is what the device accepts.
    execFile('bash', [PLUGIN_LINT, 'lint', tmp], { timeout: 20000 }, (error, stdout) => {
      try { fs.rmSync(tmp, { force: true }); } catch {}
      const text = String(stdout || '');
      socket.emit('plugin:validate:done', {
        ok: !error,
        valid: text.startsWith('VALID'),
        report: text,
        requestId,
      });
    });
  });

  socket.on('plugin:save', ({ id, source, requestId } = {}) => {
    // Path safety: an id is a filename component and nothing else.
    if (typeof id !== 'string' || !/^[a-z0-9][a-z0-9-]{0,31}$/.test(id)) {
      return socket.emit('plugin:save:done', { ok: false, error: 'plugin.id', requestId });
    }
    if (typeof source !== 'string' || source.length === 0 || source.length > 65536) {
      return socket.emit('plugin:save:done', { ok: false, error: 'plugin.size', requestId });
    }
    const tmp = path.join(os.tmpdir(), `medusa_plugin_${crypto.randomBytes(8).toString('hex')}.medusa`);
    try { fs.writeFileSync(tmp, source); } catch {
      return socket.emit('plugin:save:done', { ok: false, error: 'write', requestId });
    }
    // Never persist something the device would refuse: a saved plugin that
    // cannot run is a trap for whoever opens the list later.
    execFile('bash', [PLUGIN_LINT, 'lint', tmp], { timeout: 20000 }, (error, stdout) => {
      try { fs.rmSync(tmp, { force: true }); } catch {}
      const text = String(stdout || '');
      if (error || !text.startsWith('VALID')) {
        return socket.emit('plugin:save:done', { ok: false, error: 'plugin.invalid', report: text, requestId });
      }
      try {
        fs.mkdirSync(PLUGIN_USER_DIR, { recursive: true });
        fs.writeFileSync(path.join(PLUGIN_USER_DIR, `${id}.medusa`), source);
      } catch {
        return socket.emit('plugin:save:done', { ok: false, error: 'write', requestId });
      }
      socket.emit('plugin:save:done', { ok: true, id, report: text, requestId });
    });
  });

  socket.on('plugin:read', ({ file, source: origin, requestId } = {}) => {
    if (typeof file !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}\.medusa$/.test(file)) {
      return socket.emit('plugin:read:done', { ok: false, error: 'plugin.file', requestId });
    }
    const dir = origin === 'saved' ? PLUGIN_USER_DIR : PLUGIN_EXAMPLES_DIR;
    try {
      const text = fs.readFileSync(path.join(dir, file), 'utf8');
      socket.emit('plugin:read:done', { ok: true, file, source: text, requestId });
    } catch {
      socket.emit('plugin:read:done', { ok: false, error: 'plugin.missing', requestId });
    }
  });

  // ---- export scan/recon data → WiGLE / airodump CSV / posture HTML (+ inventory) ----
  socket.on('export', ({ format, rows, requestId, pseudonymize } = {}) => {
    if (!['wigle', 'airodump', 'posture'].includes(format)) return socket.emit('export:done', { ok: false, error: 'bad format', requestId });
    if (!Array.isArray(rows) || !rows.length || rows.length > 5000) return socket.emit('export:done', { ok: false, error: 'no rows', requestId });
    const requestPart = typeof requestId === 'string' && /^[A-Za-z0-9_-]{1,80}$/.test(requestId)
      ? requestId : 'legacy';
    // Keep each artifact tied to its liveness request without trusting the
    // request ID as a raw path or allowing a repeated ID to overwrite a file.
    const exportId = `${requestPart}_${crypto.randomBytes(8).toString('hex')}`;
    const inPath = path.join('/tmp', `medusa_export_in_${exportId}.json`);
    // Naming and argument assembly live in process_helpers so the disclosure
    // decision is unit-tested rather than inline in a socket handler.
    const plan = exportArtifactPlan({
      format, exportId, inPath, outDir: '/tmp', inventoryDb: INVENTORY_DB, pseudonymize,
    });
    if (!plan) return socket.emit('export:done', { ok: false, error: 'bad format', requestId });
    try { fs.writeFileSync(inPath, JSON.stringify({ aps: rows })); } catch { return socket.emit('export:done', { ok: false, error: 'write', requestId }); }
    runScript(socket, ESP_REPORT, plan.args,
      (code) => {
        try { fs.rmSync(inPath, { force: true }); } catch {}
        socket.emit('export:done', { ok: code === 0, name: code === 0 ? plan.name : undefined, pseudonymized: plan.pseudonymized, requestId });
      });
  });

  // ---- read the persisted asset inventory ----
  socket.on('inventory', () => {
    let rows = [];
    runScript(socket, ESP_REPORT, ['inventory', '--db', INVENTORY_DB, '--json'],
      () => socket.emit('inventory:done', { ok: true, rows }),
      (line) => { const m = line.match(/^INVENTORY_JSON\s+(\{.*\})$/); if (m) { try { rows = JSON.parse(m[1]).aps || []; } catch {} } });
  });

  // ---- passive capture → PCAP ----
  socket.on('sniff', ({ chip, channel, seconds, requestId } = {}) => {
    const key = chipToKey(chip) || (CHIPS.has(chip) ? chip : null);
    if (!key) return socket.emit('sniff:done', { ok: false, error: 'unknown chip', requestId });
    const boardError = detectedBoardError(socket.data, { chip: key });
    if (boardError) return socket.emit('sniff:done', { ok: false, error: boardError, requestId });
    const ch = Number(channel);
    if (!(ch >= 1 && ch <= 14)) return socket.emit('sniff:done', { ok: false, error: 'bad channel', requestId });
    const secs = Math.min(120, Math.max(5, Number(seconds) || 20));
    let cap = { flashed: false, complete: false };
    runVerifiedPy(socket, ['sniff', '--chip', key, '--port', socket.data.port,
      '--channel', String(ch), '--seconds', String(secs)],
      (code, identityError) => socket.emit('sniff:done', {
        ok: code === 0 && cap.complete === true,
        cap, ...cap, error: identityError || cap.error, requestId,
      }), (line) => {
        const m = line.match(/^SNIFF_JSON\s+(\{.*\})$/);
        if (m) { try { cap = JSON.parse(m[1]); socket.emit('sniff:result', { ...cap, requestId }); } catch {} }
      });
  });

  // ---- capture PMKID / 4-way handshake → hashcat 22000 + PCAP ----
  socket.on('captureWpa', ({ chip, bssid, channel, client, ssid, seconds, requestId } = {}) => {
    if (!ACTIVE_LAB_ENABLED)
      return socket.emit('wpa:done', { ok: false, error: 'active reassociation capture is disabled in the default product', requestId });
    const key = chipToKey(chip) || (CHIPS.has(chip) ? chip : null);
    if (!key) return socket.emit('wpa:done', { ok: false, error: 'unknown chip', requestId });
    const boardError = detectedBoardError(socket.data, { chip: key });
    if (boardError) return socket.emit('wpa:done', { ok: false, error: boardError, requestId });
    const ch = Number(channel);
    if (!MAC_RE.test(bssid || '') || !(ch >= 1 && ch <= 14))
      return socket.emit('wpa:done', { ok: false, error: 'bad target', requestId });
    const secs = Math.min(120, Math.max(10, Number(seconds) || 25));
    const args = ['capture-wpa', '--chip', key, '--port', socket.data.port,
      '--bssid', bssid, '--channel', String(ch), '--seconds', String(secs)];
    if (MAC_RE.test(client || '')) args.push('--client', client);
    if (typeof ssid === 'string' && ssid.length > 0 && ssid.length <= 32) args.push('--ssid', ssid);
    let res = null;
    runVerifiedPy(socket, args, (code, identityError) => socket.emit('wpa:done', {
      ok: code === 0, res, error: identityError || undefined, requestId,
    }),
      (line) => { const m = line.match(/^WPA_JSON\s+(\{.*\})$/); if (m) { try { res = JSON.parse(m[1]); socket.emit('wpa:result', { ...res, requestId }); } catch {} } });
  });

  // ---- live serial ----
  socket.on('serial:start', ({ port } = {}) => {
    if (!DEV_PORT_RE.test(port || '')) return socket.emit('serial:line', { raw: 'bad port', rc: null, state: 'error' });
    const boardError = detectedBoardError(socket.data, { port });
    if (boardError) return socket.emit('serial:line', { raw: boardError, rc: null, state: 'error' });
    if (hardwareChild || serialSession) return socket.emit('serial:line', { raw: 'hardware or serial port busy', rc: null, state: 'error' });
    const generation = ++socket.data.serialStartGeneration;
    reverifyDetectedBoard(socket, (identityError) => {
      // A stop can arrive while the exact-port identity probe is still in
      // flight. Invalidate that pending start instead of opening the tty after
      // the caller has already received a successful stop acknowledgement.
      if (generation !== socket.data.serialStartGeneration) return;
      if (identityError) {
        socket.emit('serial:line', { raw: identityError, rc: null, state: 'error' });
        return;
      }
      const child = spawn('python3', [READ_SERIAL, port, '3600'], {
        cwd: ESP_HW_DIR, env: HARDWARE_ENV,
      });
      socket.data.serialChild = child;
      const session = {
        child, socket, port, stopping: false, afterClose: [],
        stopTimer: null, forceTimer: null, stopError: null,
      };
      serialSession = session;
      let serialSettled = false;
      const emitSerialLine = (raw) => {
        let rc = null, state = null;
        const m = raw.match(/rc\s*=\s*(0x[0-9a-fA-F]+)/);
        if (m) { rc = m[1].toLowerCase(); state = rc === '0x0' ? 'accepted' : rc === '0x102' ? 'blocked' : 'other'; }
        else if (/unsupport frame type/i.test(raw)) state = 'reject-log';
        socket.emit('serial:line', { raw, rc, state });
      };
      const serialLines = createLineAccumulator(emitSerialLine);
      const closeSerial = () => {
        if (serialSettled) return;
        serialSettled = true;
        serialLines.flush();
        if (session.stopTimer) clearTimeout(session.stopTimer);
        if (session.forceTimer) clearTimeout(session.forceTimer);
        const ownsPort = serialSession === session;
        if (ownsPort) serialSession = null;
        if (socket.data.serialChild === child) socket.data.serialChild = null;
        if (ownsPort) socket.emit('serial:closed');
        if (ownsPort) completeSerialStop(session);
      };
      child.stdout.on('data', (buf) => serialLines.write(buf));
      child.on('close', () => closeSerial());
      // An error can mean a signal could not be delivered. Do not treat that as
      // proof the tty is free; only the child `close` event releases ownership.
      child.on('error', (error) => socket.emit('serial:line', { raw: error.message, rc: null, state: 'error' }));
    });
  });
  socket.on('serial:stop', (ack) => {
    socket.data.serialStartGeneration += 1;
    const done = (error) => {
      if (typeof ack === 'function') ack(error ? { ok: false, error: error.message } : { ok: true });
    };
    if (!stopSerial(socket, done)) done();
  });

  socket.on('disconnect', () => {
    socket.data.serialStartGeneration += 1;
    // Let a build/flash finish its own cleanup if its browser disappears. The
    // process-global hardware lock prevents a replacement tab from overlapping
    // it, and the next connection still receives a clear busy result.
    stopSerial(socket);
  });
});

server.listen(PORT, HOST, () => {
  console.log(`Medusa Wi-Fi Lab backend on http://${HOST}:${PORT}`);
  console.log(`esp-hw: ${ESP_HW} (${fs.existsSync(ESP_HW) ? 'found' : 'MISSING'})`);
  console.log(`active research lab: ${ACTIVE_LAB_ENABLED ? 'explicitly enabled' : 'disabled'}`);
  console.log(`token: ${TOKEN}`);
});
