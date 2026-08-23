/*
 * Gated transmission. See medusa_tx_common.h.
 *
 * The radio call itself is behind MEDUSA_TX_HAS_RADIO so this file compiles
 * and is testable on a host, where the "transmission" is counted rather than
 * emitted. The policy decisions are identical either way, which is the part
 * worth testing.
 */

#include "medusa_tx_common.h"

#include <string.h>

/* On device there is a radio; on a host build there is not, and the same file
 * must compile both ways so the policy decisions can be tested without
 * hardware. Detect rather than requiring every sketch to remember a flag. */
#if !defined(MEDUSA_TX_HAS_RADIO) && (defined(ARDUINO) || defined(ESP_PLATFORM))
#define MEDUSA_TX_HAS_RADIO 1
#endif

#if defined(MEDUSA_TX_HAS_RADIO)
#include "esp_wifi.h"
#endif

static mtg_state_t g_guard;
static mcfg_rate_t g_rate;
static mtg_decision_t g_last = MTG_DENY_NO_SESSION;
static bool g_ready = false;
static bool g_initialised = false;

#if !defined(MEDUSA_TX_HAS_RADIO)
/* Host builds count what would have gone out, so a test can assert that a
 * refusal really did prevent a transmission rather than merely log one. */
unsigned long medusa_tx_test_frames_sent = 0;
#endif

bool medusa_tx_begin(const mcfg_t *cfg, mcfg_profile_t profile,
                     const uint8_t (*allow)[MTG_MAC_LEN], uint8_t allow_count,
                     bool broadcast_confirmed, uint32_t now_ms,
                     mtg_session_grant_t *granted) {
    mcfg_t local;
    if (cfg == NULL) {
        mcfg_defaults(&local);
        cfg = &local;
    }
    if (profile < 0 || profile >= MCFG_PROFILE_COUNT) return false;

    /* Initialise ONCE. Re-initialising per session would zero the guard and
     * silently clear a latched stop, so an operator's stop would last only
     * until something opened the next session — which is precisely the
     * property the stop exists to provide. */
    if (!g_initialised) {
        mtg_init(&g_guard);
        g_initialised = true;
    }
    /* The operator's ceilings, not compiled ones. */
    mtg_apply_config(&g_guard, cfg->session_ms, cfg->rate[profile].frames_per_sec,
                     cfg->require_allowlist);

    g_rate = cfg->rate[profile];
    g_last = MTG_DENY_NO_SESSION;

    const bool ok = mtg_session_open(&g_guard, now_ms, allow, allow_count,
                                     cfg->session_ms, cfg->rate[profile].frames_per_sec,
                                     broadcast_confirmed || cfg->allow_broadcast, granted);
    g_ready = ok;
    return ok;
}

int medusa_tx_send(const uint8_t *frame, size_t len, const uint8_t target[MTG_MAC_LEN], uint32_t now_ms) {
    if (frame == NULL || len == 0u || target == NULL) return -1;
    if (!g_ready) {
        g_last = MTG_DENY_NO_SESSION;
        return -1;
    }

    g_last = mtg_authorize(&g_guard, now_ms, target);
    if (g_last != MTG_ALLOW) return -1;

#if defined(MEDUSA_TX_HAS_RADIO)
    return (int)esp_wifi_80211_tx(WIFI_IF_STA, frame, (int)len, true);
#else
    medusa_tx_test_frames_sent++;
    return 0;
#endif
}

mtg_decision_t medusa_tx_last_decision(void) { return g_last; }

uint32_t medusa_tx_pacing_ms(void) {
    /* Convert a rate into a delay. A rate above a thousand per second cannot be
     * paced by a millisecond timer, so it becomes "as fast as the loop goes"
     * and the guard's per-second window remains the real limit. */
    if (g_rate.frames_per_sec == 0u) return 1000u;
    if (g_rate.frames_per_sec >= 1000u) return 0u;
    return 1000u / g_rate.frames_per_sec;
}

uint16_t medusa_tx_burst(void) { return g_rate.burst ? g_rate.burst : 1u; }
uint16_t medusa_tx_gap_ms(void) { return g_rate.gap_ms; }

bool medusa_tx_expired(uint32_t now_ms) { return mtg_session_expired(&g_guard, now_ms); }

void medusa_tx_stop(void) {
    mtg_stop(&g_guard);
    g_ready = false;
}

void medusa_tx_end(void) {
    mtg_session_close(&g_guard);
    g_ready = false;
}

mtg_state_t *medusa_tx_guard_state(void) { return &g_guard; }
