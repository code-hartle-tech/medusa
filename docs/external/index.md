---
layout: home
title: Medusa
hero:
  name: Medusa
  text: The gorgon listens.
  tagline: ESP32-S3 inside a phone case — paired with your phone for the UI. A defensive research tool for auditing the radio surface of the networks you own. From HARTLE.TECH.
  image:
    src: /medusa-mascot.svg
    alt: Medusa mascot — gorgon head with snake-hair antennas
  actions:
    - theme: brand
      text: Get started
      link: /guide/getting-started
    - theme: alt
      text: GitHub
      link: https://github.com/code-hartle-tech/medusa

features:
  - icon: 🐍
    title: Passive RF reconnaissance
    details: 802.11 monitor mode + BLE 5 scanning. See what your devices are actually broadcasting — probe requests, beacon payloads, BLE advertisements, ESP-NOW chatter.
  - icon: 🔍
    title: Audit your own posture
    details: Built for security researchers and network operators auditing their own infrastructure. Confirm PMF is on; spot rogue devices; understand what your phone leaks before you take it somewhere it matters.
  - icon: 📱
    title: Phone-as-UI
    details: No screen on the case. The phone the case wraps is the display, paired over BLE 5. Companion to NearTrace today; dedicated Medusa app possible later.
  - icon: 🛡
    title: Defensive framing, firmware-side
    details: Active TX is operator-confirmed per session. BSSID allow-list enforced in firmware. Rate-limit ceiling. Audit log of every transmission to NVS.
  - icon: 🪶
    title: Pocketable + cheap
    details: ESP32-S3-WROOM-1, slim lipo, USB-C passthrough. Per-unit prototype BoM under €30. The hardware doesn't add weight you'd notice in a back pocket.
  - icon: 📜
    title: Open source, lawful only
    details: Apache 2.0 + a binding lawful-use clause in NOTICE Section 4(c). Source is open, the build is reproducible, and HARTLE.TECH does not document offensive utility against networks you don't own.
---

<div class="gaze-callout">

⚠️ **Lawful-use anchor.** Medusa is published for security researchers, network operators auditing their own infrastructure, educators, and CTF participants. Use against networks you don't own, administer, or have written authorization to test is illegal in most jurisdictions. The lawful-use clause in [`NOTICE`](https://github.com/code-hartle-tech/medusa/blob/develop/NOTICE) Section 4(c) is binding on derivatives. See [Lawful use](/lawful-use) for details.

</div>

## Status

🚧 **Bootstrap phase.** Repository seeded `2026-05-16`. Hardware BOM, firmware scaffold, and companion-app integration are the next visible milestones. The [project board](https://github.com/orgs/code-hartle-tech/projects/8) tracks progress. Star or watch the repo if you want to follow along.

## Sibling projects

Medusa is the MCU branch of the HARTLE.TECH **phone-as-UI hacking-case-kit** vision.

- [**Nosferato**](https://github.com/code-hartle-tech/nosferatos) — single-board-computer lane (Pi Zero 2 W). Bigger, more capable, less pocketable.
- [**NearTrace**](https://github.com/code-hartle-tech/neartrace-android-mvp) — Android BLE scanner. The companion app Medusa pairs with today.
- [**DumpSock**](https://dumpsock.hartle.tech) — sibling tool in the HARTLE.TECH portfolio (cloudless iPhone backup). Same brand house style, different mission.

## Contact

[contact@hartle.tech](mailto:contact@hartle.tech) · HARTLE.TECH (NIPC 518241327, Porto, Portugal)
