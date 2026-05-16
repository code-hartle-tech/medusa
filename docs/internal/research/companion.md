# Medusa — Companion App Integration (seed; expand in M1)

Status: **seed**. M1 researcher confirms options + recommends one.

## The question

Medusa is an MCU in a phone case. It has no screen. The companion app
(running on the phone the case wraps) is the UI. Three options:

1. **Extend NearTrace** (existing Android BLE scanner) with a
   Medusa-specific panel
2. **Build a dedicated Medusa app** (Android + iOS later)
3. **Hybrid** — NearTrace surfaces Medusa as a first-party "device
   plugin", but the Medusa-specific UI lives in a separate module

## Trade-offs

| Approach | Pros | Cons |
|---|---|---|
| Extend NearTrace | One app to install; existing BLE plumbing reusable; users already familiar | NearTrace's brand voice + UX scope creeps; conflates two products; iOS gap (NearTrace is Android-only) |
| Dedicated app | Clean brand identity; iOS-from-day-one possible; no scope creep | Two apps to maintain; user has to install two; less code reuse |
| Hybrid plugin | Best of both — clean separation, shared infra | Plugin loader complexity; need NearTrace to support a plugin pattern first |

## Pinned default (revisit in M1)

**Extend NearTrace.** NearTrace is HARTLE.TECH's flagship Android BLE
tool; users on the kit will already have it; a Medusa panel inside
NearTrace gives instant compatibility. iOS coverage is a v2 question —
not blocking bootstrap.

## Protocol shape (sketch)

BLE GATT service, single primary characteristic for JSON envelope.
Pattern similar to NUS (Nordic UART Service):

- Service UUID: TBD (assign a 128-bit project UUID during M2)
- Characteristic A: **Phone → Case**, write-without-response, JSON
  operation envelope
- Characteristic B: **Case → Phone**, notify, JSON events
- MTU negotiation: target 247-byte ATT_MTU; fall back to fragmented
  envelopes if peer-MTU smaller
- Pairing: BLE bonded, Just Works for v1 (numerical comparison for v2
  if the phone has a secure-element-backed peer model)

Schema lives at `design/api-spec.md` (TBD M2).

## Sketch: NearTrace integration

NearTrace's existing scan-result flow already handles BLE devices.
Medusa shows up as a device with a custom service UUID. When user taps,
NearTrace surfaces a "Medusa" detail panel with:

- Status (battery %, current operation, free storage)
- Operations: start WiFi sniff, start BLE scan, export captures
- Capture viewer: list of past captures with metadata
- Settings: pairing, channel preferences, retention, factory reset

The NearTrace codebase would gain one Medusa-specific Fragment or
ViewModel; the rest of the integration reuses NearTrace's BLE plumbing,
PreferenceFragments, and overall navigation.

## What this means for repos

- `code-hartle-tech/medusa` — firmware + hardware + brand + public docs
- `code-hartle-tech/neartrace-android-mvp` — gains a Medusa device-type
  module (Fragment + ViewModel + Repository for Medusa-specific BLE)
- No new app repo until v2

## Open questions

- [ ] Should the Medusa NearTrace module ship in the same APK or as a
      dynamic feature module (D8 split)? Lean toward same APK for v1
      simplicity.
- [ ] iOS path: NearTrace has no iOS sibling yet. Medusa-on-iOS goes
      via a future iOS-flavoured companion or via a web-Bluetooth PWA.
      Both are post-v1.
- [ ] Cross-platform desktop: a Tauri/Wails desktop app for power-user
      capture review? Probably no; the phone is the canonical UI by
      design.
