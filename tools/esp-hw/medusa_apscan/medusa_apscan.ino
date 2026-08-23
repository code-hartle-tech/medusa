/*
 * medusa_apscan — list nearby 2.4 GHz access points over serial.
 *
 * These ESP32 radios survey the 2.4 GHz band. Output is machine-parseable and
 * intended for defensive inventory of networks the operator is authorized to
 * assess:
 *
 *   APSCAN_BEGIN\t2
 *   AP2\t<bssid>\t<channel>\t<rssi>\t<ssid-hex>
 *   ...
 *   APSCAN_END\t2\tOK\t<count>
 *
 * Read by esp_hw.py scan-aps and presented as local survey observations.
 */
#include <Arduino.h>
#include <WiFi.h>

static const char HEXC[] = "0123456789abcdef";

static void printHex(const uint8_t* data, size_t length) {
  for (size_t i = 0; i < length; ++i) {
    const uint8_t value = data[i];
    Serial.print(HEXC[value >> 4]);
    Serial.print(HEXC[value & 0x0f]);
  }
}

void setup() {
  Serial.begin(115200);
  delay(600);
  WiFi.mode(WIFI_STA);
  WiFi.disconnect(true);
  delay(150);

  Serial.println("APSCAN_BEGIN\t2");
  int n = WiFi.scanNetworks(false /*async*/, true /*show_hidden*/);
  if (n < 0) {
    Serial.printf("APSCAN_END\t2\tERROR\t%d\n", n);
    return;
  }
  for (int i = 0; i < n; i++) {
    uint8_t* b = WiFi.BSSID(i);
    char mac[18];
    snprintf(mac, sizeof(mac), "%02X:%02X:%02X:%02X:%02X:%02X", b[0], b[1], b[2], b[3], b[4], b[5]);
    String ssid = WiFi.SSID(i);
    if (ssid.length() == 0) ssid = "(hidden)";
    Serial.printf("AP2\t%s\t%d\t%d\t", mac, WiFi.channel(i), WiFi.RSSI(i));
    printHex(reinterpret_cast<const uint8_t*>(ssid.c_str()), ssid.length());
    Serial.println();
  }
  Serial.printf("APSCAN_END\t2\tOK\t%d\n", n);
}

void loop() { delay(2000); }
