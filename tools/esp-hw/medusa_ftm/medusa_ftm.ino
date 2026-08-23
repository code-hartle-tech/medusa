/*
 * medusa_ftm — 802.11mc Fine Timing Measurement.
 *
 * Measures distance by round-trip time instead of guessing it from signal
 * strength. Every rival in this category estimates range from RSSI — Ghost ESP
 * ships an RSSI meter and a fox hunt, Marauder has FoxHuntTarget — and RSSI is
 * confounded by transmit power, antenna gain, orientation and every wall in
 * between. Time of flight is not. A wardrive position carrying a distance is
 * evidence; one carrying an RSSI is an inference.
 *
 * This is the same lesson the devkit journey already teaches about vendor OUIs,
 * moved down to the physical layer: an inference is not a measurement.
 *
 * TWO ROLES, chosen at build time.
 *
 *   INITIATOR (default) — ask a responder for a timing exchange and report the
 *   estimated distance. Requires the far end to implement 802.11mc as a
 *   responder, which most consumer access points do NOT. A negative result here
 *   is a real finding about the AP, not a failure of this sketch, and it is
 *   reported as such rather than as an error.
 *
 *   RESPONDER (-DMEDUSA_FTM_RESPONDER=1) — stand up a soft AP that answers FTM
 *   requests, so a second Medusa can be ranged against it. This is how the
 *   capability is proven end to end when no 802.11mc-capable infrastructure is
 *   available: two boards, a known tape-measured separation, and a comparison.
 *
 * TRANSMIT POSTURE. FTM is a request/response protocol, so the initiator does
 * transmit — but only unicast action frames to a single peer the operator named
 * at build time, and only in bursts the standard defines. There is no flood
 * mode and no broadcast path. It is not gated through medusa_tx_send() because
 * that gate governs frames we synthesise into the air; here the radio's own FTM
 * state machine owns the exchange and the scope is inherently one peer.
 *
 * LINE PROTOCOL (tab separated):
 *   FTM <TAB> seq <TAB> peer <TAB> status <TAB> rtt_ns <TAB> dist_cm <TAB> frames
 *   FTMERR <TAB> reason
 *   FTMINFO <TAB> key=value
 *
 * status is the driver's own verdict, reported verbatim:
 *   SUCCESS            a real measurement
 *   UNSUPPORTED        the peer does not do FTM — the common case on consumer APs
 *   CONF_REJECTED      the peer refused these burst parameters
 *   NO_RESPONSE        nothing came back
 */
#include <Arduino.h>
#include <WiFi.h>
#include "esp_wifi.h"
#include "esp_event.h"

#if defined(__has_include)
#  if __has_include("medusa_target.h")
#    include "medusa_target.h"
#  endif
#endif
#ifndef TARGET_CHANNEL
#define TARGET_CHANNEL 9
#endif
#ifndef TARGET_BSSID
#define TARGET_BSSID { 0, 0, 0, 0, 0, 0 }
#endif

#ifndef MEDUSA_FTM_RESPONDER
#define MEDUSA_FTM_RESPONDER 0
#endif

/* SURVEY MODE. Scan for access points and attempt a ranging exchange with each
 * one, reporting per BSSID which of them will actually talk 802.11mc.
 *
 * This exists because the alternative — rebuild and reflash once per candidate
 * peer — costs about a minute each and answers only one question at a time.
 * "Does anything within range support ranging at all?" is the question you
 * actually have when a survey turns up nothing, and it deserves one pass. */
#ifndef MEDUSA_FTM_SURVEY
#define MEDUSA_FTM_SURVEY 0
#endif

/* Frames per burst. More frames average down the timing noise, at the cost of
 * a longer exchange and more air time. 32 is the driver's usual sweet spot;
 * the standard allows up to 64. */
#ifndef FTM_FRAME_COUNT
#define FTM_FRAME_COUNT 32
#endif

/* Burst period in units of 100 ms, per the driver's API. */
#ifndef FTM_BURST_PERIOD
#define FTM_BURST_PERIOD 2
#endif

#ifndef FTM_INTERVAL_MS
#define FTM_INTERVAL_MS 3000
#endif

#if MEDUSA_FTM_RESPONDER
#ifndef FTM_RESPONDER_SSID
#define FTM_RESPONDER_SSID "medusa-ftm-responder"
#endif
#endif

static uint8_t peerMac[6] = TARGET_BSSID;
static uint32_t seq = 0;
static volatile bool reportReady = false;
static volatile bool sessionBusy = false;

