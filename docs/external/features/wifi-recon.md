# Wi-Fi discovery

Medusa's default product separates three different radio behaviors instead of
calling all of them "passive":

| Path | Radio behavior | Current proof |
|---|---|---|
| **Unattended beacon inventory** | Receive-only beacon collection; no association, raw-frame transmission, or upload | Current v3 compiles for C3/S3 and is host-tested; an earlier build booted fail-closed on C3, while exact-v3 boot, storage, capture, retrieval, and power-cycle proof remain pending |
| **Nearby-network survey** | Ordinary active Wi-Fi scan; may send probe requests; does not associate | The confirmed replacement control is available in the local web app and C3/S3 firmware compiles; a live survey run is still pending |
| **Monitor/research prototypes** | Promiscuous capture and separately gated raw-frame research | Not exposed by the default product and not claimed as release capabilities |

## What the current survey shows

The nearby-network survey lists SSID, BSSID, channel, and signal strength. The
unattended logger keeps a
bounded, deduplicated inventory of beaconing access points in local LittleFS and
supports validated, non-clearing USB retrieval.

These identifiers can be personal or sensitive data. Use the survey only in an
area covered by your authorization, keep identifiers masked on screen when
appropriate, and define retention before exporting an artifact. Display masking
does not redact the device database or downloaded CSV.

## Active-transmission research

The source tree includes driver-boundary research for isolated, owned lab
hardware. It is disabled in the default product. It cannot become a release
capability until firmware-enforced scope, allow-list, rate, duration, audit,
confirmation, and persistent-stop controls exist and are verified. A server
environment flag is not a substitute for those controls.

## What it does not ship

- Evil-twin access-point impersonation
- Captive-portal credential collection
- Automated password recovery
- Broadband jamming
- Covert or bulk remote export

See [Capabilities](/features/) and the public
[threat model](https://github.com/code-hartle-tech/medusa/blob/develop/wiki/design/threat-model.md)
for the current boundary.
