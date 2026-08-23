import { createContext, useContext, useEffect, useRef, useState, ReactNode, useCallback } from 'react';
import { io, Socket } from 'socket.io-client';

// Match the backend's explicit IPv4 loopback bind. On hosts where `localhost`
// resolves to ::1 first, using the hostname here can leave an otherwise healthy
// 127.0.0.1-only bridge looking offline.
const BACKEND = 'http://127.0.0.1:4300';

export type Board = {
  found: boolean;
  verified?: boolean;
  port?: string;
  chip?: string;
  chipKey?: string | null;
  arch?: string | null;
  usbSerial?: string | null;
  stableId?: string;
  needsChipSelection?: boolean;
  needsDeviceSelection?: boolean;
  candidates?: BoardCandidate[];
  ports?: string[];
  requestId?: string;
  error?: string;
};
export type BoardCandidate = { port: string; ports?: string[]; usbSerial?: string | null; vid?: string | null; pid?: string | null; hardwareId?: string | null; stableId: string };
export type UnattendedSource = 'primary-csv' | 'temp-csv-read-only-recovery' | 'backup-csv-read-only-recovery' | 'database-read-only' | 'volatile-ram-read-only';
export type SerialLine = { raw: string; rc: string | null; state: string | null; id: number };
export type LogLine = { line: string; level?: string; id: number };
export type AP = { bssid: string; channel: number; rssi: number; ssid: string };
export type ReconAP = { bssid: string; channel: number; rssi: number; auth: string; pmf: string; wps: boolean; cipher: string; ssid: string; findings: Record<string, [string, string]> };
export type Client = { mac: string; rssi: number; packets: number };
export type Capture = { path: string; packets: number; eapol: number; channel: number };
export type WpaResult = { pmkid: number; eapol: number; handshake: boolean; msgs: number[]; hc22000: string; pcap: string; channel: number };
export type InvAP = { bssid: string; ssid: string; channel: number; rssi: number; auth: string; pmf: string; wps: boolean; cipher: string; vendor: string; first_seen: string; last_seen: string; count: number };
export type ExportFmt = 'wigle' | 'airodump' | 'posture';
export type JourneyStep = { id: string; title: string; goal: string; why: string; surface: string; plugin?: string; look_for: string };
export type Journey = { id: string; title: string; summary: string; steps: JourneyStep[] };
export type PluginSummary = { file: string; source: 'bundled' | 'saved'; id: string; name: string; author: string; needs: string; steps: number };
export type BleDevice = { addr: string; rssi: number; name: string; vendor: string; company_id?: number | null; manufacturer?: string };
export type MonitorResult = { channel: number; seconds: number; total: number; mgmt: number; data: number; ctrl: number; beacons: number; deauth: number; target_rssi: number | null };
export type Variant = 'stock' | 'patched' | 'target';
export type GateState = 'idle' | 'blocked' | 'accepted';
export type TargetOptions = { bssid?: string; channel?: number; client?: string; attack?: string; ssid?: string };
export type DetectOptions = { port?: string; chip?: 'esp32-c3' | 'esp32-s3' };

type OperationKey = 'detect' | 'apscan' | 'recon' | 'clients' | 'sniff' | 'wpa'
  | 'export' | 'blescan' | 'monitor' | 'badusb' | 'unattended';
type OperationGuard = {
  token: number;
  session: number;
  connection: number;
  requestId?: string;
  timer: number;
};

const DETECT_TIMEOUT_MS = 60_000;
const HARDWARE_TIMEOUT_MS = 12 * 60_000;
const BUILD_TIMEOUT_MS = 12 * 60_000;
const FLASH_TIMEOUT_MS = 4 * 60_000;
const LOCAL_TASK_TIMEOUT_MS = 90_000;

type Lab = {
  connected: boolean;
  hardwareActive: boolean;
  board: Board | null;
  detecting: boolean;
  busy: null | { kind: 'build' | 'flash'; variant: Variant };
  flashed: Variant | null;
  flashedAttack: string | null;
  builds: Partial<Record<Variant, string>>;
  logs: LogLine[];
  serial: SerialLine[];
  gate: GateState;
  serialOn: boolean;
  aps: AP[];
  apScanning: boolean;
  apSurveyComplete: boolean;
  apSurveyError: string | null;
  reconAps: ReconAP[];
  reconning: boolean;
  clients: Client[];
  clientScanning: boolean;
  capturing: boolean;
  capture: Capture | null;
  wpaCapturing: boolean;
  wpaResult: WpaResult | null;
  backend: string;
  exporting: ExportFmt | null;
  inventory: InvAP[];
  bleDevices: BleDevice[];
  bleScanning: boolean;
  bleSurveyComplete: boolean;
  bleSurveyError: string | null;
  bleScan: (seconds?: number) => void;
  monitoring: boolean;
  monitorResult: MonitorResult | null;
  monitor: (channel: number, bssid?: string, seconds?: number) => void;
  badusbBuilding: boolean;
  badusbOk: boolean | null;
  buildBadusb: (payload: string) => void;
  unattendedDumping: boolean;
  unattendedRows: number | null;
  unattendedPartial: boolean;
  unattendedCapacityDrops: number;
  unattendedRecovery: string | null;
  unattendedSource: UnattendedSource | null;
  unattendedError: string | null;
  retrieveUnattended: () => void;
  sniff: (channel: number, seconds: number) => void;
  captureWpa: (bssid: string, channel: number, client?: string, ssid?: string, seconds?: number) => void;
  exportData: (format: ExportFmt, rows: any[], pseudonymize?: boolean) => void;
  plugins: PluginSummary[];
  journey: Journey | null;
  journeyGet: () => void;
  pluginList: () => void;
  pluginRead: (file: string, source: string, done: (text: string) => void) => void;
  pluginValidate: (source: string, done: (report: string) => void) => void;
  loadInventory: () => void;
  detect: (selection?: DetectOptions) => void;
  runVariant: (v: Variant, target?: TargetOptions) => Promise<void>;
  scanAps: () => void;
  recon: (channel: number) => void;
  scanClients: (bssid: string, channel: number) => void;
  startSerial: () => void;
  stopSerial: () => void;
  clearSerial: () => void;
};

