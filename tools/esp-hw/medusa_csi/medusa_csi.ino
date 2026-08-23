/*
 * medusa_csi — Channel State Information sensing.
 *
 * Reads per-subcarrier channel response for frames the radio was going to hear
 * anyway, and reports how that response is distributed and how much it is
 * moving. Two things fall out of that:
 *
 *   MOTION.   A body moving through the space between two radios perturbs the
 *             multipath. The channel response changes even though nothing about
 *             the transmitter did. That is presence sensing without a camera.
 *
 *   POSITION. The response is a function of where the transmitter physically
 *             is. An evil twin can clone a BSSID, SSID, channel and beacon
 *             timing exactly — it cannot clone its location, so its channel
 *             signature does not match the real AP's. See medusa_csi_analyze.py
 *             for that comparison; this sketch supplies the measurements.
 *
 * WHY THIS EXISTS AT ALL. Both major rival firmwares compile CSI support in and
 * then switch it off — Marauder sets .csi_enable = false, and every Ghost ESP
 * board config pairs CONFIG_SOC_WIFI_CSI_SUPPORT=y with CONFIG_ESP_WIFI_CSI_ENABLED
 * unset. The Arduino core this project already builds against enables it
 * (CONFIG_ESP32_WIFI_CSI_ENABLED=y), so this needs no new silicon and no core
 * patch. See wiki/research/2026-08-22-mcu-tier-completeness-audit.md.
 *
 * STRICTLY PASSIVE. Promiscuous receive only. This sketch has no transmit path
 * at all — not a gated one, none — so it cannot disturb what it measures. That
 * is the point: CSI is the most honest instrument in the catalogue precisely
 * because observing costs the observed nothing.
 *
 * LINE PROTOCOL (tab separated, one per reporting interval):
 *   CSI <TAB> seq <TAB> mac <TAB> rssi <TAB> nsub <TAB> frames <TAB> motion <TAB> profile-hex
 *     motion       integer 0..1000; L1 distance between this interval's mean
 *                  power profile and the running baseline, normalised. 0 means
 *                  the channel looks exactly like the baseline.
 *     profile-hex  one byte per subcarrier, log-quantised mean power. This is
 *                  the position fingerprint.
 *   CSIERR <TAB> reason        setup failed; nothing below is meaningful
 *   CSIINFO <TAB> key=value    one-shot facts about the run
 *
 * Target selection via medusa_target.h (TARGET_CHANNEL / TARGET_BSSID). With no
 * BSSID set, every transmitter is reported separately, which is the right
 * default for a survey but noisier.
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
#define TARGET_CHANNEL 9
#endif
#ifndef TARGET_BSSID
#define TARGET_BSSID { 0, 0, 0, 0, 0, 0 }
#endif

/* Reporting cadence. Fast enough that a person walking through the space shows
 * up as a distinct event rather than being averaged away. */
/* One second, not the half-second this started at. Against an idle AP the only
 * frames arriving are beacons, and a 500 ms window caught one to three of them
 * — below any threshold at which an average means something. A second buys
 * enough frames to be worth dividing by while still resolving a person walking
 * through a room, which takes one to two.
 *
 * The deeper limitation this exposes is worth stating plainly: CSI sensing
 * wants traffic. Against a busy AP the picture is far better than against a
 * silent one, and against a silent one the honest output is mostly "unknown". */
#ifndef CSI_REPORT_MS
#define CSI_REPORT_MS 1000
#endif

/* Two int8 per subcarrier. With L-LTF, HT-LTF and STBC-HT-LTF all enabled the
 * driver hands back up to 384 bytes — 192 subcarriers — not the 64 that a
 * "HT20 has 64 subcarriers" reading suggests.
 *
 * Measured, not assumed: an earlier build sized this at 64 and the very first
 * live run on a C3 raised the truncation flag on every reported interval. The
 * profile still looked plausible, which is the danger — a silently clipped
 * fingerprint would have compared two positions using only the first third of
 * the evidence. */
