# Capabilities

What Medusa **does** (and what it explicitly **doesn't**) ships in firmware, not in marketing copy. The [threat model](https://github.com/code-hartle-tech/medusa/blob/develop/wiki/design/threat-model.md) is the source of truth; this page is the public-friendly distillation.

## Current product surface

| Capability | Current evidence and boundary |
|---|---|
| **Unattended 2.4 GHz beacon inventory** | Current v3 receive-only firmware compiles for ESP32-C3 and ESP32-S3 and its storage/readback protocol is host-tested. An earlier build booted fail-closed on C3; exact-v3 boot, physical capture, initialized storage, power-cycle restore, and USB retrieval remain pending gates. |
| **Nearby Wi-Fi discovery** | The local web app can build, flash, and run the board's ordinary active scan. It may send probe requests, does not associate, and clearly warns that the run replaces the board's current firmware. |
| **Nearby BLE discovery** | The local web app can build, flash, and run an active advertisement scan. It may send standard scan requests, does not connect to observed devices, and replaces the current firmware. |
| **Local artifacts and inventory** | Browser downloads, bridge-side inventory, masking, and proof labels exist. Identifier masking affects the screen only; device records and downloads still contain the collected identifiers. |
| **ESP-NOW multi-sensor link** | Design direction only; not a shipped or verified capability. |

Active 802.11 transmission remains a research prototype outside the default
product. The source-pinned [research write-up](https://github.com/code-hartle-tech/medusa/blob/develop/wiki/research/marauder-deauth-patch.md)
documents the stock-driver boundary and proof ladder, but does not make the
prototype product-ready.

## Out of scope (will not ship)

| Out of scope | Why |
|---|---|
| **Evil-twin AP impersonation** | Packaged offensive utility; HARTLE.TECH does not ship, support, or document its operation |
| **Captive-portal credential harvesting** | Packaged offensive utility we won't distribute |
| **Automated handshake brute-force / WPA dictionary attack** | Different attack class entirely; outside our research lane |
| **GPS / location tagging of captured devices** without explicit consent | Privacy-incompatible default |

A downstream fork that adds out-of-scope features may exist. HARTLE.TECH does not gate, endorse, support, or document those features. Apache-2.0 governs reuse and redistribution; `NOTICE` records this project's lawful-use and support position.

## Release requirements for active transmission

The controls below are required before an active-transmission prototype can be
considered a Medusa release capability. **They are not implemented yet.** The
default product keeps active research disabled, and a server-side environment
flag is not a substitute for firmware enforcement.

- **Per-session enable** — active transmission is off at boot and requires a fresh, authenticated operator confirmation
- **Scope allow-list** — firmware rejects operations outside the explicitly authorized device and network scope
- **Rate limit** — firmware-enforced ceilings prevent an accidental unbounded run
- **Audit log** — local durable records preserve scope, timing, outcome, and operator-session context
- **Broadcast-class confirmation** — any operation affecting more than one explicitly selected device requires a separate confirmation
- **Bounded duration and persistent stop** — every run has a firmware ceiling and remains stoppable if the controlling UI disappears

These guards would reduce accidental misuse; they would not replace written
authorization or compliance with applicable law. The project's intended use
and support scope are recorded in [`NOTICE`](https://github.com/code-hartle-tech/medusa/blob/develop/NOTICE).
