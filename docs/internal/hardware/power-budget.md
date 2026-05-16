# Medusa — Power Budget (seed; refine in M2)

Status: **seed**. Numbers below are from datasheet typicals; refine on
the actual prototype with a current-clamp.

## Operating modes + draw

| Mode | Avg current (mA) | Notes |
|---:|---:|---|
| Active WiFi sniff (monitor mode, 802.11b/g/n 2.4 GHz, no TX) | 200-260 | dominated by RX-on duty cycle |
| Active BLE scan | 25-50 | depends on scan-window / scan-interval ratio |
| ESP-NOW peer link, idle (advertising-equivalent) | 10-25 | very low-duty TX |
| Companion-app BLE link, idle | 5-15 | bonded peer, conn interval ~50 ms |
| Idle (no active sub-tasks, BLE adv only) | 2-8 | LED off, RTOS tick at minimum |
| Deep sleep (button-wake or RTC-wake) | 0.01-0.05 | µA range; multi-day battery life possible |

## Battery sizing scenarios

Pick a target use-case, then size battery.

### Scenario A — pocket carry, intermittent audits

- 4 × 10-minute WiFi sniffs across a day (avg 230 mA × 10 min × 4 = ~150 mAh)
- 8 hours BLE link idle (avg 10 mA × 8h = 80 mAh)
- 16 hours deep sleep (~0)
- **Daily ~230 mAh used**

→ 500 mAh battery = 2-day comfort. ✅ for slimline case.

### Scenario B — sustained sniff session

- 2 hours continuous WiFi sniff (230 mA × 2 = 460 mAh)
- Companion app live for the duration (+~10 mA = +20 mAh)

→ **One session uses ~480 mAh.** A 500 mAh battery covers exactly one
session; a 1000 mAh battery gives ~2 sessions + headroom.

### Scenario C — mesh deployment (multiple Medusa)

Multiple cases passively cover an area:
- Each in low-duty WiFi sniff cycle (e.g. 30s on / 30s off → avg ~115 mA)
- ESP-NOW link to siblings (+~15 mA)
- ~130 mA average

500 mAh → ~4 hours coverage per unit. 1000 mAh → ~8 hours.

## Recommendation

For v1 prototype: **700 mAh** battery (compromise between case
slimness and audit duration).

Future v2 could explore:
- USB-C passthrough draw → augment runtime when the phone is plugged
  into a power source
- Optimised channel hopping (don't dwell on dead channels)
- Wake-on-radio for sleepy default with motion/voltage triggers

## Thermal

ESP32-S3-WROOM-1 active WiFi: package surface ~45 °C in still air at
21 °C ambient. Inside a phone case (thermal floor restricted), expect
~55-65 °C package, possibly hotter against the phone back. Adding a
thermal pad to vent heat into the phone's metal back is one mitigation;
another is duty-cycling sustained sniffs to limit thermal time.

**Action item M2**: thermal mockup with a 3D-printed case shell, a
representative phone-back analog, and an IR camera.

## Charging

TP4056 (or similar) at 500 mA charge rate. From 0% to 100% on a 700 mAh
cell: ~1.5 hours. Charger draws from the USB-C passthrough — Medusa
charges when the phone charges, no separate cable needed.
