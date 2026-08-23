/*
 * medusa_blescan — BLE scan / enumerate / advertisement sniff.
 *
 * Active BLE scan; for every advertiser emits
 *     BLE2 <TAB> address <TAB> rssi <TAB> name-hex <TAB> manufacturer-data-hex
 * The manufacturer data (company id in the first 2 bytes) doubles as passive
 * advertisement sniffing and lets the host label Apple/Microsoft/etc devices.
 *
 * Uses the on-die BLE radio (S3 / C3 / C6). No Wi-Fi, no core patch. LAWFUL USE.
 */
#include <Arduino.h>
#include <BLEDevice.h>
#include <BLEScan.h>
#include <BLEAdvertisedDevice.h>

static const char HEXC[] = "0123456789abcdef";
static volatile uint32_t emitted = 0;
static volatile bool scanComplete = false;

static void onScanComplete(BLEScanResults) {
  scanComplete = true;
}

static void printHex(const uint8_t* data, size_t length) {
  for (size_t i = 0; i < length; ++i) {
    const uint8_t value = data[i];
    Serial.print(HEXC[value >> 4]);
    Serial.print(HEXC[value & 0x0f]);
  }
}

class ScanCB : public BLEAdvertisedDeviceCallbacks {
  void onResult(BLEAdvertisedDevice d) override {
    const std::string name = d.haveName() ? d.getName() : std::string();
    const std::string mfg = d.haveManufacturerData() ? d.getManufacturerData() : std::string();
    Serial.printf("BLE2\t%s\t%d\t", d.getAddress().toString().c_str(), d.getRSSI());
    printHex(reinterpret_cast<const uint8_t*>(name.data()), name.size());
    Serial.print('\t');
    printHex(reinterpret_cast<const uint8_t*>(mfg.data()), mfg.size());
    Serial.println();
    ++emitted;
  }
};

void setup() {
  Serial.begin(115200);
  delay(600);
  Serial.println("\n=== medusa_blescan ===");
  Serial.println("ACTIVE BLE discovery scan; no device connection. LAWFUL USE ONLY.");
  BLEDevice::init("");
  BLEScan* s = BLEDevice::getScan();
  s->setAdvertisedDeviceCallbacks(new ScanCB());
  s->setActiveScan(true);
  s->setInterval(100);
  s->setWindow(99);
  Serial.println("scanning BLE…");
}

void loop() {
  BLEScan* s = BLEDevice::getScan();
  emitted = 0;
  scanComplete = false;
  Serial.println("BLESCAN_BEGIN\t2");
  if (!s->start(4, onScanComplete)) {
    Serial.println("BLESCAN_END\t2\tERROR\tstart-failed");
    delay(500);
    return;
  }

  const uint32_t deadline = millis() + 5500;
  while (!scanComplete && static_cast<int32_t>(deadline - millis()) > 0) {
    delay(10);
  }
  if (!scanComplete) {
    s->stop();
    Serial.println("BLESCAN_END\t2\tERROR\ttimeout");
    delay(500);
    return;
  }

  Serial.printf("BLESCAN_END\t2\tOK\t%u\n", static_cast<unsigned>(emitted));
  s->clearResults();
  delay(150);
}
