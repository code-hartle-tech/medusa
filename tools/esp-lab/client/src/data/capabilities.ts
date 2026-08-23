// Medusa capability catalog — the 1:1 parity spine.
//
// The UNION of capabilities across every rival in the 2026-08-21 competitive
// dossier (wiki/research/2026-08-21-competitive-dossier.md), corrected against
// rival SOURCE on 2026-08-22 (wiki/research/2026-08-22-mcu-tier-completeness-audit.md),
// each mapped to Medusa's status and to a product LANE:
//
// A NOTE ON WHAT THIS FILE CAN AND CANNOT TELL YOU.
//
// This ledger once reported 29/29 rival-facing MCU parity. That number was
// arithmetically true and materially misleading: it measured agreement with a
// denominator written from a single day's dossier, and a ledger cannot contain
// a capability its author never heard of. The 2026-08-22 audit re-derived the
// denominator from rival source code and found whole capabilities missing —
// including 5 GHz and the entire 802.15.4 protocol family.
//
// So: a parity percentage from this file is a claim about THIS LIST, never a
// claim about the field. Before quoting one, ask when the denominator was last
// checked against something other than itself.

//   - mcu   → Medusa (the ESP/MCU phone-case variant) — buildable now
//   - sbc   → NosferatOS (the full-Linux SBC variant, hardware TBD) — later lane
//   - addon → MCU-buildable but needs an add-on radio/peripheral (CC1101, PN532, GPS, display…)
//
// `status` tracks where Medusa actually is against the field:
//   have     — implementation path exists; see `proof` for the strongest evidence
//   beyond   — Medusa-specific implementation path; proof still governs the claim
//   build    — MCU-achievable parity gap we are implementing now
//   blocked  — MCU-achievable in principle, but the silicon is not on hand.
//              Distinct from `build` on purpose: `build` is work we can start
//              today, `blocked` is work that cannot begin until a board is
//              bought. Collapsing the two lets a shopping list masquerade as a
//              backlog.
//   research — frontier / research-class (mechanism, not a one-click attack)
//   nosferatos — deferred to the SBC lane (needs Linux / external radio / heavy compute)
//   excluded — deliberately outside the product/legal threat model
//
// This source-only ledger supports the public research dossier; the default
// product deliberately does not turn every rival feature into a launch control.
// Keep it honest: status says whether a product path exists; `proof` says how far
// it has actually been exercised. Compiled, board-tested, and receiver-verified
// are never interchangeable.

export type Lane = 'mcu' | 'sbc' | 'addon';
export type Status = 'have' | 'beyond' | 'build' | 'blocked' | 'research' | 'nosferatos' | 'excluded';
export type Proof = 'source-present' | 'ui-only' | 'compiled' | 'host-tested' | 'boot-tested' | 'board-tested' | 'receiver-verified';

export type Capability = {
  id: string;
  name: string;
  category: string;
  /** rivals that ship this capability (short codes → RIVALS) */
  rivals: string[];
  lane: Lane;
  status: Status;
  /** esp_hw.py command / socket action once implemented, if any */
  cmd?: string;
  /** strongest observed proof; absence means not yet classified */
  proof?: Proof;
  note: string;
};

export const RIVALS: Record<string, { name: string; kind: string }> = {
  marauder: { name: 'ESP32 Marauder', kind: 'ESP firmware' },
  bruce: { name: 'Bruce', kind: 'ESP firmware' },
  ghostesp: { name: 'Ghost ESP', kind: 'ESP firmware' },
  deauther: { name: 'Spacehuhn Deauther', kind: 'ESP8266 firmware' },
  esp32div: { name: 'ESP32-DIV', kind: 'ESP + add-on radios' },
  nugget: { name: 'WiFi Nugget', kind: 'ESP firmware' },
  flipper: { name: 'Flipper Zero', kind: 'handheld multitool' },
  projectzero: { name: 'ProjectZero (C5Lab)', kind: 'ESP32-C5 firmware' },
  pwnagotchi: { name: 'Pwnagotchi', kind: 'Pi dropbox' },
  p4wnp1: { name: 'P4wnP1 A.L.O.A.', kind: 'Pi implant' },
  kalipi: { name: 'Kali Pi / NetHunter', kind: 'SBC' },
  kismet: { name: 'Kismet', kind: 'recon software' },
  pineapple: { name: 'WiFi Pineapple', kind: 'commercial appliance' },
  alfa: { name: 'Alfa adapter', kind: 'USB radio' },
  airgeddon: { name: 'airgeddon', kind: 'Linux wrapper' },
  htb: { name: 'HTB Academy / WiFiChallengeLab', kind: 'education' },
};

export const CATEGORIES = [
  'Recon & profiling',
  'Deauth & DoS',
  'Rogue AP & capture',
  'Handshake / key capture',
  'Wardrive & inventory',
  'Bluetooth LE',
  'RF multitool',
  'USB / HID',
  'Autonomy & remote sensors',
  'Learn & simulate',
  'Platform & ecosystem',
  // Added 2026-08-22. The first covers a protocol family the ledger had no
  // entry for at all; the second covers capabilities that read the radio as an
  // instrument rather than as a packet source.
  '802.15.4 / IoT mesh',
  'Physical-layer sensing',
] as const;

