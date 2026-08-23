import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useLab, type AP, type BleDevice } from '../lib/lab';
import PluginsPage from './PluginsPage';
import './product.css';

export type ProductPage = 'home' | 'observe' | 'runs' | 'artifacts' | 'plugins' | 'devices';

export type ProductAppProps = {
  initialPage?: ProductPage;
};

type IconName = ProductPage | 'bridge' | 'shield' | 'radio' | 'download' | 'eye' | 'mask' | 'chevron';

const NAV_ITEMS: Array<{ id: ProductPage; label: string; helper: string }> = [
  { id: 'home', label: 'Home', helper: 'Field overview' },
  { id: 'observe', label: 'Observe', helper: 'Nearby signals' },
  { id: 'runs', label: 'Runs', helper: 'Surveys and readback' },
  { id: 'artifacts', label: 'Artifacts', helper: 'Local evidence' },
  { id: 'plugins', label: 'Plugins', helper: 'Extend Medusa' },
  { id: 'devices', label: 'Devices', helper: 'Hardware and companions' },
];

const PAGE_COPY: Record<ProductPage, { eyebrow: string; title: string; description: string }> = {
  home: {
    eyebrow: 'Field overview',
    title: 'Your radios, at a glance',
    description: 'See what is connected, what is running, and what evidence exists before starting another survey.',
  },
  observe: {
    eyebrow: 'Nearby signals',
    title: 'Observe the environment',
    description: 'Review Wi-Fi and Bluetooth discoveries in one place. No assessment action is launched from this view.',
  },
  runs: {
    eyebrow: 'Surveys and readback',
    title: 'Run scoped discovery',
    description: 'Start discovery and retrieve locally stored observations. Disruptive transmission controls are not available in this product surface.',
  },
  plugins: {
    eyebrow: 'Extend Medusa',
    title: 'Plugins',
    description: 'Short programs over the same primitives the built-in capabilities use. What a plugin declares is what the firmware will let it do.',
  },
  artifacts: {
    eyebrow: 'Local evidence',
    title: 'Artifacts',
    description: 'Download evidence created in this browser session or export the bridge inventory for your own records.',
  },
  devices: {
    eyebrow: 'Hardware and companions',
    title: 'Devices',
    description: 'Verify the bridge, attached board, firmware proof, and the intended role of each companion surface.',
  },
};

export default function ProductApp({ initialPage = 'home' }: ProductAppProps) {
  const lab = useLab();
  const [page, setPage] = useState<ProductPage>(initialPage);
  const [privacyMode, setPrivacyMode] = useState(true);
  const [pageAnnouncement, setPageAnnouncement] = useState('');
  const [noticeAcknowledged, setNoticeAcknowledged] = useState(() => {
    try { return window.localStorage.getItem('medusa.product.notice.v1') === 'acknowledged'; }
    catch { return false; }
  });
  const copy = PAGE_COPY[page];
  const operation = getOperation(lab);
  const artifactCount = getArtifactCount(lab);

  const navigate = (next: ProductPage) => {
    setPage(next);
    // An explicit `behavior` in the options dict overrides the CSS
    // scroll-behavior property, so the reduced-motion block in product.css
    // cannot reach this call. Ask for the preference directly instead.
    const reduceMotion = typeof window.matchMedia === 'function'
      && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    document.querySelector<HTMLElement>('.mp-content')?.scrollTo({
      top: 0,
      behavior: reduceMotion ? 'auto' : 'smooth',
    });
    // Swapping the whole main region is silent to assistive tech: focus stays
    // on the nav button and nothing announces the new view. Name the
    // destination in a polite live region so the change is perceivable.
    setPageAnnouncement(`${PAGE_COPY[next].title}. ${PAGE_COPY[next].eyebrow}.`);
  };

  const acknowledgeNotice = () => {
    try { window.localStorage.setItem('medusa.product.notice.v1', 'acknowledged'); }
    catch { /* The notice still closes for this browser session. */ }
    setNoticeAcknowledged(true);
  };

  return (
    <div className="medusa-product">
      <aside className="mp-sidebar">
        <div className="mp-brand" aria-label="Medusa">
          <span className="mp-brand-word">MEDUSA</span>
          <span className="mp-brand-kind">radio security</span>
        </div>

        <nav className="mp-nav" aria-label="Primary">
          {NAV_ITEMS.map((item) => (
            <button
              type="button"
              key={item.id}
              className="mp-nav-item"
              aria-current={page === item.id ? 'page' : undefined}
              onClick={() => navigate(item.id)}
            >
              <Icon name={item.id} />
              <span>
                <strong>{item.label}</strong>
                <small>{item.helper}</small>
              </span>
            </button>
          ))}
        </nav>

        <div className="mp-sidebar-foot">
          <div className="mp-safety-note">
            <Icon name="shield" />
            <span><strong>Discovery-first</strong><small>Disruptive assessment controls are separated.</small></span>
          </div>
        </div>
      </aside>

      <main className="mp-main">
        <header className="mp-header">
          <div className="mp-mobile-brand">
            <span>MEDUSA</span>
            <small>{copy.eyebrow}</small>
          </div>
          <div className="mp-status-strip" aria-label="Current device and run status">
            <StatusItem
              icon="bridge"
              label="Bridge"
              value={lab.connected ? 'Online' : 'Offline'}
              tone={lab.connected ? 'ready' : 'warning'}
            />
            <StatusItem
              icon="radio"
              label="Device"
              value={lab.board?.found ? compactChipName(lab.board.chip) : lab.board?.needsChipSelection || lab.board?.needsDeviceSelection ? 'Selection needed' : lab.board?.error ? 'Not verified' : 'Not detected'}
              tone={lab.board?.found ? 'ready' : lab.board?.error ? 'warning' : 'muted'}
            />
            <StatusItem
              icon="runs"
              label="Current run"
              value={operation.label}
              tone={operation.tone}
              live={operation.live}
            />
          </div>
          {/*
            The visible label is the action, not the state. Pairing an action
            label with aria-pressed made a screen reader announce "Show
            identifiers, pressed" while identifiers were in fact masked — the
            exact inverse of the truth, on the one control that governs whether
            personal data is on screen. Name the state in the accessible name
            instead, keeping the visible text as its prefix so the accessible
            name still contains the visible label.
          */}
          <button
            type="button"
            className="mp-privacy"
            onClick={() => setPrivacyMode((value) => !value)}
            aria-label={privacyMode
              ? 'Show identifiers. SSIDs, Bluetooth names, and hardware addresses are currently masked.'
              : 'Mask identifiers. SSIDs, Bluetooth names, and hardware addresses are currently visible.'}
          >
            <Icon name={privacyMode ? 'eye' : 'mask'} />
            <span>{privacyMode ? 'Show identifiers' : 'Mask identifiers'}</span>
          </button>
        </header>

        {/*
          Announces the destination after an in-page navigation. Visually
          hidden: it exists only so the view change is perceivable without
          sight, since focus deliberately stays on the nav control.
        */}
        <p className="mp-visually-hidden" aria-live="polite" role="status">{pageAnnouncement}</p>

        <div className="mp-content">
          <header className="mp-page-head">
            <span className="mp-eyebrow">{copy.eyebrow}</span>
            <h1>{copy.title}</h1>
            <p>{copy.description}</p>
          </header>

          {page === 'home' && (
            <HomePage
              onNavigate={navigate}
              artifactCount={artifactCount}
              operation={operation}
              privacyMode={privacyMode}
            />
          )}
          {page === 'observe' && <ObservePage privacyMode={privacyMode} onNavigate={navigate} />}
          {page === 'runs' && <RunsPage onNavigate={navigate} />}
          {page === 'artifacts' && <ArtifactsPage onNavigate={navigate} privacyMode={privacyMode} />}
          {page === 'plugins' && <PluginsPage />}
          {page === 'devices' && <DevicesPage privacyMode={privacyMode} />}
        </div>
      </main>

      {/* Distinct landmark name: only one of the two navs is displayed at a
          given width, but identical names would collide if that ever changed. */}
      <nav className="mp-bottom-nav" aria-label="Primary, compact">
        {NAV_ITEMS.map((item) => (
          <button
            type="button"
            key={item.id}
            aria-current={page === item.id ? 'page' : undefined}
            onClick={() => navigate(item.id)}
          >
            <Icon name={item.id} />
            <span>{item.label}</span>
          </button>
        ))}
      </nav>

      {!noticeAcknowledged && <FirstUseNotice onAcknowledge={acknowledgeNotice} />}
    </div>
  );
}

