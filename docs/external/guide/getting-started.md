# Getting started

Medusa is a HARTLE.TECH research project in **bootstrap phase**. There is no shipped firmware yet — this page is a placeholder describing what the v1 experience will look like once the hardware + firmware are real.

## What you'll need (planned v1)

- **An ESP32-S3-WROOM-1 dev board** (or the assembled Medusa case once available). See the [hardware overview](./hardware) for the BoM.
- **A phone running [NearTrace](https://github.com/code-hartle-tech/neartrace-android-mvp)** as the companion app (Android only for v1).
- **A network you own or are authorized to audit.** Repeat: only networks you own or have written authorization for. See [Lawful use](/lawful-use).

## The five-minute experience (planned)

1. **Pair** the case with your phone via the NearTrace companion-app panel.
2. **Enable active TX** for this session — the firmware default is passive-only. Active TX requires explicit operator confirmation per session.
3. **Configure your BSSID allow-list** — the list of access-point MAC addresses you're authorized to interact with. Medusa firmware drops any TX op against unlisted BSSIDs.
4. **Run a passive sweep** — WiFi probe-request capture + BLE advertisement scan. See what's on the air.
5. **Export the capture** to your phone over BLE.

## Where we are in the build

The current state is repository scaffold, brand kit, hardware BOM, threat model, API spec, and research notes on RF techniques (notably the [Marauder deauth-patch analysis](https://github.com/code-hartle-tech/medusa/blob/develop/docs/internal/research/marauder-deauth-patch.md) — public hand-written version + swarm-distilled cross-check, both in the repo).

What's **not** yet:
- Working firmware
- Assembled hardware prototype
- Case 3D model
- NearTrace integration module
- Public APK install path

Follow the [project board](https://github.com/orgs/code-hartle-tech/projects/8) for milestone tracking, or watch the repo for releases.

## Why we're starting open

HARTLE.TECH's stance: pentest research tooling stays **open source + defensively framed**. The threat model bounds what we ship (no evil-twin, no captive-portal credential harvesting, no automated handshake brute-force). The lawful-use clause in `NOTICE` Section 4(c) binds derivatives.

## Next

- Read [What it does — and doesn't](/features/) for the capability matrix
- Read [Lawful use](/lawful-use) for the legal posture
- Read [Hardware overview](./hardware) for the BoM + form factor
