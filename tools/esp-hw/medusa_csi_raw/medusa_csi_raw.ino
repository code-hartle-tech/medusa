/*
 * medusa_csi_raw — unprocessed CSI export, one record per frame.
 *
 * medusa_csi answers questions on the device and reports conclusions. This
 * exports the measurement itself and answers nothing, because three of the
 * choices that make medusa_csi efficient also make it useless for anything
 * spectral:
 *
 *   PHASE IS DISCARDED. medusa_csi computes power (re^2 + im^2). Power is fine
 *   for a position fingerprint and throws away exactly the quantity that
 *   carries small periodic displacement. A chest wall moving 4-12 mm changes a
 *   reflected path by 8-24 mm, which at 12.5 cm wavelength is 0.40-1.21
 *   radians of phase — large and obvious — and precisely zero of it survives
 *   the magnitude operation.
 *
 *   VALUES ARE LOG-QUANTISED. One byte per subcarrier at roughly 3 dB a step.
 *   A sub-radian wiggle disappears between two steps.
 *
 *   SAMPLES ARE AVERAGED TO 1 Hz. This is the worst of the three. Breathing
 *   runs to about 0.5 Hz, so a one-second reporting interval sits on the
 *   Nyquist limit and anything faster aliases into nonsense. Spectral work
 *   wants the frame rate, not a summary of it.
 *
 * So this sketch does none of that. Every CSI callback becomes one record
 * carrying the driver's raw int8 I/Q pairs, unmodified.
 *
 * TIMESTAMPS COME FROM THE HARDWARE, NOT FROM THE PRINT. esp_timer_get_time()
 * is read inside the callback. Using host arrival time instead would fold
 * serial buffering, USB scheduling and loop jitter into the sample interval,
 * and a spectral estimate is only as good as its time base — jitter smears a
 * sharp peak into a hill and moves it.
 *
 * DROPPED FRAMES ARE COUNTED AND REPORTED. A gap in an evenly sampled series
 * is not a missing sample, it is a wrong sample interval for everything after
 * it. Silently dropping under load would corrupt the very analysis this exists
 * to enable, so the count is emitted and the consumer can discard the run.
 *
 * TRANSMIT POSTURE. Passive by default: promiscuous receive only, no transmit
 * path at all. Active mode (-DMEDUSA_CSI_ACTIVE=1) is the exception and says so
 * at the top of its own section — it joins a named network and pings it, which
 * is the only way to make the sample rate a number we choose. The banner
 * printed at boot states which of the two is running, so a capture can never be
 * mistaken for the other.
 *
 * LINE PROTOCOL, one per received frame:
 *   CSIR <TAB> seq <TAB> us <TAB> mac <TAB> rssi <TAB> rate <TAB> sig_mode
 *        <TAB> chan <TAB> nsub <TAB> base64(iq)
 *
 *     us        microseconds since boot, taken in the callback
 *     rate      driver rate index (NOT Mbps; its meaning varies by modulation)
 *     sig_mode  0 = non-HT, 1 = HT — the layout differs between them
 *     nsub      subcarriers in this record; VARIES BETWEEN FRAMES from one
 *               radio, so records of differing nsub are different measurements
 *               and must not be compared elementwise
 *     base64    raw driver bytes, [imag, real] int8 pairs, nothing applied
 *
 *   CSIRDROP <TAB> dropped <TAB> total   emitted when frames were lost
 *   CSIRINFO <TAB> key=value
 */
#include <Arduino.h>
#include <WiFi.h>
#include "esp_wifi.h"
#include "esp_timer.h"


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

#ifndef MEDUSA_CSI_ACTIVE
#define MEDUSA_CSI_ACTIVE 0
#endif

/* Milliseconds between echo requests in active mode. 25 ms gives a nominal
 * 40 Hz, comfortably above the ~2.5 Hz that respiration needs and with margin
 * for replies that go missing. */
#ifndef CSI_PING_MS
#define CSI_PING_MS 25
#endif