export const CAPABILITIES: Capability[] = [
  // ---- Recon & profiling ----
  { id: 'ap-scan', name: 'AP scan (2.4 GHz)', category: 'Recon & profiling', rivals: ['marauder', 'bruce', 'ghostesp', 'deauther', 'nugget', 'kismet'], lane: 'mcu', status: 'have', cmd: 'scan-aps', proof: 'compiled', note: 'C3/S3 scan firmware compiles; live board results still need a recorded proof run.' },
  { id: 'sta-scan', name: 'Station / client observation', category: 'Recon & profiling', rivals: ['marauder', 'bruce', 'kismet', 'pineapple'], lane: 'mcu', status: 'have', cmd: 'scan-clients', proof: 'source-present', note: 'A target-filtered observation path exists; compile and live-radio proof are not recorded.' },
  { id: 'rsn-profile', name: 'RSN / PMF / WPS / cipher profile', category: 'Recon & profiling', rivals: [], lane: 'mcu', status: 'beyond', cmd: 'recon', proof: 'source-present', note: 'Medusa-specific decision support maps observed configuration to defensive checks; comparative uniqueness is not claimed.' },
  { id: 'packet-monitor', name: 'Packet monitor (frame counts by type)', category: 'Recon & profiling', rivals: ['marauder', 'kismet'], lane: 'mcu', status: 'have', cmd: 'monitor', proof: 'compiled', note: 'Passive per-channel counts: mgmt/data/ctrl + beacons/deauths; live RF result not yet recorded.' },
  { id: 'signal-meter', name: 'Signal / RSSI meter (find-a-device)', category: 'Recon & profiling', rivals: ['marauder'], lane: 'mcu', status: 'have', cmd: 'monitor --bssid', proof: 'compiled', note: 'Tracked-BSSID RSSI path compiles; live ranging result not yet recorded.' },
  { id: 'pcap-sniff', name: 'Raw sniffer → PCAP export', category: 'Recon & profiling', rivals: ['marauder', 'kismet', 'alfa'], lane: 'mcu', status: 'have', cmd: 'sniff', proof: 'host-tested', note: 'C3 compile plus synthetic PCAP/tcpdump validation; live RF capture still pending.' },

  // ---- Deauth & DoS ----
  { id: 'deauth-broadcast', name: 'Deauthentication resilience harness (broadcast)', category: 'Deauth & DoS', rivals: ['marauder', 'bruce', 'ghostesp', 'deauther', 'nugget'], lane: 'mcu', status: 'have', proof: 'board-tested', note: 'The controls the threat model required now exist and are enforced on the device. Routed through medusa_tx_send(), so the configured session, scope, rate ceiling, duration and latched stop govern every frame, and refusals are audited alongside successes. Compiles gated for C3. Receiver-side effect is still unverified.' },
  { id: 'deauth-unicast', name: 'Targeted AP/client resilience harness', category: 'Deauth & DoS', rivals: ['marauder'], lane: 'mcu', status: 'have', proof: 'compiled', note: 'Routed through medusa_tx_send(), so the configured session, scope, rate ceiling, duration and latched stop govern every frame, and refusals are audited alongside successes. Compiles gated for C3. Receiver-side effect is still unverified.' },
  { id: 'csa', name: 'CSA resilience harness', category: 'Deauth & DoS', rivals: ['projectzero'], lane: 'mcu', status: 'have', proof: 'compiled', note: 'Routed through medusa_tx_send(), so the configured session, scope, rate ceiling, duration and latched stop govern every frame, and refusals are audited alongside successes. Compiles gated for C3. Receiver-side effect is still unverified.' },
  { id: 'authflood', name: 'Authentication-capacity resilience harness', category: 'Deauth & DoS', rivals: [], lane: 'mcu', status: 'beyond', proof: 'compiled', note: 'Routed through medusa_tx_send(), so the configured session, scope, rate ceiling, duration and latched stop govern every frame, and refusals are audited alongside successes. Compiles gated for C3. Receiver-side effect is still unverified.' },
  { id: 'beacon-spam', name: 'Beacon handling stress-test harness', category: 'Deauth & DoS', rivals: ['marauder', 'bruce', 'ghostesp', 'flipper'], lane: 'mcu', status: 'have', proof: 'compiled', note: 'Broadcast by construction, so its scope IS broadcast and the operator broadcast confirmation governs it. Routed through medusa_tx_send(), so the configured session, scope, rate ceiling, duration and latched stop govern every frame, and refusals are audited alongside successes. Compiles gated for C3. Receiver-side effect is still unverified.' },
  { id: 'probe-flood', name: 'Probe handling stress-test harness', category: 'Deauth & DoS', rivals: ['marauder', 'bruce'], lane: 'mcu', status: 'have', proof: 'compiled', note: 'Broadcast by construction, so its scope IS broadcast and the operator broadcast confirmation governs it. Routed through medusa_tx_send(), so the configured session, scope, rate ceiling, duration and latched stop govern every frame, and refusals are audited alongside successes. Compiles gated for C3. Receiver-side effect is still unverified.' },

  // ---- Rogue AP & capture ----
  { id: 'evil-portal', name: 'Captive-portal credential collection', category: 'Rogue AP & capture', rivals: ['marauder', 'bruce', 'ghostesp', 'pineapple'], lane: 'mcu', status: 'excluded', proof: 'compiled', note: 'Explicitly excluded from the HARTLE.TECH product and support scope.' },
  { id: 'evil-twin', name: 'Rogue-AP impersonation', category: 'Rogue AP & capture', rivals: ['pineapple', 'airgeddon'], lane: 'mcu', status: 'excluded', proof: 'compiled', note: 'Explicitly excluded from the HARTLE.TECH product and support scope.' },
  { id: 'karma', name: 'Probe-response impersonation', category: 'Rogue AP & capture', rivals: ['pineapple'], lane: 'mcu', status: 'excluded', note: 'Excluded with the rogue-AP impersonation class; not a Medusa roadmap item.' },

  // ---- Handshake / key capture ----
  { id: 'pmkid', name: 'PMKID capture', category: 'Handshake / key capture', rivals: ['marauder', 'pwnagotchi'], lane: 'mcu', status: 'have', proof: 'host-tested', note: 'The reassociation nudge now passes the same gate as any other transmission, so a capture run cannot transmit outside the configured scope, rate or session. Compiles gated for C3; live handshake proof is still outstanding.' },
  { id: 'eapol', name: 'WPA 4-way handshake capture', category: 'Handshake / key capture', rivals: ['marauder', 'pwnagotchi', 'airgeddon'], lane: 'mcu', status: 'have', proof: 'host-tested', note: 'Shares the gated reassociation nudge with PMKID capture, so the same session, scope and rate policy governs it. Compiles gated for C3; live handshake proof is still outstanding.' },
  { id: 'offline-cracking', name: 'Automated offline password recovery', category: 'Handshake / key capture', rivals: ['kalipi', 'airgeddon'], lane: 'sbc', status: 'excluded', note: 'Explicitly outside Medusa and NosferatOS product scope.' },
  { id: 'wps-exploitation', name: 'Automated WPS PIN exploitation', category: 'Handshake / key capture', rivals: ['airgeddon', 'bruce'], lane: 'sbc', status: 'excluded', note: 'Explicitly outside Medusa and NosferatOS product scope.' },
  { id: 'sae-downgrade', name: 'WPA3-SAE downgrade or exploitation', category: 'Handshake / key capture', rivals: ['projectzero'], lane: 'mcu', status: 'excluded', note: 'Tracked only as competitor context; not a Medusa product roadmap item.' },

  // ---- Wardrive & inventory ----
  { id: 'wardrive', name: 'WiGLE-format scan export', category: 'Wardrive & inventory', rivals: ['marauder', 'bruce', 'kismet'], lane: 'mcu', status: 'have', cmd: 'esp_report export --format wigle', proof: 'host-tested', note: 'WigleWifi-1.4 CSV from scans; coordinates remain 0,0 until a GPS source exists, so geographic wardriving parity is not claimed.' },
  { id: 'export-kismet', name: 'airodump-compatible CSV export', category: 'Wardrive & inventory', rivals: ['kismet', 'airgeddon'], lane: 'mcu', status: 'have', cmd: 'esp_report export --format airodump', proof: 'host-tested', note: 'The local report path produces an interoperable inventory CSV.' },
  { id: 'inventory', name: 'Asset inventory (persist, dedupe, first/last-seen)', category: 'Wardrive & inventory', rivals: ['kismet'], lane: 'mcu', status: 'have', cmd: 'esp_report inventory', proof: 'host-tested', note: 'The local bridge stores a SQLite inventory and deduplicates by BSSID.' },
  { id: 'oui', name: 'Curated OUI / vendor lookup', category: 'Wardrive & inventory', rivals: ['kismet'], lane: 'mcu', status: 'have', proof: 'host-tested', note: 'An explicitly incomplete prefix map labels known vendors; no device-class fingerprinting is claimed.' },
  { id: 'posture-report', name: 'Security-posture HTML report', category: 'Wardrive & inventory', rivals: [], lane: 'mcu', status: 'beyond', cmd: 'esp_report export --format posture', proof: 'host-tested', note: 'Medusa-specific local HTML output summarizes the observed RSN, PMF, and WPS posture without claiming competitor absence.' },
  { id: 'pseudonymized-export', name: 'Pseudonymized export of a shareable artifact', category: 'Wardrive & inventory', rivals: [], lane: 'mcu', status: 'beyond', cmd: 'esp_report export --pseudonymize', proof: 'host-tested', note: 'Every export format can replace SSIDs and the device octets of each BSSID with per-export salted pseudonyms, preserving vendor prefixes and all security findings. The local inventory keeps real values. Pseudonymisation, not anonymisation: the original capture still re-derives the mapping. No comparative claim about rivals is made.' },

  // ---- Bluetooth LE ----
  { id: 'ble-scan', name: 'BLE scan / enumerate', category: 'Bluetooth LE', rivals: ['marauder', 'bruce', 'flipper'], lane: 'mcu', status: 'have', cmd: 'ble-scan', proof: 'compiled', note: 'The fail-aware address/RSSI/name/vendor path compiles for C3/S3; live enumeration is not yet recorded.' },
  { id: 'ble-sniff', name: 'BLE advertisement detail capture', category: 'Bluetooth LE', rivals: ['flipper'], lane: 'mcu', status: 'have', cmd: 'ble-scan', proof: 'host-tested', note: 'Raw manufacturer-specific payload and the little-endian company identifier are preserved end to end and shown under the vendor guess in Observe, following the same identifier mask as everything else on that screen. Deriving a vendor and discarding the payload lost the only evidence of what was actually advertised.' },
  { id: 'ble-spam', name: 'BLE advertisement load harness', category: 'Bluetooth LE', rivals: ['bruce', 'flipper'], lane: 'mcu', status: 'have', proof: 'compiled', note: 'Gated where an advertisement is published rather than around a frame send, since BLE advertises rather than addressing. Scope is the broadcast address because that is what an advertisement reaches. Compiles gated for C3; receiver behaviour is unverified.' },

  // ---- RF multitool ----
  { id: 'subghz', name: 'Sub-GHz (CC1101) TX/RX/replay', category: 'RF multitool', rivals: ['bruce', 'flipper', 'esp32div'], lane: 'addon', status: 'build', note: 'Needs a CC1101 module on the case bus.' },
  { id: 'ir', name: 'IR TX/RX', category: 'RF multitool', rivals: ['bruce', 'flipper', 'esp32div'], lane: 'addon', status: 'build', note: 'Needs an IR LED/receiver.' },
  { id: 'nfc', name: 'RFID / NFC (PN532)', category: 'RF multitool', rivals: ['flipper', 'bruce'], lane: 'addon', status: 'build', note: 'Needs a PN532 module.' },
  { id: 'nrf24', name: '2.4 GHz generic (nRF24) scan', category: 'RF multitool', rivals: ['esp32div'], lane: 'addon', status: 'build', note: 'Needs an nRF24 module.' },
  { id: 'jamming', name: 'Broadband jamming', category: 'RF multitool', rivals: ['esp32div'], lane: 'mcu', status: 'excluded', note: 'Deliberately excluded from Medusa: unlawful in normal operation; remain protocol-level.' },

  // ---- USB / HID ----
  { id: 'badusb', name: 'USB HID test image', category: 'USB / HID', rivals: ['flipper', 'p4wnp1', 'bruce'], lane: 'mcu', status: 'have', proof: 'compiled', note: 'Reachable from Runs on an S3, with the keystrokes shown and editable before the image is built and an explicit authorisation confirmation. Building is not installing. Host behaviour is unverified: what a computer does with the keystrokes has not been observed here, and the C3/C6 cannot present a keyboard at all.' },
  { id: 'usb-eth', name: 'USB Ethernet assessment interface', category: 'USB / HID', rivals: ['p4wnp1'], lane: 'sbc', status: 'nosferatos', note: 'Needs an authenticated Linux gadget stack → NosferatOS.' },

  // ---- Autonomy & remote sensors ----
  { id: 'unattended', name: 'Unattended headless beacon inventory', category: 'Autonomy & remote sensors', rivals: ['pwnagotchi', 'kismet'], lane: 'mcu', status: 'have', cmd: 'build --attack unattended', proof: 'board-tested', note: 'The settled v3 C3 binary was flashed and run: it fail-closed on blank storage, initialised on explicit confirmation, captured six APs passively, and was retrieved over USB from the committed store. Receiver-side effects are not applicable to a receive-only capability.' },
  { id: 'storage', name: 'Onboard inventory storage (LittleFS)', category: 'Autonomy & remote sensors', rivals: ['marauder', 'pwnagotchi', 'pineapple'], lane: 'mcu', status: 'have', cmd: 'unattended-dump', proof: 'board-tested', note: 'Persistence proven across a reset on hardware: both generation-tagged candidates re-mounted valid and a second retrieval returned an identical BSSID set with zero rows lost.' },
  { id: 'bulk-remote-export', name: 'Unattended bulk remote transfer', category: 'Autonomy & remote sensors', rivals: ['pineapple', 'p4wnp1'], lane: 'mcu', status: 'excluded', note: 'Deliberately excluded: unattended captures remain local, with physical USB retrieval; authenticated user-initiated sync is a separate future design.' },
  { id: 'status-display', name: 'At-a-glance status display (OLED/e-ink)', category: 'Autonomy & remote sensors', rivals: ['pwnagotchi', 'flipper'], lane: 'addon', status: 'build', note: 'Compact local status surface for unattended use.' },
  { id: 'sensor-management', name: 'Authenticated operator-managed sensors', category: 'Autonomy & remote sensors', rivals: ['pineapple'], lane: 'sbc', status: 'nosferatos', note: 'Proposed self-hosted health, configuration, and user-initiated sync for an operator’s own NosferatOS sensors; no hidden tasking or bulk collection.' },
  { id: 'kismet-source', name: 'Remote-capture datasource for Kismet', category: 'Autonomy & remote sensors', rivals: ['kismet'], lane: 'mcu', status: 'have', proof: 'host-tested', note: 'Authenticated WebSocket transport only, with the API key as a header and a refusal to send it in plaintext to a remote host. The legacy unauthenticated TCP mode is deliberately not implemented and must not be added as a fallback. Verified against a conforming mock: announce-then-accept, rejection surfaced as a failure, and packets refused before acceptance rather than queued. NOT verified against a real Kismet server, which does not install on the development workstation.' },

  // ---- Learn & simulate ----
  { id: 'study-split', name: 'Study and product interface separation', category: 'Learn & simulate', rivals: ['htb'], lane: 'mcu', status: 'have', proof: 'ui-only', note: 'The product surface is separate from the removed historical lab UI, and the Plugins destination is where a reader inspects what a capability does before running it — the study role, inside the product rather than beside it.' },
  { id: 'guided-path', name: 'Guided learning journey', category: 'Learn & simulate', rivals: ['nugget', 'htb'], lane: 'mcu', status: 'have', proof: 'ui-only', note: 'Six ordered steps from identifying the board to handing over a pseudonymised report. Each names what to look at and why it matters rather than what to click, and two link straight to a runnable plugin. Served from the devkit beside the examples it references, so a lesson cannot drift from its example.' },
  { id: 'gate-pedagogy', name: 'Raw-frame gate teardown (Xtensa and RISC-V)', category: 'Learn & simulate', rivals: [], lane: 'mcu', status: 'beyond', proof: 'host-tested', note: 'Medusa-specific learning material and host-side build verification explain the platform gate; live transmission is not implied.' },
  { id: 'simulate', name: 'Defense simulation (no airspace)', category: 'Learn & simulate', rivals: ['htb'], lane: 'mcu', status: 'have', proof: 'host-tested', note: 'Plugin dry-run executes a program against a simulated device and reports every step and the frames it would request, with no radio involved. Replaying a captured environment is not yet implemented.' },

  { id: 'tx-policy', name: 'Firmware-enforced transmission policy', category: 'Platform & ecosystem', rivals: [], lane: 'mcu', status: 'beyond', proof: 'board-tested', note: 'Session, allow-list, rate, duration, broadcast confirmation, latched stop and an audit of refusals as well as successes, enforced on the device rather than in the app. Verified on hardware across every refusal path. Operator-configurable rather than a fixed cap; no comparative claim about rivals is made.' },
  { id: 'runtime-config', name: 'Companion-set operating parameters (NVS)', category: 'Platform & ecosystem', rivals: [], lane: 'mcu', status: 'beyond', proof: 'host-tested', note: 'Rates, bursts, session length and scope live in a versioned NVS blob the companion writes, so changing how a capability behaves needs no rebuild and survives a firmware update.' },

  // ---- Platform & ecosystem ----
  { id: 'multi-board', name: 'C3/S3 multi-architecture build support', category: 'Platform & ecosystem', rivals: ['marauder', 'bruce'], lane: 'mcu', status: 'have', proof: 'compiled', note: 'C3 and S3 compile; C3 additionally has board proof. C5/C6 are configuration-only and dependency-blocked locally; ESP8266/RP2040/RP2350 remain unimplemented.' },
  { id: 'scripting', name: 'On-device programs (plugin step machine)', category: 'Platform & ecosystem', rivals: ['bruce', 'flipper', 'p4wnp1'], lane: 'mcu', status: 'have', proof: 'host-tested', note: 'A bounded step machine over firmware primitives rather than loadable native code, which has no safe form on a chip with no MMU. Capability declarations are enforced at parse time and every transmit passes the same guard as a built-in.' },
  { id: 'plugin-catalog', name: 'Plugin authoring, validation and catalogue', category: 'Platform & ecosystem', rivals: ['flipper'], lane: 'mcu', status: 'have', proof: 'host-tested', note: 'devkit scaffolds, validates and dry-runs a plugin with no hardware, using the firmware parser itself so host and device cannot disagree. The companion lists plugins with their declared capabilities and dry-runs them. A shared catalogue service is not yet built.' },
  { id: 'signed-fw', name: 'Signed firmware builds + rollback', category: 'Platform & ecosystem', rivals: [], lane: 'mcu', status: 'build', note: 'Proposed trust and recovery work; no implementation or comparative security claim yet. Ghost ESP v2.1 ships OTA with rollback protection, so this is parity work, not a differentiator.' },

  // ======================================================================
  // Added 2026-08-22 by the completeness audit.
  //
  // Everything below was MISSING from the denominator when this ledger last
  // claimed full rival parity. Each entry is sourced from rival code or from
  // Espressif documentation, not from feature-comparison articles — several of
  // those assert capabilities the rival source does not contain.
  //
  // Evidence and reproduction steps:
  //   wiki/research/2026-08-22-mcu-tier-completeness-audit.md
  // ======================================================================

  // ---- Band coverage: the largest single gap ----
  { id: 'band-5ghz', name: '5 GHz survey and assessment (ESP32-C5)', category: 'Recon & profiling', rivals: ['marauder', 'ghostesp', 'projectzero'], lane: 'mcu', status: 'blocked', note: 'Every Medusa capability is 2.4 GHz only, so an assessment is currently blind to most modern infrastructure. Marauder ships three C5 flashers, enumerates esp32c5 in installer/targets.json, and has a CI stanza commented "ESP32-C5 (5GHz support)"; Ghost ESP ships an ACE_C5 board target. Blocked, not building: no C5 on hand and the Arduino core is not installed locally.' },
  { id: 'band-6ghz', name: '6 GHz / Wi-Fi 6E survey', category: 'Recon & profiling', rivals: [], lane: 'sbc', status: 'nosferatos', note: 'No ESP part covers 6 GHz. Recorded so the band is a deliberate NosferatOS question rather than an unnoticed hole; needs an external radio.' },

  // ---- 802.15.4: an entire protocol family the ledger had no entry for ----
  { id: 'dot15d4-sniff', name: '802.15.4 capture (Zigbee / Thread)', category: '802.15.4 / IoT mesh', rivals: ['ghostesp', 'flipper'], lane: 'mcu', status: 'blocked', note: 'The C6 and C5 carry an 802.15.4 radio. Ghost ESP already exports 802.15.4 alongside PCAP/hc22000/WiGLE and includes it in wardrive sweep CSV. A survey that reports Wi-Fi and BLE while ignoring the Zigbee mesh in the same building misrepresents the estate. Blocked on C6/C5 hardware.' },
  { id: 'dot15d4-inventory', name: 'Zigbee / Thread network inventory', category: '802.15.4 / IoT mesh', rivals: ['ghostesp'], lane: 'mcu', status: 'blocked', note: 'PAN ID, channel and device enumeration to place mesh devices on the same map as Wi-Fi and BLE. Depends on dot15d4-sniff.' },

  // ---- Rival Wi-Fi capabilities absent from the old denominator ----
  { id: 'sae-audit', name: 'WPA3 / SAE configuration audit', category: 'Recon & profiling', rivals: ['ghostesp'], lane: 'mcu', status: 'build', note: 'Ghost ESP ships both an SAE flood and an SAE compliance checker. The checker half is the part that fits this product: transition-mode downgrade exposure and PMF posture are observable passively and are a defensible finding.' },
  { id: 'client-isolation', name: 'Client-isolation verification', category: 'Recon & profiling', rivals: ['ghostesp'], lane: 'mcu', status: 'build', note: 'Whether guest isolation actually isolates is a question an owner genuinely needs answered, and the honest test is to attempt reachability from an authorised client. Framed as verification rather than as the GTK abuse the rival calls it.' },
  { id: 'lan-recon', name: 'Post-association LAN recon', category: 'Recon & profiling', rivals: ['ghostesp'], lane: 'mcu', status: 'build', note: 'ARP, mDNS, NetBIOS, SNMP and port enumeration once associated to a network the operator owns. Extends the picture from the radio to the estate.' },
  { id: 'rf-congestion', name: 'Channel congestion analysis', category: 'Recon & profiling', rivals: ['ghostesp'], lane: 'mcu', status: 'have', cmd: 'congestion', proof: 'board-tested', note: 'PROVEN ON AN ESP32-C3 across all 13 channels. Ranks by OVERLAP-AWARE interference, not own occupancy: 2.4 GHz channels are 22 MHz wide on 5 MHz spacing, so ranking a channel by its own traffic recommends one that is quiet only because its neighbours are shouting — the mistake router auto-select makes. Measures estimated air time and retry share rather than frame count, since a thousand small beacons cost less air than a hundred long data frames. First live run found the survey network sitting on the most congested channel in the band, with a candidate roughly ten times quieter.' },
  { id: 'extcap-stream', name: 'Wireshark extcap USB capture', category: 'Wardrive & inventory', rivals: ['ghostesp', 'marauder'], lane: 'mcu', status: 'build', note: 'We implemented Kismet remote capture instead. Adjacent, not equivalent: extcap is markedly lower friction because the analyst already has Wireshark and needs no server.' },
  { id: 'rogue-infra-detect', name: 'Rogue infrastructure detection', category: 'Recon & profiling', rivals: ['ghostesp'], lane: 'mcu', status: 'build', note: 'Detecting Pineapple-class behaviour — over-eager probe responses, implausible SSID breadth. Defensive, passive, and squarely in the stated posture. See csi-twin-detect for a physical-layer approach that a careful adversary cannot defeat by tidying its behaviour.' },
  { id: 'droneid-detect', name: 'OpenDroneID / Remote ID observation', category: 'Wardrive & inventory', rivals: ['ghostesp'], lane: 'mcu', status: 'build', note: 'Remote ID is broadcast to be received, so observing it is receive-only and uncontroversial. Ghost ESP also offers spoofing; that half is excluded here — see droneid-spoof.' },
  { id: 'ble-skimmer', name: 'BLE skimmer detection', category: 'Bluetooth LE', rivals: ['ghostesp'], lane: 'mcu', status: 'build', note: 'Known-bad module signatures in payment terminals and fuel pumps. Passive, defensive, and the clearest consumer-protective use in the whole catalogue.' },
  { id: 'surveillance-detect', name: 'Surveillance / ALPR device detection', category: 'Wardrive & inventory', rivals: ['ghostesp'], lane: 'mcu', status: 'build', note: 'Fingerprinting fixed surveillance hardware from its radio signature. Passive; counter-surveillance rather than surveillance.' },

  // ---- Rival capabilities deliberately NOT taken ----
  // Recorded so the denominator is honest. Omitting them entirely would have
  // flattered our coverage, and there is a real difference between "we do not
  // have this" and "we decided not to have this".
  { id: 'dhcp-starvation', name: 'DHCP pool exhaustion', category: 'Deauth & DoS', rivals: ['ghostesp'], lane: 'mcu', status: 'excluded', note: 'Exhausts a lease pool and denies service to every client including ones outside any assessment scope, with no clean stop and slow recovery. Excluded: the blast radius cannot be bounded by the allow-list the way a frame-level test can.' },
  { id: 'eapol-logoff', name: 'EAPOL logoff injection', category: 'Deauth & DoS', rivals: ['ghostesp'], lane: 'mcu', status: 'excluded', note: 'Excluded as redundant rather than as uniquely dangerous: the resilience question it answers is already answered by the gated disassociation path, and a second unbounded route to the same finding is more attack surface for no extra evidence.' },
  { id: 'droneid-spoof', name: 'Remote ID spoofing', category: 'Wardrive & inventory', rivals: ['ghostesp'], lane: 'mcu', status: 'excluded', note: 'Fabricating aircraft identity broadcasts targets aviation safety infrastructure and is criminal in most jurisdictions regardless of network ownership. No consent an operator can give makes this in scope. Detection is kept; spoofing is not.' },
  { id: 'dns-sinkhole', name: 'DNS sinkhole / blocklist filtering', category: 'Platform & ecosystem', rivals: ['ghostesp'], lane: 'mcu', status: 'excluded', note: 'A network service rather than an assessment capability. Excluded on scope, not on risk — this is a job for the network, not for a survey instrument.' },

  // ---- Physical-layer sensing: the novelty lane ----
  //
  // Both rivals compile CSI support in and then switch it off. That is not an
  // oversight to copy, it is an opening. Marauder's WiFiScan.h sets
  // `.csi_enable = false`; every Ghost ESP board config carries the pair
  // `CONFIG_SOC_WIFI_CSI_SUPPORT=y` with `# CONFIG_ESP_WIFI_CSI_ENABLED is not set`.
  //
  // These are the only entries in this file with NO rival, and they are marked
  // `research` because feasibility here is documentation-level. Nothing below
  // has been written or run on hardware. Do not let the ambition of the note
  // outrun the proof field.
  { id: 'csi-sensing', name: 'CSI presence and motion sensing', category: 'Physical-layer sensing', rivals: [], lane: 'mcu', status: 'have', cmd: 'csi', proof: 'board-tested', note: 'PROVEN ON AN ESP32-C3: live per-subcarrier profiles from eight transmitters, RSSI -37 to -77, per-peer baselines and motion figures. Strictly passive — no transmit path at all, not even a gated one. The installed core already sets CONFIG_ESP32_WIFI_CSI_ENABLED=y, so this needed no new silicon and no core patch, which is the whole reason a capability both rivals disable was one sketch away. Sensing quality depends on how much the target TALKS: against an idle AP the honest output is mostly "unknown".' },
  { id: 'csi-twin-detect', name: 'Multi-position detection under one BSSID', category: 'Physical-layer sensing', rivals: [], lane: 'mcu', status: 'have', cmd: 'csi', proof: 'board-tested', note: 'VALIDATED ON BOTH SIDES, AND DELIBERATELY NOT CALLED A ROGUE-AP DETECTOR. Six twins were built from real hardware CSI by relabelling pairs of genuinely different APs to one BSSID — real physics, only the identity changed. On rich layouts it caught 3/3, including a pair separated by 0.1 dB of RSSI where signal strength carries no information and every rival approach is blind by construction. Legacy 64-subcarrier layouts are refused outright: measured scores there were 2.30-3.58 for twins and 2.36-3.64 for honest APs, i.e. no signal at all. One of seven honest APs flags, and the null control shows it is genuinely multi-source. THAT IS THE REAL LIMIT: what this detects is more than one spatial source under one BSSID, and mesh backhaul, repeaters and antenna-switching APs all do that legitimately. It reports POSITION_ANOMALY — an observation needing context — never intent.' },
  { id: 'csi-tamper', name: 'RF-environment tamper evidence', category: 'Physical-layer sensing', rivals: [], lane: 'mcu', status: 'research', note: 'A stable CSI fingerprint that shifts means the physical environment changed — a device moved, an antenna was repositioned, something was installed. Depends on csi-sensing; no separate implementation yet.' },
  { id: 'csi-raw-export', name: 'Raw per-frame CSI export (I/Q, phase preserved)', category: 'Physical-layer sensing', rivals: [], lane: 'mcu', status: 'have', cmd: 'csi-raw', proof: 'board-tested', note: 'PROVEN ON AN ESP32-C3: 1105 records in 40s, one per frame, carrying the driver\'s unmodified signed int8 I/Q with a hardware timestamp taken inside the rx callback. Verified raw rather than merely well-formatted — signed values present, phase continuous across all 36 test bins, one subcarrier sweeping -3.05 to +3.14 rad over the run. The summarised path discards all of this: it takes magnitude (killing phase), log-quantises to one byte, and averages to 1 Hz. Every spectral capability needs what this preserves. Ships a sampling report that states the highest frequency a run can honestly support, and refuses runs that would alias; dropped frames are counted and reported, because a hole makes the sample interval wrong for everything after it.' },
  { id: 'csi-vitals', name: 'Respiration rate from CSI', category: 'Physical-layer sensing', rivals: [], lane: 'mcu', status: 'research', cmd: 'vitals', proof: 'host-tested', note: 'ESTIMATOR BUILT AND VALIDATED AGAINST SYNTHETIC GROUND TRUTH; NEVER RUN AGAINST A PERSON. Recovers planted rates of 12/15/18/24 bpm within 1 bpm, survives 0.5 interval jitter, and refuses pure noise and under-sampled captures. Uses Lomb-Scargle on the real timestamps rather than interpolating onto a uniform grid and running an FFT, because interpolation invents samples and biases the spectrum; Lomb-Scargle also has a known null distribution, so it reports a false-alarm probability instead of an adjective. Requires consensus across subcarriers rather than one combined spectrum, since subcarriers are correlated and a combined significance would be overstated. End-to-end on hardware: 1768 records at 19.65 Hz, 0.67 bpm resolution, correctly declining to report a rate with nobody in position. Validation protocol needs a human breathing to a metronome — see wiki/research/2026-08-23-respiration-validation-protocol.md. HEART RATE IS NOT CLAIMED: 0.2-0.5 mm displacement is 20-30x weaker and sits under the breathing harmonics.' },
  { id: 'ftm-ranging', name: 'FTM (802.11mc) true ranging', category: 'Recon & profiling', rivals: [], lane: 'mcu', status: 'research', cmd: 'ftm', proof: 'board-tested', note: 'INITIATOR AND SURVEY PROVEN ON HARDWARE; NO DISTANCE MEASURED, BECAUSE NOTHING IN RANGE ANSWERS. Survey mode attempts a ranging exchange with every AP found in one flash: 6 of 6 APs across 3 unrelated vendors returned CONF_REJECTED at every burst size (16/24/32/64 frames, periods 2-8). Identical refusals across unrelated vendors is the signature of no 802.11mc responder present, not of a parameter this sweep got wrong. dist_est is withheld unless the exchange succeeded, and CONF_REJECTED is reported as ambiguous rather than as proof either way. A single radio is half-duplex and cannot be both ends of a round-trip timing exchange, so self-ranging cannot close this — it needs a second board or capable infrastructure.' },
  { id: 'antenna-diversity', name: 'Antenna selection and diversity control', category: 'Physical-layer sensing', rivals: [], lane: 'mcu', status: 'research', note: 'Not interesting alone, but both CSI and FTM are sensitive to antenna configuration, so this is a prerequisite for doing either well rather than merely doing them.' },
];