type Operation = { label: string; detail: string; tone: 'active' | 'ready' | 'muted' | 'warning'; live: boolean };

function getOperation(lab: ReturnType<typeof useLab>): Operation {
  if (!lab.connected) return { label: 'Bridge offline', detail: 'Reconnect the local bridge to continue.', tone: 'warning', live: false };
  if (lab.detecting) return { label: 'Detecting device', detail: 'Checking local serial ports.', tone: 'active', live: true };
  if (lab.busy?.kind === 'build') return { label: 'Building firmware', detail: 'A hardware build is in progress.', tone: 'active', live: true };
  if (lab.busy?.kind === 'flash') return { label: 'Flashing device', detail: 'Keep the board attached until the write completes.', tone: 'active', live: true };
  if (lab.unattendedDumping) return { label: 'Reading stored survey', detail: 'Flushing and reading the local log over USB.', tone: 'active', live: true };
  if (lab.apScanning) return { label: 'Wi-Fi survey', detail: 'Collecting nearby access-point beacons and scan results.', tone: 'active', live: true };
  if (lab.bleScanning) return { label: 'Bluetooth survey', detail: 'Listening for nearby Bluetooth advertisements.', tone: 'active', live: true };
  if (lab.monitoring) return { label: 'Channel watch', detail: 'A passive channel monitor is running.', tone: 'active', live: true };
  if (lab.capturing) return { label: 'Packet capture', detail: 'A local capture is running.', tone: 'active', live: true };
  if (lab.reconning || lab.clientScanning || lab.wpaCapturing) {
    return { label: 'Radio task', detail: 'A local lab task is using the attached radio.', tone: 'active', live: true };
  }
  if (!lab.board?.found && (lab.board?.needsChipSelection || lab.board?.needsDeviceSelection)) return { label: 'Board selection needed', detail: 'Choose the attached board and its C3 or S3 model before hardware actions are enabled.', tone: 'warning', live: false };
  if (!lab.board?.found) return { label: 'No run active', detail: 'Detect a board before starting a hardware survey.', tone: 'muted', live: false };
  return { label: 'Ready', detail: 'The device is available for a new survey.', tone: 'ready', live: false };
}

function getArtifactCount(lab: ReturnType<typeof useLab>) {
  let count = 0;
  if (lab.capture) count += 1;
  if (lab.inventory.length) count += 1;
  if (lab.unattendedRows !== null) count += 1;
  return count;
}

function HomePage({
  onNavigate,
  artifactCount,
  operation,
  privacyMode,
}: {
  onNavigate: (page: ProductPage) => void;
  artifactCount: number;
  operation: Operation;
  privacyMode: boolean;
}) {
  const lab = useLab();
  const lastNetwork = [...lab.aps].sort((a, b) => b.rssi - a.rssi)[0];

  return (
    <div className="mp-page-stack">
      <section className="mp-hero">
        <div className="mp-hero-copy">
          <span className="mp-proof-label"><Icon name="shield" /> Default surface · no disruptive assessment controls</span>
          <h2>{lab.board?.found ? `${compactChipName(lab.board.chip)} is ready.` : lab.board?.needsChipSelection || lab.board?.needsDeviceSelection ? 'Identify the attached radio.' : lab.board?.error ? 'Radio not verified.' : 'Connect a Medusa radio.'}</h2>
          <p>
            {lab.board?.found
              ? 'Start with observation, keep evidence local, and verify each result before treating it as proof.'
              : lab.board?.error || 'Attach a supported ESP board, verify it here, then begin with a nearby-signal survey.'}
          </p>
          <div className="mp-hero-actions">
            {!lab.board?.found ? (
              <DeviceDetectControls />
            ) : (
              <button type="button" className="mp-button mp-button-primary" onClick={() => onNavigate('observe')}>
                <Icon name="observe" /> Open Observe
              </button>
            )}
            <button type="button" className="mp-button mp-button-quiet" onClick={() => onNavigate('runs')}>
              View runs <Icon name="chevron" />
            </button>
          </div>
        </div>
        <div className="mp-hero-state" aria-live="polite">
          <span className={`mp-state-orb mp-tone-${operation.tone}`} aria-hidden="true"><i /></span>
          <span className="mp-eyebrow">Current run</span>
          <strong>{operation.label}</strong>
          <p>{operation.detail}</p>
        </div>
      </section>

      <section className="mp-metrics" aria-label="Current session summary">
        <Metric label="Wi-Fi discoveries" value={lab.aps.length} proof="this browser session" accent={lab.aps.length > 0} />
        <Metric label="Bluetooth discoveries" value={lab.bleDevices.length} proof="this browser session" accent={lab.bleDevices.length > 0} />
        <Metric label="Saved inventory" value={lab.inventory.length} proof="loaded from local bridge" accent={lab.inventory.length > 0} />
        <Metric label="Artifacts" value={artifactCount} proof="available in this session" accent={artifactCount > 0} />
      </section>

      <div className="mp-two-column">
        <section className="mp-card mp-card-roomy">
          <div className="mp-section-head">
            <div><span className="mp-eyebrow">Start here</span><h2>Observation before action</h2></div>
          </div>
          <div className="mp-start-list">
            <Step number="1" title="Verify the hardware" detail={lab.board?.found ? 'Detected in this browser session.' : 'No board has been detected in this browser session.'} done={!!lab.board?.found} />
            <Step number="2" title="Survey nearby signals" detail={lab.aps.length || lab.bleDevices.length ? 'Discovery results are ready to review.' : 'No discovery survey has completed in this browser session.'} done={lab.aps.length > 0 || lab.bleDevices.length > 0} />
            <Step number="3" title="Keep the evidence" detail={artifactCount ? `${artifactCount} artifact group${artifactCount === 1 ? '' : 's'} available.` : 'No downloadable evidence exists in this browser session.'} done={artifactCount > 0} />
          </div>
        </section>

        <section className="mp-card mp-card-roomy">
          <div className="mp-section-head">
            <div><span className="mp-eyebrow">Strongest signal</span><h2>Nearest Wi-Fi observation</h2></div>
            {lastNetwork && <SignalBadge rssi={lastNetwork.rssi} />}
          </div>
          {lastNetwork ? (
            <div className="mp-feature-observation">
              <strong>{privacyMode ? 'Masked network' : displaySsid(lastNetwork.ssid)}</strong>
              <span>{privacyMode ? maskMac(lastNetwork.bssid) : lastNetwork.bssid}</span>
              <dl>
                <div><dt>Channel</dt><dd>{lastNetwork.channel}</dd></div>
                <div><dt>Signal</dt><dd>{lastNetwork.rssi} dBm</dd></div>
                <div><dt>Proof</dt><dd>Current session</dd></div>
              </dl>
              <button type="button" className="mp-text-button" onClick={() => onNavigate('observe')}>Review all observations <Icon name="chevron" /></button>
            </div>
          ) : (
            <EmptyState
              compact
              icon="observe"
              title="No Wi-Fi survey yet"
              description="Open Runs to start a nearby-network survey with the attached board."
              action={<button type="button" className="mp-text-button" onClick={() => onNavigate('runs')}>Open Runs <Icon name="chevron" /></button>}
            />
          )}
        </section>
      </div>

    </div>
  );
}

