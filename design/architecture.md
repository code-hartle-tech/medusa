# Medusa — Architecture (draft)

Status: **draft**, M2 milestone. To be refined by architecture
agents + reviewed by operator.

## High-level

```
┌─────────────────────────────────────────────────────────┐
│                 Companion App (phone)                    │
│   - NearTrace today, dedicated Medusa app possibly later │
│   - UI: status, scan results, captured frames, settings │
│   - BLE central, optional USB-CDC fallback              │
└──────────────────────────┬──────────────────────────────┘
                           │
                           │  BLE 5 (notifications + writes)
                           │  + USB-CDC (when plugged into phone)
                           │
┌──────────────────────────▼──────────────────────────────┐
│              ESP32-S3 (WROOM-1, in case)                │
│  ┌────────────────────────────────────────────────────┐ │
│  │              Companion Link (NUS-like GATT)        │ │
│  │  - JSON over BLE characteristic notifications      │ │
│  │  - Pairing + bonding required                      │ │
│  └─────────────┬────────────┬────────────┬────────────┘ │
│                │            │            │              │
│        ┌───────▼──┐   ┌─────▼─────┐  ┌──▼──────────┐    │
│        │ WiFi     │   │ BLE Scan  │  │ ESP-NOW     │    │
│        │ Sniffer  │   │ + Beacon  │  │ (peer-link  │    │
│        │ (monitor │   │   parse   │  │  to other   │    │
│        │  mode)   │   │           │  │  Medusa     │    │
│        └──────────┘   └───────────┘  │  units)     │    │
│                                       └─────────────┘    │
│  ┌────────────────────────────────────────────────────┐ │
│  │  Local store (NVS for prefs; optional microSD     │ │
│  │  for pcap-style capture log)                       │ │
│  └────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────┘
```

## Software stack

- **Framework**: ESP-IDF (preferred) or Arduino-ESP32 (faster proto).
  Decision: **ESP-IDF** — Marauder/Bruce-class projects all use IDF for
  monitor-mode access. Arduino layer adds latency we don't want for
  packet capture.
- **Language**: C primarily; C++ for higher-level component facades.
  Optional: Rust components later via `esp-rs` if a particular module
  benefits.
- **RTOS**: FreeRTOS (built into IDF).
- **Build**: CMake via PlatformIO (multi-target friendly).
- **Test**: Unity (IDF's bundled) for unit tests; HiL via QEMU-esp32
  where possible.

## Component layout (`firmware/components/`)

```
firmware/components/
  companion_link/      # BLE GATT service + USB-CDC bridge
  wifi_sniffer/        # promiscuous-mode capture + filtering
  ble_scanner/         # central-mode scan, beacon parse
  espnow_link/         # mesh between Medusa units (later)
  storage/             # NVS prefs + optional SD pcap log
  led_status/          # RGB LED state machine
  button_input/        # debounce + multi-press detection
  config/              # operator config, defaults
```

`firmware/main/` is the orchestrator: sets up FreeRTOS tasks, wires
companion_link events to the right subsystem.

## Companion protocol (sketch)

JSON envelope over a single BLE characteristic (notification + write
without response). Schema canonical in `design/api-spec.md` (TBD).
Examples:

```json
// Phone → Case: start a 30-second WiFi monitor sweep on channel 6
{"op":"wifi.sniff.start","args":{"channel":6,"duration_s":30}}

// Case → Phone: progress event
{"event":"wifi.sniff.progress","data":{"frames":1238,"elapsed_s":12}}

// Case → Phone: sweep completed, summary
{"event":"wifi.sniff.done","data":{"frames":3092,"channels":[6],"summary_url":"file:///capture/2026-05-16T03Z.json"}}
```

Pairing: BLE bonding with Just Works; LE Secure Connections; whitelist
of paired centrals stored in NVS.

## What we explicitly DON'T build

Documented in `design/threat-model.md`. Short list:
- No active deauth / disassoc transmission (we sniff only)
- No evil-twin AP impersonation by default
- No credential-harvesting captive portals
- No automated brute-force against captured WPA handshakes

These can be enabled by users in their own forks if they want to walk
that path — we don't ship them. Lawful-use stance.

## Open questions (operator decisions pending)

- [ ] Battery capacity vs. case thickness trade
- [ ] microSD inclusion (cost + complexity vs. pcap depth)
- [ ] Sub-GHz daughterboard slot (915 MHz / 433 MHz) for v2 or later
- [ ] Companion app: extend NearTrace vs. new app

Resolved in M2 → operator review.
