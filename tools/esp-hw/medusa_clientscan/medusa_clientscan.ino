/*
 * medusa_clientscan — list stations (clients) associated with a target AP.
 *
 * Sniffs data frames on the target channel and, for frames to/from the target
 * BSSID, records the other address (the client). Output:
 *   CLIENT\t<mac>\t<rssi>\t<packets>
 * bracketed by versioned, count-checked CLIENTS_BEGIN / CLIENTS_END markers.
 * Needs medusa_target.h with
 * TARGET_BSSID + TARGET_CHANNEL (written by esp_hw.py scan-clients).
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
#define TARGET_CHANNEL 0
#endif
#ifndef TARGET_BSSID
#define TARGET_BSSID { 0, 0, 0, 0, 0, 0 }
#endif

static const uint8_t target[6] = TARGET_BSSID;

typedef struct { uint8_t mac[6]; int8_t rssi; uint16_t n; } sta_t;
#define MAXSTA 48
static sta_t stas[MAXSTA];
static int nsta = 0;

static void add(const uint8_t* m, int8_t rssi) {
  if (m[0] & 1) return;                          // skip multicast/broadcast
  for (int i = 0; i < nsta; i++) if (!memcmp(stas[i].mac, m, 6)) { stas[i].n++; stas[i].rssi = rssi; return; }
  if (nsta < MAXSTA) { memcpy(stas[nsta].mac, m, 6); stas[nsta].rssi = rssi; stas[nsta].n = 1; nsta++; }
}

static void cb(void* buf, wifi_promiscuous_pkt_type_t t) {
  if (t != WIFI_PKT_DATA) return;
  wifi_promiscuous_pkt_t* pk = (wifi_promiscuous_pkt_t*)buf;
  const uint8_t* p = pk->payload;
  if (pk->rx_ctrl.sig_len < 24) return;
  uint8_t fc1 = p[1];
  bool toDS = fc1 & 0x01, fromDS = fc1 & 0x02;
  const uint8_t *a1 = p + 4, *a2 = p + 10;
  const uint8_t *bss = NULL, *sta = NULL;
  if (toDS && !fromDS) { bss = a1; sta = a2; }
  else if (!toDS && fromDS) { bss = a2; sta = a1; }
  else return;
  if (memcmp(bss, target, 6) != 0) return;
  add(sta, pk->rx_ctrl.rssi);
}

void setup() {
  Serial.begin(115200); delay(600);
  if (!WiFi.mode(WIFI_STA)) {
    Serial.println("CLIENTS_END\t2\tERROR\twifi-mode");
    return;
  }
  esp_err_t scan_err = esp_wifi_set_promiscuous_rx_cb(&cb);
  if (scan_err == ESP_OK)
    scan_err = esp_wifi_set_channel(TARGET_CHANNEL ? TARGET_CHANNEL : 1,
                                    WIFI_SECOND_CHAN_NONE);
  if (scan_err == ESP_OK) scan_err = esp_wifi_set_promiscuous(true);
  if (scan_err != ESP_OK) {
    esp_wifi_set_promiscuous(false);
    Serial.printf("CLIENTS_END\t2\tERROR\tinit-rc-0x%X\n", (unsigned int)scan_err);
    return;
  }
  Serial.println("CLIENTS_BEGIN\t2");
  delay(7000);                                   // dwell and collect
  const esp_err_t disable_err = esp_wifi_set_promiscuous(false);
  if (disable_err != ESP_OK) {
    esp_wifi_stop();
    Serial.printf("CLIENTS_END\t2\tERROR\tdisable-rc-0x%X\n",
                  (unsigned int)disable_err);
    return;
  }
  for (int i = 0; i < nsta; i++) {
    char mac[18];
    snprintf(mac, sizeof(mac), "%02X:%02X:%02X:%02X:%02X:%02X",
             stas[i].mac[0], stas[i].mac[1], stas[i].mac[2], stas[i].mac[3], stas[i].mac[4], stas[i].mac[5]);
    Serial.printf("CLIENT\t%s\t%d\t%u\n", mac, stas[i].rssi, stas[i].n);
  }
  Serial.printf("CLIENTS_END\t2\tOK\t%d\n", nsta);
}

void loop() { delay(2000); }
