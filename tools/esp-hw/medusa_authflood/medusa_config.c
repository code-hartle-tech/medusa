/*
 * Medusa runtime configuration. See medusa_config.h.
 */

#include "medusa_config.h"

#include <string.h>

/* Upper bounds, not operating values. These exist so a corrupt blob or a
 * companion bug cannot ask for something the radio and the scheduler cannot
 * actually sustain — an ESP32 will not emit a million frames a second no
 * matter what the config says, and pretending otherwise just produces a
 * confusing runtime. They are deliberately far above the defaults. */
#define MCFG_RATE_CEILING 1000u
#define MCFG_BURST_CEILING 512u
#define MCFG_GAP_CEILING 60000u
#define MCFG_SESSION_CEILING 3600000u /* one hour */
#define MCFG_AUDIT_CEILING 1024u

void mcfg_defaults(mcfg_t *cfg) {
    if (cfg == NULL) return;
    memset(cfg, 0, sizeof(*cfg));

    cfg->magic = MCFG_MAGIC;
    cfg->version = MCFG_VERSION;

    /* Fifteen minutes: long enough to walk a floor and watch what happens,
     * short enough that a forgotten session ends on its own. */
    cfg->session_ms = 900000u;

    /* Scope is required by default because an untargeted transmission is
     * rarely what anyone actually means, and the companion can turn it off
     * deliberately. This is a default, not a lock. */
    cfg->require_allowlist = true;
    cfg->allow_broadcast = false;
    cfg->allow_count = 0u;

    /* Rates that make each test a real test. Derived from what the capability
     * has to look like on the wire, not from caution. */

    /* Deauthentication: sustained pressure with bursts, in the shape the
     * standard tooling uses — a burst of 64 then a short pause. */
    cfg->rate[MCFG_PROFILE_DEAUTH] = (mcfg_rate_t){ .frames_per_sec = 100u, .burst = 64u, .gap_ms = 100u };

    /* Beacon spam: every advertised SSID must be re-beaconed often enough to
     * survive a scan cycle, so the rate scales with how many names are in
     * play. 200/sec keeps roughly 20 names visible. */
    cfg->rate[MCFG_PROFILE_BEACON] = (mcfg_rate_t){ .frames_per_sec = 200u, .burst = 32u, .gap_ms = 0u };

    /* Probe flood: matches what the existing sketch already does on the wire
     * (delay(4) between frames). */
    cfg->rate[MCFG_PROFILE_PROBE] = (mcfg_rate_t){ .frames_per_sec = 250u, .burst = 32u, .gap_ms = 0u };

    /* Channel switch announcement: repeated beacons carrying the CSA element;
     * the existing sketch runs ten per 80 ms. */
    cfg->rate[MCFG_PROFILE_CSA] = (mcfg_rate_t){ .frames_per_sec = 125u, .burst = 10u, .gap_ms = 80u };

    /* Authentication capacity: the point is to fill a table, so it wants to be
     * fast and sustained. */
    cfg->rate[MCFG_PROFILE_AUTH] = (mcfg_rate_t){ .frames_per_sec = 200u, .burst = 50u, .gap_ms = 20u };

    /* BLE advertising is interval-driven and far slower by nature. */
    cfg->rate[MCFG_PROFILE_BLE_ADV] = (mcfg_rate_t){ .frames_per_sec = 50u, .burst = 10u, .gap_ms = 20u };

    cfg->audit_enabled = true;
    cfg->audit_capacity = 256u;
}

static uint16_t clamp_u16(uint16_t value, uint16_t lo, uint16_t hi) {
    if (value < lo) return lo;
    if (value > hi) return hi;
    return value;
}