#define CSI_MAX_SUB 192

/* How many intervals of quiet it takes to establish the baseline the motion
 * metric is measured against. Reported explicitly rather than assumed, because
 * a baseline captured while someone was walking around is worse than none. */
#ifndef CSI_BASELINE_INTERVALS
#define CSI_BASELINE_INTERVALS 8
#endif

/* Minimum frames an interval must contain before its motion figure means
 * anything.
 *
 * MEASURED, NOT GUESSED. The first live run against a mostly-idle AP averaged
 * one to three frames per interval and produced a motion mean of 634/1000 —
 * i.e. "the channel is 63% different from baseline, continuously", in an empty
 * room at three in the morning. That was not motion, it was the variance of
 * averaging two packets whose gain and rate differed.
 *
 * An interval below this threshold reports its motion as "-" (unknown) rather
 * than a number. A confident-looking figure derived from one packet is worse
 * than no figure, because only the number gets quoted later. */
#ifndef CSI_MIN_FRAMES
#define CSI_MIN_FRAMES 6
#endif

static uint8_t targetBssid[6] = TARGET_BSSID;

/* How many distinct transmitters are tracked at once.
 *
 * PER-PEER ACCUMULATION IS NOT AN OPTIMISATION, IT IS A CORRECTNESS
 * REQUIREMENT. An earlier version kept a single accumulator and stamped each
 * report with whichever MAC happened to arrive last. With a BSSID filter set
 * that was harmless, because only one transmitter ever reached it. Unfiltered,
 * it summed every access point on the channel into one profile and attributed
 * the blend to an arbitrary one of them — and the output looked entirely
 * plausible, which is why it survived a live run before being noticed.
 *
 * A channel-position fingerprint that silently averages several positions is
 * worse than no fingerprint. */
#ifndef CSI_MAX_PEERS
#define CSI_MAX_PEERS 8
#endif

typedef struct {
  uint8_t  mac[6];
  bool     used;
  uint32_t power[CSI_MAX_SUB];   /* accumulating, drained each interval */
  uint32_t frames;
  int32_t  rssiSum;
  uint16_t nsub;
  uint32_t baseline[CSI_MAX_SUB];
  uint16_t baselineSeen;
  bool     baselineReady;
} csi_peer_t;

/* Written by the WiFi task, drained by loop(). A frame landing mid-drain is
 * counted in the next interval — a rounding error at this cadence, and much
 * cheaper than locking out the driver callback. */
static volatile csi_peer_t peers[CSI_MAX_PEERS];
static volatile bool overflowed = false;
static volatile bool peerTableFull = false;

static uint32_t seq = 0;
static bool     csiReady = false;

/* Returns NULL when the table is full rather than evicting. Eviction would let
 * a busy neighbour quietly displace the AP under assessment. */
static volatile csi_peer_t *peerFor(const uint8_t *mac) {
  for (int i = 0; i < CSI_MAX_PEERS; i++) {
    if (peers[i].used && memcmp((const void *)peers[i].mac, mac, 6) == 0) return &peers[i];
  }
  for (int i = 0; i < CSI_MAX_PEERS; i++) {
    if (!peers[i].used) {
      memcpy((void *)peers[i].mac, mac, 6);
      peers[i].used = true;
      return &peers[i];
    }
  }
  peerTableFull = true;
  return NULL;
}

static bool haveTarget() {
  for (int i = 0; i < 6; i++) if (targetBssid[i]) return true;
  return false;
}

