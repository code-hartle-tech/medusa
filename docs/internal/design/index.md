---
title: Design
---

# 🏗 Design

System-level architecture, threat model, and companion-app protocol.

## Files in this section

| File | What it is |
|---|---|
| [architecture](./architecture) | Component layout (companion_link, wifi_sniffer, ble_scanner, espnow_link, storage, led_status, button_input, config). ESP-IDF + FreeRTOS stack. Open questions. |
| [threat-model](./threat-model) | Operator safety guards (per-session enable, BSSID allow-list, rate limit, audit log, broadcast-confirm). In-scope capabilities. Out-of-scope by design. Reviewer-pass checklist. |
| [api-spec](./api-spec) | BLE GATT service + characteristic UUIDs. JSON envelope. v1 op set (status, wifi sniff start/stop, ble scan, captures list/export/delete, config). Error codes. |
