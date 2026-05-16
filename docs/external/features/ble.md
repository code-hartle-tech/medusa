# BLE inventory

Bluetooth Low Energy 5, scanning mode. Captures advertisements and scan responses.

## Inventory in seconds

A 30-second BLE scan in a typical home environment surfaces:

- Your own bonded devices (phone, watch, headphones, fitness tracker)
- Smart-home BLE (door sensors, light bulbs, thermostats, locks)
- BLE beacons from passers-by (people walking past, neighbors' devices)
- Trackers (AirTags, Tile, Galaxy SmartTag) — known + unknown

Each captured advertisement carries: device address (sometimes randomized, sometimes not), RSSI, advertised services, manufacturer-specific data, sometimes a friendly name.

## What this tells you

- **Trackers in your space.** If you've ever wondered whether someone's AirTag is in your bag, a BLE scan will surface it. iOS will alert you about unknown AirTags traveling with you; Medusa lets you check actively.
- **Device leak surface.** Many BLE devices broadcast more than they need to. A smart bulb advertising its manufacturer string + service UUIDs is fingerprintable from across the room. Audit your own gear.
- **Service inventory.** What BLE services are devices on your network exposing? Some are well-known (e.g. Heart Rate Service `180D`), others are vendor-specific. Useful for asset inventory in a small office.

## Active scanning

Optional — Medusa can issue SCAN_REQ frames to coax additional data from devices that don't broadcast their full identity in the passive advertisement. **Off by default**, like every active-transmission feature, behind the same per-session operator confirmation.

## Companion-app surface

In the NearTrace Medusa panel, BLE scan results show as a card list:
- Device name (if advertised) or randomized MAC
- RSSI graph
- Advertised services + manufacturer data
- "Seen first / Seen last" timestamps across captures
- Filter by RSSI threshold, name substring, service UUID

Captures can be exported as JSON for downstream analysis, or pcap-format for Wireshark.
