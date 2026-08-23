/*
 * Arduino-ESP32 companion to the ESP-IDF Medusa Wi-Fi internals lab.
 *
 * Lawful-use scope: hardware and radio environments the operator owns,
 * administers, or has written authorization to test. Espressif's stock raw
 * frame validator remains intact. No target MAC input and no TX loop exist.
 */

#include <Arduino.h>
#include <ctype.h>
#include <stdlib.h>
#include <string.h>

#include "esp_arduino_version.h"
#include "esp_chip_info.h"
#include "esp_err.h"
#include "esp_idf_version.h"
#include "esp_mac.h"
#include "esp_wifi.h"

#ifndef MEDUSA_LAB_COUNTRY_CODE
#define MEDUSA_LAB_COUNTRY_CODE "PT"
#endif

namespace {

constexpr size_t kHeaderLength = 24;
constexpr size_t kActionLength = 39;
constexpr size_t kSelfProbeLength = 26;
constexpr size_t kCommandBufferLength = 96;
constexpr uint8_t kBroadcast[6] = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff};

struct Counters {
  uint32_t rx_callbacks = 0;
  uint32_t management = 0;
  uint32_t beacons = 0;
  uint32_t probe_requests = 0;
  uint32_t deauth_observed = 0;
  int32_t last_rssi = 0;
  uint32_t last_channel = 0;
};

uint8_t station_mac[6] = {};
uint8_t action_frame[kActionLength] = {};
uint8_t self_probe_frame[kSelfProbeLength] = {};
uint8_t current_channel = 1;
Counters counters;
portMUX_TYPE counters_mux = portMUX_INITIALIZER_UNLOCKED;
char command_buffer[kCommandBufferLength] = {};
size_t command_length = 0;
bool discarding_overlong_command = false;
bool swallow_lf_after_cr = false;

void copyMac(uint8_t *destination, const uint8_t source[6]) {
  memcpy(destination, source, 6);
}

void buildManagementHeader(uint8_t *frame,
                           uint8_t frame_control_low,
                           const uint8_t destination[6],
                           const uint8_t source[6],
                           const uint8_t bssid[6]) {
  memset(frame, 0, kHeaderLength);
  frame[0] = frame_control_low;
  copyMac(frame + 4, destination);
  copyMac(frame + 10, source);
  copyMac(frame + 16, bssid);
}

void buildActionFrame() {
  constexpr uint8_t marker[] = {'M', 'E', 'D', 'U', 'S', 'A', '-', 'L', 'A', 'B'};
  buildManagementHeader(action_frame, 0xd0, kBroadcast, station_mac, station_mac);
  action_frame[24] = 127;
  action_frame[25] = 0x02;
  action_frame[26] = 0x00;
  action_frame[27] = 0x00;
  action_frame[28] = 0x01;
  memcpy(action_frame + 29, marker, sizeof(marker));
}

void buildSelfProbeFrame() {
  buildManagementHeader(self_probe_frame, 0xc0, station_mac, station_mac, station_mac);
  self_probe_frame[24] = 0x01;
  self_probe_frame[25] = 0x00;
}

void promiscuousCallback(void *buffer, wifi_promiscuous_pkt_type_t type) {
  auto *packet = static_cast<wifi_promiscuous_pkt_t *>(buffer);
  if (packet == nullptr) {
    return;
  }

  const bool has_management_header =
      type == WIFI_PKT_MGMT && packet->rx_ctrl.sig_len >= 1;
  const uint8_t subtype =
      has_management_header ? ((packet->payload[0] >> 4) & 0x0f) : 0xff;

  portENTER_CRITICAL(&counters_mux);
  ++counters.rx_callbacks;
  counters.last_rssi = packet->rx_ctrl.rssi;
  counters.last_channel = packet->rx_ctrl.channel;
  if (type == WIFI_PKT_MGMT) {
    ++counters.management;
    if (subtype == 0x08) {
      ++counters.beacons;
    } else if (subtype == 0x04) {
      ++counters.probe_requests;
    } else if (subtype == 0x0c) {
      ++counters.deauth_observed;
    }
  }
  portEXIT_CRITICAL(&counters_mux);
}

void printResult(const char *label, esp_err_t result) {
  Serial.printf("%s: esp_wifi_80211_tx -> %s (0x%x)\n",
                label,
                esp_err_to_name(result),
                static_cast<unsigned int>(result));
  Serial.println("API return is validation/queue evidence, not over-air proof.");
}

void printHelp() {
  Serial.println("Commands:");
  Serial.println("  help");
  Serial.println("  status");
  Serial.println("  channel <1-13>");
  Serial.println("  tx-action");
  Serial.println("  probe-stock-gate SELF");
  Serial.println("No command accepts a target MAC. There are no transmit loops.");
}

void printStatus() {
  Counters snapshot;
  wifi_country_t country = {};
  portENTER_CRITICAL(&counters_mux);
  snapshot = counters;
  portEXIT_CRITICAL(&counters_mux);

  Serial.printf("arduino_esp32=%d.%d.%d idf=%s channel=%u sta_mac=" MACSTR "\n",
                ESP_ARDUINO_VERSION_MAJOR,
                ESP_ARDUINO_VERSION_MINOR,
                ESP_ARDUINO_VERSION_PATCH,
                esp_get_idf_version(),
                current_channel,
                MAC2STR(station_mac));
  if (esp_wifi_get_country(&country) == ESP_OK) {
    Serial.printf("country=%.2s start_channel=%u channel_count=%u policy=%d\n",
                  country.cc,
                  country.schan,
                  country.nchan,
                  country.policy);
  }
  Serial.printf("rx callbacks=%u management=%u beacons=%u probe_requests=%u deauth_observed=%u "
                "last_rssi=%ld last_channel=%u\n",
                snapshot.rx_callbacks,
                snapshot.management,
                snapshot.beacons,
                snapshot.probe_requests,
                snapshot.deauth_observed,
                static_cast<long>(snapshot.last_rssi),
                snapshot.last_channel);
  Serial.println("proof: API return != second-radio capture != receiver action");
}

