/*
 * medusa_congestion — passive per-channel congestion survey.
 *
 * Sweeps the 2.4 GHz channels, dwells on each, and reports what is actually
 * competing for air time. This answers the question an owner genuinely has —
 * "my network is slow, which channel should I be on?" — with measurement
 * rather than with the channel-picker guesswork built into most routers.
 *
 * WHAT IS MEASURED, AND WHY EACH ONE.
 *
 *   frames        raw count. The obvious metric and the most misleading one on
 *                 its own: a thousand tiny beacons cost less air time than a
 *                 hundred long data frames.
 *
 *   airtime_us    estimated microseconds of channel occupancy. This is the
 *                 quantity that actually runs out. Estimated from length and
 *                 the driver's reported rate — see the note on the estimate
 *                 below, because it is an approximation and saying otherwise
 *                 would be a lie told in units.
 *
 *   retries       frames with the retry bit set. A high retry share means the
 *                 air is contended or the link is marginal, and it separates
 *                 "busy but healthy" from "busy and failing" — two situations
 *                 that need opposite advice.
 *
 *   networks      distinct BSSIDs heard. Co-channel neighbours.
 *
 * OVERLAP MATTERS MORE THAN OCCUPANCY. 2.4 GHz channels are 22 MHz wide on 5
 * MHz spacing, so channel 6 is damaged by traffic on 4, 5, 7 and 8 as well as
 * on 6. A ranking that ignores that recommends channels that are quiet only
 * because their neighbours are doing the shouting. The host-side scoring
 * applies the overlap penalty; this sketch reports raw per-channel facts.
 *
 * STRICTLY PASSIVE. Promiscuous receive only, no transmit path.
 *
 * LINE PROTOCOL:
 *   CONG <TAB> channel <TAB> frames <TAB> airtime_us <TAB> retries <TAB> networks <TAB> dwell_ms
 *   CONGDONE
 *   CONGINFO <TAB> key=value
 */
#include <Arduino.h>
#include <WiFi.h>
#include "esp_wifi.h"

#ifndef CONG_FIRST_CHANNEL
#define CONG_FIRST_CHANNEL 1
#endif
#ifndef CONG_LAST_CHANNEL
#define CONG_LAST_CHANNEL 13
#endif

/* Dwell per channel. Long enough to catch at least one beacon interval from
 * every AP present (they beacon about every 100 ms), short enough that a full
 * sweep is not a coffee break. */
#ifndef CONG_DWELL_MS
#define CONG_DWELL_MS 1200
#endif

#ifndef CONG_SWEEPS
#define CONG_SWEEPS 3
#endif

#define CONG_MAX_BSSIDS 24

static volatile uint32_t cFrames = 0;
static volatile uint64_t cAirtime = 0;
static volatile uint32_t cRetries = 0;
static uint8_t seenBssid[CONG_MAX_BSSIDS][6];
static volatile uint8_t seenCount = 0;

static void noteBssid(const uint8_t *mac) {
  for (uint8_t i = 0; i < seenCount; i++)
    if (memcmp(seenBssid[i], mac, 6) == 0) return;
  if (seenCount < CONG_MAX_BSSIDS) {
    memcpy(seenBssid[seenCount], mac, 6);
    seenCount++;
  }
}

static void onRx(void *buf, wifi_promiscuous_pkt_type_t type) {
  const wifi_promiscuous_pkt_t *p = (const wifi_promiscuous_pkt_t *)buf;
  const int len = p->rx_ctrl.sig_len;
  if (len < 4) return;
  const uint8_t *d = p->payload;

  cFrames++;

  /* Retry bit is bit 3 of the second frame-control octet. */
  if (d[1] & 0x08) cRetries++;

  /* AIR TIME ESTIMATE. rx_ctrl.rate is the driver's rate index, not a Mbps
   * value, and its meaning differs between modulations. Rather than pretend to
   * a precision we do not have, this uses a coarse two-tier model: 802.11b
   * rates occupy the air far longer per byte than OFDM ones, and that
   * distinction is most of the story for congestion. The absolute figure is
   * approximate; the RANKING between channels, which is what the survey is
   * for, is robust to the approximation because the same model applies to
   * every channel. */
  uint32_t bits = (uint32_t)len * 8u;
  uint32_t rate_kbps = p->rx_ctrl.sig_mode == 0 ? 6000u : 26000u;  /* non-HT vs HT */
  if (p->rx_ctrl.sig_mode == 0 && p->rx_ctrl.rate <= 3) rate_kbps = 1000u; /* DSSS */
  uint32_t us = (bits * 1000u) / rate_kbps;
  /* Every frame also costs preamble plus the inter-frame gap and, for most,
   * an ack. A flat overhead captures the bulk of that without pretending to
   * model the MAC. */
  cAirtime += us + 200u;

  /* Beacons and probe responses carry the BSSID in addr3. */
  const uint8_t ft = (d[0] >> 2) & 0x3, st = (d[0] >> 4) & 0xF;
  if (ft == 0 && (st == 8 || st == 5) && len >= 22) noteBssid(d + 16);
}

typedef struct {
  uint32_t frames;
  uint64_t airtime;
  uint32_t retries;
  uint8_t  networks;
} chan_stat_t;

static chan_stat_t stats[CONG_LAST_CHANNEL + 1];

void setup() {
  Serial.begin(115200);
  delay(600);
  Serial.println("\n=== medusa_congestion ===");
  Serial.println("STRICTLY PASSIVE — receive only, no transmit path. LAWFUL USE ONLY.");

  memset(stats, 0, sizeof(stats));

  if (!WiFi.mode(WIFI_STA)) { Serial.println("CONGINFO\terror=wifi-mode"); return; }
  esp_wifi_set_promiscuous(true);
  esp_wifi_set_promiscuous_rx_cb(&onRx);

  Serial.printf("CONGINFO\tchannels=%d-%d\n", CONG_FIRST_CHANNEL, CONG_LAST_CHANNEL);
  Serial.printf("CONGINFO\tdwell_ms=%d\n", CONG_DWELL_MS);
  Serial.printf("CONGINFO\tsweeps=%d\n", CONG_SWEEPS);

  /* Several passes rather than one long dwell per channel. A single visit can
   * land entirely inside or entirely outside a burst of traffic, and the
   * channel visited first would otherwise be systematically compared against a
   * different moment than the one visited last. */
  for (int sweep = 0; sweep < CONG_SWEEPS; sweep++) {
    for (int ch = CONG_FIRST_CHANNEL; ch <= CONG_LAST_CHANNEL; ch++) {
      cFrames = 0; cAirtime = 0; cRetries = 0; seenCount = 0;
      esp_wifi_set_channel(ch, WIFI_SECOND_CHAN_NONE);
      delay(CONG_DWELL_MS);
      stats[ch].frames  += cFrames;
      stats[ch].airtime += cAirtime;
      stats[ch].retries += cRetries;
      if (seenCount > stats[ch].networks) stats[ch].networks = seenCount;
    }
  }

  esp_wifi_set_promiscuous(false);

  for (int ch = CONG_FIRST_CHANNEL; ch <= CONG_LAST_CHANNEL; ch++) {
    Serial.printf("CONG\t%d\t%lu\t%llu\t%lu\t%u\t%d\n", ch,
                  (unsigned long)stats[ch].frames,
                  (unsigned long long)stats[ch].airtime,
                  (unsigned long)stats[ch].retries,
                  (unsigned)stats[ch].networks,
                  CONG_DWELL_MS * CONG_SWEEPS);
  }
  Serial.println("CONGDONE");
}

void loop() { delay(5000); }
