# Medusa — Threat Model & Defensive Posture

Status: **draft**, M2 milestone. Reviewer-passed by operator before
any public surface (README, GitHub issues) inherits its conclusions.

## Stance in one line

Medusa is a **defensive research tool**. It is built to help operators
understand the radio surface of networks and devices **they own or are
authorized to test**. It is not, and will not become, a packaged
offensive tool.

## In-scope capabilities (v1)

| Capability | Description | Defensive use case |
|---|---|---|
| WiFi 2.4 GHz **passive** sniffing (monitor mode) | Capture raw 802.11 frames without transmitting | Audit which devices are broadcasting probe requests, what SSIDs they're looking for, are any of them inadvertently leaking owner identity |
| BLE 5 **passive** scanning | Capture advertisements, parse beacon payloads | Inventory BLE devices in a space; spot trackers/AirTags; check that your own BLE devices don't broadcast more than they should |
| ESP-NOW peer link between Medusa units | Encrypted mesh between operator's own cases | Multi-point coverage of a larger area for the same audit |
| Companion-app pairing (BLE-bonded) | Authenticated link to the operator's phone | UI surface; capture summary; export to phone storage |

## Explicitly out-of-scope (won't ship)

| Out of scope | Why |
|---|---|
| **Active deauth/disassoc** frames | Disruptive to operating networks; illegal in most jurisdictions when used against networks you don't own; defensible audit doesn't need it |
| **Evil-twin AP impersonation** | Real attack technique; ships only with explicit user-flagged research forks; HARTLE.TECH does not document its operation |
| **Captive-portal credential harvesting** | Same logic — packaged offensive utility we won't distribute |
| **Automated handshake brute-force** | Different attack class; out of our research lane |
| **GPS / location tagging of captured devices** without explicit consent | Privacy-incompatible default |

A fork that adds out-of-scope features may exist downstream of Medusa;
we don't gatekeep the codebase. But HARTLE.TECH does not endorse,
support, or document those features. Lawful-use clause in `NOTICE`
travels with derivatives.

## Risks Medusa introduces

| Risk | Mitigation |
|---|---|
| The hardware is shaped like an everyday phone case → discreet possession in spaces where pentest tooling would normally be flagged | Documentation explicitly states lawful-use only; case design retains visible Medusa branding (mascot on back) so it's identifiable, not concealable |
| Captures can include sensitive metadata (SSIDs, MACs, BLE identifiers) | Pcap-style captures are local-only by default (case → phone); no cloud upload anywhere in the design; pairing required to read captures |
| ESP-NOW mesh between cases enables coordinated coverage | Mesh is encrypted; pairing happens out-of-band via the companion app; not a hidden default |
| Open-source firmware → forks can add offensive features | Inevitable for any open tool. We don't gate; we don't endorse. Apache 2.0 + NOTICE clause; HARTLE.TECH support is for the in-scope feature set only |

## Threats Medusa is NOT defending against

- Targeted state-actor surveillance
- Hardware supply-chain attacks (we don't audit our own components
  beyond reasonable diligence)
- RF reverse-engineering of our own firmware once shipped (it's open
  source — that's not a "threat", that's the point)

## Compliance pointers (jurisdictions in scope as the project grows)

Draft list, validated in `research/legal-frame.md`:

- **EU (GDPR)**: passive RF sniffing captures personal data (MAC
  addresses, BLE identifiers, probe-request SSIDs). Storage must be
  local-only by default, retention configurable, with operator-as-
  controller documented. Companion app should surface a clear
  "captures contain personal data" notice on first launch.
- **US**: 18 U.S.C. § 2511 (Wiretap Act) prohibits intercepting
  electronic communications without consent. Passive 802.11 frame
  capture is a gray area depending on jurisdiction-within-US;
  authorisation from network owner removes ambiguity. We document
  authorisation requirement up front.
- **Portugal (HARTLE.TECH home)**: similar GDPR-driven framework;
  Law 41/2004 ePrivacy (radio communication confidentiality).
- **General**: every README + docs page links the lawful-use clause.

## Reviewer pass — required before each public-surface change

Any change to README, docs/external/**, GitHub issue descriptions,
project board content, or social-media posts about Medusa goes through
one defensive-framing review before merge. The reviewer checks:

1. Is the framing defensive (audit / research / education)?
2. Are capabilities described in terms of what the **operator** can
   learn about **their own network**?
3. Are out-of-scope features mentioned only to clarify scope, not as
   "future work"?
4. Is the lawful-use clause linked or summarized?
5. Is the operator's personal identity absent?

Reviewer can be operator, HARTLE.TECH-aligned community member, or a
Claude Code session given this file as input.
