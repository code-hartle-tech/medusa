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
    details: The responsive local web app is available now. Bonded native phone and watch companions remain design targets, not shipped applications.
  - icon: 🛡
    title: Passive-first by default
    details: Discovery and local readback are the default surface. Active-transmission research remains disabled and cannot become a release capability until firmware-enforced scope, rate, duration, audit, confirmation, and stop controls exist.
  - icon: 🪶
    title: Pocketable target
    details: ESP32-S3-WROOM-1, slim lipo, and USB-C passthrough remain the case-design direction. Final weight, power budget, and bill of materials still need physical validation.
  - icon: 📜
    title: Open source, lawful only
    details: Apache 2.0 source with a clear lawful-use and support position in NOTICE. HARTLE.TECH does not document offensive utility against networks you do not own or have authorization to test.
---

<div class="gaze-callout">

⚠️ **Lawful-use anchor.** Medusa is published for security researchers, network operators auditing their own infrastructure, educators, and CTF participants. Do not use it against networks or devices you do not own, administer, or have written authorization to test. [`NOTICE`](https://github.com/code-hartle-tech/medusa/blob/develop/NOTICE) records the project's intended use and support scope; see [Lawful use](/lawful-use) for details.

</div>

## Status

🚧 **Research prototype.** The responsive local web control surface and several firmware research paths exist. The current v3 passive unattended logger compiles for ESP32-C3 and ESP32-S3 and is host-tested; an earlier build booted fail-closed on a C3 with uninitialized storage. Exact-v3 boot, physical capture, storage initialization, power-cycle persistence, USB readback, case integration, and native companions remain separate proof gates. The [project board](https://github.com/orgs/code-hartle-tech/projects/8) tracks progress.

## Sibling projects

Medusa is the MCU branch of the HARTLE.TECH **phone-as-UI hacking-case-kit** vision.

- [**Nosferato**](https://github.com/code-hartle-tech/nosferatos) — single-board-computer lane (Pi Zero 2 W). Bigger, more capable, less pocketable.
- [**NearTrace**](https://github.com/code-hartle-tech/neartrace-android-mvp) — Android BLE-scanner sibling and companion-pattern reference; it is not yet a shipped Medusa companion.
- [**DumpSock**](https://dumpsock.hartle.tech) — sibling tool in the HARTLE.TECH portfolio (cloudless iPhone backup). Same brand house style, different mission.

## Contact

[contact@hartle.tech](mailto:contact@hartle.tech) · HARTLE.TECH (NIPC 518241327, Porto, Portugal)
