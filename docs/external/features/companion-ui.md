# Companion-app UI

The case has no screen. The phone is the UI — paired over BLE 5, talking JSON over a single GATT service.

## The pairing flow

1. Plug in the case via USB-C passthrough (or insert the phone into the case for the first time).
2. Open NearTrace → it sees the case advertising the Medusa service UUID.
3. Tap **Pair** → BLE bonded, LE Secure Connections, Just Works confirmation.
4. The bond is stored in the case's NVS — the phone is now its authorized central.

Up to N paired centrals (operator-configurable) — typically 1 (your phone) or 2 (your phone + a backup tablet for ops work).

## What you can do from the phone

| Surface | What it shows |
|---|---|
| **Status** | Battery %, current operation (idle / sniff / scan), free storage on the case, paired-central count |
| **Captures** | List of past captures with type / start time / duration / frame count / size |
| **Capture detail** | Per-frame view with filter + export |
| **Operations** | Buttons to start WiFi sniff, BLE scan, broadcast deauth (with active-TX confirmation flow), stop in-flight ops |
| **Safety** | Active-TX enable toggle for this session, BSSID allow-list editor, rate limit slider, audit-log viewer |
| **Settings** | Channel preferences, retention period, LED brightness, button bindings, factory reset |

## Active-TX confirmation flow

When the operator taps any active-transmission button (e.g. "Test deauth against my own AP"), NearTrace presents a confirmation modal:

- The target BSSID (must be on the allow-list, otherwise the modal won't appear)
- The target STA MAC (or broadcast — broadcast requires a SEPARATE confirmation)
- The reason code (default: 0x0001)
- The rate (deauths/sec)
- A reminder of the lawful-use clause

The case won't transmit until the operator hits Confirm.

## Two-Medusa coordination

If the operator owns more than one Medusa case, they can be linked via ESP-NOW. NearTrace surfaces them as a fleet:

- See which cases are in range of which others
- Trigger coordinated sweeps (multi-point coverage of a larger area)
- Aggregate captures across the fleet

ESP-NOW pairing happens out-of-band through the companion app (the cases don't auto-mesh with random peers).
