# Medusa — Companion API Spec (seed; expand in M2)

Status: **seed**. M2 architect agent expands per-operation schema +
versioning + error handling.

## Transport

- **BLE GATT** primary
- Service UUID: TBD (custom 128-bit, allocated in M2)
- Characteristics:
  - `medusa.cmd` (write-without-response) — phone → case, JSON ops
  - `medusa.evt` (notify) — case → phone, JSON events
- ATT_MTU: target 247; auto-fragment for smaller peers
- Pairing: BLE bonded, Just Works (v1); LE Secure Connections required

## Envelope

Every message is a JSON object with at minimum:

```json
{ "v": 1, "id": "<correlation-uuid>", "ts": "<iso8601>", "type": "...", ... }
```

- `v` — protocol version (currently 1)
- `id` — correlation ID; events emitted in response to a command echo
  the command's id
- `ts` — wall-clock timestamp from the phone (case has no RTC by
  default)
- `type` — `cmd` | `evt` | `err`

## Operations (v1 minimum set)

### `medusa.status.get`

Phone → Case. Returns current state.

Response (`type=evt`):

```json
{
  "battery_pct": 78,
  "free_kb": 1024,
  "current_op": "idle",
  "fw_version": "v0.1.0-bootstrap",
  "paired_centrals": 1
}
```

### `medusa.wifi.sniff.start`

Phone → Case. Begin passive 802.11 capture.

Args:

```json
{
  "channels": [1, 6, 11],     // 2.4 GHz channels; hop through them
  "duration_s": 60,           // 0 = until stop
  "filter": {                 // optional filter
    "frame_types": ["beacon", "probe_req"],
    "min_rssi": -85
  }
}
```

Emits `medusa.wifi.sniff.progress` every 1 s; `medusa.wifi.sniff.done`
on completion.

### `medusa.wifi.sniff.stop`

Phone → Case. Stop in-progress capture. Emits `medusa.wifi.sniff.done`.

### `medusa.ble.scan.start`

Phone → Case. Begin BLE advertisement scan.

Args:

```json
{
  "duration_s": 30,
  "active": false,    // active = send SCAN_REQ (out-of-scope by default)
  "filter": {
    "min_rssi": -90,
    "name_contains": null
  }
}
```

Emits `medusa.ble.scan.adv` per advertisement; `medusa.ble.scan.done`
on completion.

### `medusa.captures.list`

Phone → Case. List stored captures.

Response:

```json
{
  "captures": [
    {
      "id": "2026-05-16T03Z",
      "type": "wifi.sniff",
      "started_at": "2026-05-16T03:00:00Z",
      "duration_s": 60,
      "frame_count": 3092,
      "size_kb": 412
    }
  ]
}
```

### `medusa.captures.export`

Phone → Case. Stream a capture to the phone.

Args: `{ "id": "<capture-id>", "format": "json" | "pcap" }`

Response: paginated event stream — `medusa.captures.chunk` events with
base64-encoded chunks, then `medusa.captures.done`.

### `medusa.captures.delete`

Phone → Case. Delete a capture from local storage.

### `medusa.config.get` / `medusa.config.set`

Phone → Case. Read/write operator config (retention, default filters,
LED brightness, button bindings).

## Events the case emits unsolicited

- `medusa.system.ready` — emitted shortly after a BLE connection
  establishes; first thing the phone sees
- `medusa.system.lowbattery` — emitted at 15%, 5%, 1%
- `medusa.system.error` — non-fatal errors (sub-task failures); fatal
  errors disconnect

## Error envelope

```json
{ "v": 1, "id": "...", "ts": "...", "type": "err",
  "code": "wifi.channel.invalid", "message": "Channel 14 not allowed in this region" }
```

Errors carry a stable `code` (dot-separated); operators can match on
prefix for handling. Messages are human-readable but not load-bearing.

## Authorisation

- BLE bonded centrals only — non-bonded peers get nothing
- Optional operator PIN gate for capture export (set via config)
- Whitelist of paired centrals stored in NVS; `medusa.security.unpair`
  + factory reset are explicit ops

## Open

- [ ] Service + characteristic UUIDs (allocate in M2)
- [ ] Larger payload streaming pattern — chunk size, ack/nack, resume
- [ ] USB-CDC fallback envelope (same JSON, different transport)
- [ ] Versioning: how do we deprecate ops between v1 and v2?
- [ ] Multi-Medusa ESP-NOW mesh ops (peer discovery, etc.)