export const laneLabel: Record<Lane, string> = { mcu: 'Medusa (MCU)', sbc: 'NosferatOS (SBC)', addon: 'Medusa + add-on' };
export const statusLabel: Record<Status, string> = {
  have: 'path present', beyond: 'Medusa-specific', build: 'not product-ready', blocked: 'needs hardware',
  research: 'research', nosferatos: 'NosferatOS lane', excluded: 'excluded',
};
export const proofLabel: Record<Proof, string> = {
  'source-present': 'source only', 'ui-only': 'UI-only', compiled: 'compiled', 'host-tested': 'host-tested',
  'boot-tested': 'boot-tested', 'board-tested': 'board-tested', 'receiver-verified': 'receiver-verified',
};

// Lane-aware rollup: rival parity, hardware add-ons, SBC work, deliberate
// exclusions, and Medusa-original moat must never share one denominator.
export function parityRollup() {
  const isDone = (c: Capability) => c.status === 'have' || c.status === 'beyond';
  const rivalMcu = CAPABILITIES.filter((c) => c.lane === 'mcu' && c.rivals.length > 0 && c.status !== 'excluded');
  const addons = CAPABILITIES.filter((c) => c.lane === 'addon');
  const nosferatos = CAPABILITIES.filter((c) => c.lane === 'sbc');
  const excluded = CAPABILITIES.filter((c) => c.status === 'excluded');
  const moat = CAPABILITIES.filter((c) => c.lane === 'mcu' && c.rivals.length === 0);
  const done = rivalMcu.filter(isDone).length;
  return {
    total: rivalMcu.length,
    done,
    building: rivalMcu.filter((c) => c.status === 'build').length,
    // Reported separately from `building` so a shopping list cannot read as a
    // backlog: these need silicon we do not have, and no amount of work here
    // moves them.
    blocked: rivalMcu.filter((c) => c.status === 'blocked').length,
    rivalMcu: { total: rivalMcu.length, done },
    addons: { total: addons.length, done: addons.filter(isDone).length },
    nosferatos: { total: nosferatos.length, done: nosferatos.filter(isDone).length },
    excluded: excluded.length,
    moat: { total: moat.length, done: moat.filter(isDone).length },
  };
}
