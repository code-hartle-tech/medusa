# Companion app

Medusa has no screen on the case. The **phone** the case wraps is the UI — paired over BLE 5.

## Today: extend NearTrace

[**NearTrace**](https://github.com/code-hartle-tech/neartrace-android-mvp) is HARTLE.TECH's flagship Android BLE scanner. The Medusa integration ships as a **Medusa device-type module inside NearTrace** — when NearTrace's scan discovers a paired Medusa case, tapping it opens a Medusa-specific panel.

Inside that panel:

- Status (battery %, current operation, free storage)
- Operations (start WiFi sniff, start BLE scan, export captures)
- Capture viewer (date-sorted list with summary metadata)
- Settings (pairing, channel preferences, retention, factory reset)
- The active-TX guard UI (enable/disable per session, BSSID allow-list editor, rate limit, audit log viewer)

## Protocol

JSON envelope over BLE GATT. Single primary service + two characteristics (write + notify). Pattern similar to Nordic's NUS but with a Medusa-specific service UUID.

Full schema lives in the [API spec](https://github.com/code-hartle-tech/medusa/blob/develop/docs/internal/design/api-spec.md) in the repo. v1 op set:

- `medusa.status.get` — current state
- `medusa.wifi.sniff.start` / `stop` — passive 802.11 capture
- `medusa.ble.scan.start` — BLE advertisement scan
- `medusa.captures.list` / `export` / `delete`
- `medusa.config.get` / `set`
- `medusa.security.unpair` — explicit factory reset

## iOS support

**Not in v1.** NearTrace is Android-only today. iOS support comes via one of three later paths:

1. A native iOS companion app
2. A web-Bluetooth PWA (cross-platform, no app store)
3. Standalone capture export via USB-CDC when plugged into a desktop

We'll pick one when iOS users start asking for it.

## Pairing

BLE bonded, **LE Secure Connections required**, Just Works v1 (numerical-comparison v2). The whitelist of paired centrals is stored in NVS on the case. `medusa.security.unpair` + factory reset is an explicit op (no implicit "forget" behavior).
