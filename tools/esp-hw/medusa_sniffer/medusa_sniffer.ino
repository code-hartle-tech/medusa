/*
 * medusa_sniffer — passive 802.11 capture → serial → PCAP (the lab writes the file).
 *
 * Promiscuous capture on a pinned channel. Keeps all management frames plus data
 * frames that carry EAPOL (LLC/SNAP ethertype 0x888E) — the foundation for PMKID /
 * 4-way handshake capture and for PCAP export. Rate-limited to fit the serial link;
 * EAPOL frames are never dropped. Each frame is emitted as:
 *     PKT <TAB> rssi <TAB> len <TAB> hex
 * plus a periodic  STAT <TAB> seen=.. sent=.. dropped=..
 *
 * PASSIVE — never transmits, so it does NOT need the patched core. LAWFUL USE ONLY.
 * Target channel via medusa_target.h (TARGET_CHANNEL).
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

#define SLOTS 16
#define SLOT_LEN 256
struct Rec { int16_t rssi; uint16_t len; uint8_t data[SLOT_LEN]; };
static Rec ring[SLOTS];
static volatile uint32_t head = 0, tail = 0;   // head=write, tail=read
static volatile uint32_t seen = 0, sent = 0, dropped = 0;
static uint32_t winStart = 0, winCount = 0;
static bool radioReady = false;
static const uint32_t MAX_PER_SEC = 30;
static const char HEXC[] = "0123456789abcdef";

static void onRx(void* buf, wifi_promiscuous_pkt_type_t type) {
  const wifi_promiscuous_pkt_t* p = (const wifi_promiscuous_pkt_t*)buf;
  int len = p->rx_ctrl.sig_len;
  if (len < 10) return;
  const uint8_t* d = p->payload;
  uint8_t ftype = (d[0] >> 2) & 0x3;             // 0 mgmt, 1 ctrl, 2 data
  bool eapol = false;
  if (ftype == 2) {                              // keep data frames only if EAPOL
    for (int i = 24; i + 1 < len && i <= 40; i++)
      if (d[i] == 0x88 && d[i + 1] == 0x8e) { eapol = true; break; }
    if (!eapol) return;
  } else if (ftype != 0) {
    return;                                       // drop control frames
  }
  seen++;
  uint32_t now = millis();
  if (now - winStart >= 1000) { winStart = now; winCount = 0; }
  if (!eapol) { if (winCount >= MAX_PER_SEC) { dropped++; return; } winCount++; }
  uint32_t nh = (head + 1) % SLOTS;
  if (nh == tail) { dropped++; return; }          // ring full
  int n = len > SLOT_LEN ? SLOT_LEN : len;
  ring[head].rssi = p->rx_ctrl.rssi;
  ring[head].len = n;
  memcpy(ring[head].data, d, n);
  head = nh;
}

void setup() {
  Serial.begin(115200);
  delay(600);
  Serial.println("\n=== medusa_sniffer ===");
  Serial.println("PASSIVE capture (no TX). LAWFUL USE ONLY.");
  if (!WiFi.mode(WIFI_STA)) {
    Serial.println("SNIFFER_ERROR\twifi-mode");
    return;
  }
  esp_err_t err = esp_wifi_set_promiscuous_rx_cb(&onRx);
  if (err == ESP_OK)
    err = esp_wifi_set_channel(TARGET_CHANNEL, WIFI_SECOND_CHAN_NONE);
  if (err == ESP_OK) err = esp_wifi_set_promiscuous(true);
  if (err != ESP_OK) {
    esp_wifi_set_promiscuous(false);
    Serial.printf("SNIFFER_ERROR\tinit-rc=0x%X\n", (unsigned int)err);
    return;
  }
  radioReady = true;
  Serial.printf("sniffing ch %d (mgmt + EAPOL)\n", TARGET_CHANNEL);
}

void loop() {
  if (!radioReady) { delay(1000); return; }
  static char line[SLOT_LEN * 2 + 40];
  while (tail != head) {
    Rec* r = &ring[tail];
    int n = snprintf(line, sizeof(line), "PKT\t%d\t%u\t", r->rssi, r->len);
    for (int i = 0; i < r->len; i++) { line[n++] = HEXC[r->data[i] >> 4]; line[n++] = HEXC[r->data[i] & 0xf]; }
    line[n] = 0;
    Serial.println(line);
    sent++;
    tail = (tail + 1) % SLOTS;
  }
  static uint32_t last = 0;
  if (millis() - last > 2000) { last = millis(); Serial.printf("STAT\tseen=%u sent=%u dropped=%u\n", seen, sent, dropped); }
  delay(20);
}
