# Medusa local radio lab

Medusa's web app is a local, hardware-backed workbench for inspecting nearby
radio signals, managing an attached ESP board, and reviewing evidence from
authorized surveys. The default product is passive-first: it exposes discovery,
the receive-only unattended Wi-Fi logger, local readback, and artifact review.
Dual-use active-research prototypes are not part of the default product.

## Run locally

From this directory:

```bash
./dev.sh
```

The script installs missing Node dependencies, starts the bridge on
`http://127.0.0.1:4300`, and starts the web app on
`http://localhost:5250`. To run the processes separately:

```bash
cd server && npm install && node server.js
cd client && npm install && npm run dev
```

The bridge drives [`../esp-hw/esp_hw.py`](../esp-hw/esp_hw.py). Hardware actions
can replace firmware on the attached board; read the confirmation and proof
state in the UI before proceeding.

## Default product surface

[`client/src/product/ProductApp.tsx`](client/src/product/ProductApp.tsx) is the
default web surface. It is organized around five stable destinations:

| Destination | Purpose |
|---|---|
| **Home** | Shows bridge, board, current-run, observation, and artifact state for this browser session. |
| **Observe** | Reviews Wi-Fi and Bluetooth discoveries with search and optional on-screen identifier masking. |
| **Runs** | Starts discovery surveys, provisions the passive logger, retrieves its stored CSV over USB, or loads the local bridge inventory. |
| **Artifacts** | Lists evidence that actually exists in the current session and offers local downloads or exports. |
| **Devices** | Detects the attached board and separates detected, built, flashed, and storage-confirmed state. |

The web app is available now. Phone and watch entries shown under **Devices**
are product-direction notes, not shipped iOS, Android, or watch applications.

"Passive-first" does not mean every discovery mechanism is radio-silent. The
unattended Wi-Fi logger is beacon-receive-only. Short Wi-Fi and Bluetooth
discovery surveys can use their platforms' ordinary scan requests, but do not
connect to observed devices or expose disruptive assessment controls.

## Passive logger provisioning and readback

The **Install passive logger** run builds and flashes the stock-SDK unattended
firmware after the operator confirms control of the board. Flashing replaces the
board's current firmware. The logger is designed to:

- receive Wi-Fi beacons while hopping across 2.4 GHz channels;
- deduplicate observations into a bounded local inventory;
- store a checksummed database and CSV in LittleFS;
- avoid association, raw 802.11 transmission, network credentials, and upload;
- fail closed instead of formatting or clearing storage automatically.

Blank or unavailable LittleFS therefore produces a visible storage error and no
capture. Storage initialization is a separate, explicit console operation; the
product UI never formats storage. Clearing the device inventory is likewise a
separate explicit operation and is not part of USB readback.

**Retrieve over USB** asks the logger to flush its current state. In the normal
path it reads the committed CSV; in a fail-closed recovery state it reads a
source-labelled, read-only preserved CSV, database rendering, or frozen RAM
snapshot without claiming that source was committed. The bridge validates the
protocol, source, fields, and row count before staging
`medusa_unattended.csv` for local browser download. Readback never clears
recovery artifacts. If the logger reports capacity drops, the UI labels the
result partial rather than presenting it as complete.

See the [`esp-hw` README](../esp-hw/README.md) for the lower-level build and
retrieval contract.

## Evidence and proof levels

The UI deliberately keeps these states separate:

| State | What it proves | What it does not prove |
|---|---|---|
| **Available in the UI** | A control path is present. | That its firmware built or hardware behavior occurred. |
| **Compiled** | The selected toolchain produced firmware. | That a board was flashed, booted, or observed radio traffic. |
| **Host-tested** | Host protocol, validation, or parser tests passed. | Physical storage or radio behavior on a board. |
| **Flashed** | A flash operation completed on the detected board in this session. | Successful boot, persistence, capture, or receiver-visible behavior. |
| **Boot-tested** | Expected serial boot behavior was observed. | Durable storage, a completed survey, or power-cycle survival. |
| **Storage-confirmed** | The running firmware reported successful database and CSV writes. | Persistence across a later power cycle. |
| **Retrieved** | A device CSV passed the USB readback validation. | Completeness if the device reports capacity drops. |
| **Receiver-verified** | Independent receiver evidence observed the claimed RF behavior. | Broader interoperability beyond the tested setup. |

