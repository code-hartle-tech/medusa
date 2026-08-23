# Getting started

Medusa is a research prototype, not a finished hardware product. The current
usable surface is a local web workbench backed by a USB-connected ESP board.

## What you need now

- A locally supported ESP32 board connected over USB.
- Node.js, Python, `arduino-cli`, and a compatible ESP32 board core.
- A network and physical survey area you own, administer, or have written
  authorization to assess.

From `tools/esp-lab/`, run:

```bash
./dev.sh
```

Open `http://localhost:5250`, acknowledge the data notice, and detect the board.
The **Runs** page distinguishes each operation and tells you when it will
replace the board's firmware. Receive-only logging, Wi-Fi active discovery, and
BLE active discovery are labeled separately.

## Passive logger storage gate

The passive logger never formats storage automatically. On an uninitialized or
unavailable LittleFS partition it boots fail-closed and collects nothing.
Initialization is a separate explicit console action and is not exposed by the
web app. USB readback flushes and validates the stored CSV without clearing it.

## Current evidence

- Responsive web product shell: built and browser-checked.
- Unattended firmware: compiles for ESP32-C3 and ESP32-S3.
- Host USB protocol: automated validation and atomic-download tests pass.
- ESP32-C3: an earlier build booted fail-closed with uninitialized storage; the
  exact current v3 binary has compile and host-test evidence only.
- Still pending: approved storage initialization, live capture, power-cycle
  restore, physical USB retrieval, case integration, and native companions.

## Next

- Read [Capabilities](/features/) for current proof and exclusions.
- Read [Lawful use](/lawful-use) before collecting radio identifiers.
- Read [Hardware overview](./hardware) for the unvalidated form-factor plan.