/* ACTIVE MODE.
 *
 * Passive promiscuous capture is the stronger posture and it is the default,
 * but its sampling rate is whatever the air happens to offer. Measured in a
 * real room: 0.4-1.2 Hz against an idle access point, and 0.5 Hz against a
 * strong phone hotspot sitting three metres away — because those frames were
 * addressed to a laptop, not to this board, and CSI is only reliably produced
 * for frames the receiver is actually the recipient of.
 *
 * Anything spectral needs several samples per second. So in active mode the
 * board JOINS a named network and pings its gateway itself. Every reply is a
 * frame addressed to us, which means the sample rate becomes a number we
 * choose rather than one we hope for.
 *
 * This is how Espressif's own CSI examples work, and it is the difference
 * between a measurement and a wish.
 *
 * WHAT IT COSTS. The board transmits: ICMP echo requests to one address on a
 * network the operator supplied credentials for. That is an ordinary client
 * doing an ordinary thing, it is not routed through medusa_tx_send() because
 * these are not frames we synthesise into the air — the IP stack owns them —
 * and the scope is inherently the one network named at build time.
 *
 * The credentials arrive as build defines and land in the generated
 * medusa_target.h, which the build deletes afterwards and .gitignore covers.
 * They are never printed.
 */
#if MEDUSA_CSI_ACTIVE
#include "ping/ping_sock.h"
#include "lwip/inet.h"
#endif

/* Largest buffer the driver produces: L-LTF + HT-LTF + STBC-HT-LTF, two int8
 * per subcarrier. */
#define CSIR_MAX_BYTES 384

/* Ring slots. The callback runs in the WiFi task and must return fast, so it
 * copies and leaves; encoding and printing happen in loop(). Depth covers a
 * burst arriving while the previous record is still going out over USB.
 * Power of two so the index wrap is a mask. */
#ifndef CSIR_RING
#define CSIR_RING 32
#endif

typedef struct {
  int64_t  us;
  uint8_t  mac[6];
  int8_t   rssi;
  uint8_t  rate;
  uint8_t  sig_mode;
  uint8_t  channel;
  uint16_t bytes;
  int8_t   iq[CSIR_MAX_BYTES];
} csir_slot_t;

static csir_slot_t ring[CSIR_RING];
static volatile uint32_t ringHead = 0;   /* written by the WiFi task */
static volatile uint32_t ringTail = 0;   /* written by loop()        */
static volatile uint32_t dropped = 0;
static volatile uint32_t received = 0;

static uint8_t targetBssid[6] = TARGET_BSSID;
static bool csiReady = false;
static uint32_t emitted = 0;
static uint32_t lastDropReport = 0;

static bool haveTarget() {
  for (int i = 0; i < 6; i++) if (targetBssid[i]) return true;
  return false;
}

static void onCsi(void *ctx, wifi_csi_info_t *info) {
  (void)ctx;
  if (!info || !info->buf || info->len < 4) return;
  if (haveTarget() && memcmp(info->mac, targetBssid, 6) != 0) return;

  /* Read the clock first: everything after this is bookkeeping whose duration
   * should not be attributed to the measurement. */
  const int64_t now = esp_timer_get_time();

  received++;

  const uint32_t head = ringHead;
  if ((uint32_t)(head - ringTail) >= CSIR_RING) { dropped++; return; }

  csir_slot_t *s = &ring[head & (CSIR_RING - 1)];
  s->us = now;
  memcpy(s->mac, info->mac, 6);
  s->rssi = info->rx_ctrl.rssi;
  s->rate = info->rx_ctrl.rate;
  s->sig_mode = info->rx_ctrl.sig_mode;
  s->channel = info->rx_ctrl.channel;

  /* The driver flags when the leading four bytes are not CSI. They are dropped
   * here rather than exported with a flag, so that a consumer cannot forget to
   * honour it — a fictional spike in the first two subcarriers would otherwise
   * ride into every downstream transform. */
  uint16_t start = info->first_word_invalid ? 4 : 0;
  uint16_t len = info->len;
  if (len <= start) return;
  len -= start;
  if (len > CSIR_MAX_BYTES) len = CSIR_MAX_BYTES;

  memcpy(s->iq, info->buf + start, len);
  s->bytes = len;

  ringHead = head + 1;   /* publish last: the slot is complete before it is visible */
}