static void onCsi(void *ctx, wifi_csi_info_t *info) {
  (void)ctx;
  if (!info || !info->buf || info->len < 4) return;
  if (haveTarget() && memcmp(info->mac, targetBssid, 6) != 0) return;

  const int8_t *buf = info->buf;
  uint16_t len = info->len;

  /* The driver flags when the first four bytes are not valid CSI. Skipping
   * them is not optional — treating that word as data puts a large fictional
   * spike in subcarrier 0 and 1, which then propagates into the baseline and
   * makes every later motion reading wrong by a constant. */
  uint16_t start = info->first_word_invalid ? 4 : 0;
  if (len <= start) return;

  uint16_t n = (uint16_t)((len - start) / 2);
  if (n > CSI_MAX_SUB) { n = CSI_MAX_SUB; overflowed = true; }

  volatile csi_peer_t *p = peerFor(info->mac);
  if (!p) return;

  for (uint16_t i = 0; i < n; i++) {
    /* Layout is [imaginary, real] per subcarrier. Which of the pair is which
     * does not matter here — power is symmetric in the two — but it matters to
     * anything that later wants phase, so it is recorded rather than guessed. */
    int32_t im = buf[start + 2 * i];
    int32_t re = buf[start + 2 * i + 1];
    p->power[i] += (uint32_t)(re * re + im * im);
  }

  p->nsub = n;
  p->frames++;
  p->rssiSum += info->rx_ctrl.rssi;
}

/* Log-ish quantisation to one byte. Power spans several orders of magnitude
 * across subcarriers, so a linear squeeze into 8 bits throws away exactly the
 * low-power detail that distinguishes two positions. */
static uint8_t quantise(uint32_t power) {
  uint8_t q = 0;
  while (power > 0 && q < 255) { power >>= 1; q++; }        /* integer log2 */
  return (uint8_t)(q * 8 > 255 ? 255 : q * 8);
}

void setup() {
  Serial.begin(115200);
  delay(600);
  Serial.println("\n=== medusa_csi ===");
  Serial.println("STRICTLY PASSIVE — receive only, no transmit path. LAWFUL USE ONLY.");

  memset((void *)peers, 0, sizeof(peers));

  if (!WiFi.mode(WIFI_STA)) { Serial.println("CSIERR\twifi-mode"); return; }
  esp_wifi_set_promiscuous(true);
  esp_wifi_set_channel(TARGET_CHANNEL, WIFI_SECOND_CHAN_NONE);

  /* lltf/htltf/stbc all enabled: more subcarriers is a richer position
   * fingerprint, which is the whole reason we are here. channel_filter_en is
   * left on because it removes a known distortion rather than data we want. */
  wifi_csi_config_t cfg = {};
  cfg.lltf_en = true;
  cfg.htltf_en = true;
  cfg.stbc_htltf2_en = true;
  cfg.ltf_merge_en = true;
  cfg.channel_filter_en = true;
  cfg.manu_scale = false;
  cfg.shift = 0;

  esp_err_t err = esp_wifi_set_csi_config(&cfg);
  if (err != ESP_OK) { Serial.printf("CSIERR\tcsi-config-%d\n", (int)err); return; }
  err = esp_wifi_set_csi_rx_cb(&onCsi, NULL);
  if (err != ESP_OK) { Serial.printf("CSIERR\tcsi-cb-%d\n", (int)err); return; }
  err = esp_wifi_set_csi(true);
  if (err != ESP_OK) {
    /* The one failure worth naming precisely: if the core was built without
     * CSI this is where it shows up, and it is otherwise indistinguishable
     * from "the air is quiet". */
    Serial.printf("CSIERR\tcsi-enable-%d (core may lack CONFIG_ESP32_WIFI_CSI_ENABLED)\n", (int)err);
    return;
  }

  csiReady = true;
  Serial.printf("CSIINFO\tchannel=%d\n", TARGET_CHANNEL);
  Serial.printf("CSIINFO\tfiltered=%s\n", haveTarget() ? "yes" : "no");
  Serial.printf("CSIINFO\tbaseline_intervals=%d\n", CSI_BASELINE_INTERVALS);
  Serial.printf("CSIINFO\treport_ms=%d\n", CSI_REPORT_MS);
}

