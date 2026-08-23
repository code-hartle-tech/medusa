# Control surfaces

The responsive, host-local web app is Medusa's current control surface. Native
iOS, Android, and watch applications are design targets; they are not shipped
companions and no production BLE pairing protocol is implemented yet.

## One information architecture

Every future surface should use the same five destinations as the web app:

| Destination | Responsibility |
|---|---|
| **Home** | Board, bridge, run, and evidence state at a glance |
| **Observe** | Searchable Wi-Fi and Bluetooth discoveries with masking on by default |
| **Runs** | Confirmed discovery, passive-logger provisioning, and local readback |
| **Artifacts** | Evidence that actually exists, with provenance and partial-state labels |
| **Devices** | Detection, firmware, storage, pairing, and companion status |

## Proposed native roles

- **Phone:** physical-presence pairing, field discovery, run preflight, maps
  where consent permits them, notifications, and artifact handoff.
- **Watch:** device health, bounded alerts, elapsed time, and a persistent stop
  control only. It should not become a dense configuration screen.
- **Web:** the full observation workbench, comparison, evidence review, and
  administration.

Any wireless companion must use authenticated, bonded pairing with per-device
keys in the platform credential store. No companion may turn the local bridge
into an open remote-control service. See the
[control-surface benchmark](https://github.com/code-hartle-tech/medusa/blob/develop/wiki/research/2026-08-21-control-surface-benchmark.md)
for the evidence behind this model.
