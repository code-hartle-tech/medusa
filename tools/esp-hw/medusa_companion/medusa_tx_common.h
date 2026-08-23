/*
 * Gated transmission — the one way a Medusa sketch reaches the radio.
 *
 * Every transmitting capability calls medusa_tx_send() instead of
 * esp_wifi_80211_tx() directly. That single indirection is what makes the
 * operator's policy apply everywhere rather than only where somebody
 * remembered to check: session, allow-list, rate, duration, broadcast
 * confirmation, latched stop and the audit trail all come along for free.
 *
 * It also means a capability's *rate* stops being a hardcoded delay() in its
 * loop and becomes a configured value the companion owns. A sketch that used
 * to spin at whatever delay(4) happened to produce now asks for a profile and
 * gets the operator's number for it.
 *
 * The guard and configuration are process-wide singletons here on purpose. A
 * sketch runs one capability; giving each translation unit its own policy
 * state would let two paths disagree about whether a stop is latched, which is
 * exactly the bug this module exists to prevent.
 *
 * This header is C, and Arduino compiles .ino as C++, hence the extern "C".
 */

#ifndef MEDUSA_TX_COMMON_H
#define MEDUSA_TX_COMMON_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "medusa_config.h"
#include "medusa_tx_guard.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Adopt a configuration and open a transmission session for `profile`.
 *
 * `allow`/`allow_count` is the scope. Passing none is refused unless the
 * configuration explicitly says scope is not required — so a sketch cannot
 * transmit untargeted by forgetting to set a target.
 *
 * Returns false if the session could not be opened; the caller must then not
 * transmit. `granted` reports the values actually in force after clamping,
 * which is what a sketch should print so the operator sees what they got.
 */
bool medusa_tx_begin(const mcfg_t *cfg, mcfg_profile_t profile,
                     const uint8_t (*allow)[MTG_MAC_LEN], uint8_t allow_count,
                     bool broadcast_confirmed, uint32_t now_ms,
                     mtg_session_grant_t *granted);

/**
 * Authorise and send one frame.
 *
 * `target` is the address the policy is evaluated against — the destination
 * for a unicast frame, or the BSSID a broadcast frame concerns. Returns the
 * radio's return code on success, or a negative value if the guard refused,
 * in which case nothing was transmitted.
 *
 * The refusal reason is available through medusa_tx_last_decision().
 */
int medusa_tx_send(const uint8_t *frame, size_t len, const uint8_t target[MTG_MAC_LEN], uint32_t now_ms);

/** The decision recorded by the most recent medusa_tx_send(). */
mtg_decision_t medusa_tx_last_decision(void);

/** Inter-frame delay in milliseconds implied by the active profile's rate. */
uint32_t medusa_tx_pacing_ms(void);

/** Frames to emit back to back before pausing, from the active profile. */
uint16_t medusa_tx_burst(void);

/** Pause between bursts, from the active profile. */
uint16_t medusa_tx_gap_ms(void);

/** True once the session's duration has elapsed. */
bool medusa_tx_expired(uint32_t now_ms);

/** Latch an unconditional stop. Valid at any time. */
void medusa_tx_stop(void);

/** Close the session. The audit log and any latched stop survive. */
void medusa_tx_end(void);

/** Direct access for reporting — audit dumps and status lines. */
mtg_state_t *medusa_tx_guard_state(void);

#ifdef __cplusplus
}
#endif

#endif /* MEDUSA_TX_COMMON_H */
