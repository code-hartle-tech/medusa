/*
 * medusa_monitor — passive traffic monitor + signal meter.
 *
 * Promiscuous on a pinned channel; every second reports frame counts by type
 * (mgmt/data/ctrl) plus beacons + deauths, and — if TARGET_BSSID is set — the
 * strongest RSSI seen for that BSSID (a find-a-device signal meter). Emits:
 *     MON <TAB> ch <TAB> mgmt <TAB> data <TAB> ctrl <TAB> beacons <TAB> deauth <TAB> target_rssi
 *
 * PASSIVE — no TX, no core patch. LAWFUL USE. Channel/BSSID via medusa_target.h.
 */
#include <Arduino.h>
#include <WiFi.h>
#include "esp_wifi.h"

#if defined(__has_include)
#  if __has_include("medusa_target.h")
#    include "medusa_target.h"
#  endif
#endif
#ifndef TARGET_CHANNEL
#define TARGET_CHANNEL 1
#endif
#ifndef TARGET_BSSID
#define TARGET_BSSID { 0, 0, 0, 0, 0, 0 }
#endif

static uint8_t bssid[6] = TARGET_BSSID;
static volatile uint32_t cMgmt = 0, cData = 0, cCtrl = 0, cBeacon = 0, cDeauth = 0;
static volatile int tgtRssi = -128;
static bool radioReady = false;

static bool haveTarget() { for (int i = 0; i < 6; i++) if (bssid[i]) return true; return false; }

static void onRx(void* buf, wifi_promiscuous_pkt_type_t type) {
  const wifi_promiscuous_pkt_t* p = (const wifi_promiscuous_pkt_t*)buf;
  int len = p->rx_ctrl.sig_len;
  if (len < 2) return;
  const uint8_t* d = p->payload;
  uint8_t ft = (d[0] >> 2) & 0x3, st = (d[0] >> 4) & 0xF;
  if (ft == 0) { cMgmt++; if (st == 8) cBeacon++; else if (st == 12) cDeauth++; }
  else if (ft == 2) cData++;
  else cCtrl++;
  if (haveTarget() && len >= 22) {
    const uint8_t* a2 = d + 10; const uint8_t* a3 = d + 16;
    if (!memcmp(a2, bssid, 6) || !memcmp(a3, bssid, 6))
      if (p->rx_ctrl.rssi > tgtRssi) tgtRssi = p->rx_ctrl.rssi;
  }
}

void setup() {
  Serial.begin(115200);
  delay(600);
  Serial.println("\n=== medusa_monitor ===");
  Serial.println("PASSIVE monitor (no TX). LAWFUL USE ONLY.");
  if (!WiFi.mode(WIFI_STA)) {
    Serial.println("MONITOR_ERROR\twifi-mode");
    return;
  }
  esp_err_t err = esp_wifi_set_promiscuous_rx_cb(&onRx);
  if (err == ESP_OK)
    err = esp_wifi_set_channel(TARGET_CHANNEL, WIFI_SECOND_CHAN_NONE);
  if (err == ESP_OK) err = esp_wifi_set_promiscuous(true);
  if (err != ESP_OK) {
    esp_wifi_set_promiscuous(false);
    Serial.printf("MONITOR_ERROR\tinit-rc=0x%X\n", (unsigned int)err);
    return;
  }
  radioReady = true;
  Serial.printf("monitoring ch %d%s\n", TARGET_CHANNEL, haveTarget() ? " (+ signal meter)" : "");
}

void loop() {
  if (!radioReady) { delay(1000); return; }
  static uint32_t last = 0;
  if (millis() - last >= 1000) {
    last = millis();
    Serial.printf("MON\t%d\t%u\t%u\t%u\t%u\t%u\t%d\n",
                  TARGET_CHANNEL, cMgmt, cData, cCtrl, cBeacon, cDeauth, tgtRssi);
    cMgmt = cData = cCtrl = cBeacon = cDeauth = 0;
    tgtRssi = -128;
  }
  delay(20);
}
