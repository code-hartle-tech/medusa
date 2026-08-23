/*
 * medusa_recon — read each nearby AP's real security capabilities off its beacon.
 *
 * Sniffs 802.11 beacons/probe-responses in promiscuous mode and parses the RSN
 * Information Element to report, per BSSID:
 *   auth (OPEN/WEP/WPA1/WPA2-PSK/WPA3-SAE/WPA2+WPA3), pairwise cipher,
 *   PMF (none / capable / required),
 *   WPS present.
 *
 * Output (one line per AP):
 *   RECON2\t<bssid>\t<ch>\t<rssi>\t<auth>\t<pmf>\t<wps|->\t<cipher>\t<ssid-hex>
 *
 * If medusa_target.h defines TARGET_CHANNEL it dwells there; else it sweeps 1..13.
 */
#include <Arduino.h>
#include <WiFi.h>
#include "esp_wifi.h"

#if defined(__has_include)
#  if __has_include("medusa_target.h")
#    include "medusa_target.h"
#  endif
#endif

typedef struct {
  uint8_t bssid[6]; uint8_t ssid[32], ssid_len; int8_t rssi; uint8_t ch;
  bool seen, privacy, rsn, wpa1, sae, psk, mfpc, mfpr, wps, ccmp, tkip;
} apinfo_t;

#define MAXAP 40
static apinfo_t aps[MAXAP];
static int nap = 0;

static apinfo_t* find(const uint8_t* b) {
  for (int i = 0; i < nap; i++) if (!memcmp(aps[i].bssid, b, 6)) return &aps[i];
  if (nap < MAXAP) { memset(&aps[nap], 0, sizeof(apinfo_t)); memcpy(aps[nap].bssid, b, 6); return &aps[nap++]; }
  return NULL;
}

static void parseRSN(const uint8_t* d, int tlen, apinfo_t* a) {
  a->rsn = true;
  int j = 2;                 // skip version
  if (j + 4 > tlen) return;
  j += 4;                    // group cipher suite
  if (j + 2 > tlen) return;
  int pc = d[j] | (d[j + 1] << 8); j += 2;
  for (int k = 0; k < pc && j + 4 <= tlen; k++) { if (d[j + 3] == 4) a->ccmp = true; if (d[j + 3] == 2) a->tkip = true; j += 4; }
  if (j + 2 > tlen) return;
  int ac = d[j] | (d[j + 1] << 8); j += 2;
  for (int k = 0; k < ac && j + 4 <= tlen; k++) { uint8_t s = d[j + 3]; if (s == 8 || s == 9) a->sae = true; if (s == 2 || s == 6) a->psk = true; j += 4; }
  if (j + 2 <= tlen) { uint16_t cap = d[j] | (d[j + 1] << 8); a->mfpr = (cap >> 6) & 1; a->mfpc = (cap >> 7) & 1; }
}

static void onFrame(const uint8_t* p, int len, int8_t rssi, uint8_t ch) {
  if (len < 38) return;
  uint8_t fc0 = p[0]; uint8_t type = (fc0 >> 2) & 3, subtype = (fc0 >> 4) & 0xF;
  if (type != 0 || (subtype != 8 && subtype != 5)) return;   // beacon / probe-resp only
  apinfo_t* a = find(p + 16);                                // addr3 = BSSID
  if (!a) return;
  a->rssi = rssi; a->ch = ch; a->seen = true;
  uint16_t capabilities = p[34] | (p[35] << 8);
  a->privacy = (capabilities & 0x0010) != 0;
  int i = 36;                                                // 24 hdr + 12 fixed
  while (i + 2 <= len) {
    uint8_t tag = p[i], tlen = p[i + 1]; const uint8_t* d = p + i + 2;
    if (i + 2 + tlen > len) break;
    if (tag == 0) { a->ssid_len = tlen > 32 ? 32 : tlen; memcpy(a->ssid, d, a->ssid_len); }
    else if (tag == 48) parseRSN(d, tlen, a);
    else if (tag == 221 && tlen >= 4 && d[0] == 0x00 && d[1] == 0x50 && d[2] == 0xF2) {
      if (d[3] == 0x04) a->wps = true;
      if (d[3] == 0x01) a->wpa1 = true;
    }
    i += 2 + tlen;
  }
}