bool mcfg_sanitise(mcfg_t *cfg) {
    if (cfg == NULL) return false;
    bool was_sane = true;

    if (cfg->magic != MCFG_MAGIC) { cfg->magic = MCFG_MAGIC; was_sane = false; }
    if (cfg->version != MCFG_VERSION) { cfg->version = MCFG_VERSION; was_sane = false; }

    if (cfg->session_ms > MCFG_SESSION_CEILING) {
        cfg->session_ms = MCFG_SESSION_CEILING;
        was_sane = false;
    }

    if (cfg->allow_count > MCFG_ALLOWLIST_MAX) {
        cfg->allow_count = MCFG_ALLOWLIST_MAX;
        was_sane = false;
    }

    for (int i = 0; i < MCFG_PROFILE_COUNT; ++i) {
        mcfg_rate_t *r = &cfg->rate[i];
        /* A rate of zero is the "two frames a minute" failure in its purest
         * form: the capability is present, configured, and does nothing. Treat
         * it as a mistake and give it a working value. */
        uint16_t fps = clamp_u16(r->frames_per_sec == 0u ? 1u : r->frames_per_sec, 1u, MCFG_RATE_CEILING);
        uint16_t burst = clamp_u16(r->burst == 0u ? 1u : r->burst, 1u, MCFG_BURST_CEILING);
        uint16_t gap = clamp_u16(r->gap_ms, 0u, MCFG_GAP_CEILING);
        if (fps != r->frames_per_sec || burst != r->burst || gap != r->gap_ms) was_sane = false;
        r->frames_per_sec = fps;
        r->burst = burst;
        r->gap_ms = gap;
    }

    uint16_t audit = clamp_u16(cfg->audit_capacity == 0u ? 1u : cfg->audit_capacity, 1u, MCFG_AUDIT_CEILING);
    if (audit != cfg->audit_capacity) { cfg->audit_capacity = audit; was_sane = false; }

    return was_sane;
}

/* Flat little-endian layout. Written field by field rather than by dumping the
 * struct, so padding and compiler layout never reach flash — a struct memcpy
 * would make the blob depend on the toolchain that produced it. */
static void put_u16(uint8_t *p, uint16_t v) { p[0] = (uint8_t)(v & 0xFFu); p[1] = (uint8_t)(v >> 8); }
static void put_u32(uint8_t *p, uint32_t v) {
    p[0] = (uint8_t)(v & 0xFFu);
    p[1] = (uint8_t)((v >> 8) & 0xFFu);
    p[2] = (uint8_t)((v >> 16) & 0xFFu);
    p[3] = (uint8_t)((v >> 24) & 0xFFu);
}
static uint16_t get_u16(const uint8_t *p) { return (uint16_t)((uint16_t)p[0] | ((uint16_t)p[1] << 8)); }
static uint32_t get_u32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

size_t mcfg_blob_size(void) {
    return 4u                                    /* magic */
         + 2u                                    /* version */
         + 4u                                    /* session_ms */
         + 1u + 1u                               /* require_allowlist, allow_broadcast */
         + 1u                                    /* allow_count */
         + (MCFG_ALLOWLIST_MAX * MCFG_MAC_LEN)   /* allow */
         + ((size_t)MCFG_PROFILE_COUNT * 6u)     /* rates: 3 x u16 */
         + 1u + 2u;                              /* audit_enabled, audit_capacity */
}

size_t mcfg_serialise(const mcfg_t *cfg, uint8_t *out, size_t cap) {
    if (cfg == NULL || out == NULL) return 0u;
    const size_t need = mcfg_blob_size();
    if (cap < need) return 0u;

    size_t n = 0;
    put_u32(out + n, MCFG_MAGIC); n += 4u;
    put_u16(out + n, MCFG_VERSION); n += 2u;
    put_u32(out + n, cfg->session_ms); n += 4u;
    out[n++] = cfg->require_allowlist ? 1u : 0u;
    out[n++] = cfg->allow_broadcast ? 1u : 0u;
    out[n++] = cfg->allow_count;
    memcpy(out + n, cfg->allow, MCFG_ALLOWLIST_MAX * MCFG_MAC_LEN);
    n += MCFG_ALLOWLIST_MAX * MCFG_MAC_LEN;
    for (int i = 0; i < MCFG_PROFILE_COUNT; ++i) {
        put_u16(out + n, cfg->rate[i].frames_per_sec); n += 2u;
        put_u16(out + n, cfg->rate[i].burst); n += 2u;
        put_u16(out + n, cfg->rate[i].gap_ms); n += 2u;
    }
    out[n++] = cfg->audit_enabled ? 1u : 0u;
    put_u16(out + n, cfg->audit_capacity); n += 2u;
    return n;
}

