import { StringDecoder } from 'node:string_decoder';

export const SERIAL_STOP_TIMEOUT_MESSAGE =
  'Serial reader did not close after SIGKILL; the port remains reserved.';

/**
 * Decode a byte stream into non-empty newline-delimited records.
 *
 * Each child stream needs its own accumulator: stdout and stderr chunks may
 * end in the middle of unrelated records.  `flush` delivers a final fragment
 * once, including a UTF-8 sequence completed by StringDecoder.end().
 */
export function createLineAccumulator(onLine, cleanLine = (line) => line) {
  const decoder = new StringDecoder('utf8');
  let carry = '';
  let flushed = false;

  const deliver = (line) => {
    const cleaned = cleanLine(line);
    if (cleaned.length) onLine(cleaned);
  };

  const drainCompleteLines = () => {
    const lines = carry.split(/\r?\n/);
    carry = lines.pop() || '';
    for (const line of lines) deliver(line);
  };

  return {
    write(buf) {
      if (flushed) return;
      carry += decoder.write(buf);
      drainCompleteLines();
    },
    flush() {
      if (flushed) return;
      flushed = true;
      carry += decoder.end();
      drainCompleteLines();
      if (carry.length) deliver(carry);
      carry = '';
    },
  };
}

function queueSerialCallbacks(session, error, queueTask) {
  const queued = session.afterClose.splice(0);
  for (const callback of queued) queueTask(() => callback(error));
}

/**
 * Begin event-driven serial shutdown and fail closed if even SIGKILL does not
 * produce a child `close` event.  A timeout reports failure to queued callers
 * but deliberately does not clear ownership; only the server's close handler
 * may release the tty.
 */
export function requestSerialStop(session, {
  isCurrent,
  afterClose,
  onFailure,
  setTimer = setTimeout,
  queueTask = queueMicrotask,
  gracefulTimeoutMs = 1000,
  forceTimeoutMs = 500,
} = {}) {
  if (afterClose) {
    if (session.stopError) queueTask(() => afterClose(session.stopError));
    else session.afterClose.push(afterClose);
  }
  if (session.stopError || session.stopping) return;

  session.stopping = true;
  session.child.kill();
  session.stopTimer = setTimer(() => {
    if (!isCurrent()) return;
    session.child.kill('SIGKILL');
    session.forceTimer = setTimer(() => {
      if (!isCurrent() || session.stopError) return;
      const error = new Error(SERIAL_STOP_TIMEOUT_MESSAGE);
      session.stopError = error;
      if (onFailure) onFailure(error);
      queueSerialCallbacks(session, error, queueTask);
    }, forceTimeoutMs);
  }, gracefulTimeoutMs);
}

export function completeSerialStop(session, queueTask = queueMicrotask) {
  queueSerialCallbacks(session, undefined, queueTask);
}

/** Consume a server-issued build ticket exactly once. */
export function consumeBuildTicket(builds, { buildId, chip, activeLabEnabled }) {
  if (typeof buildId !== 'string' || !/^[0-9a-f]{48}$/.test(buildId))
    return { ok: false, error: 'unknown or expired build' };
  const record = builds.get(buildId);
  if (!record) return { ok: false, error: 'unknown or expired build' };
  builds.delete(buildId);
  if (record.chip !== chip) return { ok: false, error: 'build does not match detected chip' };
  if (!activeLabEnabled && !(record.variant === 'target' && record.attack === 'unattended'))
    return { ok: false, error: 'only a recorded passive unattended build may be flashed in the default product' };
  return { ok: true, record };
}

/** Require privileged hardware actions to use this socket's detected board. */
export function detectedBoardError(socketData, { port, chip } = {}) {
  if (!socketData?.port || !socketData?.chipKey || !socketData?.deviceIdentity)
    return 'detect a supported device before using the hardware bridge';
  if (port !== undefined && port !== socketData.port)
    return 'port does not match the detected device';
  if (chip !== undefined && chip !== socketData.chipKey)
    return 'chip does not match the detected device';
  return null;
}