function ObservePage({ privacyMode, onNavigate }: { privacyMode: boolean; onNavigate: (page: ProductPage) => void }) {
  const lab = useLab();
  const [kind, setKind] = useState<'wifi' | 'bluetooth'>('wifi');
  const [query, setQuery] = useState('');

  const wifi = useMemo(
    () => [...lab.aps]
      .sort((a, b) => b.rssi - a.rssi)
      .filter((ap) => `${ap.ssid} ${ap.bssid} ${ap.channel}`.toLowerCase().includes(query.trim().toLowerCase())),
    [lab.aps, query],
  );
  const bluetooth = useMemo(
    () => [...lab.bleDevices]
      .sort((a, b) => b.rssi - a.rssi)
      .filter((device) => `${device.name} ${device.addr} ${device.vendor}`.toLowerCase().includes(query.trim().toLowerCase())),
    [lab.bleDevices, query],
  );
  const currentCount = kind === 'wifi' ? wifi.length : bluetooth.length;

  return (
    <div className="mp-page-stack">
      <section className="mp-toolbar" aria-label="Observation filters">
        <div className="mp-segmented" role="tablist" aria-label="Signal type">
          <button type="button" role="tab" aria-selected={kind === 'wifi'} onClick={() => setKind('wifi')}>Wi-Fi <span>{lab.aps.length}</span></button>
          <button type="button" role="tab" aria-selected={kind === 'bluetooth'} onClick={() => setKind('bluetooth')}>Bluetooth <span>{lab.bleDevices.length}</span></button>
        </div>
        <label className="mp-search">
          <span className="mp-sr-only">Search observations</span>
          <Icon name="observe" />
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={`Search ${kind === 'wifi' ? 'SSID, BSSID, or channel' : 'name, address, or vendor'}`} />
          {query && <button type="button" onClick={() => setQuery('')} aria-label="Clear search">×</button>}
        </label>
        <button type="button" className="mp-button mp-button-quiet" onClick={() => onNavigate('runs')}>New survey <Icon name="chevron" /></button>
      </section>

      <section className="mp-card mp-observations">
        <div className="mp-list-head">
          <div>
            <span className="mp-eyebrow">{kind === 'wifi' ? '2.4 GHz Wi-Fi' : 'Bluetooth Low Energy'}</span>
            <h2>{currentCount} visible {currentCount === 1 ? 'result' : 'results'}</h2>
          </div>
          <span className="mp-proof-label"><i className={kind === 'wifi' ? (lab.apScanning ? 'live' : '') : (lab.bleScanning ? 'live' : '')} /> {kind === 'wifi' ? (lab.apScanning ? 'Survey in progress' : 'Current session') : (lab.bleScanning ? 'Listening now' : 'Current session')}</span>
        </div>

        {kind === 'wifi' ? (
          wifi.length ? <WifiList rows={wifi} privacyMode={privacyMode} /> : (
            <EmptyState icon="observe" title={query ? 'No matching networks' : 'No Wi-Fi observations'} description={query ? 'Change or clear the search to see other results.' : 'Run a Wi-Fi survey with a connected board, then results will appear here.'} action={!query ? <button type="button" className="mp-button mp-button-primary" onClick={() => onNavigate('runs')}>Open Runs</button> : undefined} />
          )
        ) : (
          bluetooth.length ? <BluetoothList rows={bluetooth} privacyMode={privacyMode} /> : (
            <EmptyState icon="radio" title={query ? 'No matching Bluetooth devices' : 'No Bluetooth observations'} description={query ? 'Change or clear the search to see other results.' : 'Run a short Bluetooth survey with a connected board, then results will appear here.'} action={!query ? <button type="button" className="mp-button mp-button-primary" onClick={() => onNavigate('runs')}>Open Runs</button> : undefined} />
          )
        )}
      </section>

      <p className="mp-footnote"><Icon name="mask" /> Identifier masking changes only what is shown on screen. It does not rewrite or remove data from downloaded artifacts.</p>
    </div>
  );
}

function DeviceDetectControls() {
  const lab = useLab();
  const needsSelection = !!(lab.board?.needsChipSelection || lab.board?.needsDeviceSelection);
  const candidates = lab.board?.candidates || [];
  const candidateKey = candidates.map((candidate) => `${candidate.stableId}:${candidate.port}`).join('|');
  const [port, setPort] = useState('');
  const [chip, setChip] = useState<'' | 'esp32-c3' | 'esp32-s3'>('');

  useEffect(() => {
    if (!needsSelection) {
      setPort('');
      setChip('');
      return;
    }
    setPort(lab.board?.needsDeviceSelection ? '' : lab.board?.port || candidates[0]?.port || '');
    setChip('');
  }, [candidateKey, lab.board?.needsDeviceSelection, lab.board?.port, needsSelection]);

  if (!needsSelection) {
    return (
      <button
        type="button"
        className="mp-button mp-button-primary"
        disabled={!lab.connected || lab.detecting || lab.hardwareActive}
        onClick={() => lab.detect()}
      >
        {lab.detecting ? <Spinner /> : <Icon name="radio" />}
        {lab.detecting ? 'Detecting…' : lab.board?.found ? 'Detect again' : 'Detect device'}
      </button>
    );
  }

  return (
    <div className="mp-detect-controls" aria-label="Identify attached ESP board">
      <label>
        <span>USB device</span>
        <select value={port} onChange={(event) => setPort(event.target.value)} disabled={lab.detecting}>
          <option value="">Select attached device</option>
          {candidates.map((candidate) => (
            <option key={candidate.stableId} value={candidate.port}>
              {candidate.port.replace('/dev/', '')}{candidate.usbSerial ? ` · USB …${candidate.usbSerial.slice(-4)}` : ''}
            </option>
          ))}
          {!candidates.length && lab.board?.port && <option value={lab.board.port}>{lab.board.port.replace('/dev/', '')}</option>}
        </select>
      </label>
      <label>
        <span>Board model</span>
        <select value={chip} onChange={(event) => setChip(event.target.value as typeof chip)} disabled={lab.detecting}>
          <option value="">Select C3 or S3</option>
          <option value="esp32-c3">ESP32-C3</option>
          <option value="esp32-s3">ESP32-S3</option>
        </select>
      </label>
      <button
        type="button"
        className="mp-button mp-button-primary"
        disabled={!lab.connected || lab.detecting || lab.hardwareActive || !port || !chip}
        onClick={() => lab.detect({ port, chip: chip || undefined })}
      >
        {lab.detecting ? <Spinner /> : <Icon name="shield" />}
        {lab.detecting ? 'Verifying…' : 'Use selected board'}
      </button>
      <small>Choose the model printed on your board. Detection reads USB descriptors only and does not open or reset the logger.</small>
    </div>
  );
}

