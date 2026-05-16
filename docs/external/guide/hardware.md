# Hardware overview

The Medusa case is built around an **ESP32-S3-WROOM-1 module** — Espressif's Xtensa LX7 dual-core MCU with WiFi 2.4 GHz, BLE 5, USB-OTG, and onboard PCB antenna.

## Why ESP32-S3

- **WiFi monitor mode** is well-supported via ESP-IDF + the broader pentest ecosystem
- **BLE 5** native (no companion chip needed)
- **USB-OTG** opens the door to HID class (BadUSB-style audit techniques) without an external controller
- **Pentest community** (Marauder, Bruce, Pwnagotchi-S3 ports) targets ESP32-S3 — code patterns + library compatibility are mature
- **Cost** — ~€3-5 per module in single quantities

Backup options for capabilities ESP32-S3 can't deliver: Pi Pico 2 W (CYW43439 radio). ESP8266 NodeMCU and XIAO-RP2040 are documented as fallbacks but in display / companion-MCU roles only.

## Bill of materials (draft)

The [internal BOM](https://github.com/code-hartle-tech/medusa/blob/develop/docs/internal/hardware/bom.md) has the per-part details. Summary:

| Bucket | Per-unit prototype cost (USD) |
|---|---:|
| ESP32-S3-WROOM-1 module | $3–5 |
| Charging (TP4056) + 700 mAh lipo + protection | $4–8 |
| LED, button, connectors | $1–2 |
| Custom PCB (JLCPCB hobby qty 1) | $3–6 |
| 3D-printed case (PETG / nylon) | $2–6 |
| **Total** | **~$13–27** |

Mass-production unit cost (qty 100+) drops considerably; the prototype range above assumes JLCPCB single-quantity + retail module pricing.

## Form factor (v1 target)

iPhone 13 / iPhone 14 size class (146.7 × 71.5 × 7.65 mm). Case adds ~4 mm thickness for the PCB + battery + USB-C passthrough cutout. Single tactile button on the edge; RGB LED visible through a pinhole on the back.

Other phone models will need their own case variants — the firmware stays the same; only the case shell changes per model.

## Power budget

ESP32-S3 in WiFi promiscuous mode draws ~200-260 mA. A 700 mAh battery gives roughly:

- **~3 hours** of continuous WiFi sniff
- **~1 day** of typical pocket-carry with intermittent ~10-min audit sessions
- **Multiple days** of deep-sleep idle (~10-50 µA)

USB-C passthrough means Medusa charges whenever the phone charges — no separate cable needed.

## What we're not building

- No external antenna (U.FL / SMA) in v1 — the module's PCB antenna is sufficient for audit-range work
- No display on the case — the phone is the UI
- No GPS module — privacy + the case form factor is busy enough already
- No sub-GHz radio (915 MHz / 433 MHz) — v2+ if there's demand

## Source of truth

The internal hardware notes (BOM with part links, power budget detail, form-factor CAD plans, antenna clearance rules) live in the [tailnet-only wiki](/wiki/hardware/). The summary above is the public-friendly distillation; specific supplier links + thermal mockup notes are operator-only.
