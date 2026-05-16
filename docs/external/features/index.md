# Capabilities

What Medusa **does** (and what it explicitly **doesn't**) ships in firmware, not in marketing copy. The [threat model](https://github.com/code-hartle-tech/medusa/blob/develop/docs/internal/design/threat-model.md) is the source of truth; this page is the public-friendly distillation.

## In scope (ships in v1 firmware)

### Passive

| Capability | What you get |
|---|---|
| **WiFi 2.4 GHz passive sniffing** (monitor mode) | Captured beacon + probe-request + (optionally) data frames, RSSI, channel, source/destination MACs, sequence numbers. See what your devices broadcast when they think nobody's looking. |
| **BLE 5 passive scanning** | Captured advertisements + scan responses. Beacon payload parsed. Build an inventory of BLE devices in a space; spot trackers (AirTags, Tiles); check what your own BLE devices broadcast. |
| **ESP-NOW link between Medusa units** | Encrypted mesh between cases the operator owns. Useful for multi-point coverage of a larger area for the same audit. |

### Active

| Capability | What you get |
|---|---|
| **Active 802.11 transmission** (raw management frames including deauth/disassoc) | For auditing your own AP's deployment of 802.11w (PMF) — confirm forged deauths get dropped. For reproducing techniques used by Marauder-class tools on hardware you own. Firmware-gated: per-session enable, BSSID allow-list, rate limit, audit log to NVS. |

The active-transmission story has a deep [research write-up](https://github.com/code-hartle-tech/medusa/blob/develop/docs/internal/research/marauder-deauth-patch.md) in the repo (and a [swarm-distilled cross-check](https://github.com/code-hartle-tech/medusa/blob/develop/docs/internal/research/marauder-deauth-patch-swarm.md) for the same content from a different angle).

## Out of scope (will not ship)

| Out of scope | Why |
|---|---|
| **Evil-twin AP impersonation** | Real attack technique; ships only in user-flagged research forks; HARTLE.TECH does not document its operation |
| **Captive-portal credential harvesting** | Packaged offensive utility we won't distribute |
| **Automated handshake brute-force / WPA dictionary attack** | Different attack class entirely; outside our research lane |
| **GPS / location tagging of captured devices** without explicit consent | Privacy-incompatible default |

A downstream fork that adds out-of-scope features may exist. HARTLE.TECH does not gate, endorse, support, or document those features. Apache 2.0 + the lawful-use clause in `NOTICE` Section 4(c) binds derivatives regardless.

## Operator safety guards (firmware-enforced)

Every active-TX operation in v1 ships with these guards. They're **firmware**, not UI:

- **Per-session enable** — active TX is OFF at boot; operator confirms per session via the companion app
- **BSSID allow-list** — operator-configured list of access-point MACs they're authorized to interact with; firmware drops TX ops against unlisted BSSIDs
- **Rate limit** — max deauths/sec ceiling, prevents accidental fire-hose
- **Audit log** — every TX op writes to NVS (target BSSID, target STA, timestamp, operator session id), exportable to companion
- **Broadcast confirmation** — broadcast deauths (DA = FF:FF:FF:FF:FF:FF) require a separate operator confirmation

These guards don't make the capability legal in jurisdictions where transmission against unowned networks is illegal — they make it harder to misuse accidentally. The [`NOTICE`](https://github.com/code-hartle-tech/medusa/blob/develop/NOTICE) lawful-use clause is binding regardless.
