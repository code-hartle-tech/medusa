/*
 * medusa_probeflood — research-only probe-handling load harness.
 *
 * The prototype varies locally administered source identities and optional test
 * SSIDs to exercise an owned lab receiver's probe-processing and audit path. A
 * driver return is not evidence of receiver behavior.
 *
 * It is excluded from the default product until the firmware-enforced scope,
 * rate, duration, audit, confirmation, and stop controls in the threat model
 * exist and have physical tests.
 */
#include <Arduino.h>
#include <WiFi.h>
#include <string.h>
#include "esp_wifi.h"
#include "medusa_tx_common.h"

#if defined(__has_include)
#  if __has_include("medusa_target.h")
#    include "medusa_target.h"
#  endif
#endif

#ifndef TARGET_CHANNEL
#define TARGET_CHANNEL 0   /* 0 = hop 1/6/11 */
#endif
#ifndef TARGET_SSID
#define TARGET_SSID ""     /* "" = wildcard/random SSIDs */
#endif

static uint8_t pkt[96];
static const char CH[] = "abcdefghijklmnopqrstuvwxyz0123456789 _-";
static const char* directed = TARGET_SSID;

static int build_probe(const uint8_t* src, const char* ssid, int sl, uint8_t channel) {
  int n = 0;
  pkt[n++] = 0x40; pkt[n++] = 0x00;                 // FC: Probe Request
  pkt[n++] = 0x00; pkt[n++] = 0x00;                 // duration
  for (int i = 0; i < 6; i++) pkt[n++] = 0xFF;      // DA = broadcast
  for (int i = 0; i < 6; i++) pkt[n++] = src[i];    // SA = random
  for (int i = 0; i < 6; i++) pkt[n++] = 0xFF;      // BSSID = broadcast
  pkt[n++] = 0x00; pkt[n++] = 0x00;                 // sequence
  pkt[n++] = 0x00; pkt[n++] = sl;                   // SSID IE (directed or wildcard)
  for (int i = 0; i < sl; i++) pkt[n++] = ssid[i];
  pkt[n++] = 0x01; pkt[n++] = 0x08;                 // supported rates
  uint8_t rates[8] = { 0x82, 0x84, 0x8b, 0x96, 0x0c, 0x12, 0x18, 0x24 };
  for (int i = 0; i < 8; i++) pkt[n++] = rates[i];
  (void)channel;
  return n;
}


// ---- gated transmission -------------------------------------------------
// Every frame this sketch emits passes medusa_tx_send(), which consults the
// operator's session, scope, rate ceiling and latched stop before the radio
// is touched. Rate and burst come from configuration rather than a hardcoded
// delay(), so changing how hard this pushes needs no rebuild.
static bool tx_session_ready = false;
// Broadcast by construction: there is no unicast target to scope to, so the
// scope IS broadcast and the operator's broadcast confirmation governs it.
static const uint8_t tx_broadcast[MTG_MAC_LEN] = { 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF };

static void tx_session_start(const uint8_t *target) {
  mcfg_t cfg;
  mcfg_defaults(&cfg);           // replaced by the stored config once NVS is wired
  uint8_t allow[1][MTG_MAC_LEN];
  memcpy(allow[0], target, MTG_MAC_LEN);

  mtg_session_grant_t grant;
  cfg.allow_broadcast = true;   // this capability cannot be anything but broadcast
  tx_session_ready = medusa_tx_begin(&cfg, MCFG_PROFILE_PROBE, allow, 1, false, millis(), &grant);
  if (!tx_session_ready) {
    Serial.println("TX refused: no session (a stop may be latched, or scope is missing).");
    return;
  }
  // Report what was actually granted, not what was asked for.
  Serial.printf("TX session: %lu ms, %u frames/sec, burst %u, gap %u ms\n",
                (unsigned long)grant.duration_ms, (unsigned)grant.rate_per_sec,
                (unsigned)medusa_tx_burst(), (unsigned)medusa_tx_gap_ms());
}

void setup() {
  Serial.begin(115200);
  delay(600);
  Serial.println("\n=== medusa_probeflood ===");
  Serial.println("LAWFUL USE ONLY - your own space / authorized tests.");
  WiFi.mode(WIFI_STA);
  esp_wifi_start();
  esp_wifi_set_promiscuous(true);
  Serial.printf("probe flood -> ssid='%s' (%s), %s\n",
                directed, directed[0] ? "directed" : "wildcard/random",
                TARGET_CHANNEL ? "pinned channel" : "hopping 1/6/11");
  tx_session_start(tx_broadcast);
}

void loop() {
  static const uint8_t chans[3] = { 1, 6, 11 };
  static uint32_t sent = 0, last = 0;
  uint8_t ch = TARGET_CHANNEL ? (uint8_t)TARGET_CHANNEL : chans[esp_random() % 3];
  esp_wifi_set_channel(ch, WIFI_SECOND_CHAN_NONE);
  esp_err_t e = ESP_OK;
  char rnd[24];
  for (int a = 0; a < 16; a++) {
    uint8_t src[6];
    for (int i = 0; i < 6; i++) src[i] = esp_random() & 0xFF;
    src[0] = (src[0] & 0xFE) | 0x02;                // locally-administered unicast
    const char* ssid; int sl;
    if (directed[0]) { ssid = directed; sl = strlen(directed); if (sl > 32) sl = 32; }
    else { sl = 6 + (esp_random() % 10); for (int i = 0; i < sl; i++) rnd[i] = CH[esp_random() % (sizeof(CH) - 1)]; ssid = rnd; }
    int len = build_probe(src, ssid, sl, ch);
    e = medusa_tx_send(pkt, (size_t)len, tx_broadcast, millis());
    sent++;
  }
  if (millis() - last > 500) {
    last = millis();
    Serial.printf("probe flood rc=0x%x sent=%u ch=%d  %s\n",
                  e, sent, ch, e == 0x102 ? "(BLOCKED - stock core)" : "(flooding)");
  }
  delay(medusa_tx_pacing_ms());
}
