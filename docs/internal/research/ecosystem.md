# Medusa — Prior Art Ecosystem (seed; expand in M1)

Status: **seed**. M1 researcher fills in license + scope details and
identifies what Medusa adds on top.

## Pentest tools targeting ESP32

| Project | What it does | License | Notes for Medusa |
|---|---|---|---|
| **ESP32 Marauder** | WiFi + BLE recon framework on ESP32 family; large user base | GPL-3.0 | Architectural precedent; we won't fork (license mismatch with Apache 2.0) but can study the radio-handling patterns |
| **Bruce** | Multi-tool firmware for M5StickC + ESP32-S3 hardware variants | GPL-3.0 | Similar license caveat. Useful for hardware variant ergonomics. |
| **ESP32-Hashmon** | Captures + parses WPA EAPOL handshakes | various | Out of Medusa scope (handshake capture itself is borderline; brute-force is explicit out-of-scope) |
| **Pwnagotchi-S3 forks** | Off-the-shelf wardriving tool ports | various | Different threat model (wardriving / network-cracking); out of Medusa lane |
| **Flipper Zero** (ecosystem) | Multi-protocol pocket tool (RF, NFC, GPIO); not ESP32-based | varied | Same UX category as Medusa-+-companion-phone — useful UX reference even though hardware differs entirely |

## What Medusa adds that the above don't

1. **Phone-case form factor** — the case wraps the user's daily-carry
   phone. Marauder/Bruce typically live in dedicated handhelds. The
   case form factor changes who carries it (anyone who wants a phone
   case) and where (everywhere a phone goes). Privacy + branding +
   legal-frame implications follow.
2. **Defensive-only stance, hard-coded** — Marauder/Bruce default to
   "all features enabled including aggressive offensive ones". Medusa
   intentionally ships without active deauth, evil-twin, or credential
   harvesting; downstream forks add those if desired.
3. **Tight companion-app integration** — Marauder uses an on-device
   tiny screen; we offload UI to NearTrace on the phone the case wraps.
4. **HARTLE.TECH brand house style** — sibling to Nosferato and
   NearTrace; same legal posture + Apache 2.0 + lawful-use clause.

## Tools we DEPEND on (or might)

| Tool | Role |
|---|---|
| ESP-IDF (Espressif) | Firmware framework |
| PlatformIO | Build orchestration |
| FreeRTOS | RTOS (bundled with IDF) |
| Wireshark | Pcap inspection downstream (operator-side, on phone export) |
| KiCad (or Horizon / Fritzing for sketches) | PCB design |
| OpenSCAD or FreeCAD | Case 3D modeling |

## Tools we explicitly DON'T integrate

- **hashcat, aircrack-ng, hcxtools**: handshake / WEP / brute-force
  toolchain. Out of scope (see `design/threat-model.md`).
- **Bettercap / wifiphisher**: active attack frameworks. Out of scope.

## Communities to engage (later, not bootstrap)

- /r/AskNetsec, /r/HowToHack — but only for defensive-framed Q&A
- Espressif forum + GitHub discussions
- BSides regional meetups (HARTLE.TECH is Porto-based; BSides Lisbon is
  the obvious first venue)
- Defcon villages (Wireless village, Hardware village) — once Medusa
  has a stable v1.0

## What M1 fills in

- License audit on each cited project
- Active maintenance status (last commit dates)
- One-paragraph summary per project
- Explicit "what Medusa adds" pull-quote for the README
