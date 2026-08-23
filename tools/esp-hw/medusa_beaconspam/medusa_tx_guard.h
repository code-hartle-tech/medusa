/*
 * Medusa active-transmission guard.
 *
 * The threat model (wiki/design/threat-model.md) requires that any product TX
 * capability ship with a per-session enable, a BSSID allow-list, rate and
 * duration ceilings, an audit log, separate confirmation for broadcast, and a
 * persistent stop path — and states that these are NOT implemented. This is
 * that module.
 *
 * It is deliberately in firmware, not in the companion app. The app is the
 * least trusted part of the system: anyone can write another one, and a guard
 * that lives there guards nothing. Every decision here is made on the device.
 *
 * IMPORTANT SCOPE NOTE. This module decides whether a transmission is
 * PERMITTED. It does not transmit, and building it does not authorise
 * transmitting. Operator approval is necessary but not sufficient: the threat
 * model still governs whether any given operation ships at all, and a passing
 * gate is not a legal conclusion about a particular use.
 *
 * Freestanding C99, no malloc, no clock of its own — the caller supplies
 * monotonic milliseconds so this is testable on a host without faking time.
 */

#ifndef MEDUSA_TX_GUARD_H
#define MEDUSA_TX_GUARD_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* Arduino compiles a .ino as C++ while this stays C, so without this the
 * sketch links against mangled names that do not exist. */
#ifdef __cplusplus
extern "C" {
#endif

#define MTG_MAC_LEN 6u

/* Fallback ceilings, used only until a configuration is applied. These are
 * NOT the operating values: mtg_apply_config() replaces them with whatever the
 * operator set through the companion. They exist so a guard that is used
 * before configuration still behaves sensibly rather than unboundedly.
 *
 * They are generous on purpose. A ceiling low enough to make a capability
 * useless is the same defect as not having the capability. */
#ifndef MTG_MAX_SESSION_MS
#define MTG_MAX_SESSION_MS 900000u /* fifteen minutes, matching mcfg defaults */
#endif
#ifndef MTG_MAX_RATE_PER_SEC
#define MTG_MAX_RATE_PER_SEC 250u /* what the existing sketches already emit */
#endif
#ifndef MTG_MAX_ALLOWLIST
#define MTG_MAX_ALLOWLIST 32u /* a site audit has dozens of BSSIDs */
#endif
#ifndef MTG_AUDIT_CAPACITY
#define MTG_AUDIT_CAPACITY 32u
#endif

typedef enum {
    MTG_ALLOW = 0,
    MTG_DENY_NO_SESSION,     /* tx.no_session   — nothing is open */
    MTG_DENY_EXPIRED,        /* tx.expired      — the session ran out */
    MTG_DENY_NOT_ALLOWLISTED,/* tx.scope        — target outside the allow-list */
    MTG_DENY_RATE,           /* tx.rate         — over the per-second ceiling */
    MTG_DENY_BROADCAST,      /* tx.broadcast    — broadcast without confirmation */
    MTG_DENY_STOPPED,        /* tx.stopped      — a stop is latched */
} mtg_decision_t;

const char *mtg_decision_code(mtg_decision_t decision);

typedef struct {
    uint8_t bssid[MTG_MAC_LEN];
    uint32_t at_ms;
    mtg_decision_t decision;
} mtg_audit_entry_t;

typedef struct {
    bool open;
    /* A latched stop survives session close and must be cleared explicitly.
     * "The operator pressed stop" must not be undone by a companion that
     * reconnects and opens a new session. */
    bool stopped;

    uint8_t allow[MTG_MAX_ALLOWLIST][MTG_MAC_LEN];
    uint8_t allow_count;
    bool broadcast_confirmed;

    uint32_t opened_at_ms;
    uint32_t duration_ms;   /* clamped */
    uint16_t rate_per_sec;  /* clamped */

    /* Ceilings actually in force. Seeded from the fallbacks above and
     * overwritten by mtg_apply_config(), so the operator's configuration is
     * what bounds a session rather than a compiled constant. */
    uint32_t max_session_ms;
    uint16_t max_rate_per_sec;
    bool require_allowlist;

    /* Rate accounting over a rolling one-second window. */
    uint32_t window_start_ms;
    uint16_t window_count;

    /* Ring buffer. Oldest entries are overwritten, and `audit_dropped` records
     * that they were, so a full log never reads as a complete one. */
    mtg_audit_entry_t audit[MTG_AUDIT_CAPACITY];
    uint16_t audit_count;
    uint16_t audit_head;
    uint32_t audit_dropped;
} mtg_state_t;

typedef struct {
    uint32_t duration_ms;
    uint16_t rate_per_sec;
    uint8_t allow_count;
    bool broadcast_confirmed;
} mtg_session_grant_t;

void mtg_init(mtg_state_t *state);

/**
 * Adopt the operator's configuration as the ceilings in force.
 *
 * Call after loading config from NVS and whenever the companion changes it.
 * Passing NULL leaves the compiled fallbacks in place.
 *
 * This is what makes the guard a policy engine rather than a limiter: the
 * operator decides how much and how long, the firmware enforces the decision
 * consistently and records it.
 */
void mtg_apply_config(mtg_state_t *state, uint32_t max_session_ms,
                      uint16_t max_rate_per_sec, bool require_allowlist);

/**
 * Open a transmission session.
 *
 * Returns false if a stop is latched or the allow-list is empty — an empty
 * allow-list is refused rather than treated as "everything", because
 * "unscoped" must never be the easy default.
 *
 * `granted` reports the values actually in force after clamping.
 */
bool mtg_session_open(mtg_state_t *state, uint32_t now_ms,
                      const uint8_t (*allow)[MTG_MAC_LEN], uint8_t allow_count,
                      uint32_t requested_duration_ms, uint16_t requested_rate_per_sec,
                      bool broadcast_confirmed, mtg_session_grant_t *granted);

void mtg_session_close(mtg_state_t *state);

/** Latch a stop. Unconditional and valid in any state, including no session. */
void mtg_stop(mtg_state_t *state);

/** Clear a latched stop. Deliberately separate from opening a session. */
void mtg_clear_stop(mtg_state_t *state);

/**
 * Decide whether one transmission to `bssid` is permitted right now, and
 * record the decision.
 *
 * Both permitted and refused attempts are audited: a log that only contains
 * what succeeded cannot answer "did this thing try".
 */
mtg_decision_t mtg_authorize(mtg_state_t *state, uint32_t now_ms, const uint8_t bssid[MTG_MAC_LEN]);

/** True once the session's duration ceiling has passed. */
bool mtg_session_expired(const mtg_state_t *state, uint32_t now_ms);

/** Number of audit entries currently held (not the number ever recorded). */
uint16_t mtg_audit_count(const mtg_state_t *state);

/** Oldest-first access. Returns NULL when out of range. */
const mtg_audit_entry_t *mtg_audit_at(const mtg_state_t *state, uint16_t index);

#ifdef __cplusplus
}
#endif

#endif /* MEDUSA_TX_GUARD_H */
