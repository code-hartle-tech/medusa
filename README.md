# 🐍 Medusa

> Embedded wireless-recon research, paired with a phone for the UI.

Medusa is a HARTLE.TECH research project: an **ESP32-S3 microcontroller**
intended for a phone-case form factor, with a larger screen acting as its
control surface. The MCU does the radio work; the responsive local web app is
the current interface. Bonded native phone and watch companions over BLE/USB
remain product direction rather than shipped applications.

Sibling to [**Nosferato**](https://github.com/code-hartle-tech/nosferatos)
(single-board-computer lane, Pi Zero 2 W) and
[**NearTrace**](https://github.com/code-hartle-tech/neartrace-android-mvp)
(Android BLE scanner / planned UI). Part of the broader phone-as-UI
hacking-case-kit arc.

## Status

🚧 **Bootstrap.** Repo seeded `2026-05-16`. The Wi-Fi internals research lane
now includes a compile-verified stock-driver firmware lab and two offline
teaching labs; the broader product firmware and hardware integration remain in
progress. Star/watch the repo if you want to follow along.

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
The lawful-use statement in [`NOTICE`](./NOTICE) records the project's
intended use and support scope; the Apache-2.0 license governs reuse and
redistribution.

## Hardware

- **MCU**: ESP32-S3 (WROOM-1 module recommended for case fit)
- **Power**: USB-C passthrough + small lipo (running BOM TBD)
- **Form factor**: phone-case shaped, slimline

Draft BOM, power-budget, and form-factor notes live in
[`wiki/hardware/`](./wiki/hardware/).

## Control surfaces

The current interface is the local responsive web app under
[`tools/esp-lab/`](./tools/esp-lab/). A bonded phone companion, with a
watch limited to health, alerts, and stop controls, is the next product lane;
neither native surface is shipped yet. See
[`wiki/design/api-spec.md`](./wiki/design/api-spec.md) for the proposed device
protocol and the [control-surface benchmark](./wiki/research/2026-08-21-control-surface-benchmark.md)
for the cross-device interaction model.

## Wi-Fi internals study lane

- [`wiki/research/marauder-deauth-patch.md`](./wiki/research/marauder-deauth-patch.md)
  is the public defensive summary of the stock raw-TX boundary, isolated-build
  requirement, proof ladder, and release boundary. Candid reproduction notes
  live in the tailnet-only internal wiki.
- [`code/firmware/`](./code/firmware/) keeps Espressif's stock validator intact
  and exposes bounded, self-addressed learning experiments without a target
  input or transmit loop.
- [`code/labs/frame-anatomy/`](./code/labs/frame-anatomy/) and
  [`code/labs/xtensa-link-lab/`](./code/labs/xtensa-link-lab/) teach frame
  layout, static-archive extraction, linker resolution, and the Xtensa ABIs
  without opening a radio.

## Project layout

```
medusa/
  code/firmware/    # stock-driver ESP-IDF/Arduino learning firmware
  code/labs/        # offline frame, linker, and Xtensa exercises
  wiki/             # design, hardware, and source-pinned research notes
  assets/brand/     # mascot, brand_tokens, brief
  docs/external/    # public VitePress documentation
  MISSION.md        # project mission + non-negotiables
  CLAUDE.md         # house rules for Claude Code sessions
```

## License

Apache 2.0 — see [`LICENSE`](./LICENSE) for the license terms and
[`NOTICE`](./NOTICE) for project attribution and the lawful-use position.

## Contact

[contact@hartle.tech](mailto:contact@hartle.tech) — HARTLE.TECH
(NIPC 518241327, Porto, Portugal).
