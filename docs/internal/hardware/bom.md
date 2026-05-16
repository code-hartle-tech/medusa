# Medusa — Bill of Materials (draft)

Status: **draft**, pending hardware-phase milestone (M2). Quantities and
sources TBD; this file is the shape, not the order list.

## Core

| Qty | Part | Notes |
|---:|---|---|
| 1 | **ESP32-S3-WROOM-1** module | 16 MB flash, 8 MB PSRAM variant preferred; integrated PCB antenna; Espressif first-party |
| 1 | USB-C connector (passthrough) | Phone charges through the case via this port |
| 1 | TP4056 (or equivalent) lipo charge controller | 1A current limit; protection circuit recommended |
| 1 | Lipo battery | 500-1000 mAh; flat profile for case-fit; protected cell |
| 1 | RGB status LED (WS2812 or similar) | Single addressable; gaze-amber + serpent-green visual hints |
| 1 | Tactile button | One control button on the case edge (BOOT/USER multi-function) |
| 0..1 | microSD slot | For pcap logging on capacity-heavy captures (optional in v1) |
| 1 | Custom PCB | Slimline; trace routing optimised for module antenna clearance |

## Mechanical

| Qty | Part | Notes |
|---:|---|---|
| 1 | Case shell (3D printed v1; injection-molded v2+) | Per phone-model variant. Initial target: iPhone 13/14 size class |
| 0..1 | Silicone bumper | Optional drop protection |
| 0..2 | M1.6 / M2 screws | Case-to-PCB fasteners |
| 0..1 | Thermal pad | Only if sustained sniffing pushes the WROOM-1 thermal envelope |

## Companion side (not in BOM, for clarity)

- A phone running **NearTrace** (or, later, a dedicated Medusa app).
  Talks to the case over BLE 5; future variant may use USB-CDC over
  the passthrough port.

## Estimated cost (per unit, prototype run, USD)

Numbers are speculative pending procurement research. Updated when M2
researcher hits the part suppliers (LCSC, JLCPCB, Digi-Key).

| Bucket | Low | High |
|---|---:|---:|
| ESP32-S3-WROOM-1 (8 MB PSRAM, qty 1) | 3.00 | 5.00 |
| Charging + battery + protection | 4.00 | 8.00 |
| LED + button + connectors | 1.00 | 2.00 |
| Custom PCB (qty 1, JLCPCB hobby) | 3.00 | 6.00 |
| 3D-printed case (PETG / nylon) | 2.00 | 6.00 |
| Assembly | (DIY) | (DIY) |
| **TOTAL (per unit, prototype)** | **~13.00** | **~27.00** |

## Power budget pointer

See `hardware/power-budget.md` (TBD M2). Quick anchor:
- ESP32-S3 in WiFi promiscuous mode: ~190-260 mA average
- BLE scan: ~20-50 mA
- Deep sleep idle: ~10-50 µA
- 700 mAh battery: ~3 hours active sniffing, days idle

## Why ESP32-S3 (vs alternatives)

See `research/ecosystem.md` (TBD M1) for the comparative analysis.
Pinned in `MISSION.md`; deviation only on hard blocker.