Current checkpoint (2026-08-21): the v3 unattended firmware compiles for
ESP32-C3 and ESP32-S3 and its host readback tests pass. An earlier build was
observed booting fail-closed on an ESP32-C3 with uninitialized LittleFS; the
exact v3 binary has not yet been booted. Initialization, capture, power-cycle
persistence, and physical USB retrieval remain separate proof gates. ESP32-C5
and C6 remain configuration-only until compatible local board cores are
installed and compile-tested.

## Local bridge and data boundary

The Node bridge in [`server/server.js`](server/server.js) is a privileged local
adapter, not a remote control service. It:

- binds to `127.0.0.1` only;
- origin-checks browser requests and requires a per-launch token for the
  privileged control socket; loopback health, token bootstrap, and fixed-name
  artifact-download routes remain deliberately narrow exceptions;
- invokes fixed Python programs with validated arguments and no shell;
- serializes hardware and serial-port ownership across browser tabs;
- leaves a build or flash to finish its cleanup if a tab disconnects;
- stages generated artifacts in the host's temporary directory;
- has no cloud upload path.

Browser-session state, temporary host files, device storage, and downloaded
files are different retention domains. Closing the browser does not erase the
logger or files already downloaded, and the lab does not currently enforce an
automatic retention schedule.

## Active-research boundary

The bridge reads the server-side `MEDUSA_ACTIVE_LAB` environment gate. Unless
its value is exactly `1`, guarded non-passive research builds and related
dual-use handlers are rejected. The default `ProductApp` does not mount those
controls even when the backend gate is enabled.

That gate is an additional research boundary, not a product-safety guarantee.
Active-transmission prototypes remain unavailable by default and do not qualify
as release capabilities until the firmware-enforced scope, allow-list, rate,
duration, audit, confirmation, and persistent-stop requirements in the
[`threat model`](../../wiki/design/threat-model.md) exist and are verified.

## Privacy, retention, and lawful use

Wi-Fi SSIDs and BSSIDs, Bluetooth names and addresses, and radio captures can be
personal or sensitive data. Use Medusa only on networks and devices you own,
administer, or have explicit written permission to assess.

- Identifier masking in the header changes only the on-screen presentation. It
  does not redact downloads, device storage, or bridge data.
- Exports are the copy that leaves you, so they carry their own disclosure
  control. **Pseudonymize identifiers** in Artifacts replaces network names and
  the device half of each hardware address before the file is written, names the
  artifact `*_pseudonymized_*`, and marks the posture report on its face. It
  defaults to whatever the header toggle currently says.
  - Vendor prefixes and every security finding are preserved, so the report is
    still worth reading.
  - The replacement is salted per export: the same network does not map to the
    same pseudonym in a second report, so two exports cannot be correlated.
  - Your local inventory keeps the real values. Only the artifact is redacted.
  - This is pseudonymisation, not anonymisation. Anyone holding the original
    capture can re-derive the mapping; it protects the file you hand over, not
    the capture it came from.
- Keep only the minimum evidence needed for the authorized purpose and define a
  retention period before collecting it.
- Delete downloaded and temporary artifacts when that period ends, and clear
  device storage through the separate explicit maintenance path when required.
- Do not treat loopback binding as anonymization; local evidence still contains
  the identifiers collected by the radio.
- No cloud upload is enabled by default. Any future wireless companion transfer
  must use authenticated, bonded pairing rather than an open management link.

The repository's lawful-use and attribution terms are in the
[`NOTICE`](../../NOTICE). The project is licensed under Apache-2.0; `NOTICE` is
the source for Medusa's project-specific lawful-use statement, while the Apache
License separately defines notice-preservation obligations for derivative works.

## Code map

- `client/src/product/` — the responsive product shell and visual system.
- `client/src/lib/lab.tsx` — socket state, request ownership, and proof state.
- `server/server.js` — loopback bridge, validation, hardware serialization, and
  local artifact delivery.
- `../esp-hw/` — firmware builds, board detection and flashing, serial helpers,
  and unattended-readback validation.