static const char *ftmStatusName(uint8_t s) {
  switch (s) {
    case FTM_STATUS_SUCCESS:       return "SUCCESS";
    case FTM_STATUS_UNSUPPORTED:   return "UNSUPPORTED";
    case FTM_STATUS_CONF_REJECTED: return "CONF_REJECTED";
    case FTM_STATUS_NO_RESPONSE:   return "NO_RESPONSE";
    case FTM_STATUS_FAIL:          return "FAIL";
    default:                       return "UNKNOWN";
  }
}

static void onWifiEvent(void *arg, esp_event_base_t base, int32_t id, void *data) {
  (void)arg;
  if (base != WIFI_EVENT || id != WIFI_EVENT_FTM_REPORT) return;

  wifi_event_ftm_report_t *r = (wifi_event_ftm_report_t *)data;

  /* dist_est is only meaningful when the exchange actually succeeded. Printing
   * it regardless would put a confident-looking centimetre figure next to an
   * UNSUPPORTED status, which is precisely the sort of number someone later
   * quotes without reading the column beside it. */
  if (r->status == FTM_STATUS_SUCCESS) {
    Serial.printf("FTM\t%lu\t%02X:%02X:%02X:%02X:%02X:%02X\t%s\t%lu\t%lu\t%d\n",
                  (unsigned long)seq++,
                  r->peer_mac[0], r->peer_mac[1], r->peer_mac[2],
                  r->peer_mac[3], r->peer_mac[4], r->peer_mac[5],
                  ftmStatusName(r->status),
                  (unsigned long)r->rtt_est, (unsigned long)r->dist_est,
                  (int)r->ftm_report_num_entries);
  } else {
    Serial.printf("FTM\t%lu\t%02X:%02X:%02X:%02X:%02X:%02X\t%s\t-\t-\t0\n",
                  (unsigned long)seq++,
                  r->peer_mac[0], r->peer_mac[1], r->peer_mac[2],
                  r->peer_mac[3], r->peer_mac[4], r->peer_mac[5],
                  ftmStatusName(r->status));
  }

  /* The driver allocates this for us and hands over ownership. */
  if (r->ftm_report_data) free(r->ftm_report_data);
  sessionBusy = false;
}

static bool havePeer() {
  for (int i = 0; i < 6; i++) if (peerMac[i]) return true;
  return false;
}

void setup() {
  Serial.begin(115200);
  delay(600);
  Serial.println("\n=== medusa_ftm ===");

#if MEDUSA_FTM_RESPONDER
  Serial.println("RESPONDER role — answers FTM requests. LAWFUL USE ONLY.");
  WiFi.mode(WIFI_AP);
  if (!WiFi.softAP(FTM_RESPONDER_SSID, NULL, TARGET_CHANNEL)) {
    Serial.println("FTMERR\tsoftap-start");
    return;
  }
  /* Advertising FTM responder support is a per-AP-config flag; without it the
   * soft AP comes up and silently answers nothing. */
  wifi_config_t cfg;
  if (esp_wifi_get_config(WIFI_IF_AP, &cfg) != ESP_OK) {
    Serial.println("FTMERR\tget-config");
    return;
  }
  cfg.ap.ftm_responder = true;
  if (esp_wifi_set_config(WIFI_IF_AP, &cfg) != ESP_OK) {
    Serial.println("FTMERR\tset-ftm-responder (core may lack CONFIG_ESP_WIFI_FTM_RESPONDER_SUPPORT)");
    return;
  }
  Serial.printf("FTMINFO\tssid=%s\n", FTM_RESPONDER_SSID);
  Serial.printf("FTMINFO\tchannel=%d\n", TARGET_CHANNEL);
  Serial.print("FTMINFO\tmac=");
  Serial.println(WiFi.softAPmacAddress());
  Serial.println("FTMINFO\trole=responder");

#else
  Serial.println("INITIATOR role — ranges a named peer. LAWFUL USE ONLY.");
  WiFi.mode(WIFI_STA);
  esp_event_handler_instance_register(WIFI_EVENT, WIFI_EVENT_FTM_REPORT,
                                      &onWifiEvent, NULL, NULL);
#if MEDUSA_FTM_SURVEY
  Serial.println("FTMINFO\trole=survey");
  Serial.println("FTMINFO\tnote=attempts a ranging exchange with every AP found");
#else
  if (!havePeer()) {
    Serial.println("FTMERR\tno-peer (build with --bssid)");
    return;
  }
#endif
  Serial.printf("FTMINFO\tpeer=%02X:%02X:%02X:%02X:%02X:%02X\n",
                peerMac[0], peerMac[1], peerMac[2], peerMac[3], peerMac[4], peerMac[5]);
  Serial.printf("FTMINFO\tchannel=%d\n", TARGET_CHANNEL);
  Serial.printf("FTMINFO\tframes=%d\n", FTM_FRAME_COUNT);
  Serial.println("FTMINFO\trole=initiator");
  Serial.println("FTMINFO\tnote=UNSUPPORTED is a finding about the peer, not an error here");
#endif
}

