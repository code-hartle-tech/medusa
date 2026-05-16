# WiFi reconnaissance

**Passive** by default. Optional **active** transmission, gated behind firmware safety guards (per-session enable, BSSID allow-list, rate limit, audit log).

## What Medusa captures

In WiFi promiscuous mode on the 2.4 GHz band, the case captures any 802.11 frame within radio range. Beacons, probe requests, probe responses, data frames, management frames. RSSI, channel, source/destination MACs, sequence numbers.

Channel hopping is operator-configurable: a fixed channel (e.g. dwell on channel 6), or a round-robin across an operator-picked subset.

## What you'd learn from a sweep of your own home network

- **Probe requests from your devices** — your phone, laptop, watch broadcast SSIDs they've previously connected to. Cafés, hotels, employers — anyone in radio range can build a movement-history profile from probe requests alone. The defense: disable "auto-join known networks" or use MAC randomization (most modern OSes do this by default but not consistently).
- **Beacon-frame anomalies** — your own AP's beacon every ~100 ms. Anything else beaconing the same SSID is a rogue. Coffee-shop names you've connected to in the past, broadcast nearby, are a known phishing pattern.
- **Inactive-device chatter** — IoT devices keep talking even when nothing's actively using them. Audit what's broadcasting.

## What active transmission is for

Specifically for **auditing your own network's posture**:

- Confirm your AP has **802.11w (PMF)** enabled — a forged deauth from Medusa should be rejected by your client (you'll see no disconnect). If your client disconnects, PMF isn't fully on.
- Verify your client devices handle reconnect cycles cleanly under transient disconnect — same exercise.
- Reproduce techniques described in published security research on hardware you own.

The deep technical write-up of how the ESP32 WiFi library is patched to enable raw management-frame transmission lives in the [internal wiki](/wiki/research/marauder-deauth-patch) — paired with a [swarm-distilled cross-check](/wiki/research/marauder-deauth-patch-swarm) of the same material from a complementary angle.

## What it doesn't do

- No evil-twin AP impersonation
- No captive-portal credential harvesting
- No automated WPA handshake brute-force
- No active probing of devices you haven't allow-listed

Out-of-scope by design — see [Capabilities — out of scope](/features/#out-of-scope-will-not-ship).
