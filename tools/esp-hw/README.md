# esp-hw — detect · build · flash · retrieve from an ESP

A small, dependency-light CLI. Point it at a connected ESP to identify the chip,
build passive discovery firmware, flash it, and inspect or retrieve the local
result over USB. No framework is required for the direct hardware loop.

## Passive logger workflow

```bash
python3 esp_hw.py scan --usb --json
python3 esp_hw.py build --chip esp32-c3 --attack unattended --no-patch
python3 esp_hw.py flash --port PORT --chip esp32-c3 --build-dir /tmp/medusa_unattended_esp32c3
python3 read_serial.py PORT 20
python3 esp_hw.py unattended-dump --port PORT --out medusa_inventory.csv
```

Pass the board model explicitly. A chip probe opens and resets the serial device,
so it is not part of the logger-preserving discovery path; flush any wanted
in-memory observations before using a deliberate probe in a separate lab flow.

## Active research boundary

The source tree contains stock-versus-modified SDK research used to study
Espressif's raw-frame validation boundary on owned lab hardware. Those paths are
not part of the default product, and this public README intentionally does not
provide a launch recipe. The local web bridge rejects them unless
`MEDUSA_ACTIVE_LAB=1`, and that environment flag is only a research boundary:
it does not make the prototypes release-ready. The firmware-enforced scope,
allow-list, rate, duration, audit, confirmation, and persistent-stop controls in
the [threat model](../../wiki/design/threat-model.md) must exist first.

## Build safety

`build` creates a private hard-linked view of the selected SDK, replaces only
that view's `libnet80211.a`, and points arduino-cli at it for the compile. The
installed Arduino core is never modified, including if a build is interrupted. A
cross-process lock also protects generated target headers and output directories.
Generated headers and output directories are protected across concurrent build
processes.

The unattended build is separately policy-locked to the stock SDK. It receives
beacons, persists a local LittleFS inventory, and exposes only non-erasing USB
retrieval by default. If the partition is unavailable it fails closed; formatting
requires the exact `INIT CONFIRM` serial command, and clearing requires
`CLEAR CONFIRM`.

## Prerequisites

- `esptool` v5.3.1, `arduino-cli` 1.5.1, `dns-sd` (macOS).
- An ESP32 Arduino core and toolchain compatible with the selected board.
- C5/C6 recipes are configuration-only on this workstation until a compatible
  board core is installed and compile-tested; they are not claimed as working.

## Lawful use

Transmit deauth/management frames only against networks you own, administer, or
are explicitly authorized to test. The project's lawful-use statement is in the
[repository-root `NOTICE`](../../NOTICE). Apache License §4(c) separately
requires prominent change notices in modified files, while §4(d) addresses
attribution notices from a NOTICE file; neither is the source of the lawful-use
statement.