function RunsPage({ onNavigate }: { onNavigate: (page: ProductPage) => void }) {
  const lab = useLab();
  const [loggerConfirmed, setLoggerConfirmed] = useState(false);
  const [wifiConfirmed, setWifiConfirmed] = useState(false);
  const [bluetoothConfirmed, setBluetoothConfirmed] = useState(false);
  const [hidConfirmed, setHidConfirmed] = useState(false);
  // Shown and editable before anything is built. A keystroke image whose
  // contents you cannot read before building is one you cannot consent to.
  const [hidPayload, setHidPayload] = useState('MEDUSA-LAB self test');
  const hidChipOk = lab.board?.chipKey === 'esp32-s3';
  const hardwareBusy = !!lab.busy || lab.detecting || lab.apScanning || lab.bleScanning || lab.monitoring || lab.capturing || lab.reconning || lab.clientScanning || lab.wpaCapturing || lab.unattendedDumping;
  const loggerInstalling = lab.busy?.variant === 'target';
  const loggerFlashed = lab.flashedAttack === 'unattended';
  const wifiSurveyFlashed = lab.flashedAttack === 'apscan';
  const bluetoothSurveyFlashed = lab.flashedAttack === 'blescan';
  const csvRecovery = lab.unattendedRecovery === 'orphaned-csv-preserved';
  const databaseRecovery = lab.unattendedRecovery === 'database-read-only';
  const volatileRecovery = lab.unattendedRecovery === 'volatile-ram-read-only';
  const recoveredReadback = csvRecovery || databaseRecovery || volatileRecovery;
  const readbackSource = unattendedSourceLabel(lab.unattendedSource);
  const loggerStorageBlocked = loggerFlashed && lab.serial.some((line) => /LittleFS mount failed/i.test(line.raw));
  const boardIdentity = `${lab.board?.stableId || lab.board?.usbSerial || ''}|${lab.board?.port || ''}|${lab.board?.chipKey || ''}`;

  useEffect(() => {
    setLoggerConfirmed(false);
    setWifiConfirmed(false);
    setBluetoothConfirmed(false);
  }, [boardIdentity]);

  return (
    <div className="mp-page-stack">
      {!lab.board?.found && (
        <section className="mp-inline-banner">
          <Icon name="radio" />
          <div><strong>{lab.board?.error ? 'Device not ready' : 'No device detected'}</strong><span>{lab.board?.error || 'Attach a supported ESP board and detect it before starting a hardware survey.'}</span></div>
          <DeviceDetectControls />
        </section>
      )}

      <section className="mp-run-grid">
        <RunCard
          icon="radio"
          title="Install passive logger"
          description="Build and flash beacon-only inventory firmware. It does not associate, transmit 802.11 frames, upload data, format storage, or clear an existing inventory."
          boundary="Replaces current firmware · no storage format"
          state={loggerInstalling ? 'running' : loggerFlashed ? 'complete' : 'idle'}
          evidence={loggerStorageBlocked
            ? 'Firmware booted fail-closed because LittleFS is unavailable; storage initialization remains a separate manual approval'
            : loggerFlashed
              ? 'Built and flashed in this browser session; capture, persistence, and USB readback remain separate proof gates'
              : 'Not installed in this browser session'}
          action={
            <div className="mp-run-confirm">
              <label>
                <input type="checkbox" checked={loggerConfirmed} onChange={(event) => setLoggerConfirmed(event.target.checked)} />
                <span>I control this board and understand flashing replaces its current firmware.</span>
              </label>
              <button
                type="button"
                className="mp-button mp-button-primary"
                disabled={!lab.board?.found || hardwareBusy || !loggerConfirmed}
                onClick={() => lab.runVariant('target', { attack: 'unattended' })}
              >
                {loggerInstalling ? <Spinner /> : <Icon name="radio" />}
                {loggerInstalling ? (lab.busy?.kind === 'flash' ? 'Flashing…' : 'Building…') : loggerFlashed ? 'Install again' : 'Build + flash'}
              </button>
            </div>
          }
        />
        <RunCard
          icon="observe"
          title="Nearby Wi-Fi survey"
          description="Compile and flash Wi-Fi survey firmware, replacing the board's current firmware, then actively scan for nearby 2.4 GHz access points. The scan may send ordinary probe requests but does not associate with an access point."
          boundary="Replaces current firmware · active discovery scan · no association"
          state={lab.apScanning ? 'running' : lab.apSurveyComplete ? 'complete' : lab.apSurveyError ? 'attention' : 'idle'}
          evidence={lab.apScanning
            ? 'Build, flash, and survey in progress'
            : lab.apSurveyComplete
              ? `${lab.aps.length} result${lab.aps.length === 1 ? '' : 's'} returned; Wi-Fi survey firmware is now installed`
              : wifiSurveyFlashed
                ? `Firmware was flashed, but the survey read phase was not proven complete: ${lab.apSurveyError || 'completion markers were not received'}`
                : lab.apSurveyError
                  ? `No survey proof: ${lab.apSurveyError}`
              : 'No successful Wi-Fi survey flash recorded in this browser session'}
          action={
            <div className="mp-run-confirm">
              <label>
                <input type="checkbox" checked={wifiConfirmed} onChange={(event) => setWifiConfirmed(event.target.checked)} />
                <span>I understand this survey replaces the board's current firmware.</span>
              </label>
              <button type="button" className="mp-button mp-button-primary" disabled={!lab.board?.found || hardwareBusy || !wifiConfirmed} onClick={lab.scanAps}>
                {lab.apScanning ? <Spinner /> : <Icon name="radio" />}
                {lab.apScanning ? 'Surveying…' : lab.apSurveyComplete ? 'Run again' : wifiSurveyFlashed ? 'Retry survey' : 'Build, flash + survey'}
              </button>
            </div>
          }
        />
        <RunCard
          icon="radio"
          title="Bluetooth presence survey"
          description="Compile and flash Bluetooth survey firmware, replacing the board's current firmware, then scan nearby advertisements for ten seconds. Active scanning may send standard scan requests but does not connect to devices."
          boundary="Replaces current firmware · active discovery scan · no connection"
          state={lab.bleScanning ? 'running' : lab.bleSurveyComplete ? 'complete' : lab.bleSurveyError ? 'attention' : 'idle'}
          evidence={lab.bleScanning
            ? 'Build, flash, and survey in progress'
            : lab.bleSurveyComplete
              ? `${lab.bleDevices.length} result${lab.bleDevices.length === 1 ? '' : 's'} returned; Bluetooth survey firmware is now installed`
              : bluetoothSurveyFlashed
                ? `Firmware was flashed, but the survey read phase was not proven complete: ${lab.bleSurveyError || 'completion markers were not received'}`
                : lab.bleSurveyError
                  ? `No survey proof: ${lab.bleSurveyError}`
              : 'No successful Bluetooth survey flash recorded in this browser session'}
          action={
            <div className="mp-run-confirm">
              <label>
                <input type="checkbox" checked={bluetoothConfirmed} onChange={(event) => setBluetoothConfirmed(event.target.checked)} />
                <span>I understand this survey replaces the board's current firmware.</span>
              </label>
              <button type="button" className="mp-button mp-button-primary" disabled={!lab.board?.found || hardwareBusy || !bluetoothConfirmed} onClick={() => lab.bleScan(10)}>
                {lab.bleScanning ? <Spinner /> : <Icon name="radio" />}
                {lab.bleScanning ? 'Listening…' : lab.bleSurveyComplete ? 'Run again' : bluetoothSurveyFlashed ? 'Retry survey' : 'Build, flash + survey'}
              </button>
            </div>
          }
        />
        <RunCard
          icon="devices"
          title="USB HID test image"
          description="Builds a keyboard image that types the text below when the board is plugged into a computer. It is built, not flashed — installing it is a separate, explicit step."
          boundary="ESP32-S3 only · your own machines"
          state={lab.badusbOk === true ? 'complete' : lab.badusbBuilding ? 'running' : hidChipOk ? 'idle' : 'attention'}
          evidence={
            lab.badusbOk === true
              ? 'A build succeeded. Host behaviour is unverified: what a computer does with the keystrokes has not been observed here.'
              : hidChipOk
                ? 'Nothing is built yet.'
                : 'Needs an ESP32-S3. The C3 and C6 expose only USB-Serial-JTAG and cannot present a keyboard at all.'
          }
          action={
            <div className="mp-row-actions mp-row-actions-stacked">
              <label className="mp-export-privacy">
                <input
                  type="checkbox"
                  checked={hidConfirmed}
                  disabled={!hidChipOk || lab.badusbBuilding}
                  onChange={(event) => setHidConfirmed(event.target.checked)}
                />
                <span>
                  <strong>This is a machine I am authorised to test</strong>
                  <small>
                    A HID image types into whatever it is plugged into, with the rights of whoever is
                    logged in. Read the payload below before building it.
                  </small>
                </span>
              </label>
              <input
                className="mp-hid-payload"
                value={hidPayload}
                disabled={lab.badusbBuilding}
                onChange={(event) => setHidPayload(event.target.value)}
                aria-label="Keystrokes the image will type"
                placeholder="Keystrokes to type"
              />
              <button
                type="button"
                className="mp-button mp-button-quiet"
                disabled={!hidChipOk || !hidConfirmed || !hidPayload.trim() || hardwareBusy}
                onClick={() => lab.buildBadusb(hidPayload)}
              >
                {lab.badusbBuilding ? 'Building…' : 'Build image'}
              </button>
            </div>
          }
        />
        <RunCard
          icon="download"
          title="Unattended survey readback"
          description="Flush and retrieve the passive logger's local CSV over USB. Readback cannot clear the board."
          boundary="USB · non-clearing"
          state={lab.unattendedDumping ? 'running' : recoveredReadback ? 'recovery' : lab.unattendedRows !== null ? 'complete' : 'idle'}
          evidence={lab.unattendedRows !== null
            ? volatileRecovery
              ? `Volatile recovery: ${lab.unattendedRows} row${lab.unattendedRows === 1 ? '' : 's'} rendered read-only from non-persisted RAM; the committed database may contain an earlier snapshot and CSV recovery artifacts may remain`
              : databaseRecovery
              ? `Database recovery: ${lab.unattendedRows} row${lab.unattendedRows === 1 ? '' : 's'} rendered read-only from ${readbackSource}; readback did not clear recovery artifacts, and additional CSV artifacts may remain on the device`
              : csvRecovery
                ? `CSV recovery: ${lab.unattendedRows} row${lab.unattendedRows === 1 ? '' : 's'} downloaded from ${readbackSource}; readback did not clear recovery artifacts and ordinary committed-store proof is unavailable`
              : lab.unattendedPartial
              ? `Partial: ${lab.unattendedRows} stored rows downloaded from ${readbackSource}; ${lab.unattendedCapacityDrops} observations were dropped after device capacity was reached`
              : `${lab.unattendedRows} stored row${lab.unattendedRows === 1 ? '' : 's'} downloaded from ${readbackSource}; no capacity drops reported`
            : lab.unattendedError
              ? `Readback failed: ${lab.unattendedError}`
              : 'No readback in this browser session'}
          action={<button type="button" className="mp-button mp-button-primary" disabled={!lab.board?.found || hardwareBusy} onClick={lab.retrieveUnattended}>{lab.unattendedDumping ? <Spinner /> : <Icon name="download" />}{lab.unattendedDumping ? 'Reading…' : 'Retrieve over USB'}</button>}
        />
        <RunCard
          icon="artifacts"
          title="Bridge inventory"
          description="Load the inventory already held by the local bridge. This does not use the attached radio."
          boundary="Local bridge"
          state={lab.inventory.length ? 'complete' : 'idle'}
          evidence={lab.inventory.length ? `${lab.inventory.length} row${lab.inventory.length === 1 ? '' : 's'} loaded` : 'Not loaded in this browser session'}
          action={<button type="button" className="mp-button mp-button-quiet" disabled={!lab.connected} onClick={lab.loadInventory}>Load inventory</button>}
        />
      </section>

      <section className="mp-scope-card">
        <div className="mp-scope-icon"><Icon name="shield" /></div>
        <div><span className="mp-eyebrow">Clear boundary</span><h2>Assessment workflows are not mixed into discovery.</h2><p>These runs collect observations or read local data. Disruptive transmission remains unavailable until firmware-enforced scope, rate, audit, duration, and stop controls exist.</p></div>
        <button type="button" className="mp-text-button" onClick={() => onNavigate('artifacts')}>Review artifacts <Icon name="chevron" /></button>
      </section>
    </div>
  );
}

