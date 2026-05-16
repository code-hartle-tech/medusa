# 🐍 Medusa

> Embedded wireless-recon research, paired with a phone for the UI.

Medusa is a HARTLE.TECH research project: an **ESP32-S3 microcontroller**
inside a phone case, talking to a companion mobile app over BLE/USB.
The MCU does the radio work (WiFi monitor mode, BLE scanning, ESP-NOW);
the phone gives you the screen, buttons, and human factors. Together:
a pocketable, low-power, defensive-network-posture analysis tool.

Sibling to [**Nosferato**](https://github.com/code-hartle-tech/nosferatos)
(single-board-computer lane, Pi Zero 2 W) and
[**NearTrace**](https://github.com/code-hartle-tech/neartrace-android-mvp)
(Android BLE scanner / planned UI). Part of the broader phone-as-UI
hacking-case-kit arc.

## Status

🚧 **Bootstrap.** Repo seeded `2026-05-16`. Hardware BOM + firmware
scaffold + project-board issues are the next visible milestones.
Star/watch the repo if you want to follow along.

## What it does (and doesn't)

Medusa is for **lawful** use on networks you own, administer, or have
written authorization to test. Specifically built for:

- Security researchers auditing their own infrastructure
- Network operators looking for rogue devices and policy violations
- Educators and CTF participants
- Curious humans who want to understand what their phone, watch, and
  laptop are *actually* broadcasting

It is **not** packaged or supported for unauthorized network intrusion.
HARTLE.TECH does not document offensive utility against third parties.
The lawful-use statement in [`NOTICE`](./NOTICE) is binding for any
fork or derivative.

## Hardware

- **MCU**: ESP32-S3 (WROOM-1 module recommended for case fit)
- **Power**: USB-C passthrough + small lipo (running BOM TBD)
- **Form factor**: phone-case shaped, slimline

Full BOM + schematic sketch in [`hardware/`](./hardware/).

## Companion

The intended UI is a phone app — **NearTrace** today, possibly a
dedicated Medusa app later. The MCU exposes a small JSON protocol over
BLE; the app renders. See [`design/api-spec.md`](./design/api-spec.md).

## Project layout

```
medusa/
  hardware/         # BOM, schematic sketches, power budget, form factor
  design/           # architecture, threat model, API spec
  research/         # ecosystem survey, legal frame, companion design
  assets/brand/     # mascot, brand_tokens, brief
  firmware/         # ESP32-S3 source (PlatformIO project, future)
  docs/external/    # public VitePress site (medusa.hartle.tech, future)
  MISSION.md        # project mission + non-negotiables
  CLAUDE.md         # house rules for Claude Code sessions
```

## License

Apache 2.0 — see [`LICENSE`](./LICENSE) and [`NOTICE`](./NOTICE) for the
attribution requirement (`Medusa — © HARTLE.TECH` must travel with any
fork / repackaging).

## Contact

[contact@hartle.tech](mailto:contact@hartle.tech) — HARTLE.TECH
(NIPC 518241327, Porto, Portugal).