void runActionExperiment() {
  buildActionFrame();
  const esp_err_t result = esp_wifi_80211_tx(
      WIFI_IF_STA, action_frame, sizeof(action_frame), true);
  printResult("benign action frame", result);
}

void runStockGateExperiment() {
  buildSelfProbeFrame();
  const esp_err_t result = esp_wifi_80211_tx(
      WIFI_IF_STA, self_probe_frame, sizeof(self_probe_frame), true);
  printResult("self-addressed unsupported-subtype probe", result);
  if (result == ESP_OK) {
    Serial.println("Unexpected acceptance: stop and verify with a second monitor radio.");
  } else if (result == ESP_ERR_INVALID_ARG) {
    Serial.println("ESP_ERR_INVALID_ARG is consistent with the stock subtype gate because the other public arguments are fixed.");
  } else {
    Serial.println("Inconclusive: this may be an interface, state, memory, or other environmental error.");
  }
}

void processCommand(const char *command) {
  if (strcmp(command, "help") == 0) {
    printHelp();
  } else if (strcmp(command, "status") == 0) {
    printStatus();
  } else if (strcmp(command, "tx-action") == 0) {
    runActionExperiment();
  } else if (strcmp(command, "probe-stock-gate SELF") == 0) {
    runStockGateExperiment();
  } else if (strncmp(command, "channel ", 8) == 0) {
    const char *argument = command + 8;
    char *end = nullptr;
    const long requested = strtol(argument, &end, 10);
    while (end != nullptr && isspace(static_cast<unsigned char>(*end))) {
      ++end;
    }
    if (argument == end || end == nullptr || *end != '\0' ||
        requested < 1 || requested > 13) {
      Serial.println("channel expects one integer in the range 1..13");
      return;
    }
    const esp_err_t result = esp_wifi_set_channel(
        static_cast<uint8_t>(requested), WIFI_SECOND_CHAN_NONE);
    if (result == ESP_OK) {
      current_channel = static_cast<uint8_t>(requested);
    }
    Serial.printf("channel: %s (0x%x), current=%u\n",
                  esp_err_to_name(result),
                  static_cast<unsigned int>(result),
                  current_channel);
  } else if (command[0] != '\0') {
    Serial.println("unknown command; type 'help'");
  }
}

void finishCommandLine() {
  if (discarding_overlong_command) {
    Serial.println("command too long; line discarded");
  } else {
    command_buffer[command_length] = '\0';
    processCommand(command_buffer);
  }
  command_length = 0;
  discarding_overlong_command = false;
}

void consumeSerialCharacter(int character) {
  if (character == '\n' && swallow_lf_after_cr) {
    swallow_lf_after_cr = false;
    return;
  }
  if (character == '\r' || character == '\n') {
    swallow_lf_after_cr = character == '\r';
    finishCommandLine();
    return;
  }

  swallow_lf_after_cr = false;
  if (character == '\b' || character == 0x7f) {
    if (!discarding_overlong_command && command_length > 0) {
      --command_length;
    }
    return;
  }
  if (discarding_overlong_command) {
    return;
  }
  if (command_length + 1 >= sizeof(command_buffer)) {
    discarding_overlong_command = true;
    return;
  }
  command_buffer[command_length++] = static_cast<char>(character);
}

void initializeWifi() {
  wifi_init_config_t configuration = WIFI_INIT_CONFIG_DEFAULT();
  ESP_ERROR_CHECK(esp_wifi_init(&configuration));
  ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
  ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
  ESP_ERROR_CHECK(esp_wifi_set_country_code(MEDUSA_LAB_COUNTRY_CODE, false));
  ESP_ERROR_CHECK(esp_wifi_start());
  ESP_ERROR_CHECK(esp_wifi_get_mac(WIFI_IF_STA, station_mac));
  ESP_ERROR_CHECK(esp_wifi_set_channel(current_channel, WIFI_SECOND_CHAN_NONE));

  const wifi_promiscuous_filter_t filter = {
      .filter_mask = WIFI_PROMIS_FILTER_MASK_MGMT,
  };
  ESP_ERROR_CHECK(esp_wifi_set_promiscuous_filter(&filter));
  ESP_ERROR_CHECK(esp_wifi_set_promiscuous_rx_cb(promiscuousCallback));
  ESP_ERROR_CHECK(esp_wifi_set_promiscuous(true));
}

}  // namespace

void setup() {
  Serial.begin(115200);
  delay(750);
  initializeWifi();
  Serial.printf("Medusa stock-gate lab ready: target=%s, IDF=%s\n",
                CONFIG_IDF_TARGET,
                esp_get_idf_version());
  Serial.println("Stock Espressif raw-frame validator is intentionally intact.");
  printHelp();
}

void loop() {
  while (Serial.available() > 0) {
    consumeSerialCharacter(Serial.read());
  }
  delay(10);
}