function ArtifactsPage({ onNavigate, privacyMode }: { onNavigate: (page: ProductPage) => void; privacyMode: boolean }) {
  const lab = useLab();
  const captureName = lab.capture?.path.split('/').pop();
  const artifactCount = getArtifactCount(lab);
  // Default to the on-screen disclosure choice, then let the operator override
  // it per export. Masking on screen and exporting in full is the mismatch
  // this control exists to close.
  const [exportPseudonymized, setExportPseudonymized] = useState(privacyMode);

  return (
    <div className="mp-page-stack">
      <section className="mp-artifact-summary">
        <div><span>{artifactCount}</span><small>artifact groups in this browser session</small></div>
        <p><Icon name="shield" /> Files are served by the local Medusa bridge. No cloud upload is enabled here.</p>
      </section>

      {artifactCount ? (
        <section className="mp-artifact-list">
          {lab.capture && captureName && (
            <ArtifactRow
              title="Passive packet capture"
              detail={`${lab.capture.packets} frames · channel ${lab.capture.channel} · ${lab.capture.eapol} EAPOL`}
              proof="Created in this browser session"
              format="PCAP"
              action={<a className="mp-button mp-button-quiet" href={`${lab.backend}/capture/${encodeURIComponent(captureName)}`} download><Icon name="download" /> Download</a>}
            />
          )}
          {lab.inventory.length > 0 && (
            <ArtifactRow
              title="Bridge network inventory"
              detail={`${lab.inventory.length} rows loaded from the local bridge`}
              proof="Current bridge snapshot"
              format="CSV / posture"
              action={
                <div className="mp-row-actions mp-row-actions-stacked">
                  {/*
                    Identifiers could be masked on screen while every export
                    still wrote them in full. An export is the copy that leaves
                    the operator, so the disclosure choice belongs here, next to
                    the buttons that produce it — and it defaults to whatever
                    the header toggle already says.
                  */}
                  <label className="mp-export-privacy">
                    <input
                      type="checkbox"
                      checked={exportPseudonymized}
                      disabled={!!lab.exporting}
                      onChange={(event) => setExportPseudonymized(event.target.checked)}
                    />
                    <span>
                      <strong>Pseudonymize identifiers</strong>
                      <small>
                        Replaces network names and the device half of each hardware address.
                        Vendor and every security finding are preserved. Your local inventory keeps the real values.
                      </small>
                    </span>
                  </label>
                  <div className="mp-row-actions">
                    <button type="button" className="mp-button mp-button-quiet" disabled={!!lab.exporting} onClick={() => lab.exportData('posture', lab.inventory, exportPseudonymized)}>Posture</button>
                    <button type="button" className="mp-button mp-button-quiet" disabled={!!lab.exporting} onClick={() => lab.exportData('airodump', lab.inventory, exportPseudonymized)}>Airodump CSV</button>
                    <button type="button" className="mp-button mp-button-quiet" disabled={!!lab.exporting} onClick={() => lab.exportData('wigle', lab.inventory, exportPseudonymized)}>WiGLE CSV</button>
                  </div>
                </div>
              }
            />
          )}
          {lab.unattendedRows !== null && (
            <ArtifactRow
              title={lab.unattendedRecovery === 'volatile-ram-read-only' ? 'Non-persisted RAM recovery export' : lab.unattendedRecovery === 'database-read-only' ? 'Read-only database recovery export' : lab.unattendedRecovery === 'orphaned-csv-preserved' ? 'Recovery-preserved unattended CSV' : lab.unattendedPartial ? 'Partial unattended survey readback' : 'Unattended survey readback'}
              detail={lab.unattendedRecovery === 'volatile-ram-read-only'
                ? `${lab.unattendedRows} rows were rendered from volatile RAM and are not proof of the committed database or CSV; recovery artifacts may remain on the device`
                : lab.unattendedRecovery === 'database-read-only'
                ? `${lab.unattendedRows} rows were rendered from ${unattendedSourceLabel(lab.unattendedSource)}; other CSV recovery artifacts may remain on the device`
                : lab.unattendedRecovery === 'orphaned-csv-preserved'
                ? `${lab.unattendedRows} rows were recovered from ${unattendedSourceLabel(lab.unattendedSource)}; readback did not clear recovery artifacts`
                : lab.unattendedPartial
                ? `${lab.unattendedRows} stored rows were retrieved from ${unattendedSourceLabel(lab.unattendedSource)}; ${lab.unattendedCapacityDrops} observations were dropped after device capacity was reached`
                : `${lab.unattendedRows} stored rows were retrieved from ${unattendedSourceLabel(lab.unattendedSource)} with no capacity drops reported`}
              proof={lab.unattendedRecovery === 'volatile-ram-read-only'
                ? 'Validated volatile rendering · non-persisted recovery evidence'
                : lab.unattendedRecovery === 'database-read-only'
                ? 'Validated database rendering · CSV store and remaining recovery artifacts not proven'
                : lab.unattendedRecovery === 'orphaned-csv-preserved'
                ? 'Validated recovery download · committed store not proven'
                : lab.unattendedPartial ? 'Validated download · explicitly incomplete' : 'Validated download · device reported no capacity drops'}
              format="CSV"
              action={<span className="mp-proof-label mp-proof-ok">Downloaded</span>}
            />
          )}
        </section>
      ) : (
        <section className="mp-card">
          <EmptyState icon="artifacts" title="No artifacts in this browser session" description="Run a survey, retrieve the unattended logger, or load the bridge inventory. Evidence will appear here only after the relevant action completes." action={<button type="button" className="mp-button mp-button-primary" onClick={() => onNavigate('runs')}>Open Runs</button>} />
        </section>
      )}

      <section className="mp-inline-banner mp-inline-banner-soft">
        <Icon name="artifacts" />
        <div><strong>Need the existing bridge inventory?</strong><span>Load it without starting a hardware survey.</span></div>
        <button type="button" className="mp-button mp-button-quiet" disabled={!lab.connected} onClick={lab.loadInventory}>Load inventory</button>
      </section>
    </div>
  );
}