#if !MEDUSA_FTM_RESPONDER && MEDUSA_FTM_SURVEY
/* Try one peer and wait for its report. Returns when the exchange settles or
 * the wait expires; the event handler does the printing. */
static void rangeOnce(const uint8_t *mac, uint8_t channel) {
  wifi_ftm_initiator_cfg_t cfg = {};
  memcpy(cfg.resp_mac, mac, 6);
  cfg.channel = channel;
  cfg.frm_count = FTM_FRAME_COUNT;
  cfg.burst_period = FTM_BURST_PERIOD;

  sessionBusy = true;
  if (esp_wifi_ftm_initiate_session(&cfg) != ESP_OK) {
    sessionBusy = false;
    Serial.printf("FTM\t%lu\t%02X:%02X:%02X:%02X:%02X:%02X\tINITIATE_FAILED\t-\t-\t0\n",
                  (unsigned long)seq++, mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    return;
  }
  uint32_t waited = 0;
  while (sessionBusy && waited < 4000) { delay(50); waited += 50; }
  if (sessionBusy) {
    sessionBusy = false;
    Serial.printf("FTM\t%lu\t%02X:%02X:%02X:%02X:%02X:%02X\tNO_REPORT\t-\t-\t0\n",
                  (unsigned long)seq++, mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
  }
}

static void surveyOnce() {
  int n = WiFi.scanNetworks(false, true);
  if (n <= 0) { Serial.println("FTMINFO\tsurvey=no-aps"); return; }
  Serial.printf("FTMINFO\tsurvey_aps=%d\n", n);
  for (int i = 0; i < n; i++) {
    const uint8_t *mac = WiFi.BSSID(i);
    if (!mac) continue;
    Serial.printf("FTMINFO\tpeer=%02X:%02X:%02X:%02X:%02X:%02X ch=%d rssi=%d\n",
                  mac[0], mac[1], mac[2], mac[3], mac[4], mac[5],
                  WiFi.channel(i), WiFi.RSSI(i));
    rangeOnce(mac, (uint8_t)WiFi.channel(i));
    delay(400);
  }
  WiFi.scanDelete();
  Serial.println("FTMSURVEYDONE");
}
#endif

void loop() {
#if MEDUSA_FTM_RESPONDER
  delay(5000);
  Serial.println("FTMINFO\tresponder=alive");
#elif MEDUSA_FTM_SURVEY
  surveyOnce();
  delay(3000);
#else
  if (!havePeer()) { delay(5000); return; }
  if (sessionBusy) { delay(100); return; }

  wifi_ftm_initiator_cfg_t cfg = {};
  memcpy(cfg.resp_mac, peerMac, 6);
  cfg.channel = TARGET_CHANNEL;
  cfg.frm_count = FTM_FRAME_COUNT;
  cfg.burst_period = FTM_BURST_PERIOD;

  sessionBusy = true;
  esp_err_t err = esp_wifi_ftm_initiate_session(&cfg);
  if (err != ESP_OK) {
    sessionBusy = false;
    Serial.printf("FTMERR\tinitiate-%d\n", (int)err);
  }

  /* Wait out the exchange plus slack. If no report arrives the flag is cleared
   * here so a peer that never answers cannot wedge the loop permanently. */
  uint32_t waited = 0;
  while (sessionBusy && waited < 4000) { delay(50); waited += 50; }
  if (sessionBusy) {
    sessionBusy = false;
    Serial.printf("FTM\t%lu\t%02X:%02X:%02X:%02X:%02X:%02X\tNO_REPORT\t-\t-\t0\n",
                  (unsigned long)seq++,
                  peerMac[0], peerMac[1], peerMac[2], peerMac[3], peerMac[4], peerMac[5]);
  }
  delay(FTM_INTERVAL_MS);
#endif
}
