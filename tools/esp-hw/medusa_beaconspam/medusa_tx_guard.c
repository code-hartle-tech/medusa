/*
 * Medusa active-transmission guard. See medusa_tx_guard.h.
 */

#include "medusa_tx_guard.h"

#include <string.h>

const char *mtg_decision_code(mtg_decision_t decision) {
    switch (decision) {
        case MTG_ALLOW:                return "ok";
        case MTG_DENY_NO_SESSION:      return "tx.no_session";
        case MTG_DENY_EXPIRED:         return "tx.expired";
        case MTG_DENY_NOT_ALLOWLISTED: return "tx.scope";
        case MTG_DENY_RATE:            return "tx.rate";
        case MTG_DENY_BROADCAST:       return "tx.broadcast";
        case MTG_DENY_STOPPED:         return "tx.stopped";
    }
    return "tx.unknown";
}

void mtg_init(mtg_state_t *state) {
    if (state == NULL) return;
    memset(state, 0, sizeof(*state));
    /* Seed the fallbacks; mtg_apply_config() replaces them once the operator's
     * configuration has been read from NVS. */
    state->max_session_ms = MTG_MAX_SESSION_MS;
    state->max_rate_per_sec = MTG_MAX_RATE_PER_SEC;
    state->require_allowlist = true;
    /* Everything off. A zeroed guard denies, which is the only safe value for
     * a struct that might be reached before initialisation. */
}

static bool is_broadcast(const uint8_t bssid[MTG_MAC_LEN]) {
    for (size_t i = 0; i < MTG_MAC_LEN; ++i) {
        if (bssid[i] != 0xFFu) return false;
    }
    return true;
}

static void audit(mtg_state_t *state, uint32_t now_ms, const uint8_t bssid[MTG_MAC_LEN],
                  mtg_decision_t decision) {
    mtg_audit_entry_t *slot = &state->audit[(state->audit_head + state->audit_count) % MTG_AUDIT_CAPACITY];
    if (state->audit_count == MTG_AUDIT_CAPACITY) {
        /* Full: overwrite the oldest and say so. A log that silently drops is
         * indistinguishable from one that recorded nothing. */
        slot = &state->audit[state->audit_head];
        state->audit_head = (uint16_t)((state->audit_head + 1u) % MTG_AUDIT_CAPACITY);
        if (state->audit_dropped != 0xFFFFFFFFu) state->audit_dropped++;
    } else {
        state->audit_count++;
    }
    memcpy(slot->bssid, bssid, MTG_MAC_LEN);
    slot->at_ms = now_ms;
    slot->decision = decision;
}

bool mtg_session_open(mtg_state_t *state, uint32_t now_ms,
                      const uint8_t (*allow)[MTG_MAC_LEN], uint8_t allow_count,
                      uint32_t requested_duration_ms, uint16_t requested_rate_per_sec,
                      bool broadcast_confirmed, mtg_session_grant_t *granted) {
    if (state == NULL) return false;
    /* A latched stop outranks a request to start. The operator's stop is not
     * cleared by asking again. */
    if (state->stopped) return false;
    /* An empty allow-list is refused whenever scope is required, so unscoped
     * transmission is never reachable by omission. The operator can turn the
     * requirement off deliberately through configuration — that is a decision,
     * not an oversight, and it is recorded as one. */
    if (state->require_allowlist && (allow == NULL || allow_count == 0u)) return false;

    if (allow_count > MTG_MAX_ALLOWLIST) allow_count = MTG_MAX_ALLOWLIST;

    /* Clamp against the ceilings in force, which are the operator's unless
     * nothing has been applied yet. */
    const uint32_t max_session = state->max_session_ms ? state->max_session_ms : MTG_MAX_SESSION_MS;
    const uint16_t max_rate = state->max_rate_per_sec ? state->max_rate_per_sec : MTG_MAX_RATE_PER_SEC;

    uint32_t duration = requested_duration_ms;
    if (duration == 0u || duration > max_session) duration = max_session;
    uint16_t rate = requested_rate_per_sec;
    if (rate == 0u || rate > max_rate) rate = max_rate;

    state->open = true;
    state->allow_count = allow_count;
    for (uint8_t i = 0; i < allow_count; ++i) memcpy(state->allow[i], allow[i], MTG_MAC_LEN);
    state->broadcast_confirmed = broadcast_confirmed;
    state->opened_at_ms = now_ms;
    state->duration_ms = duration;
    state->rate_per_sec = rate;
    state->window_start_ms = now_ms;
    state->window_count = 0u;

    if (granted != NULL) {
        granted->duration_ms = duration;
        granted->rate_per_sec = rate;
        granted->allow_count = allow_count;
        granted->broadcast_confirmed = broadcast_confirmed;
    }
    return true;
}