function DevicesPage({ privacyMode }: { privacyMode: boolean }) {
  const lab = useLab();
  const storeConfirmed = lab.flashedAttack === 'unattended'
    && lab.serial.some((line) => line.raw.startsWith('UNATT_STORE\t')
      && line.raw.includes('\tdb=ok') && line.raw.includes('\tcsv=ok')
      && line.raw.includes('\tpath=/medusa_log.csv'));

  return (
    <div className="mp-page-stack">
      <section className="mp-device-grid">
        <article className="mp-device-primary">
          <div className="mp-device-visual" aria-hidden="true"><span>M</span><i className={lab.board?.found ? 'online' : ''} /></div>
          <div className="mp-device-copy">
            <span className="mp-eyebrow">Attached radio</span>
            <h2>{lab.board?.found ? compactChipName(lab.board.chip) : lab.board?.error ? 'Board not verified' : 'No board detected'}</h2>
            <p>{lab.board?.found ? 'Detected through the local Medusa bridge in this browser session.' : lab.board?.error || 'Connect a supported board over USB, then detect it from this browser.'}</p>
            <div className="mp-device-facts">
              <Fact label="Chip" value={lab.board?.chip || '—'} />
              <Fact label="Architecture" value={lab.board?.arch || '—'} />
              <Fact label="Port" value={lab.board?.port ? (privacyMode ? 'Masked USB port' : lab.board.port.replace('/dev/', '')) : '—'} />
              <Fact label="USB serial" value={lab.board?.usbSerial ? (privacyMode ? maskIdentity(lab.board.usbSerial) : lab.board.usbSerial) : '—'} />
            </div>
            <DeviceDetectControls />
          </div>
        </article>

        <article className="mp-card mp-proof-card">
          <span className="mp-eyebrow">Proof from this session</span>
          <h2>Firmware state</h2>
          <ProofRow label="Detected" value={lab.board?.found ? 'Yes' : 'No'} ok={!!lab.board?.found} />
          <ProofRow label="Built" value={Object.keys(lab.builds).length ? `${Object.keys(lab.builds).length} variant${Object.keys(lab.builds).length === 1 ? '' : 's'}` : 'No session proof'} ok={Object.keys(lab.builds).length > 0} />
          <ProofRow label="Flashed" value={lab.flashedAttack ? `${lab.flashedAttack} firmware this session` : lab.flashed ? `${lab.flashed} this session` : 'No session proof'} ok={!!lab.flashed} />
          <ProofRow label="Passive store" value={storeConfirmed ? 'Serial-confirmed' : 'Not confirmed'} ok={storeConfirmed} />
          <ProofRow label="Readback" value={lab.unattendedRecovery === 'volatile-ram-read-only' ? 'Volatile RAM rendering; not persisted proof' : lab.unattendedRecovery === 'database-read-only' ? 'Read-only database rendering; CSV not proven' : lab.unattendedRecovery === 'orphaned-csv-preserved' ? 'Recovery-preserved CSV; store not proven' : lab.unattendedRows !== null ? `Validated USB download · ${unattendedSourceLabel(lab.unattendedSource)}` : 'No session proof'} ok={lab.unattendedRows !== null && !lab.unattendedRecovery} />
          <p className="mp-card-note">A successful build is not a flash, and a flash is not proof that storage or RF observation worked.</p>
        </article>
      </section>

      <section className="mp-card mp-card-roomy">
        <div className="mp-section-head">
          <div><span className="mp-eyebrow">Companion surfaces</span><h2>One system, different jobs</h2></div>
          <span className="mp-proof-label">Product direction · not shipped proof</span>
        </div>
        <div className="mp-companions">
          <Companion title="Web" status="Available now" current description="Device setup, clearly labelled discovery, local readback, and evidence review." />
          <Companion title="Phone" status="Design target" description="Pairing, field surveys, run status, notifications, and artifact handoff for Medusa and NosferatOS devices." />
          <Companion title="Watch" status="Design target" description="Glanceable device health, run status, alerts, and a stop request. No setup screens or raw-capture review." />
        </div>
      </section>

      <section className="mp-inline-banner mp-inline-banner-soft">
        <Icon name="download" />
        <div>
          <strong>{lab.unattendedRecovery === 'volatile-ram-read-only' ? 'Non-persisted RAM recovery' : lab.unattendedRecovery === 'database-read-only' ? 'Read-only database recovery' : lab.unattendedRecovery === 'orphaned-csv-preserved' ? 'Recovery-preserved CSV download' : lab.unattendedError ? 'Readback needs attention' : 'Passive logger attached?'}</strong>
          <span>{lab.unattendedRecovery === 'volatile-ram-read-only'
            ? 'The download reflects volatile RAM after a partial persistence failure. It is not committed-store proof; database and CSV recovery artifacts require maintenance review.'
            : lab.unattendedRecovery === 'database-read-only'
            ? 'The download was rendered from a validated database. Readback did not clear recovery artifacts, and additional CSV artifacts may remain for maintenance review.'
            : lab.unattendedRecovery === 'orphaned-csv-preserved'
            ? 'A preserved CSV source was downloaded. Readback did not clear recovery artifacts; this is recovery evidence, not committed-store proof.'
            : lab.unattendedError || 'Readback flushes and downloads the local CSV. It cannot clear stored observations.'}</span>
        </div>
        <button type="button" className="mp-button mp-button-quiet" disabled={!lab.board?.found || lab.unattendedDumping || !!lab.busy} onClick={lab.retrieveUnattended}>{lab.unattendedDumping ? <Spinner /> : null}{lab.unattendedDumping ? 'Reading…' : 'Retrieve over USB'}</button>
      </section>
    </div>
  );
}

