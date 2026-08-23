# Arduino-ESP32 stock-gate companion

This sketch mirrors the essential experiments from the ESP-IDF firmware while
using the Arduino build system. It exists because copying Marauder code into an
Arduino sketch can appear to compile yet still leave Espressif's validator in
control—or fail at link time if the duplicate-symbol recipe was omitted.

The sketch intentionally contains no private-symbol override and no linker
modification. Its only unsupported-subtype submission is one self-addressed
frame after the exact serial command `probe-stock-gate SELF`. It cannot accept
another device's MAC and cannot loop transmissions. Use only in an authorized
radio environment; the repository `NOTICE` applies.

The sketch defaults `MEDUSA_LAB_COUNTRY_CODE` to `"PT"` because this lab was
built in Portugal. Before radio use elsewhere, set that compile-time macro to
your actual ISO 3166-1 alpha-2 country code. The command parser displays
`1..13`, but the configured Wi-Fi driver domain remains authoritative and may
reject a channel; the Portugal default is not evidence of compliance elsewhere.

The locally installed M5Stack core 2.1.3 bundles Arduino-ESP32 2.0.14 and
ESP-IDF 4.4.6. A verified local build can be reproduced with Arduino CLI:

```bash
arduino-cli compile \
  --config-dir "$HOME/Library/Arduino15" \
  --fqbn m5stack:esp32:m5stack_atoms3 \
  code/firmware/arduino/medusa_stock_gate_lab
```

That FQBN is used only as an available ESP32-S3/LX7 build target. Before
flashing, identify the physical board and select its real FQBN. A successful
build proves source/API/link compatibility with that installed core; it does
not prove flashing, serial behavior, on-air transmission, or receiver behavior.

Flashing overwrites the board's application, and Arduino-ESP32 core startup
runs before this sketch's `setup()`. Core 2.0.14 can automatically reformat an
incompatible/full default NVS partition at that earlier stage, which the sketch
cannot prevent. Back up the attached board or use one whose application and
stored settings are disposable.
