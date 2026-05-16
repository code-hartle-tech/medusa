# FAQ

## When can I buy one?

Not yet. Medusa is in **bootstrap phase** as of `2026-05-16`. Hardware BOM is drafted, firmware scaffold isn't built. Watch the [project board](https://github.com/orgs/code-hartle-tech/projects/8) and the GitHub Releases page for the first hardware run announcement.

## Is this legal?

**Against networks you own or are authorized to test: yes, everywhere we know of.** Against networks you don't: no, illegal in most jurisdictions. See [Lawful use](/lawful-use).

The firmware ships with an operator-side BSSID allow-list — the case won't transmit at access points you haven't listed. That's safety scaffolding, not legal cover.

## Why ESP32-S3 and not Pi Zero 2 W?

The Pi Zero 2 W lane is a **separate project** — [Nosferato](https://github.com/code-hartle-tech/nosferatos). Medusa is the MCU branch for cases where you want pocketable + low-power + cheap + immediate boot. Nosferato is the SBC branch for cases where you want more capable + Linux + bigger storage. Both are HARTLE.TECH projects.

## Why "Medusa"?

The Greek gorgon. Snake-hair becomes antennas. The petrifying gaze becomes passive RF reconnaissance (freezing the moment to inspect packets). Perseus killed her using a mirror — *don't look directly at adversaries; look at their reflection in the medium*. The mascot is a gorgon head with one prominent eye fixed on a screen of radio spectrum, snake-antennas fanning out.

Full brand brief: [`assets/brand/v1_brief.md`](https://github.com/code-hartle-tech/medusa/blob/develop/assets/brand/v1_brief.md).

## Does it work with iPhone?

For now Medusa pairs with an **Android phone** running [NearTrace](https://github.com/code-hartle-tech/neartrace-android-mvp) as the companion. iOS isn't planned for v1. Future options: native iOS companion app, web-Bluetooth PWA, or USB-CDC export to desktop.

## Why does the case have to look identifiable?

By design. The case has visible Medusa branding (mascot on back) so it's identifiable, not concealable. Per the [threat model](https://github.com/code-hartle-tech/medusa/blob/develop/docs/internal/design/threat-model.md), discreet possession in spaces where pentest tooling would be flagged is a risk we mitigate via visible identification. If you want a stealth pentest tool, Medusa isn't it.

## What does Medusa NOT do?

Read the [out-of-scope table](/features/#out-of-scope-will-not-ship): no evil-twin, no captive-portal credential harvesting, no automated handshake brute-force, no GPS device-location tagging. These are deliberate scope decisions, not "future work."

## Is the firmware closed-source?

No. **Apache 2.0**, source mirrors on GitHub. The build is reproducible. The only non-open binary in the build is Espressif's WiFi/PHY library (`libnet80211.a`, `libpp.a`, `libphy.a`) — same as every ESP-IDF project.

## How do I report a security issue?

Email [contact@hartle.tech](mailto:contact@hartle.tech) with the subject prefix `[medusa security]`. PGP key on the [hartle.tech](https://hartle.tech) site (once it's published). Coordinated-disclosure baseline: 90-day window, faster if widely exploited.

## How do I support the project?

[Donation channels](https://github.com/code-hartle-tech/medusa/blob/develop/.github/FUNDING.yml) coming as accounts are created (Liberapay, GitHub Sponsors, Ko-fi, Buy Me a Coffee, Open Collective). You can also just star the repo, file good issues, and tell other security researchers about it.

## Where's the rest of the docs?

The **internal wiki** (operator-side specifics, hardware BOM with supplier links, threat model, design notes, research write-ups) is at `medusa.hartle.tech/wiki/` — **tailnet-only**. If you're on the HARTLE.TECH tailnet, you can read it. If not, the public docs you're reading now are what's available.