static void cb(void* buf, wifi_promiscuous_pkt_type_t t) {
  if (t != WIFI_PKT_MGMT) return;
  wifi_promiscuous_pkt_t* pk = (wifi_promiscuous_pkt_t*)buf;
  uint8_t ch; wifi_second_chan_t sc; esp_wifi_get_channel(&ch, &sc);
  onFrame(pk->payload, pk->rx_ctrl.sig_len, pk->rx_ctrl.rssi, ch);
}

static void printHex(const uint8_t* data, size_t len) {
  static const char HEXC[] = "0123456789abcdef";
  for (size_t i = 0; i < len; i++) {
    Serial.write(HEXC[data[i] >> 4]);
    Serial.write(HEXC[data[i] & 0x0f]);
  }
}

void setup() {
  Serial.begin(115200); delay(600);
  if (!WiFi.mode(WIFI_STA)) {
    Serial.println("RECON_END\t2\tERROR\twifi-mode");
    return;
  }
  esp_err_t scan_err = esp_wifi_set_promiscuous_rx_cb(&cb);
#ifdef TARGET_CHANNEL
  if (scan_err == ESP_OK)
    scan_err = esp_wifi_set_channel(TARGET_CHANNEL, WIFI_SECOND_CHAN_NONE);
#else
  if (scan_err == ESP_OK)
    scan_err = esp_wifi_set_channel(1, WIFI_SECOND_CHAN_NONE);
#endif
  if (scan_err == ESP_OK) scan_err = esp_wifi_set_promiscuous(true);
  if (scan_err != ESP_OK) {
    esp_wifi_set_promiscuous(false);
    Serial.printf("RECON_END\t2\tERROR\tinit-rc-0x%X\n", (unsigned int)scan_err);
    return;
  }
  Serial.println("RECON_BEGIN\t2");
#ifdef TARGET_CHANNEL
  delay(3000);
#else
  for (int c = 1; c <= 13 && scan_err == ESP_OK; c++) {
    if (c > 1) scan_err = esp_wifi_set_channel(c, WIFI_SECOND_CHAN_NONE);
    if (scan_err == ESP_OK) delay(350);
  }
#endif
  const esp_err_t disable_err = esp_wifi_set_promiscuous(false);
  if (scan_err != ESP_OK || disable_err != ESP_OK) {
    esp_wifi_stop();
    Serial.printf("RECON_END\t2\tERROR\tscan-rc-0x%X-disable-rc-0x%X\n",
                  (unsigned int)scan_err, (unsigned int)disable_err);
    return;
  }

  int emitted = 0;
  for (int i = 0; i < nap; i++) {
    apinfo_t* a = &aps[i];
    if (!a->seen) continue;
    const char* auth = (a->sae && a->psk) ? "WPA2/WPA3" : a->sae ? "WPA3-SAE"
                       : a->rsn ? (a->psk ? "WPA2-PSK" : "WPA2") : a->wpa1 ? "WPA1"
                       : a->privacy ? "WEP" : "OPEN";
    const char* pmf = a->mfpr ? "required" : a->mfpc ? "capable" : "none";
    const char* cipher = a->ccmp ? "CCMP" : a->tkip ? "TKIP" : "-";
    char mac[18];
    snprintf(mac, sizeof(mac), "%02X:%02X:%02X:%02X:%02X:%02X",
             a->bssid[0], a->bssid[1], a->bssid[2], a->bssid[3], a->bssid[4], a->bssid[5]);
    Serial.printf("RECON2\t%s\t%d\t%d\t%s\t%s\t%s\t%s\t",
                  mac, a->ch, a->rssi, auth, pmf, a->wps ? "wps" : "-", cipher);
    printHex(a->ssid, a->ssid_len);
    Serial.println();
    emitted++;
  }
  Serial.printf("RECON_END\t2\tOK\t%d\n", emitted);
}

void loop() { delay(2000); }