const USB_ID_RE = /^(?:0x)?([0-9a-f]{4})$/i;
const DEV_PORT_RE = /^\/dev\/[A-Za-z0-9._-]+$/;
const PRODUCT_READY_CHIPS = new Set(['esp32-c3', 'esp32-s3']);

/** The Product discovery command only reads USB descriptors; it never opens a tty. */
export function usbDescriptorDiscoveryCommand(executable = 'arduino-cli') {
  return { executable, args: ['board', 'list', '--format', 'json'] };
}

export function isProductReadyChip(chipKey) {
  return PRODUCT_READY_CHIPS.has(chipKey);
}

function normalizeUsbId(value) {
  const match = typeof value === 'string' ? value.trim().match(USB_ID_RE) : null;
  return match ? `0x${match[1].toLowerCase()}` : null;
}

function normalizeDescriptorText(value) {
  return typeof value === 'string' && value.trim() ? value.trim().toLowerCase() : null;
}

function portAliasFamily(port) {
  const match = port.match(/^\/dev\/(?:cu|tty)\.(.+)$/);
  return match ? `macos-serial:${match[1]}` : `exact:${port}`;
}

function preferredCalloutPort(ports) {
  return [...ports].sort((left, right) => {
    const rank = (port) => port.startsWith('/dev/cu.') ? 0 : port.startsWith('/dev/tty.') ? 1 : 2;
    return rank(left) - rank(right) || left.localeCompare(right);
  })[0];
}

/** Return every serial path reported by arduino-cli, including unverified ones. */
export function usbSerialPorts(listing) {
  const rows = Array.isArray(listing?.detected_ports) ? listing.detected_ports : [];
  return [...new Set(rows.filter((row) => {
    const portInfo = row?.port;
    const properties = portInfo?.properties || {};
    return portInfo?.protocol === 'serial'
      && typeof portInfo.address === 'string'
      && DEV_PORT_RE.test(portInfo.address)
      && (String(portInfo.protocol_label || '').includes('USB')
        || properties.serialNumber || properties.vid || properties.pid
        || portInfo.hardware_id || row.hardware_id);
  }).map((row) => row.port.address))].sort();
}

/**
 * Parse non-invasive arduino-cli USB descriptors into physical identities.
 *
 * A stable USB serial plus VID/PID is mandatory. macOS `/dev/cu.*` and
 * `/dev/tty.*` aliases with the same suffix are one physical device; the
 * callout (`cu`) path is preferred. The same descriptor appearing on unrelated
 * paths is marked ambiguous and can never become Product-ready.
 */
export function usbDescriptorCandidates(listing) {
  const rows = Array.isArray(listing?.detected_ports) ? listing.detected_ports : [];
  const grouped = new Map();

  for (const row of rows) {
    const portInfo = row?.port;
    const port = portInfo?.address;
    const properties = portInfo?.properties;
    if (portInfo?.protocol !== 'serial' || typeof port !== 'string' || !DEV_PORT_RE.test(port)
        || !properties || typeof properties !== 'object') continue;
    const usbSerial = normalizeDescriptorText(properties.serialNumber);
    const vid = normalizeUsbId(properties.vid);
    const pid = normalizeUsbId(properties.pid);
    if (!usbSerial || !vid || !pid) continue;
    const hardwareId = normalizeDescriptorText(portInfo.hardware_id ?? row.hardware_id);
    const descriptorKey = `usb:${vid.slice(2)}:${pid.slice(2)}:${usbSerial}`;
    let group = grouped.get(descriptorKey);
    if (!group) {
      group = {
        descriptorKey, usbSerial, vid, pid, hardwareId,
        ports: new Set(), descriptorConflict: false,
      };
      grouped.set(descriptorKey, group);
    } else if (group.hardwareId && hardwareId && group.hardwareId !== hardwareId) {
      group.descriptorConflict = true;
    } else if (!group.hardwareId && hardwareId) {
      group.hardwareId = hardwareId;
    }
    group.ports.add(port);
  }

  return [...grouped.values()].map((group) => {
    const ports = [...group.ports].sort();
    const aliasFamilies = new Set(ports.map(portAliasFamily));
    return {
      stableId: `${group.descriptorKey}:${encodeURIComponent(group.hardwareId || '-')}`,
      usbSerial: group.usbSerial,
      vid: group.vid,
      pid: group.pid,
      hardwareId: group.hardwareId,
      port: preferredCalloutPort(ports),
      ports,
      ambiguousPorts: group.descriptorConflict || aliasFamilies.size !== 1,
    };
  }).sort((left, right) => left.port.localeCompare(right.port));
}