function WifiList({ rows, privacyMode }: { rows: AP[]; privacyMode: boolean }) {
  return (
    <div className="mp-signal-list" role="table" aria-label="Observed Wi-Fi networks">
      <div className="mp-signal-row mp-signal-head" role="row">
        <span role="columnheader">Network</span><span role="columnheader">Signal</span><span role="columnheader">Channel</span><span role="columnheader">Address</span>
      </div>
      {rows.map((ap, index) => (
        <div className="mp-signal-row" role="row" key={`${ap.bssid}-${ap.channel}`}>
          <span className="mp-signal-name" role="cell"><SignalBars rssi={ap.rssi} /><span><strong>{privacyMode ? `Network ${String(index + 1).padStart(2, '0')}` : displaySsid(ap.ssid)}</strong><small>Observed this session</small></span></span>
          <span role="cell"><SignalBadge rssi={ap.rssi} /></span>
          <span role="cell"><b className="mp-channel">{ap.channel}</b></span>
          <span className="mp-mono" role="cell">{privacyMode ? maskMac(ap.bssid) : ap.bssid}</span>
        </div>
      ))}
    </div>
  );
}

function BluetoothList({ rows, privacyMode }: { rows: BleDevice[]; privacyMode: boolean }) {
  return (
    <div className="mp-signal-list" role="table" aria-label="Observed Bluetooth devices">
      <div className="mp-signal-row mp-signal-head" role="row">
        <span role="columnheader">Device</span><span role="columnheader">Signal</span><span role="columnheader">Vendor</span><span role="columnheader">Address</span>
      </div>
      {rows.map((device, index) => (
        <div className="mp-signal-row" role="row" key={device.addr}>
          <span className="mp-signal-name" role="cell"><SignalBars rssi={device.rssi} /><span><strong>{privacyMode ? `Bluetooth device ${String(index + 1).padStart(2, '0')}` : displayBluetoothName(device.name)}</strong><small>Advertisement observed</small></span></span>
          <span role="cell"><SignalBadge rssi={device.rssi} /></span>
          <span role="cell">
            {privacyMode ? 'Masked' : device.vendor || 'Unknown'}
            {/* The raw advertisement is the only evidence of what was actually
                broadcast. It is also identifying, so it follows the same mask
                as every other identifier on this screen. */}
            {device.manufacturer ? (
              <small className="mp-adv-payload">
                {privacyMode
                  ? `${(device.manufacturer.length / 2)} bytes advertised`
                  : formatAdvertisement(device.manufacturer)}
              </small>
            ) : null}
          </span>
          <span className="mp-mono" role="cell">{privacyMode ? maskMac(device.addr) : device.addr}</span>
        </div>
      ))}
      {/*
        A Bluetooth advertising address is frequently a private, rotating one,
        and this scan does not record the address type the controller reports —
        so a vendor derived from the address prefix cannot be shown to be
        authoritative. Say that next to the column rather than letting the
        reader assume every row identifies a manufacturer or a stable device.
      */}
      <p className="mp-signal-note">
        Vendor is inferred from the address prefix and is only meaningful for a public
        address. Bluetooth devices commonly advertise private addresses that rotate, and
        this survey does not record which type each address is — so repeat sightings may
        be one device, and a vendor here is a hint, not an identification.
      </p>
    </div>
  );
}

function StatusItem({ icon, label, value, tone, live = false }: { icon: IconName; label: string; value: string; tone: Operation['tone']; live?: boolean }) {
  // aria-label on a bare <i> carries no role, so most screen readers drop it.
  // Hide the dot and carry the meaning in real text instead.
  return <div className={`mp-status-item mp-tone-${tone}`}><Icon name={icon} /><span><small>{label}</small><strong>{value}</strong></span>{live && <><i className="mp-live-dot" aria-hidden="true" /><span className="mp-visually-hidden">in progress</span></>}</div>;
}

function Metric({ label, value, proof, accent }: { label: string; value: number | string; proof: string; accent?: boolean }) {
  return <article className={`mp-metric ${accent ? 'is-accent' : ''}`}><span>{label}</span><strong>{value}</strong><small>{proof}</small></article>;
}

function Step({ number, title, detail, done }: { number: string; title: string; detail: string; done: boolean }) {
  return <div className={`mp-step ${done ? 'is-done' : ''}`}><span>{done ? '✓' : number}</span><div><strong>{title}</strong><small>{detail}</small></div></div>;
}

function RunCard({ icon, title, description, boundary, state, evidence, action }: { icon: IconName; title: string; description: string; boundary: string; state: 'idle' | 'running' | 'complete' | 'attention' | 'recovery'; evidence: string; action: ReactNode }) {
  return (
    <article className={`mp-run-card is-${state}`}>
      <div className="mp-run-top"><span className="mp-run-icon"><Icon name={icon} /></span><span className={`mp-run-state is-${state}`}>{state === 'running' ? 'Running' : state === 'complete' ? 'Session proof' : state === 'attention' ? 'Needs proof' : state === 'recovery' ? 'Recovery copy' : 'Ready'}</span></div>
      <h2>{title}</h2><p>{description}</p>
      <div className="mp-run-meta"><span>{boundary}</span><small>{evidence}</small></div>
      <div className="mp-run-action">{action}</div>
    </article>
  );
}

