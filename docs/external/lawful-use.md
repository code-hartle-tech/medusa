# Lawful use

Medusa is **research and educational** software, published for:

- Security researchers auditing their own infrastructure
- Network operators looking for rogue devices and policy violations
- Educators and CTF participants
- Engineers curious about RF protocol behavior on hardware they own

**Use on networks you do not own, do not administer, and do not have written authorization to test is illegal in most jurisdictions.** HARTLE.TECH does not endorse, support, or document such use.

## The binding clause

The `NOTICE` file in the repository carries the legally-binding lawful-use clause. Per Apache License 2.0 Section 4(c), any derivative work distributed must preserve this `NOTICE` unchanged:

> Medusa is research and educational software for use on networks the operator owns, administers, or has explicit written permission to test. Use against networks or devices without authorization is illegal in most jurisdictions. HARTLE.TECH publishes this work for security researchers, network operators auditing their own infrastructure, educators, and capture-the-flag participants — not for unauthorized intrusion.

If you fork, repackage, or otherwise redistribute Medusa, this clause comes with it. You cannot strip it.

## What the law actually says (informational; not legal advice)

### European Union

- **GDPR + ePrivacy** — passive RF reception records personal data (MAC addresses, BLE identifiers, probe-request SSIDs). The operator using Medusa to audit their own infrastructure is a controller; captures stay on the operator's device; no cloud transmission by default. Companion app surfaces a first-launch notice on first capture.
- **ePrivacy** — passive reception of broadcasts intentionally transmitted (beacons, probe requests) has generally not been treated as interception by courts. Active capture of unicast traffic is a different question — Medusa does not do that by default.

### United States

- **18 U.S.C. § 2511** (Wiretap Act) — prohibits intercepting electronic communications without consent. Beacon/probe-request capture is generally treated as consensual receipt of broadcasts; unicast 802.11 frame capture is gray-area depending on circuit. Authorization from the network owner is the cleanest path.
- **18 U.S.C. § 1030** (CFAA) — Medusa's passive capabilities don't directly implicate access to protected computers. Active capabilities (deauth, disassoc transmission) against unowned networks would — that's the line Medusa's firmware-side allow-list is designed to keep you on the right side of.

### Portugal (HARTLE.TECH's jurisdiction)

- **Constitution Art. 34** — communication secrecy
- **Law 41/2004** — ePrivacy transposition
- **GDPR via Law 58/2019**
- **Cybercrime Law 109/2009** — defines "illegitimate access"; passive RF reception is generally not access

### General principles (everywhere)

| Scenario | Legality |
|---|---|
| Your own network, your own devices | Legal |
| Network where you have written authorization | Legal |
| Public hotspot you're a legitimate user of | Passive beacon/probe capture: usually fine. Other users' unicast traffic: usually not. |
| Network you don't own and aren't authorized for | Not OK anywhere |

**This is not legal advice.** Consult counsel for your specific jurisdiction and use case. Audit engagements should have a signed scope-of-work document.

## How HARTLE.TECH writes about Medusa

Documentation, marketing, blog posts, and any public artifact about Medusa is framed defensively:

- "Audit the radio surface of **your own** network"
- "Understand what **your** devices broadcast"
- "Educational tool for security researchers"
- "For use on networks you own or are authorized to test"

We do not write:

- Step-by-step attack tutorials against unowned networks
- Tooling to target specific unconsenting entities
- Anything that implies the operator is going to use Medusa offensively

## Reviewer pass

Every change to public-facing copy about Medusa gets a defensive-framing review (see [`design/threat-model.md`](https://github.com/code-hartle-tech/medusa/blob/develop/docs/internal/design/threat-model.md) §"Reviewer pass" for the 5-question checklist). Same checklist applies to community PRs.