/** Choose one unambiguous descriptor, requiring a port when multiple exist. */
export function selectUsbDescriptor(candidates, port) {
  const eligible = (Array.isArray(candidates) ? candidates : [])
    .filter((candidate) => candidate && candidate.ambiguousPorts !== true);
  if (port !== undefined) {
    if (typeof port !== 'string' || !DEV_PORT_RE.test(port)) return null;
    const matches = eligible.filter((candidate) => candidate.ports?.includes(port));
    return matches.length === 1 ? { ...matches[0], port } : null;
  }
  return eligible.length === 1 ? eligible[0] : null;
}

/** Bind an explicit, proven C3/S3 selection to one USB descriptor and path. */
export function bindUsbDescriptor(candidate, chipKey) {
  if (!candidate || candidate.ambiguousPorts === true || !candidate.port
      || !candidate.ports?.includes(candidate.port) || !candidate.stableId
      || !candidate.usbSerial || !candidate.vid || !candidate.pid
      || !isProductReadyChip(chipKey)) return null;
  return {
    port: candidate.port,
    chipKey,
    stableId: candidate.stableId,
    usbSerial: candidate.usbSerial,
    vid: candidate.vid,
    pid: candidate.pid,
    hardwareId: candidate.hardwareId || null,
  };
}

/** Compare a fresh descriptor listing with the accepted exact-port identity. */
export function usbDescriptorIdentityError(expected, candidates) {
  if (!expected?.port || !expected?.chipKey || !isProductReadyChip(expected.chipKey)
      || !expected?.stableId || !expected?.usbSerial || !expected?.vid || !expected?.pid)
    return 'no verified USB device identity is available; detect the board again';
  const exact = (Array.isArray(candidates) ? candidates : [])
    .filter((candidate) => candidate?.ports?.includes(expected.port));
  if (exact.length !== 1 || exact[0].ambiguousPorts === true)
    return 'USB device identity could not be re-verified on its original port';
  const observed = exact[0];
  if (observed.stableId !== expected.stableId || observed.usbSerial !== expected.usbSerial
      || observed.vid !== expected.vid || observed.pid !== expected.pid
      || (expected.hardwareId && observed.hardwareId !== expected.hardwareId))
    return 'USB device identity changed on the detected port; detect the board again';
  return null;
}

/**
 * Decide the artifact filename and esp_report arguments for one export.
 *
 * Extracted from the socket handler so the disclosure decision is testable.
 * Two rules matter here and neither is obvious from the call site:
 *
 * - `--pseudonymize` is opt-in and must never be inferred from anything but an
 *   explicit `true`. A truthy-ish value arriving from the client is treated as
 *   "no", because quietly redacting an export the operator expected in full is
 *   as wrong as the reverse.
 * - The inventory database always receives the real rows. It is the operator's
 *   own local record and BSSID is its dedupe key; redacting it would corrupt
 *   the inventory to protect a file that is not the inventory.
 */
export function exportArtifactPlan({ format, exportId, inPath, outDir, inventoryDb, pseudonymize } = {}) {
  const stems = {
    wigle: ['medusa_wardrive', 'csv'],
    airodump: ['medusa_airodump', 'csv'],
    posture: ['medusa_posture', 'html'],
  };
  if (!stems[format]) return null;
  const [stem, extension] = stems[format];
  const redact = pseudonymize === true;
  const name = `${stem}${redact ? '_pseudonymized' : ''}_${exportId}.${extension}`;
  const outPath = outDir ? `${outDir}/${name}` : name;
  const args = ['export', '--format', format, '--in', inPath, '--out', outPath];
  if (inventoryDb) args.push('--db', inventoryDb);
  if (redact) args.push('--pseudonymize');
  return { name, outPath, args, pseudonymized: redact };
}