bool mcfg_deserialise(const uint8_t *blob, size_t len, mcfg_t *cfg) {
    if (cfg == NULL) return false;
    /* Any doubt at all falls back to known-good values. */
    if (blob == NULL || len != mcfg_blob_size()) { mcfg_defaults(cfg); return false; }
    if (get_u32(blob) != MCFG_MAGIC) { mcfg_defaults(cfg); return false; }
    if (get_u16(blob + 4) != MCFG_VERSION) { mcfg_defaults(cfg); return false; }

    memset(cfg, 0, sizeof(*cfg));
    size_t n = 0;
    cfg->magic = get_u32(blob + n); n += 4u;
    cfg->version = get_u16(blob + n); n += 2u;
    cfg->session_ms = get_u32(blob + n); n += 4u;
    cfg->require_allowlist = blob[n++] != 0u;
    cfg->allow_broadcast = blob[n++] != 0u;
    cfg->allow_count = blob[n++];
    memcpy(cfg->allow, blob + n, MCFG_ALLOWLIST_MAX * MCFG_MAC_LEN);
    n += MCFG_ALLOWLIST_MAX * MCFG_MAC_LEN;
    for (int i = 0; i < MCFG_PROFILE_COUNT; ++i) {
        cfg->rate[i].frames_per_sec = get_u16(blob + n); n += 2u;
        cfg->rate[i].burst = get_u16(blob + n); n += 2u;
        cfg->rate[i].gap_ms = get_u16(blob + n); n += 2u;
    }
    cfg->audit_enabled = blob[n++] != 0u;
    cfg->audit_capacity = get_u16(blob + n); n += 2u;

    mcfg_sanitise(cfg);
    return true;
}

bool mcfg_allow_contains(const mcfg_t *cfg, const uint8_t bssid[MCFG_MAC_LEN]) {
    if (cfg == NULL || bssid == NULL) return false;
    for (uint8_t i = 0; i < cfg->allow_count && i < MCFG_ALLOWLIST_MAX; ++i) {
        if (memcmp(cfg->allow[i], bssid, MCFG_MAC_LEN) == 0) return true;
    }
    return false;
}

bool mcfg_allow_add(mcfg_t *cfg, const uint8_t bssid[MCFG_MAC_LEN]) {
    if (cfg == NULL || bssid == NULL) return false;
    if (mcfg_allow_contains(cfg, bssid)) return false;
    if (cfg->allow_count >= MCFG_ALLOWLIST_MAX) return false;
    memcpy(cfg->allow[cfg->allow_count], bssid, MCFG_MAC_LEN);
    cfg->allow_count++;
    return true;
}

bool mcfg_allow_remove(mcfg_t *cfg, const uint8_t bssid[MCFG_MAC_LEN]) {
    if (cfg == NULL || bssid == NULL) return false;
    for (uint8_t i = 0; i < cfg->allow_count && i < MCFG_ALLOWLIST_MAX; ++i) {
        if (memcmp(cfg->allow[i], bssid, MCFG_MAC_LEN) != 0) continue;
        /* Compact so the list stays dense; order carries no meaning. */
        for (uint8_t j = i; j + 1u < cfg->allow_count; ++j) {
            memcpy(cfg->allow[j], cfg->allow[j + 1u], MCFG_MAC_LEN);
        }
        cfg->allow_count--;
        memset(cfg->allow[cfg->allow_count], 0, MCFG_MAC_LEN);
        return true;
    }
    return false;
}

const char *mcfg_profile_name(mcfg_profile_t profile) {
    switch (profile) {
        case MCFG_PROFILE_DEAUTH:  return "deauth";
        case MCFG_PROFILE_BEACON:  return "beacon";
        case MCFG_PROFILE_PROBE:   return "probe";
        case MCFG_PROFILE_CSA:     return "csa";
        case MCFG_PROFILE_AUTH:    return "auth";
        case MCFG_PROFILE_BLE_ADV: return "ble-adv";
        case MCFG_PROFILE_COUNT:   break;
    }
    return "unknown";
}