function ArtifactRow({ title, detail, proof, format, action }: { title: string; detail: string; proof: string; format: string; action: ReactNode }) {
  return (
    <article className="mp-artifact-row">
      <span className="mp-artifact-icon"><Icon name="artifacts" /></span>
      <div className="mp-artifact-copy"><h2>{title}</h2><p>{detail}</p><small>{proof}</small></div>
      <span className="mp-format">{format}</span>
      <div className="mp-artifact-action">{action}</div>
    </article>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return <div><dt>{label}</dt><dd>{value}</dd></div>;
}

function ProofRow({ label, value, ok }: { label: string; value: string; ok: boolean }) {
  return <div className="mp-proof-row"><span>{label}</span><strong className={ok ? 'is-ok' : ''}><i />{value}</strong></div>;
}

function Companion({ title, status, description, current = false }: { title: string; status: string; description: string; current?: boolean }) {
  return <article className={current ? 'is-current' : ''}><div><strong>{title}</strong><span>{status}</span></div><p>{description}</p></article>;
}

function SignalBars({ rssi }: { rssi: number }) {
  const strength = rssi >= -55 ? 4 : rssi >= -67 ? 3 : rssi >= -75 ? 2 : 1;
  return <span className="mp-signal-bars" aria-hidden="true">{[1, 2, 3, 4].map((bar) => <i key={bar} className={bar <= strength ? 'on' : ''} />)}</span>;
}

function SignalBadge({ rssi }: { rssi: number }) {
  const quality = rssi >= -55 ? 'Strong' : rssi >= -67 ? 'Good' : rssi >= -75 ? 'Fair' : 'Weak';
  return <span className="mp-signal-badge"><strong>{rssi}</strong> dBm <small>{quality}</small></span>;
}

function EmptyState({ icon, title, description, action, compact = false }: { icon: IconName; title: string; description: string; action?: ReactNode; compact?: boolean }) {
  return <div className={`mp-empty ${compact ? 'is-compact' : ''}`}><span><Icon name={icon} /></span><h2>{title}</h2><p>{description}</p>{action && <div>{action}</div>}</div>;
}

function FirstUseNotice({ onAcknowledge }: { onAcknowledge: () => void }) {
  const dialogRef = useRef<HTMLElement | null>(null);

  // aria-modal="true" only tells a screen reader's virtual cursor to ignore the
  // background; it does nothing about Tab. Without this, one Tab from the only
  // button lands in the sidebar behind the notice, so a keyboard user reaches
  // the workspace without ever passing the authorization copy. Keep focus in
  // the dialog until it is acknowledged.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Tab') return;
      const dialog = dialogRef.current;
      if (!dialog) return;
      const focusable = [...dialog.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      )].filter((element) => element.getClientRects().length > 0);
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      // Wrap at both ends, and pull focus back if it already escaped.
      if (event.shiftKey && (active === first || !dialog.contains(active))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (active === last || !dialog.contains(active))) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener('keydown', onKeyDown, true);
    return () => document.removeEventListener('keydown', onKeyDown, true);
  }, []);

  return (
    <div className="mp-notice-backdrop" role="presentation">
      <section
        ref={dialogRef}
        className="mp-first-use"
        role="dialog"
        aria-modal="true"
        aria-labelledby="mp-first-use-title"
        aria-describedby="mp-first-use-copy"
      >
        <span className="mp-notice-icon"><Icon name="shield" /></span>
        <span className="mp-eyebrow">Before you begin</span>
        <h2 id="mp-first-use-title">A scoped workspace for authorized discovery</h2>
        <p id="mp-first-use-copy">Use Medusa only on hardware and networks you own or have explicit permission to assess. SSIDs, MAC addresses, and Bluetooth identifiers can be personal data. This workspace includes receive-only logging, ordinary active discovery scans, and local readback; it does not expose disruptive assessment transmission controls.</p>
        <ul>
          <li><Icon name="observe" /><span><strong>Discovery-first</strong>Receive-only logging and bounded discovery scans come before assessment work.</span></li>
          <li><Icon name="shield" /><span><strong>Authorized use</strong>Keep every survey inside the scope you were given.</span></li>
          <li><Icon name="download" /><span><strong>Local data</strong>Evidence stays on the board or local bridge unless you choose to export it. Retention is manual; this interface never silently clears device data.</span></li>
        </ul>
        <button type="button" className="mp-button mp-button-primary" autoFocus onClick={onAcknowledge}>Enter discovery workspace</button>
      </section>
    </div>
  );
}

function Spinner() {
  return <span className="mp-spinner" aria-hidden="true" />;
}

function displaySsid(ssid: string) {
  return !ssid || ssid === '(hidden)' ? 'Hidden network' : ssid;
}

function displayBluetoothName(name: string) {
  return name || 'Unnamed Bluetooth device';
}

/** Group advertisement bytes into readable pairs, truncating politely. */
function formatAdvertisement(hex: string) {
  const bytes = hex.match(/../g) ?? [];
  const shown = bytes.slice(0, 12).join(' ');
  return bytes.length > 12 ? `${shown} … ${bytes.length} bytes` : `${shown} · ${bytes.length} bytes`;
}

function maskMac(value: string) {
  const parts = value.split(':');
  if (parts.length < 4) return '••••••••';
  return `${parts.slice(0, 2).join(':')}:••:••:••:••`;
}

function maskIdentity(value: string) {
  return value.length > 4 ? `••••${value.slice(-4)}` : '••••';
}

function unattendedSourceLabel(source: ReturnType<typeof useLab>['unattendedSource']) {
  if (source === 'primary-csv') return 'the primary device CSV';
  if (source === 'temp-csv-read-only-recovery') return 'the preserved temporary CSV';
  if (source === 'backup-csv-read-only-recovery') return 'the preserved backup CSV';
  if (source === 'database-read-only') return 'the validated device database';
  if (source === 'volatile-ram-read-only') return 'non-persisted volatile RAM';
  return 'an unverified device source';
}

function compactChipName(chip?: string) {
  if (!chip) return 'ESP board';
  return chip.replace(/^ESP32-?/i, 'ESP32 ').replace(/\s+/g, ' ').trim();
}

function Icon({ name }: { name: IconName }) {
  const paths: Record<IconName, ReactNode> = {
    home: <><path d="M3 10.5 12 3l9 7.5" /><path d="M5.5 9.5V21h13V9.5M9.5 21v-7h5v7" /></>,
    observe: <><circle cx="12" cy="12" r="2.5" /><path d="M4.9 4.9a10 10 0 0 0 0 14.2M19.1 4.9a10 10 0 0 1 0 14.2M8.4 8.4a5 5 0 0 0 0 7.2M15.6 8.4a5 5 0 0 1 0 7.2" /></>,
    runs: <><path d="M7 3h10v4H7zM5 5H3v16h18V5h-2" /><path d="m9 12 2 2 4-4M9 18h6" /></>,
    artifacts: <><path d="M6 3h9l4 4v14H6z" /><path d="M15 3v5h4M9 13h6M9 17h6" /></>,
    plugins: <><path d="M9 3v4M15 3v4" /><rect x="6" y="7" width="12" height="8" rx="2" /><path d="M12 15v6" /></>,
    devices: <><rect x="4" y="3" width="16" height="13" rx="2" /><path d="M9 21h6M12 16v5M8 7h8M8 11h5" /></>,
    bridge: <><path d="M5 8v8M19 8v8M5 12h14" /><circle cx="5" cy="5" r="2" /><circle cx="5" cy="19" r="2" /><circle cx="19" cy="5" r="2" /><circle cx="19" cy="19" r="2" /></>,
    shield: <path d="M12 3 4.5 6v5.5c0 4.7 3.2 8.2 7.5 9.5 4.3-1.3 7.5-4.8 7.5-9.5V6z" />,
    radio: <><rect x="4" y="7" width="16" height="13" rx="2" /><path d="m7 7 9-4M8 12h8M8 16h4" /><circle cx="17" cy="16" r="1" /></>,
    download: <><path d="M12 3v12m0 0 4-4m-4 4-4-4" /><path d="M5 19h14" /></>,
    eye: <><path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Z" /><circle cx="12" cy="12" r="2.5" /></>,
    mask: <><path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Z" /><path d="m4 4 16 16" /></>,
    chevron: <path d="m9 5 7 7-7 7" />,
  };
  return <svg className="mp-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}
