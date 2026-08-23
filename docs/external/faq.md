# FAQ

## When can I buy one?

Not yet. Medusa is a research prototype with a local web control surface and several firmware paths, but the case, final bill of materials, power budget, native companions, and physical end-to-end proof are unfinished. Watch the [project board](https://github.com/orgs/code-hartle-tech/projects/8) and GitHub Releases for any hardware-run announcement.

## Is this legal?

Use Medusa only on hardware, networks, and in survey areas you own, administer, or have explicit written authorization to assess. Requirements vary by jurisdiction and engagement; see [Lawful use](/lawful-use), which is informational rather than legal advice.

The firmware-enforced allow-list, rate, duration, audit, confirmation, and stop controls required for disruptive transmission are **not implemented yet**. Active research stays disabled in the default product and is not a release capability.

## Why ESP32-S3 and not Pi Zero 2 W?

The Pi Zero 2 W lane is a **separate project** — [Nosferato](https://github.com/code-hartle-tech/nosferatos). Medusa is the MCU branch for cases where you want pocketable + low-power + cheap + immediate boot. Nosferato is the SBC branch for cases where you want more capable + Linux + bigger storage. Both are HARTLE.TECH projects.

## Why "Medusa"?

The Greek gorgon. Snake-hair becomes antennas. The petrifying gaze becomes passive RF reconnaissance (freezing the moment to inspect packets). Perseus killed her using a mirror — *don't look directly at adversaries; look at their reflection in the medium*. The mascot is a gorgon head with one prominent eye fixed on a screen of radio spectrum, snake-antennas fanning out.

Full brand brief: [`assets/brand/v1_brief.md`](https://github.com/code-hartle-tech/medusa/blob/develop/assets/brand/v1_brief.md).

## Does it work with iPhone?

There is no native Medusa companion yet. The responsive control surface is currently host-local, while native iOS and Android companions are design targets. NearTrace is an Android BLE-scanner sibling and reference pattern, not a shipped Medusa integration.

## Why does the case have to look identifiable?

By design. The case has visible Medusa branding (mascot on back) so it's identifiable, not concealable. Per the [threat model](https://github.com/code-hartle-tech/medusa/blob/develop/wiki/design/threat-model.md), discreet possession in spaces where pentest tooling would be flagged is a risk we mitigate via visible identification. If you want a stealth pentest tool, Medusa isn't it.

## What does Medusa NOT do?

Read the [out-of-scope table](/features/#out-of-scope-will-not-ship): no evil-twin, no captive-portal credential harvesting, no automated handshake brute-force, no GPS device-location tagging. These are deliberate scope decisions, not "future work."

## Is the firmware closed-source?

No. **Apache 2.0**, source mirrors on GitHub. The current evidence is repeatable local compilation with the installed Arduino core; the toolchain is not yet pinned for bit-for-bit reproducible builds. The build also uses Espressif's precompiled Wi-Fi/PHY libraries (`libnet80211.a`, `libpp.a`, `libphy.a`), as ESP-IDF projects normally do.

## How do I report a security issue?

Email [contact@hartle.tech](mailto:contact@hartle.tech) with the subject prefix `[medusa security]`. PGP key on the [hartle.tech](https://hartle.tech) site (once it's published). Coordinated-disclosure baseline: 90-day window, faster if widely exploited.

## How do I support the project?

[Donation channels](https://github.com/code-hartle-tech/medusa/blob/develop/.github/FUNDING.yml) coming as accounts are created (Liberapay, GitHub Sponsors, Ko-fi, Buy Me a Coffee, Open Collective). You can also just star the repo, file good issues, and tell other security researchers about it.

## Where's the rest of the docs?

The public repository includes reviewed design and research material under `wiki/`. Operational notes and candid internal handoffs belong in HARTLE.TECH's separate tailnet-only wiki and are not part of this public documentation site.
