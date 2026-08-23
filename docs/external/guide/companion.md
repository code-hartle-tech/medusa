# Companion direction

Medusa does not currently ship a native companion. The responsive local web app
is available now; NearTrace is an Android BLE-scanner sibling and a useful
interaction-pattern reference, not a production Medusa device module.

## Proposed phone role

A future iOS or Android companion should handle work that benefits from physical
presence and a pocketable screen:

- authenticated, bonded device enrollment;
- device health and storage state;
- scoped discovery preflight and bounded run control;
- local notifications and evidence handoff;
- explicit retention and deletion controls.

The phone must not silently make the board remotely reachable or reuse a shared
factory password. Pairing keys belong in the platform credential store, and
loss of the controlling session must not defeat a firmware stop path.

## Proposed watch role

A watch surface should remain deliberately small: connection health, current
run, elapsed time, important alerts, and stop. Configuration, targeting, and
artifact inspection belong on the phone or web workbench.

## Protocol status

The repository contains a proposed JSON/BLE protocol in the
[API specification](https://github.com/code-hartle-tech/medusa/blob/develop/wiki/design/api-spec.md).
It is design material, not proof of an implemented GATT service, bonded pairing,
native application, or cross-platform interoperability.
