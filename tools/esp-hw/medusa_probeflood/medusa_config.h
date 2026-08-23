/*
 * Medusa runtime configuration.
 *
 * Operating parameters live here, not in compile-time constants, because the
 * companion app is what decides how much, how fast, how long and in what
 * bursts. Baking those into firmware means a rebuild to change a rate, which
 * is not how an assessment tool is used.
 *
 * DEFAULTS ARE OPERATIONAL, NOT TIMID. A deauth mode that emits two frames a
 * minute is indistinguishable from a broken one: it proves nothing about
 * whether a network survives the condition being tested. The defaults below
 * are drawn from what the existing research sketches already do on the wire
 * (probe flood at delay(4) is ~250 frames/sec, CSA at ten-per-80ms is ~125)
 * and from what the test has to look like to be a real test.
 *
 * PERSISTENCE. This struct serialises to a flat blob for NVS, which lives in
 * its own flash partition. An application reflash rewrites the bootloader,
 * partition table and app — it does not erase NVS — so settings survive
 * firmware updates. A full chip erase does wipe it; that is the intended
 * escape hatch, not a bug.
 *
 * Freestanding C99, no malloc, no NVS calls. The platform shim in the sketch
 * does the actual Preferences.getBytes/putBytes, so this whole file is
 * host-testable.
 */

#ifndef MEDUSA_CONFIG_H
#define MEDUSA_CONFIG_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Bump when the struct layout changes. A blob carrying a different version is
 * discarded and defaults are used, rather than being reinterpreted — a
 * misparsed rate is worse than a forgotten one. */
#define MCFG_VERSION 1u
#define MCFG_MAGIC 0x4D435631u /* "MCV1" */

#define MCFG_MAC_LEN 6u
#define MCFG_ALLOWLIST_MAX 32u /* a site audit routinely has dozens of BSSIDs */

/* Operating profiles, one per transmitting capability. They differ because the
 * capabilities differ: beacon spam has to repeat every SSID often enough to
 * appear in a scan list, whereas a deauth test is about sustained pressure. */
typedef enum {
    MCFG_PROFILE_DEAUTH = 0,
    MCFG_PROFILE_BEACON,
    MCFG_PROFILE_PROBE,
    MCFG_PROFILE_CSA,
    MCFG_PROFILE_AUTH,
    MCFG_PROFILE_BLE_ADV,
    MCFG_PROFILE_COUNT
} mcfg_profile_t;

typedef struct {
    uint16_t frames_per_sec; /* sustained rate */
    uint16_t burst;          /* frames emitted back-to-back before pausing */
    uint16_t gap_ms;         /* pause between bursts; 0 = continuous */
} mcfg_rate_t;

typedef struct {
    uint32_t magic;
    uint16_t version;
    uint16_t reserved;

    /* Session shape. */
    uint32_t session_ms;    /* 0 = no time limit */
    bool require_allowlist; /* false permits untargeted transmission */
    bool allow_broadcast;   /* broadcast destinations without a per-call confirm */

    /* Scope. */
    uint8_t allow[MCFG_ALLOWLIST_MAX][MCFG_MAC_LEN];
    uint8_t allow_count;

    /* Per-capability rates. */
    mcfg_rate_t rate[MCFG_PROFILE_COUNT];

    /* Evidence. */
    bool audit_enabled;
    uint16_t audit_capacity;
} mcfg_t;

/** Populate with operational defaults. Never fails. */
void mcfg_defaults(mcfg_t *cfg);

/**
 * Clamp anything absurd into range and repair inconsistencies.
 *
 * Returns true if the config was already sane, false if something had to be
 * corrected. Correction rather than rejection is deliberate: a companion that
 * sends a nonsense rate should get a working device and a report of what was
 * actually applied, not a brick.
 */
bool mcfg_sanitise(mcfg_t *cfg);

/** Serialised size of the config blob. */
size_t mcfg_blob_size(void);

/** Write `cfg` into `out`. Returns bytes written, or 0 if `cap` is too small. */
size_t mcfg_serialise(const mcfg_t *cfg, uint8_t *out, size_t cap);

/**
 * Parse a blob into `cfg`.
 *
 * Returns false and fills `cfg` with defaults when the blob is absent, the
 * wrong size, or carries a different magic or version. A device that cannot
 * read its settings runs on known-good values rather than on whatever the
 * bytes happened to decode to.
 */
bool mcfg_deserialise(const uint8_t *blob, size_t len, mcfg_t *cfg);

/** Add a BSSID to the allow-list. False if full or already present. */
bool mcfg_allow_add(mcfg_t *cfg, const uint8_t bssid[MCFG_MAC_LEN]);

/** Remove a BSSID. False if not present. */
bool mcfg_allow_remove(mcfg_t *cfg, const uint8_t bssid[MCFG_MAC_LEN]);

/** True if the BSSID is listed. */
bool mcfg_allow_contains(const mcfg_t *cfg, const uint8_t bssid[MCFG_MAC_LEN]);

/** Human-readable profile name, for the companion and the audit log. */
const char *mcfg_profile_name(mcfg_profile_t profile);

#ifdef __cplusplus
}
#endif

#endif /* MEDUSA_CONFIG_H */