/* Report one peer and reset its accumulator. Each transmitter carries its own
 * baseline, because a baseline is a statement about one radio in one place. */
static void reportPeer(volatile csi_peer_t *p) {
  uint32_t frames = p->frames;
  if (frames == 0) return;

  uint16_t n = p->nsub;
  if (n > CSI_MAX_SUB) n = CSI_MAX_SUB;

  uint32_t mean[CSI_MAX_SUB];
  for (uint16_t i = 0; i < n; i++) mean[i] = p->power[i] / frames;
  int32_t rssi = p->rssiSum / (int32_t)frames;
  uint8_t mac[6];
  memcpy(mac, (const void *)p->mac, 6);

  memset((void *)p->power, 0, sizeof(p->power));
  p->frames = 0;
  p->rssiSum = 0;

  /* Motion: how far this interval's profile sits from this peer's baseline,
   * scaled by the baseline's own magnitude so the number means the same thing
   * at different signal strengths. Without that normalisation a strong AP
   * always looks more "in motion" than a weak one. */
  int motion = -1;
  /* Too few frames to say anything about motion, and — just as important — too
   * few to contribute to the baseline. A baseline polluted by thin intervals
   * biases every later reading against it. */
  if (frames < CSI_MIN_FRAMES) {
    /* fall through: profile is still emitted, motion stays unknown */
  } else if (!p->baselineReady) {
    for (uint16_t i = 0; i < n; i++) p->baseline[i] += mean[i];
    if (++p->baselineSeen >= CSI_BASELINE_INTERVALS) {
      for (uint16_t i = 0; i < n; i++) p->baseline[i] /= p->baselineSeen;
      p->baselineReady = true;
      Serial.printf("CSIINFO\tbaseline_ready=%02X:%02X:%02X:%02X:%02X:%02X\n",
                    mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    }
  } else {
    uint64_t diff = 0, total = 0;
    for (uint16_t i = 0; i < n; i++) {
      uint32_t b = p->baseline[i];
      diff += (mean[i] > b) ? (mean[i] - b) : (b - mean[i]);
      total += b;
    }
    motion = total ? (int)((diff * 1000ULL) / total) : 0;
    if (motion > 1000) motion = 1000;
  }

  char prof[CSI_MAX_SUB * 2 + 1];
  for (uint16_t i = 0; i < n; i++) sprintf(prof + i * 2, "%02x", quantise(mean[i]));
  prof[n * 2] = 0;

  Serial.printf("CSI\t%lu\t%02X:%02X:%02X:%02X:%02X:%02X\t%d\t%u\t%lu\t",
                (unsigned long)seq++, mac[0], mac[1], mac[2], mac[3], mac[4], mac[5],
                (int)rssi, (unsigned)n, (unsigned long)frames);
  if (motion < 0) Serial.print("-"); else Serial.print(motion);
  Serial.printf("\t%s\n", prof);
}

void loop() {
  if (!csiReady) { delay(1000); return; }
  delay(CSI_REPORT_MS);

  bool any = false;
  for (int i = 0; i < CSI_MAX_PEERS; i++) {
    if (peers[i].used && peers[i].frames > 0) { any = true; reportPeer(&peers[i]); }
  }
  if (!any) Serial.printf("CSI\t%lu\t-\t-\t0\t0\t-\t-\n", (unsigned long)seq++);

  if (overflowed) { Serial.println("CSIINFO\ttruncated=subcarriers-exceeded-buffer"); overflowed = false; }
  if (peerTableFull) {
    /* Said out loud rather than silently dropped: a transmitter that never
     * gets a slot is invisible, and invisible is indistinguishable from absent. */
    Serial.printf("CSIINFO\tpeer_table_full=%d (use --bssid to focus)\n", CSI_MAX_PEERS);
    peerTableFull = false;
  }
}
