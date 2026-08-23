# Medusa ESP Wi-Fi internals lab

This is a deliberately small ESP-IDF lab for learning where an 802.11 frame
goes between your C buffer and the ESP radio. It keeps Espressif's stock
`libnet80211` validator intact. It does not contain Marauder's private-symbol
override, does not accept a target MAC, and has no transmit loop.

Medusa is research and educational software for use on networks the operator
owns, administers, or has explicit written permission to test. Use against
networks or devices without authorization is illegal in most jurisdictions.
HARTLE.TECH publishes this work for security researchers, network operators
auditing their own infrastructure, educators, and capture-the-flag
participants — not for unauthorized intrusion.

Preserve the repository [`NOTICE`](../../NOTICE) unchanged and surface the
credit “Medusa — © HARTLE.TECH” wherever attribution for third-party
components is displayed.

## What the firmware proves

The serial console separates four claims that are often accidentally collapsed:

1. `esp_wifi_80211_tx()` accepted or rejected the buffer.
2. On ESP-IDF 6.x, the Wi-Fi driver invoked its TX completion callback and
   reported success or failure. The default IDF 4.4 lane has no public raw-TX
   callback API and prints that this evidence is unavailable.
3. A second monitor-mode radio captured the frame over the air.
4. A receiver interpreted or acted on the frame.

Only the matching evidence proves each claim. Marauder's on-screen packet count,
for example, is an attempt counter; it is not automatically claim 3.

The lab also counts nearby management-frame metadata without retaining MAC
addresses or payloads. The `tx-action` command emits one documented,
vendor-specific action frame with a visible `MEDUSA-LAB` marker. The
`probe-stock-gate SELF` command submits one unsupported management subtype with
destination, source, and BSSID all set to the ESP's own STA address. On the
stock driver this should be rejected; even unexpected acceptance has no
external target.

## Toolchain and builds

The default archaeology lane pins PlatformIO's `espressif32` platform 5.4.0
(ESP-IDF 4.4.5), close to the IDF 4.4.x base used by Marauder's Arduino 2.x
targets. A separate current comparison environment pins platform 7.0.1
(ESP-IDF 6.0.1).

PlatformIO Core is pinned to 6.1.19. Create it once in a durable location
rather than a temporary directory, so a later session can reproduce the same
build instead of finding the environment gone:

```bash
bash scripts/setup_pio_env.sh
export PATH="$HOME/.local/share/medusa/pio-venv/bin:$PATH"
```

The script installs only the pinned PlatformIO Core, asserts the version it
got, and never opens a serial port or touches an attached board. Override
`MEDUSA_PIO_HOME` or `MEDUSA_PIO_PYTHON` if this host needs a different
location or interpreter. Then:

```bash
cd code/firmware
pio run -e esp32s3-idf44
pio run -e esp32-idf44
python3 scripts/verify_stock_validator.py
python3 scripts/inspect_stock_tx.py .pio/build/esp32s3-idf44/firmware.elf
```

The checked-in regulatory-domain default is `PT` because this lab was built in
Portugal. Before using a radio elsewhere, set `MEDUSA_LAB_COUNTRY_CODE` through
`pio run -t menuconfig` to your actual ISO 3166-1 alpha-2 country code. The
console accepts the superset `1..13`, but the configured driver domain remains
authoritative and may reject a channel. Do not use `PT` as a way to claim
compliance in another jurisdiction.

- `esp32s3-idf44` targets the pinned Medusa MCU on the historical stack.
- `esp32-idf44` targets a classic ESP32 Dev Module/WROOM-32 on that stack.
- `esp32s3-idf60` is the non-default current ESP-IDF comparison lane.
- An ESP8266 (`ESP-WROOM-02`, `ESP-12`, NodeMCU 1.0) is a different LX106 SDK
  lane and cannot use this binary.

## Read the real stock gate in Xtensa assembly

`verify_stock_validator.py` proves which object owns the selected symbol.
`inspect_stock_tx.py` answers the next question from the final ELF: what does
the vendor-owned caller actually call, and what does it do with the result?

For the checked IDF 4.4 S3 build, the fail-closed report proves this sequence:

```text
public esp_wifi_80211_tx arguments:  a2=ifx  a3=buffer  a4=len  a5=en_sys_seq
outgoing call8 registers:           a10     a11       a12     a13
                                     |        |         |       |
                                     +---- call8 stock validator
validator return: callee a2 -> caller a10 -> bnez rejection branch
```

Inside the selected checker, the report identifies `l8ui a8, a3, 0`, which
loads the first Frame Control octet from the rotated `buffer` argument. It then
proves `0x0c` and `0xf0` masks are applied to extract the type and subtype bits.
The observed rejection paths load `a2=0x102` (`ESP_ERR_INVALID_ARG`); the
accepted path loads zero. These are version-pinned instructions from the built
blob, not a source-level guess or a supported private ABI.

The inspector invokes only `objdump`, writes nothing, and refuses to pass if
the call target, argument staging, masks, branch, or return values disappear.
It also accepts `--json` and `--objdump /absolute/path` for reproducible
evidence capture.

Before choosing a lane, plug in one board and run the read-only inventory:

```bash
python3 scripts/board_inventory.py
python3 scripts/board_inventory.py --port /dev/cu.usbmodemXXXX
```

The second command calls `esptool chip-id`; it never flashes or erases, but it
can reset the board and interrupt whatever firmware is currently running.

## On-device experiment

After confirming the exact board and serial port:

```bash
pio run -e esp32s3-idf44 --target upload
pio device monitor --baud 115200
```

Type `help`. Start with `status`, then `tx-action`. If you have a second radio
in monitor mode, put it on the same channel and look for the ASCII marker. The
self-addressed gate probe is intentionally a separate, explicit command.

The checked ESP32-S3 IDF 4.4 build keeps UART0 as its primary input console.
On an ESP32-S3-DevKitC-1, use the connector/port backed by the USB-to-UART
bridge for interactive commands. The native USB-Serial-JTAG connector can show
secondary logs under this configuration but is not proven as `stdin`; a native
USB primary-console variant is a separate configuration and hardware test.

No ESP board was connected during the initial local build. Compilation and
link-map ownership are proven for `esp32s3-idf44` and `esp32-idf44`; flashing,
serial readback, driver callback, over-air capture, and receiver behavior remain
distinct hardware proof gates. The IDF 6 callback path was checked against its
public header, but its much larger GCC 15 toolchain could not be installed in
the available disk space, so `esp32s3-idf60` remains compile-unverified here.

The ESP-IDF lab itself fails closed if the default NVS partition cannot be
opened (for example after an incompatible on-flash NVS version). It reports the
error and aborts before Wi-Fi initialization; under the checked panic setting,
the board then reboots. It never performs the common automatic
`nvs_flash_erase()` fallback. The Arduino core starts before sketch `setup()`
and some versions can format incompatible NVS automatically, outside the
sketch's control. Uploading either build also overwrites application flash.
Back up the attached board or use one whose stored application/settings are
disposable before flashing.

## Why the separate offline labs exist

- [`../labs/frame-anatomy`](../labs/frame-anatomy) constructs and decodes the
  same 802.11 fields without opening any radio interface.
- [`../labs/xtensa-link-lab`](../labs/xtensa-link-lab) reconstructs Marauder's
  duplicate-symbol/link-order trick with harmless stand-in functions and the
  real Xtensa toolchain.

Those two experiments let you understand packet layout, C linkage, archive
extraction, the windowed Xtensa ABI, and linker resolution independently before
adding RF and timing to the problem.