void mtg_apply_config(mtg_state_t *state, uint32_t max_session_ms,
                      uint16_t max_rate_per_sec, bool require_allowlist) {
    if (state == NULL) return;
    if (max_session_ms) state->max_session_ms = max_session_ms;
    if (max_rate_per_sec) state->max_rate_per_sec = max_rate_per_sec;
    state->require_allowlist = require_allowlist;
}

void mtg_session_close(mtg_state_t *state) {
    if (state == NULL) return;
    state->open = false;
    state->allow_count = 0u;
    state->broadcast_confirmed = false;
    state->window_count = 0u;
    /* The audit log and any latched stop deliberately survive: both exist to
     * outlive the session they describe. */
}

void mtg_stop(mtg_state_t *state) {
    if (state == NULL) return;
    state->stopped = true;
    state->open = false;
    state->allow_count = 0u;
    state->broadcast_confirmed = false;
}

void mtg_clear_stop(mtg_state_t *state) {
    if (state == NULL) return;
    state->stopped = false;
}

bool mtg_session_expired(const mtg_state_t *state, uint32_t now_ms) {
    if (state == NULL || !state->open) return true;
    /* Unsigned wrap-around is correct here: the difference stays right across
     * the ~49.7-day millisecond rollover, where a signed comparison would not. */
    return (uint32_t)(now_ms - state->opened_at_ms) >= state->duration_ms;
}

mtg_decision_t mtg_authorize(mtg_state_t *state, uint32_t now_ms, const uint8_t bssid[MTG_MAC_LEN]) {
    if (state == NULL || bssid == NULL) return MTG_DENY_NO_SESSION;

    mtg_decision_t decision;

    if (state->stopped) {
        decision = MTG_DENY_STOPPED;
    } else if (!state->open) {
        decision = MTG_DENY_NO_SESSION;
    } else if (mtg_session_expired(state, now_ms)) {
        /* Expiry closes the session as a side effect: a session that has run
         * out must not linger in a state where a later clock change revives it. */
        state->open = false;
        decision = MTG_DENY_EXPIRED;
    } else if (is_broadcast(bssid) && !state->broadcast_confirmed) {
        decision = MTG_DENY_BROADCAST;
    } else {
        bool listed = !state->require_allowlist;
        for (uint8_t i = 0; i < state->allow_count && !listed; ++i) {
            if (memcmp(state->allow[i], bssid, MTG_MAC_LEN) == 0) listed = true;
        }
        if (!listed) {
            decision = MTG_DENY_NOT_ALLOWLISTED;
        } else {
            if ((uint32_t)(now_ms - state->window_start_ms) >= 1000u) {
                state->window_start_ms = now_ms;
                state->window_count = 0u;
            }
            if (state->window_count >= state->rate_per_sec) {
                decision = MTG_DENY_RATE;
            } else {
                state->window_count++;
                decision = MTG_ALLOW;
            }
        }
    }

    /* Refusals are audited too. A log containing only successes cannot answer
     * "did something try to transmit outside its scope". */
    audit(state, now_ms, bssid, decision);
    return decision;
}

uint16_t mtg_audit_count(const mtg_state_t *state) {
    return (state == NULL) ? 0u : state->audit_count;
}

const mtg_audit_entry_t *mtg_audit_at(const mtg_state_t *state, uint16_t index) {
    if (state == NULL || index >= state->audit_count) return NULL;
    return &state->audit[(state->audit_head + index) % MTG_AUDIT_CAPACITY];
}