const Ctx = createContext<Lab | null>(null);
export const useLab = () => {
  const v = useContext(Ctx);
  if (!v) throw new Error('useLab outside provider');
  return v;
};

export function LabProvider({ children }: { children: ReactNode }) {
  const sockRef = useRef<Socket | null>(null);
  const idRef = useRef(0);
  const requestRef = useRef(0);
  const boardSessionRef = useRef(0);
  const connectionRef = useRef(0);
  const detectRequestRef = useRef<string | null>(null);
  const unattendedRequestRef = useRef<{ id: string; session: number } | null>(null);
  const operationRef = useRef<Partial<Record<OperationKey, OperationGuard>>>({});
  const pendingAwaitsRef = useRef(new Map<number, () => void>());
  const [connected, setConnected] = useState(false);
  const [board, setBoard] = useState<Board | null>(null);
  const [detecting, setDetecting] = useState(false);
  const [busy, setBusy] = useState<Lab['busy']>(null);
  const [flashed, setFlashed] = useState<Variant | null>(null);
  const [flashedAttack, setFlashedAttack] = useState<string | null>(null);
  const [builds, setBuilds] = useState<Partial<Record<Variant, string>>>({});
  const [logs, setLogs] = useState<LogLine[]>([]);
  const [serial, setSerial] = useState<SerialLine[]>([]);
  const [gate, setGate] = useState<GateState>('idle');
  const [serialOn, setSerialOn] = useState(false);
  const [aps, setAps] = useState<AP[]>([]);
  const [apScanning, setApScanning] = useState(false);
  const [apSurveyComplete, setApSurveyComplete] = useState(false);
  const [apSurveyError, setApSurveyError] = useState<string | null>(null);
  const [reconAps, setReconAps] = useState<ReconAP[]>([]);
  const [reconning, setReconning] = useState(false);
  const [clients, setClients] = useState<Client[]>([]);
  const [clientScanning, setClientScanning] = useState(false);
  const [capturing, setCapturing] = useState(false);
  const [capture, setCapture] = useState<Capture | null>(null);
  const [wpaCapturing, setWpaCapturing] = useState(false);
  const [wpaResult, setWpaResult] = useState<WpaResult | null>(null);
  const [exporting, setExporting] = useState<ExportFmt | null>(null);
  const [plugins, setPlugins] = useState<PluginSummary[]>([]);
  const [journey, setJourney] = useState<Journey | null>(null);
  const [inventory, setInventory] = useState<InvAP[]>([]);
  const [bleDevices, setBleDevices] = useState<BleDevice[]>([]);
  const [bleScanning, setBleScanning] = useState(false);
  const [bleSurveyComplete, setBleSurveyComplete] = useState(false);
  const [bleSurveyError, setBleSurveyError] = useState<string | null>(null);
  const [monitoring, setMonitoring] = useState(false);
  const [monitorResult, setMonitorResult] = useState<MonitorResult | null>(null);
  const [badusbBuilding, setBadusbBuilding] = useState(false);
  const [badusbOk, setBadusbOk] = useState<boolean | null>(null);
  const [unattendedDumping, setUnattendedDumping] = useState(false);
  const [unattendedRows, setUnattendedRows] = useState<number | null>(null);
  const [unattendedPartial, setUnattendedPartial] = useState(false);
  const [unattendedCapacityDrops, setUnattendedCapacityDrops] = useState(0);
  const [unattendedRecovery, setUnattendedRecovery] = useState<string | null>(null);
  const [unattendedSource, setUnattendedSource] = useState<UnattendedSource | null>(null);
  const [unattendedError, setUnattendedError] = useState<string | null>(null);
  const hardwareActive = !!busy || apScanning || reconning || clientScanning || capturing
    || wpaCapturing || !!exporting || bleScanning || monitoring || badusbBuilding || unattendedDumping;

  const clearOperation = useCallback((key: OperationKey) => {
    const operation = operationRef.current[key];
    if (!operation) return;
    window.clearTimeout(operation.timer);
    delete operationRef.current[key];
  }, []);

  const cancelOperations = useCallback(() => {
    for (const operation of Object.values(operationRef.current)) {
      if (operation) window.clearTimeout(operation.timer);
    }
    operationRef.current = {};
  }, []);

  const cancelAwaits = useCallback(() => {
    const cancellations = [...pendingAwaitsRef.current.values()];
    pendingAwaitsRef.current.clear();
    for (const cancel of cancellations) cancel();
  }, []);

  const beginOperation = useCallback((
    key: OperationKey,
    timeoutMs: number,
    onTimeout: () => void,
    requestId?: string,
  ) => {
    clearOperation(key);
    const token = ++requestRef.current;
    const operation: OperationGuard = {
      token,
      session: boardSessionRef.current,
      connection: connectionRef.current,
      requestId,
      timer: 0,
    };
    operation.timer = window.setTimeout(() => {
      const current = operationRef.current[key];
      if (!current || current.token !== token) return;
      delete operationRef.current[key];
      onTimeout();
    }, timeoutMs);
    operationRef.current[key] = operation;
    return operation;
  }, [clearOperation]);

  const operationIsCurrent = useCallback((key: OperationKey, requestId?: string) => {
    const operation = operationRef.current[key];
    if (!operation) return false;
    if (operation.session !== boardSessionRef.current || operation.connection !== connectionRef.current) return false;
    if (operation.requestId && requestId !== operation.requestId) return false;
    return true;
  }, []);

  const finishOperation = useCallback((key: OperationKey, requestId?: string) => {
    if (!operationIsCurrent(key, requestId)) return false;
    clearOperation(key);
    return true;
  }, [clearOperation, operationIsCurrent]);

  const hasCurrentProcess = useCallback(() => {
    if (pendingAwaitsRef.current.size > 0) return true;
    return Object.values(operationRef.current).some((operation) => operation
      && operation.session === boardSessionRef.current
      && operation.connection === connectionRef.current);
  }, []);

  const addTimeoutLog = useCallback((label: string) => {
    setLogs((previous) => [...previous.slice(-400), {
      line: `${label} did not return a completion before the safety timeout; no completion proof was recorded.`,
      level: 'error',
      id: idRef.current++,
    }]);
  }, []);

  const resetBoardSession = useCallback(() => {
    boardSessionRef.current += 1;
    detectRequestRef.current = null;
    unattendedRequestRef.current = null;
    cancelOperations();
    cancelAwaits();
    setBoard(null);
    setDetecting(false);
    setBusy(null);
    setFlashed(null);
    setFlashedAttack(null);
    setBuilds({});
    setLogs([]);
    setSerial([]);
    setGate('idle');
    setSerialOn(false);
    setAps([]);
    setApScanning(false);
    setApSurveyComplete(false);
    setApSurveyError(null);
    setReconAps([]);
    setReconning(false);
    setClients([]);
    setClientScanning(false);
    setCapturing(false);
    setCapture(null);
    setWpaCapturing(false);
    setWpaResult(null);
    setExporting(null);
    setBleDevices([]);
    setBleScanning(false);
    setBleSurveyComplete(false);
    setBleSurveyError(null);
    setMonitoring(false);
    setMonitorResult(null);
    setBadusbBuilding(false);
    setBadusbOk(null);
    setUnattendedDumping(false);
    setUnattendedRows(null);
    setUnattendedPartial(false);
    setUnattendedCapacityDrops(0);
    setUnattendedRecovery(null);
    setUnattendedSource(null);
    setUnattendedError(null);
  }, [cancelAwaits, cancelOperations]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      let token = '';
      try {
        token = (await (await fetch(`${BACKEND}/auth-token`)).json()).token;
      } catch {
        return;
      }
      if (cancelled) return;
      const s = io(BACKEND, { auth: { token } });
      sockRef.current = s;
      s.on('connect', () => {
        connectionRef.current += 1;
        setConnected(true);
      });
      s.on('disconnect', () => {
        connectionRef.current += 1;
        setConnected(false);
        // A transport break loses completion events and physical-board
        // identity. Clear every in-flight flag and all session proof, then
        // require a fresh detect after reconnecting.
        resetBoardSession();
      });
      s.on('detect:result', (b: Board) => {
        if (!b.requestId || b.requestId !== detectRequestRef.current) return;
        if (!finishOperation('detect', b.requestId)) return;
        detectRequestRef.current = null;
        const error = b.error;
        // Keep an unverified detection result so the Product surface can show
        // why the serial device is not ready, while `found:false` keeps every
        // hardware action disabled.
        setBoard(b);
        setDetecting(false);
        if (error) setLogs((p) => [...p.slice(-400), { line: error, level: 'error', id: idRef.current++ }]);
      });
      s.on('log', ({ line, level }: { line: string; level?: string }) => {
        // Backend process logs are uncorrelated. Accept them only while this
        // board session owns a live request, so output from an abandoned task
        // cannot appear beneath a later detection.
        if (!hasCurrentProcess()) return;
        setLogs((p) => [...p.slice(-400), { line, level, id: idRef.current++ }]);
      });
      s.on('serial:line', ({ raw, rc, state }: { raw: string; rc: string | null; state: string | null }) => {
        setSerial((p) => [...p.slice(-220), { raw, rc, state, id: idRef.current++ }]);
        if (state === 'accepted') setGate('accepted');
        else if (state === 'blocked') setGate('blocked');
        else if (state === 'error') setSerialOn(false);
      });
      s.on('serial:closed', () => setSerialOn(false));
      s.on('apscan:result', ({ aps, complete, requestId }: { aps: AP[]; complete?: boolean; requestId?: string }) => {
        if (operationIsCurrent('apscan', requestId) && complete === true) setAps(aps || []);
      });
      s.on('apscan:done', ({ ok, aps, flashed, complete, error, requestId }: { ok: boolean; aps?: AP[]; flashed?: boolean; complete?: boolean; error?: string; requestId?: string }) => {
        if (!finishOperation('apscan', requestId)) return;
        const surveyComplete = ok && complete === true;
        setApScanning(false);
        setApSurveyComplete(surveyComplete);
        setApSurveyError(surveyComplete ? null : error || 'Survey firmware was not confirmed to have completed its read phase.');
        setAps(surveyComplete ? aps || [] : []);
        if (flashed === true) { setFlashed('target'); setFlashedAttack('apscan'); }
      });
      s.on('recon:result', ({ aps, complete, requestId }: { aps: ReconAP[]; complete?: boolean; requestId?: string }) => {
        if (operationIsCurrent('recon', requestId) && complete === true) setReconAps(aps || []);
      });
      s.on('recon:done', ({ ok, aps, flashed, complete, requestId }: { ok: boolean; aps?: ReconAP[]; flashed?: boolean; complete?: boolean; requestId?: string }) => {
        if (!finishOperation('recon', requestId)) return;
        setReconning(false);
        setReconAps(ok && complete === true ? aps || [] : []);
        if (flashed === true) { setFlashed('target'); setFlashedAttack('recon'); }
      });
      s.on('clients:result', ({ clients, complete, requestId }: { clients: Client[]; complete?: boolean; requestId?: string }) => {
        if (operationIsCurrent('clients', requestId) && complete === true) setClients(clients || []);
      });
      s.on('clients:done', ({ ok, clients, flashed, complete, requestId }: { ok: boolean; clients?: Client[]; flashed?: boolean; complete?: boolean; requestId?: string }) => {
        if (!finishOperation('clients', requestId)) return;
        setClientScanning(false);
        setClients(ok && complete === true ? clients || [] : []);
        if (flashed === true) { setFlashed('target'); setFlashedAttack('clientscan'); }
      });
      s.on('sniff:result', (cap: Capture & { complete?: boolean; requestId?: string }) => {
        if (operationIsCurrent('sniff', cap.requestId) && cap.complete === true) setCapture(cap);
      });
      s.on('sniff:done', ({ ok, flashed, complete, requestId }: { ok: boolean; flashed?: boolean; complete?: boolean; requestId?: string }) => {
        if (!finishOperation('sniff', requestId)) return;
        setCapturing(false);
        if (!ok || complete !== true) setCapture(null);
        if (flashed === true) { setFlashed('target'); setFlashedAttack('sniff'); }
      });
      s.on('wpa:result', (r: WpaResult & { requestId?: string }) => {
        if (operationIsCurrent('wpa', r.requestId)) setWpaResult(r);
      });
      s.on('wpa:done', ({ ok, requestId }: { ok: boolean; requestId?: string }) => {
        if (!finishOperation('wpa', requestId)) return;
        setWpaCapturing(false);
        if (ok) { setFlashed('target'); setFlashedAttack('capture-wpa'); }
      });
      s.on('export:done', ({ ok, name, requestId }: { ok: boolean; name?: string; requestId?: string }) => {
        if (!finishOperation('export', requestId)) return;
        setExporting(null);
        if (ok && name) { const a = document.createElement('a'); a.href = `${BACKEND}/capture/${name}`; a.download = name; document.body.appendChild(a); a.click(); a.remove(); }
      });
      s.on('inventory:done', ({ rows }: { rows: InvAP[] }) => setInventory(rows || []));
      s.on('plugin:list:done', ({ plugins: list }: { plugins: PluginSummary[] }) => setPlugins(list || []));
      s.on('journey:get:done', ({ ok, journey: j }: { ok: boolean; journey: Journey }) => setJourney(ok ? j : null));
      s.on('blescan:result', ({ devices, complete, requestId }: { devices: BleDevice[]; complete?: boolean; requestId?: string }) => {
        if (operationIsCurrent('blescan', requestId) && complete === true) setBleDevices(devices || []);
      });
      s.on('blescan:done', ({ ok, devices, flashed, complete, error, requestId }: { ok: boolean; devices?: BleDevice[]; flashed?: boolean; complete?: boolean; error?: string; requestId?: string }) => {
        if (!finishOperation('blescan', requestId)) return;
        const surveyComplete = ok && complete === true;
        setBleScanning(false);
        setBleSurveyComplete(surveyComplete);
        setBleSurveyError(surveyComplete ? null : error || 'Survey firmware was not confirmed to have completed its read phase.');
        setBleDevices(surveyComplete ? devices || [] : []);
        if (flashed === true) { setFlashed('target'); setFlashedAttack('blescan'); }
      });
      s.on('monitor:result', (r: MonitorResult & { complete?: boolean; requestId?: string }) => {
        if (operationIsCurrent('monitor', r.requestId) && r.complete === true) setMonitorResult(r);
      });
      s.on('monitor:done', ({ ok, flashed, complete, requestId }: { ok: boolean; flashed?: boolean; complete?: boolean; requestId?: string }) => {
        if (!finishOperation('monitor', requestId)) return;
        setMonitoring(false);
        if (!ok || complete !== true) setMonitorResult(null);
        if (flashed === true) { setFlashed('target'); setFlashedAttack('monitor'); }
      });
      s.on('badusb:done', ({ ok, requestId }: { ok: boolean; requestId?: string }) => {
        if (!finishOperation('badusb', requestId)) return;
        setBadusbBuilding(false);
        setBadusbOk(ok);
      });
      s.on('unattended:done', ({ ok, name, rows, partial, capacityDrops, recovery, source, error, requestId }: { ok: boolean; name?: string; rows?: number; partial?: boolean; capacityDrops?: number; recovery?: string; source?: string; error?: string; requestId?: string }) => {
        const pending = unattendedRequestRef.current;
        if (!pending || requestId !== pending.id || pending.session !== boardSessionRef.current) return;
        if (!finishOperation('unattended', requestId)) return;
        unattendedRequestRef.current = null;
        setUnattendedDumping(false);
        setUnattendedPartial(false);
        setUnattendedCapacityDrops(0);
        setUnattendedRecovery(null);
        setUnattendedSource(null);
        setUnattendedError(ok ? null : error || 'USB readback failed. Review the local bridge log.');
        if (!ok || !name) return;
        const knownSources: UnattendedSource[] = ['primary-csv', 'temp-csv-read-only-recovery', 'backup-csv-read-only-recovery', 'database-read-only', 'volatile-ram-read-only'];
        const csvSources: UnattendedSource[] = ['primary-csv', 'temp-csv-read-only-recovery', 'backup-csv-read-only-recovery'];
        const safeSource = knownSources.includes(source as UnattendedSource) ? source as UnattendedSource : null;
        const validRecoveryPair = (recovery == null && safeSource === 'primary-csv')
          || (recovery === 'orphaned-csv-preserved' && safeSource !== null && csvSources.includes(safeSource))
          || (recovery === 'database-read-only' && safeSource === 'database-read-only')
          || (recovery === 'volatile-ram-read-only' && safeSource === 'volatile-ram-read-only');
        const safeRows = Number.isSafeInteger(rows) && (rows as number) >= 0 && (rows as number) <= 120 ? rows as number : null;
        const safeDrops = Number.isSafeInteger(capacityDrops) && (capacityDrops as number) >= 0 && (capacityDrops as number) <= 0xFFFFFFFF ? capacityDrops as number : null;
        const validCounts = safeRows !== null && safeDrops !== null && partial === (safeDrops > 0);
        const safeName = /^[A-Za-z0-9._-]+$/.test(name) ? name : null;
        if (!safeSource || !validRecoveryPair || !validCounts || !safeName) {
          setUnattendedError('USB readback returned unverified source or inventory metadata. No download proof was recorded.');
          return;
        }
        setUnattendedRows(safeRows);
        setUnattendedPartial(safeDrops > 0);
        setUnattendedCapacityDrops(safeDrops);
        setUnattendedRecovery(recovery === 'orphaned-csv-preserved' || recovery === 'database-read-only' || recovery === 'volatile-ram-read-only' ? recovery : null);
        setUnattendedSource(safeSource);
        const a = document.createElement('a');
        a.href = `${BACKEND}/capture/${encodeURIComponent(safeName)}`;
        a.download = safeName;
        document.body.appendChild(a);
        a.click();
        a.remove();
      });
    })();
    return () => {
      cancelled = true;
      sockRef.current?.disconnect();
      sockRef.current = null;
      cancelOperations();
      cancelAwaits();
    };
  }, [cancelAwaits, cancelOperations, finishOperation, hasCurrentProcess, operationIsCurrent, resetBoardSession]);

  const emitAwait = useCallback(<T extends { ok: boolean; requestId?: string },>(
    ev: string,
    payload: any,
    doneEv: string,
    timeoutMs: number,
    requestId: string,
  ): Promise<T> => {
    return new Promise((resolve) => {
      const s = sockRef.current;
      if (!s?.connected) {
        resolve({ ok: false, requestId } as T);
        return;
      }
      const pendingId = ++requestRef.current;
      const session = boardSessionRef.current;
      const connection = connectionRef.current;
      let settled = false;
      const settle = (data: T) => {
        if (settled) return;
        settled = true;
        window.clearTimeout(timer);
        s.off(doneEv, onDone);
        pendingAwaitsRef.current.delete(pendingId);
        resolve(data);
      };
      const onDone = (data: T) => {
        // Request IDs keep a late completion from a timed-out attempt from
        // satisfying a retry on the same socket.
        if (data?.requestId !== requestId) return;
        if (session !== boardSessionRef.current || connection !== connectionRef.current) return;
        settle(data);
      };
      const timer = window.setTimeout(() => {
        addTimeoutLog(ev === 'build' ? 'Firmware build' : 'Firmware flash');
        settle({ ok: false, requestId } as T);
      }, timeoutMs);
      pendingAwaitsRef.current.set(pendingId, () => settle({ ok: false, requestId } as T));
      s.on(doneEv, onDone);
      s.emit(ev, { ...payload, requestId });
    });
  }, [addTimeoutLog]);

  const releaseSerial = useCallback((): Promise<void> => {
    return new Promise((resolve) => {
      const s = sockRef.current;
      if (!s?.connected) return resolve();
      let settled = false;
      const finish = () => {
        if (settled) return;
        settled = true;
        window.clearTimeout(timer);
        setSerialOn(false);
        resolve();
      };
      const timer = window.setTimeout(finish, 2500);
      s.emit('serial:stop', finish);
    });
  }, []);

  const detect = useCallback(async (selection: DetectOptions = {}) => {
    const s = sockRef.current;
    if (!s?.connected || hardwareActive) return;
    // Detection starts a new physical-board session. Never carry flash,
    // serial, or retrieval proof from the board that was previously attached.
    resetBoardSession();
    const requestId = `detect-${++requestRef.current}`;
    detectRequestRef.current = requestId;
    setDetecting(true);
    beginOperation('detect', DETECT_TIMEOUT_MS, () => {
      if (detectRequestRef.current !== requestId) return;
      detectRequestRef.current = null;
      setDetecting(false);
      setBoard(null);
      addTimeoutLog('Device detection');
    }, requestId);
    await releaseSerial();
    if (detectRequestRef.current !== requestId || !operationIsCurrent('detect', requestId) || !s.connected) return;
    s.emit('detect', { requestId, ...selection });
  }, [addTimeoutLog, beginOperation, hardwareActive, operationIsCurrent, releaseSerial, resetBoardSession]);

  const startSerial = useCallback(() => {
    const s = sockRef.current;
    if (!s?.connected || !board?.port) return;
    s.emit('serial:start', { port: board.port });
    setSerialOn(true);
  }, [board]);

  const stopSerial = useCallback(() => {
    sockRef.current?.emit('serial:stop');
    setSerialOn(false);
  }, []);

  const clearSerial = useCallback(() => { setSerial([]); setGate('idle'); }, []);

  const scanAps = useCallback(async () => {
    const s = sockRef.current;
    if (!s?.connected || !board?.chipKey || !board?.port || apScanning) return;
    const requestId = `apscan-${++requestRef.current}`;
    setLogs([]); setAps([]); setApScanning(true);
    setFlashed(null); setFlashedAttack(null); setUnattendedRows(null);
    setApSurveyComplete(false); setApSurveyError(null);
    setUnattendedPartial(false); setUnattendedCapacityDrops(0); setUnattendedRecovery(null); setUnattendedSource(null); setUnattendedError(null);
    beginOperation('apscan', HARDWARE_TIMEOUT_MS, () => {
      setApScanning(false);
      setAps([]);
      setApSurveyComplete(false);
      setApSurveyError('The local bridge did not return survey completion before the safety timeout.');
      addTimeoutLog('Wi-Fi survey');
    }, requestId);
    await releaseSerial();
    if (!operationIsCurrent('apscan', requestId) || !s.connected) return;
    s.emit('scanAps', { chip: board.chipKey, port: board.port, requestId });
  }, [addTimeoutLog, apScanning, beginOperation, board, operationIsCurrent, releaseSerial]);

  const recon = useCallback(async (channel: number) => {
    const s = sockRef.current;
    if (!s?.connected || !board?.chipKey || reconning) return;
    const requestId = `recon-${++requestRef.current}`;
    setLogs([]); setReconAps([]); setReconning(true);
    setFlashed(null); setFlashedAttack(null); setUnattendedRows(null);
    setUnattendedRecovery(null);
    setUnattendedSource(null);
    beginOperation('recon', HARDWARE_TIMEOUT_MS, () => {
      setReconning(false);
      setReconAps([]);
      addTimeoutLog('Wi-Fi security survey');
    }, requestId);
    await releaseSerial();
    if (!operationIsCurrent('recon', requestId) || !s.connected) return;
    s.emit('recon', { chip: board.chipKey, channel, port: board.port, requestId });
  }, [addTimeoutLog, beginOperation, board, operationIsCurrent, reconning, releaseSerial]);

  const scanClients = useCallback(async (bssid: string, channel: number) => {
    const s = sockRef.current;
    if (!s?.connected || !board?.chipKey || clientScanning) return;
    const requestId = `clients-${++requestRef.current}`;
    setLogs([]); setClients([]); setClientScanning(true);
    setFlashed(null); setFlashedAttack(null); setUnattendedRows(null);
    setUnattendedRecovery(null);
    setUnattendedSource(null);
    beginOperation('clients', HARDWARE_TIMEOUT_MS, () => {
      setClientScanning(false);
      setClients([]);
      addTimeoutLog('Client survey');
    }, requestId);
    await releaseSerial();
    if (!operationIsCurrent('clients', requestId) || !s.connected) return;
    s.emit('scanClients', { chip: board.chipKey, bssid, channel, port: board.port, requestId });
  }, [addTimeoutLog, beginOperation, board, clientScanning, operationIsCurrent, releaseSerial]);

  const sniff = useCallback(async (channel: number, seconds: number) => {
    const s = sockRef.current;
    if (!s?.connected || !board?.chipKey || capturing) return;
    const requestId = `sniff-${++requestRef.current}`;
    setLogs([]); setCapture(null); setCapturing(true);
    setFlashed(null); setFlashedAttack(null); setUnattendedRows(null);
    setUnattendedRecovery(null);
    setUnattendedSource(null);
    beginOperation('sniff', HARDWARE_TIMEOUT_MS, () => {
      setCapturing(false);
      setCapture(null);
      addTimeoutLog('Packet capture');
    }, requestId);
    await releaseSerial();
    if (!operationIsCurrent('sniff', requestId) || !s.connected) return;
    s.emit('sniff', { chip: board.chipKey, channel, seconds, port: board.port, requestId });
  }, [addTimeoutLog, beginOperation, board, capturing, operationIsCurrent, releaseSerial]);

  const exportData = useCallback((format: ExportFmt, rows: any[], pseudonymize = false) => {
    const s = sockRef.current;
    if (!s?.connected || !rows.length || exporting) return;
    const requestId = `export-${++requestRef.current}`;
    setExporting(format);
    beginOperation('export', LOCAL_TASK_TIMEOUT_MS, () => {
      setExporting(null);
      addTimeoutLog('Artifact export');
    }, requestId);
    s.emit('export', { format, rows, requestId, pseudonymize });
  }, [addTimeoutLog, beginOperation, exporting]);

  // Plugin operations are host-side only: no hardware lock, so they stay
  // usable while a survey is running.
  const pluginList = useCallback(() => {
    sockRef.current?.emit('plugin:list');
  }, []);

  const journeyGet = useCallback(() => {
    sockRef.current?.emit('journey:get');
  }, []);

  const pluginRead = useCallback((file: string, source: string, done: (text: string) => void) => {
    const s = sockRef.current;
    if (!s?.connected) return done('');
    const requestId = `plugin-read-${++requestRef.current}`;
    const handler = (payload: any) => {
      if (payload?.requestId !== requestId) return;
      s.off('plugin:read:done', handler);
      done(payload?.ok ? String(payload.source ?? '') : `could not read ${file}`);
    };
    s.on('plugin:read:done', handler);
    s.emit('plugin:read', { file, source, requestId });
  }, []);

  const pluginValidate = useCallback((source: string, done: (report: string) => void) => {
    const s = sockRef.current;
    if (!s?.connected) return done('bridge is offline');
    const requestId = `plugin-validate-${++requestRef.current}`;
    const handler = (payload: any) => {
      if (payload?.requestId !== requestId) return;
      s.off('plugin:validate:done', handler);
      done(String(payload?.report ?? 'no report'));
    };
    s.on('plugin:validate:done', handler);
    s.emit('plugin:validate', { source, requestId });
  }, []);

  const loadInventory = useCallback(() => {
    if (sockRef.current?.connected) sockRef.current.emit('inventory');
  }, []);

  const bleScan = useCallback(async (seconds?: number) => {
    const s = sockRef.current;
    if (!s?.connected || !board?.chipKey || bleScanning) return;
    const requestId = `blescan-${++requestRef.current}`;
    setLogs([]); setBleDevices([]); setBleScanning(true);
    setFlashed(null); setFlashedAttack(null); setUnattendedRows(null);
    setBleSurveyComplete(false); setBleSurveyError(null);
    setUnattendedPartial(false); setUnattendedCapacityDrops(0); setUnattendedRecovery(null); setUnattendedSource(null); setUnattendedError(null);
    beginOperation('blescan', HARDWARE_TIMEOUT_MS, () => {
      setBleScanning(false);
      setBleDevices([]);
      setBleSurveyComplete(false);
      setBleSurveyError('The local bridge did not return survey completion before the safety timeout.');
      addTimeoutLog('Bluetooth survey');
    }, requestId);
    await releaseSerial();
    if (!operationIsCurrent('blescan', requestId) || !s.connected) return;
    s.emit('bleScan', { chip: board.chipKey, seconds, port: board.port, requestId });
  }, [addTimeoutLog, beginOperation, bleScanning, board, operationIsCurrent, releaseSerial]);

  const monitor = useCallback(async (channel: number, bssid?: string, seconds?: number) => {
    const s = sockRef.current;
    if (!s?.connected || !board?.chipKey || monitoring) return;
    const requestId = `monitor-${++requestRef.current}`;
    setLogs([]); setMonitorResult(null); setMonitoring(true);
    setFlashed(null); setFlashedAttack(null); setUnattendedRows(null);
    setUnattendedRecovery(null);
    setUnattendedSource(null);
    beginOperation('monitor', HARDWARE_TIMEOUT_MS, () => {
      setMonitoring(false);
      setMonitorResult(null);
      addTimeoutLog('Traffic monitor');
    }, requestId);
    await releaseSerial();
    if (!operationIsCurrent('monitor', requestId) || !s.connected) return;
    s.emit('monitor', { chip: board.chipKey, channel, bssid, seconds, port: board.port, requestId });
  }, [addTimeoutLog, beginOperation, board, monitoring, operationIsCurrent, releaseSerial]);

  const buildBadusb = useCallback(async (payload: string) => {
    const s = sockRef.current;
    if (!s?.connected || !payload || badusbBuilding) return;
    const requestId = `badusb-${++requestRef.current}`;
    setLogs([]); setBadusbOk(null); setBadusbBuilding(true);
    beginOperation('badusb', BUILD_TIMEOUT_MS, () => {
      setBadusbBuilding(false);
      setBadusbOk(null);
      addTimeoutLog('USB HID research build');
    }, requestId);
    await releaseSerial();
    if (!operationIsCurrent('badusb', requestId) || !s.connected) return;
    s.emit('buildBadusb', { payload, requestId });
  }, [addTimeoutLog, badusbBuilding, beginOperation, operationIsCurrent, releaseSerial]);

  const retrieveUnattended = useCallback(async () => {
    const s = sockRef.current;
    if (!s?.connected || !board?.port || unattendedDumping) return;
    setLogs([]);
    setUnattendedRows(null);
    setUnattendedPartial(false);
    setUnattendedCapacityDrops(0);
    setUnattendedRecovery(null);
    setUnattendedSource(null);
    setUnattendedError(null);
    setUnattendedDumping(true);
    const session = boardSessionRef.current;
    const requestId = `unattended-${++requestRef.current}`;
    unattendedRequestRef.current = { id: requestId, session };
    beginOperation('unattended', LOCAL_TASK_TIMEOUT_MS, () => {
      const pending = unattendedRequestRef.current;
      if (!pending || pending.id !== requestId || pending.session !== boardSessionRef.current) return;
      unattendedRequestRef.current = null;
      setUnattendedDumping(false);
      setUnattendedRows(null);
      setUnattendedPartial(false);
      setUnattendedCapacityDrops(0);
      setUnattendedRecovery(null);
      setUnattendedSource(null);
      setUnattendedError('The local bridge did not return a completion before the safety timeout. No readback proof was recorded.');
      addTimeoutLog('Passive logger readback');
    }, requestId);
    await releaseSerial();
    const pending = unattendedRequestRef.current;
    if (!pending || pending.id !== requestId || pending.session !== boardSessionRef.current
      || !operationIsCurrent('unattended', requestId) || !s.connected) return;
    s.emit('unattendedDump', { port: board.port, requestId });
  }, [addTimeoutLog, beginOperation, board, operationIsCurrent, releaseSerial, unattendedDumping]);

  const captureWpa = useCallback(async (bssid: string, channel: number, client?: string, ssid?: string, seconds?: number) => {
    const s = sockRef.current;
    if (!s?.connected || !board?.chipKey || wpaCapturing) return;
    const requestId = `wpa-${++requestRef.current}`;
    setLogs([]); setWpaResult(null); setWpaCapturing(true);
    setFlashed(null); setFlashedAttack(null); setUnattendedRows(null);
    setUnattendedRecovery(null);
    setUnattendedSource(null);
    beginOperation('wpa', HARDWARE_TIMEOUT_MS, () => {
      setWpaCapturing(false);
      setWpaResult(null);
      addTimeoutLog('WPA assessment capture');
    }, requestId);
    await releaseSerial();
    if (!operationIsCurrent('wpa', requestId) || !s.connected) return;
    s.emit('captureWpa', { chip: board.chipKey, bssid, channel, client, ssid, seconds, port: board.port, requestId });
  }, [addTimeoutLog, beginOperation, board, operationIsCurrent, releaseSerial, wpaCapturing]);

  const runVariant = useCallback(async (variant: Variant, target?: TargetOptions) => {
    const s = sockRef.current;
    if (!s?.connected || !board?.chipKey || !board?.port || busy) return;
    const boardSession = boardSessionRef.current;
    // A new attempt invalidates the previous board proof even when the new
    // build or flash fails part-way through.
    setLogs([]); setSerial([]); setGate('idle');
    setFlashed(null); setFlashedAttack(null); setUnattendedRows(null);
    setUnattendedPartial(false); setUnattendedCapacityDrops(0); setUnattendedRecovery(null); setUnattendedSource(null); setUnattendedError(null);
    setBusy({ kind: 'build', variant });
    await releaseSerial();
    if (boardSession !== boardSessionRef.current || !s.connected) { setBusy(null); return; }
    const payload: any = { variant, chip: board.chipKey };
    if (target) {
      if (target.bssid) payload.bssid = target.bssid;
      if (target.channel !== undefined) payload.channel = target.channel;
      if (target.client) payload.client = target.client;
      if (target.attack) payload.attack = target.attack;
      if (target.ssid !== undefined) payload.ssid = target.ssid;
    }
    const buildRequestId = `build-${++requestRef.current}`;
    const b = await emitAwait<{ ok: boolean; buildId?: string; requestId?: string }>(
      'build', payload, 'build:done', BUILD_TIMEOUT_MS, buildRequestId,
    );
    if (boardSession !== boardSessionRef.current) return;
    if (!b.ok || !b.buildId || !s.connected) { setBusy(null); return; }
    setBuilds((p) => ({ ...p, [variant]: b.buildId! }));
    setBusy({ kind: 'flash', variant });
    const flashRequestId = `flash-${++requestRef.current}`;
    const f = await emitAwait<{ ok: boolean; requestId?: string }>(
      'flash', { chip: board.chipKey, buildId: b.buildId, port: board.port },
      'flash:done', FLASH_TIMEOUT_MS, flashRequestId,
    );
    if (boardSession !== boardSessionRef.current) return;
    setBusy(null);
    if (f.ok) {
      setFlashed(variant);
      setFlashedAttack(variant === 'target' ? target?.attack || null : null);
      setSerial([]); setGate('idle');
      if (s.connected) {
        s.emit('serial:start', { port: board.port });
        setSerialOn(true);
      }
    }
  }, [board, busy, emitAwait, releaseSerial]);

  return (
    <Ctx.Provider value={{
      connected, hardwareActive, board, detecting, busy, flashed, flashedAttack, builds, logs, serial, gate, serialOn,
      aps, apScanning, apSurveyComplete, apSurveyError,
      reconAps, reconning, clients, clientScanning, capturing, capture, wpaCapturing, wpaResult, backend: BACKEND,
      plugins, pluginList, pluginRead, pluginValidate, journey, journeyGet,
      exporting, inventory, bleDevices, bleScanning, bleSurveyComplete, bleSurveyError,
      monitoring, monitorResult, badusbBuilding, badusbOk,
      unattendedDumping, unattendedRows, unattendedPartial, unattendedCapacityDrops, unattendedRecovery, unattendedSource, unattendedError,
      detect, runVariant, scanAps, recon, scanClients, sniff, captureWpa, exportData, loadInventory, bleScan, monitor, buildBadusb, retrieveUnattended, startSerial, stopSerial, clearSerial,
    }}>
      {children}
    </Ctx.Provider>
  );
}