static const char B64[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

/* Encode straight into a caller buffer; no allocation on a hot path. */
static size_t b64(const int8_t *in, size_t n, char *out) {
  size_t o = 0;
  for (size_t i = 0; i < n; i += 3) {
    const uint32_t a = (uint8_t)in[i];
    const uint32_t b = (i + 1 < n) ? (uint8_t)in[i + 1] : 0;
    const uint32_t c = (i + 2 < n) ? (uint8_t)in[i + 2] : 0;
    const uint32_t v = (a << 16) | (b << 8) | c;
    out[o++] = B64[(v >> 18) & 0x3F];
    out[o++] = B64[(v >> 12) & 0x3F];
    out[o++] = (i + 1 < n) ? B64[(v >> 6) & 0x3F] : '=';
    out[o++] = (i + 2 < n) ? B64[v & 0x3F] : '=';
  }
  out[o] = 0;
  return o;
}

void setup() {
  /* Raw export is roughly an order of magnitude more data than the summarised
   * path: 384 bytes a frame, base64 to 512 characters, tens of frames a
   * second. On this part serial runs over USB CDC and the figure is nominal,
   * but it is set high so a UART-bridged board is not the bottleneck. */
  Serial.begin(460800);
  delay(600);
  Serial.println("\n=== medusa_csi_raw ===");
#if MEDUSA_CSI_ACTIVE
  Serial.println("ACTIVE — joins a named network and pings it. LAWFUL USE ONLY.");
#else
  Serial.println("STRICTLY PASSIVE — receive only, no transmit path. LAWFUL USE ONLY.");
#endif

  if (!WiFi.mode(WIFI_STA)) { Serial.println("CSIRINFO\terror=wifi-mode"); return; }

#if MEDUSA_CSI_ACTIVE
  /* Promiscuous stays OFF here. We want frames addressed to this board, which
   * is exactly what the normal receive path delivers. */
  Serial.printf("CSIRINFO\tmode=active ssid=%s\n", TARGET_WIFI_SSID);
  /* RETRY, DO NOT GIVE UP AFTER ONE ATTEMPT.
   *
   * A phone hotspot powers its radio down when nothing is associated, and
   * between two captures the board is reflashed — so by the second run the
   * hotspot may be asleep and a single 20-second attempt fails. That is what
   * happened on the first real validation session: run one captured fine, run
   * two died on join, and the subject had already breathed to a metronome for
   * two minutes before finding out.
   *
   * Repeated association attempts are also what WAKES such a hotspot, so
   * retrying is the fix rather than merely a courtesy. */
  uint32_t waited = 0;
  for (int attempt = 0; attempt < 4 && WiFi.status() != WL_CONNECTED; attempt++) {
    if (attempt) {
      Serial.printf("CSIRINFO\tjoin_retry=%d\n", attempt);
      WiFi.disconnect(true);
      delay(500);
    }
    WiFi.begin(TARGET_WIFI_SSID, TARGET_WIFI_PASS);
    uint32_t spent = 0;
    while (WiFi.status() != WL_CONNECTED && spent < 15000) { delay(250); spent += 250; }
    waited += spent;
  }
  if (WiFi.status() != WL_CONNECTED) {
    /* Name the two causes that look identical from here, because the fix
     * differs: a 5 GHz-only hotspot is invisible to this radio no matter how
     * correct the password is. */
    Serial.println("CSIRINFO\terror=join-failed (wrong password, or the network "
                   "is 5 GHz only — this part is 2.4 GHz)");
    return;
  }
  Serial.print("CSIRINFO\tjoined_bssid=");
  Serial.println(WiFi.BSSIDstr());
  Serial.printf("CSIRINFO\tchannel=%d rssi=%d\n", WiFi.channel(), WiFi.RSSI());
  /* Sense against the access point we just joined, whatever its BSSID turned
   * out to be — a randomised hotspot address cannot be known at build time. */
  memcpy(targetBssid, WiFi.BSSID(), 6);
#else
  esp_wifi_set_promiscuous(true);
  esp_wifi_set_channel(TARGET_CHANNEL, WIFI_SECOND_CHAN_NONE);
#endif

  wifi_csi_config_t cfg = {};
  cfg.lltf_en = true;
  cfg.htltf_en = true;
  cfg.stbc_htltf2_en = true;
  cfg.ltf_merge_en = true;
  cfg.channel_filter_en = true;
  /* manu_scale off: the driver applies its own automatic gain scaling and
   * reports it consistently. Hand scaling would need the shift exported too,
   * and an unexported scale factor is a silent multiplier on every sample. */
  cfg.manu_scale = false;
  cfg.shift = 0;

  esp_err_t err = esp_wifi_set_csi_config(&cfg);
  if (err != ESP_OK) { Serial.printf("CSIRINFO\terror=csi-config-%d\n", (int)err); return; }
  err = esp_wifi_set_csi_rx_cb(&onCsi, NULL);
  if (err != ESP_OK) { Serial.printf("CSIRINFO\terror=csi-cb-%d\n", (int)err); return; }
  err = esp_wifi_set_csi(true);
  if (err != ESP_OK) {
    Serial.printf("CSIRINFO\terror=csi-enable-%d (core may lack CONFIG_ESP32_WIFI_CSI_ENABLED)\n", (int)err);
    return;
  }

#if MEDUSA_CSI_ACTIVE
  {
    ip_addr_t target;
    IPAddress gw = WiFi.gatewayIP();
    ipaddr_aton(gw.toString().c_str(), &target);
    esp_ping_config_t cfg = ESP_PING_DEFAULT_CONFIG();
    cfg.target_addr = target;
    cfg.count = ESP_PING_COUNT_INFINITE;
    cfg.interval_ms = CSI_PING_MS;
    cfg.timeout_ms = 1000;
    esp_ping_callbacks_t cbs = {};
    esp_ping_handle_t ping = NULL;
    if (esp_ping_new_session(&cfg, &cbs, &ping) == ESP_OK && esp_ping_start(ping) == ESP_OK) {
      Serial.printf("CSIRINFO\tping=%s every %dms\n", gw.toString().c_str(), CSI_PING_MS);
    } else {
      Serial.println("CSIRINFO\terror=ping-start");
    }
  }
#endif

  csiReady = true;
  Serial.printf("CSIRINFO\tchannel=%d\n", TARGET_CHANNEL);
  Serial.printf("CSIRINFO\tfiltered=%s\n", haveTarget() ? "yes" : "no");
  Serial.printf("CSIRINFO\tring=%d\n", CSIR_RING);
  Serial.println("CSIRINFO\tformat=int8 [imag,real] pairs, base64, no scaling applied");
  Serial.println("CSIRINFO\ttimebase=esp_timer_get_time microseconds, sampled in the rx callback");
}

void loop() {
  if (!csiReady) { delay(1000); return; }

  static char payload[((CSIR_MAX_BYTES + 2) / 3) * 4 + 1];

  while (ringTail != ringHead) {
    const csir_slot_t *s = &ring[ringTail & (CSIR_RING - 1)];
    b64(s->iq, s->bytes, payload);
    Serial.printf("CSIR\t%lu\t%lld\t%02X:%02X:%02X:%02X:%02X:%02X\t%d\t%u\t%u\t%u\t%u\t%s\n",
                  (unsigned long)emitted++,
                  (long long)s->us,
                  s->mac[0], s->mac[1], s->mac[2], s->mac[3], s->mac[4], s->mac[5],
                  (int)s->rssi, (unsigned)s->rate, (unsigned)s->sig_mode,
                  (unsigned)s->channel, (unsigned)(s->bytes / 2), payload);
    ringTail++;
  }

  /* Report losses as they accumulate. A consumer that sees a non-zero count
   * knows the sample interval is no longer uniform and can refuse the run
   * rather than transform a series with holes in it. */
  const uint32_t d = dropped;
  if (d != lastDropReport) {
    Serial.printf("CSIRDROP\t%lu\t%lu\n", (unsigned long)d, (unsigned long)received);
    lastDropReport = d;
  }
}
