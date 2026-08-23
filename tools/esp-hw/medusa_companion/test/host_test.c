/*
 * Host tests for the device-side Companion Protocol codec and the active
 * transmission guard.
 *
 * Compiled and run on a workstation, not on hardware. That is the point: the
 * bytes this firmware would put on the air are checked against the same
 * conformance vectors the Python, TypeScript, Swift, and Kotlin codecs run,
 * and the TX guard's refusal rules are checked without a radio existing.
 *
 *   cc -std=c99 -Wall -Wextra -Werror -O2 \
 *      ../medusa_companion_protocol.c ../medusa_tx_guard.c host_test.c \
 *      -o /tmp/medusa-companion-test && /tmp/medusa-companion-test <frames.tsv>
 *
 * Nothing here opens a radio, a serial port, or a socket.
 */

#include "../medusa_companion_protocol.h"
#include "../medusa_config.h"
#include "../medusa_display.h"
#include "../medusa_plugin.h"
#include "../medusa_tx_common.h"
#include "../medusa_tx_guard.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int checks = 0;
static int failures = 0;

static void check(int condition, const char *label) {
    checks++;
    if (!condition) {
        failures++;
        fprintf(stderr, "FAIL: %s\n", label);
    }
}

static void check_eq_str(const char *actual, const char *expected, const char *label) {
    checks++;
    if (strcmp(actual, expected) != 0) {
        failures++;
        fprintf(stderr, "FAIL: %s\n  actual:   %s\n  expected: %s\n", label, actual, expected);
    }
}

static void check_eq_u32(uint32_t actual, uint32_t expected, const char *label) {
    checks++;
    if (actual != expected) {
        failures++;
        fprintf(stderr, "FAIL: %s\n  actual:   %u\n  expected: %u\n", label, actual, expected);
    }
}

static size_t unhex(const char *text, uint8_t *out, size_t cap) {
    size_t len = strlen(text) / 2u;
    if (len > cap) len = cap;
    for (size_t i = 0; i < len; ++i) {
        unsigned value = 0;
        sscanf(text + (i * 2u), "%2x", &value);
        out[i] = (uint8_t)value;
    }
    return len;
}

static void tohex(const uint8_t *bytes, size_t len, char *out) {
    for (size_t i = 0; i < len; ++i) sprintf(out + (i * 2u), "%02x", bytes[i]);
    out[len * 2u] = '\0';
}

/* ------------------------------------------------------------------ */
/* codec                                                              */
/* ------------------------------------------------------------------ */

static void test_crc_known_values(void) {
    check_eq_u32(mc_crc32((const uint8_t *)"hi", 2), 0xD8932AACu, "crc32(\"hi\")");
    check_eq_u32(mc_crc32((const uint8_t *)"", 0), 0u, "crc32(empty)");
    check_eq_u32(mc_crc32((const uint8_t *)"123456789", 9), 0xCBF43926u, "crc32 check vector");
    const uint8_t high = 0xFFu;
    check_eq_u32(mc_crc32(&high, 1), 0xFF000000u, "crc32(0xFF) — the sign-extension trap");
}

static void test_hand_computed_header(void) {
    uint8_t frame[64];
    size_t len = 0;
    uint16_t count = 0;
    mc_status_t status = mc_encode_frame((const uint8_t *)"hi", 2, 1, 64, 0, frame, sizeof frame, &len, &count);
    check(status == MC_OK, "encode 'hi' succeeds");
    check_eq_u32(count, 1u, "'hi' is one frame");

    char hex[160];
    tohex(frame, len, hex);
    check_eq_str(hex, "0101010000000200ac2a93d86869", "hand-computed header bytes");
}

static void test_reassembly_failures(void) {
    /* Build a real multi-fragment message to tamper with. */
    uint8_t payload[600];
    memset(payload, 'a', sizeof payload);

    uint8_t frames[8][256];
    size_t lens[8];
    uint16_t count = 0;
    for (uint16_t i = 0; i < 8; ++i) {
        mc_status_t status = mc_encode_frame(payload, sizeof payload, 9, 244, i,
                                             frames[i], sizeof frames[i], &lens[i], &count);
        if (i >= count) break;
        check(status == MC_OK, "encode fragment");
    }
    check(count > 1u, "600 bytes fragments at 244");

    mc_reassembler_t state;
    size_t out_len = 0;

    /* Truncated frame. */
    mc_reassembler_reset(&state);
    check(mc_reassembler_push(&state, frames[0], 8, &out_len) == MC_ERR_SHORT, "truncated frame refused");

    /* Unknown version is never guessed at. */
    mc_reassembler_reset(&state);
    uint8_t bad_version[256];
    memcpy(bad_version, frames[0], lens[0]);
    bad_version[0] = 0x02u;
    check(mc_reassembler_push(&state, bad_version, lens[0], &out_len) == MC_ERR_VERSION, "unknown version refused");

    /* Mid-message fragment with no start. */
    mc_reassembler_reset(&state);
    check(mc_reassembler_push(&state, frames[1], lens[1], &out_len) == MC_ERR_ORPHAN, "orphan fragment refused");

    /* Out of order. */
    mc_reassembler_reset(&state);
    check(mc_reassembler_push(&state, frames[0], lens[0], &out_len) == MC_OK, "first fragment accepted");
    check(mc_reassembler_push(&state, frames[2], lens[2], &out_len) == MC_ERR_ORDER, "out-of-order refused");

    /* Spliced: a fragment disagreeing about the message checksum. */
    mc_reassembler_reset(&state);
    uint8_t spliced[256];
    memcpy(spliced, frames[1], lens[1]);
    spliced[8] ^= 0xFFu;
    check(mc_reassembler_push(&state, frames[0], lens[0], &out_len) == MC_OK, "first fragment accepted again");
    check(mc_reassembler_push(&state, spliced, lens[1], &out_len) == MC_ERR_MISMATCH, "spliced fragment refused");

    /* Corrupt payload must fail the whole-message checksum. */
    mc_reassembler_reset(&state);
    mc_status_t last = MC_OK;
    for (uint16_t i = 0; i < count; ++i) {
        uint8_t copy[256];
        memcpy(copy, frames[i], lens[i]);
        if (i == 1u) copy[MC_HEADER_LEN] ^= 0xFFu;
        last = mc_reassembler_push(&state, copy, lens[i], &out_len);
        if (last != MC_OK) break;
    }
    check(last == MC_ERR_CRC, "corrupt payload fails the checksum");

    /* A round trip must actually work. */
    mc_reassembler_reset(&state);
    last = MC_OK;
    for (uint16_t i = 0; i < count; ++i) {
        last = mc_reassembler_push(&state, frames[i], lens[i], &out_len);
        check(last == MC_OK, "clean fragment accepted");
    }
    check(out_len == sizeof payload, "reassembled length matches");
    check(memcmp(state.buffer, payload, sizeof payload) == 0, "reassembled bytes match");

    /* A new message abandons an incomplete one. */
    mc_reassembler_reset(&state);
    check(mc_reassembler_push(&state, frames[0], lens[0], &out_len) == MC_OK, "partial message started");
    uint8_t other[64];
    size_t other_len = 0;
    mc_encode_frame((const uint8_t *)"second", 6, 10, 244, 0, other, sizeof other, &other_len, NULL);
    check(mc_reassembler_push(&state, other, other_len, &out_len) == MC_OK, "new msg_id accepted");
    check(out_len == 6u && memcmp(state.buffer, "second", 6) == 0, "new message replaced the partial one");
}

static void test_capacity_is_refused_not_truncated(void) {
    /* A peer may declare a message larger than this device will buffer. It
     * must be told no, not silently truncated into a checksum failure. */
    uint8_t frame[64];
    memset(frame, 0, sizeof frame);
    frame[0] = (uint8_t)MC_VERSION;
    frame[1] = 0u;                 /* not last */
    frame[2] = 1u; frame[3] = 0u;  /* msg_id */
    frame[4] = 0u; frame[5] = 0u;  /* frag 0 */
    /* total_len = 0xF000, far beyond MC_REASSEMBLY_CAPACITY */
    frame[6] = 0x00u; frame[7] = 0xF0u;

    mc_reassembler_t state;
    mc_reassembler_reset(&state);
    size_t out_len = 0;
    check(mc_reassembler_push(&state, frame, 20, &out_len) == MC_ERR_CAPACITY,
          "an oversized message is refused, not truncated");
}

static int run_vectors(const char *path) {
    FILE *file = fopen(path, "r");
    if (file == NULL) {
        fprintf(stderr, "FATAL: cannot open vectors at %s\n", path);
        return -1;
    }

    char line[65536];
    int vectors = 0;
    while (fgets(line, sizeof line, file) != NULL) {
        if (line[0] == '#' || line[0] == '\n') continue;
        line[strcspn(line, "\n")] = '\0';

        char *name = strtok(line, "\t");
        char *msg_id_text = strtok(NULL, "\t");
        char *max_len_text = strtok(NULL, "\t");
        char *payload_hex = strtok(NULL, "\t");
        char *frames_hex = strtok(NULL, "\t");
        if (name == NULL || msg_id_text == NULL || max_len_text == NULL) continue;
        if (payload_hex == NULL) payload_hex = (char *)"";
        /* An empty payload leaves an empty 4th field, so strtok hands the
         * frames back in the 4th slot. Detect and shift. */
        if (frames_hex == NULL) {
            frames_hex = payload_hex;
            payload_hex = (char *)"";
        }

        uint16_t msg_id = (uint16_t)strtoul(msg_id_text, NULL, 10);
        size_t max_frame_len = (size_t)strtoul(max_len_text, NULL, 10);

        static uint8_t payload[8192];
        size_t payload_len = unhex(payload_hex, payload, sizeof payload);

        uint16_t count = 0;
        char label[256];
        int index = 0;
        for (char *expected = strtok(frames_hex, ","); expected != NULL; expected = strtok(NULL, ",")) {
            static uint8_t frame[8192];
            char hex[16384];
            size_t len = 0;
            mc_status_t status = mc_encode_frame(payload, payload_len, msg_id, max_frame_len,
                                                 (uint16_t)index, frame, sizeof frame, &len, &count);
            snprintf(label, sizeof label, "vector '%s' fragment %d", name, index);
            if (status != MC_OK) {
                checks++;
                failures++;
                fprintf(stderr, "FAIL: %s — encode returned %s\n", label, mc_status_code(status));
                index++;
                continue;
            }
            tohex(frame, len, hex);
            check_eq_str(hex, expected, label);
            index++;
        }
        snprintf(label, sizeof label, "vector '%s' fragment count", name);
        check_eq_u32((uint32_t)index, (uint32_t)count, label);
        vectors++;
    }
    fclose(file);
    return vectors;
}

/* ------------------------------------------------------------------ */
/* TX guard                                                           */
/* ------------------------------------------------------------------ */

static const uint8_t OWNED[MTG_MAC_LEN] = {0x02, 0x00, 0x00, 0x00, 0x00, 0x01};
static const uint8_t STRANGER[MTG_MAC_LEN] = {0x02, 0x00, 0x00, 0x00, 0x00, 0x02};
static const uint8_t BROADCAST[MTG_MAC_LEN] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};

static void open_default(mtg_state_t *state, mtg_session_grant_t *grant, bool broadcast_ok) {
    uint8_t allow[1][MTG_MAC_LEN];
    memcpy(allow[0], OWNED, MTG_MAC_LEN);
    check(mtg_session_open(state, 1000u, allow, 1u, 30000u, 5u, broadcast_ok, grant),
          "session opens with a non-empty allow-list");
}

static void test_guard_denies_by_default(void) {
    mtg_state_t state;
    mtg_init(&state);
    /* A zeroed, never-opened guard must deny. */
    check(mtg_authorize(&state, 0u, OWNED) == MTG_DENY_NO_SESSION, "no session denies");
    check(mtg_session_expired(&state, 0u), "a closed session reads as expired");
}

static void test_empty_allowlist_is_refused(void) {
    mtg_state_t state;
    mtg_init(&state);
    mtg_session_grant_t grant;
    /* "Unscoped" must not be reachable by omission. */
    check(!mtg_session_open(&state, 0u, NULL, 0u, 1000u, 1u, false, &grant),
          "an empty allow-list is refused rather than meaning 'any target'");
}

static void test_ceilings_are_clamped_and_reported(void) {
    mtg_state_t state;
    mtg_init(&state);
    mtg_session_grant_t grant;
    uint8_t allow[1][MTG_MAC_LEN];
    memcpy(allow[0], OWNED, MTG_MAC_LEN);

    check(mtg_session_open(&state, 0u, allow, 1u, 0xFFFFFFFFu, 60000u, false, &grant), "session opens");
    check_eq_u32(grant.duration_ms, MTG_MAX_SESSION_MS, "duration clamped to the firmware ceiling");
    check_eq_u32(grant.rate_per_sec, MTG_MAX_RATE_PER_SEC, "rate clamped to the firmware ceiling");
}

static void test_allowlist_scope(void) {
    mtg_state_t state;
    mtg_init(&state);
    mtg_session_grant_t grant;
    open_default(&state, &grant, false);

    check(mtg_authorize(&state, 1000u, OWNED) == MTG_ALLOW, "an allow-listed target is permitted");
    check(mtg_authorize(&state, 1000u, STRANGER) == MTG_DENY_NOT_ALLOWLISTED, "a target outside scope is refused");
}

static void test_broadcast_needs_its_own_confirmation(void) {
    mtg_state_t state;
    mtg_init(&state);
    mtg_session_grant_t grant;

    open_default(&state, &grant, false);
    check(mtg_authorize(&state, 1000u, BROADCAST) == MTG_DENY_BROADCAST,
          "broadcast is refused without explicit confirmation");

    mtg_init(&state);
    open_default(&state, &grant, true);
    /* Even confirmed, broadcast still has to pass the allow-list; ff:ff:.. is
     * not in it, so this must still refuse. Confirmation widens one gate, not
     * all of them. */
    check(mtg_authorize(&state, 1000u, BROADCAST) == MTG_DENY_NOT_ALLOWLISTED,
          "confirmation does not bypass the allow-list");
}

static void test_rate_ceiling(void) {
    mtg_state_t state;
    mtg_init(&state);
    mtg_session_grant_t grant;
    uint8_t allow[1][MTG_MAC_LEN];
    memcpy(allow[0], OWNED, MTG_MAC_LEN);
    check(mtg_session_open(&state, 1000u, allow, 1u, 30000u, 3u, false, &grant), "session opens at 3/sec");

    check(mtg_authorize(&state, 1000u, OWNED) == MTG_ALLOW, "1st within the window");
    check(mtg_authorize(&state, 1100u, OWNED) == MTG_ALLOW, "2nd within the window");
    check(mtg_authorize(&state, 1200u, OWNED) == MTG_ALLOW, "3rd within the window");
    check(mtg_authorize(&state, 1300u, OWNED) == MTG_DENY_RATE, "4th exceeds the ceiling");
    check(mtg_authorize(&state, 2100u, OWNED) == MTG_ALLOW, "the window rolls over");
}

static void test_session_expiry(void) {
    mtg_state_t state;
    mtg_init(&state);
    mtg_session_grant_t grant;
    uint8_t allow[1][MTG_MAC_LEN];
    memcpy(allow[0], OWNED, MTG_MAC_LEN);
    mtg_session_open(&state, 1000u, allow, 1u, 5000u, 10u, false, &grant);

    check(mtg_authorize(&state, 5000u, OWNED) == MTG_ALLOW, "inside the duration");
    check(mtg_authorize(&state, 6000u, OWNED) == MTG_DENY_EXPIRED, "at the ceiling the session is over");
    check(mtg_authorize(&state, 6001u, OWNED) == MTG_DENY_NO_SESSION, "expiry closed the session");
}

static void test_expiry_survives_millisecond_rollover(void) {
    /* A session opened just before the ~49.7-day wrap must still expire on
     * time. A signed comparison here would let it run for weeks. */
    mtg_state_t state;
    mtg_init(&state);
    mtg_session_grant_t grant;
    uint8_t allow[1][MTG_MAC_LEN];
    memcpy(allow[0], OWNED, MTG_MAC_LEN);

    const uint32_t near_wrap = 0xFFFFF000u;
    mtg_session_open(&state, near_wrap, allow, 1u, 5000u, 10u, false, &grant);

    check(!mtg_session_expired(&state, near_wrap + 4000u), "not expired before the ceiling, across the wrap");
    check(mtg_session_expired(&state, near_wrap + 6000u), "expired after the ceiling, across the wrap");
}

static void test_stop_is_unconditional_and_latched(void) {
    mtg_state_t state;
    mtg_init(&state);
    mtg_session_grant_t grant;
    open_default(&state, &grant, false);
    check(mtg_authorize(&state, 1000u, OWNED) == MTG_ALLOW, "permitted before the stop");

    mtg_stop(&state);
    check(mtg_authorize(&state, 1000u, OWNED) == MTG_DENY_STOPPED, "stop takes effect immediately");

    /* The crucial property: a companion that reconnects cannot undo the stop
     * simply by opening a new session. */
    uint8_t allow[1][MTG_MAC_LEN];
    memcpy(allow[0], OWNED, MTG_MAC_LEN);
    check(!mtg_session_open(&state, 2000u, allow, 1u, 30000u, 5u, false, &grant),
          "a latched stop refuses a new session");

    mtg_clear_stop(&state);
    check(mtg_session_open(&state, 3000u, allow, 1u, 30000u, 5u, false, &grant),
          "an explicitly cleared stop allows a new session");
}

static void test_stop_works_with_no_session(void) {
    /* The persistent stop path must be valid in any state, including before
     * anything was ever opened. */
    mtg_state_t state;
    mtg_init(&state);
    mtg_stop(&state);
    check(mtg_authorize(&state, 0u, OWNED) == MTG_DENY_STOPPED, "stop with no session still denies");
}

static void test_audit_records_refusals_too(void) {
    mtg_state_t state;
    mtg_init(&state);
    mtg_session_grant_t grant;
    open_default(&state, &grant, false);

    mtg_authorize(&state, 1000u, OWNED);     /* allow */
    mtg_authorize(&state, 1000u, STRANGER);  /* deny  */

    check_eq_u32(mtg_audit_count(&state), 2u, "both the permitted and the refused attempt are logged");
    const mtg_audit_entry_t *first = mtg_audit_at(&state, 0);
    const mtg_audit_entry_t *second = mtg_audit_at(&state, 1);
    check(first != NULL && first->decision == MTG_ALLOW, "first entry is the permitted one");
    check(second != NULL && second->decision == MTG_DENY_NOT_ALLOWLISTED, "second entry is the refusal");
    check(second != NULL && memcmp(second->bssid, STRANGER, MTG_MAC_LEN) == 0, "the refused target is recorded");
}

static void test_audit_overflow_is_visible(void) {
    /* A full log must never read as a complete one. */
    mtg_state_t state;
    mtg_init(&state);
    mtg_session_grant_t grant;
    open_default(&state, &grant, false);

    for (unsigned i = 0; i < MTG_AUDIT_CAPACITY + 5u; ++i) {
        mtg_authorize(&state, 1000u, STRANGER); /* denials, so the rate limiter is not involved */
    }
    check_eq_u32(mtg_audit_count(&state), MTG_AUDIT_CAPACITY, "the log holds its capacity");
    check(state.audit_dropped == 5u, "dropped entries are counted, not silently lost");
}

static void test_session_close_keeps_the_audit_log(void) {
    mtg_state_t state;
    mtg_init(&state);
    mtg_session_grant_t grant;
    open_default(&state, &grant, false);
    mtg_authorize(&state, 1000u, OWNED);
    mtg_session_close(&state);

    check_eq_u32(mtg_audit_count(&state), 1u, "closing a session does not erase what it did");
    check(mtg_authorize(&state, 1000u, OWNED) == MTG_DENY_NO_SESSION, "a closed session denies");
}

/* ------------------------------------------------------------------ */
/* status display                                                     */
/* ------------------------------------------------------------------ */

static bool screen_contains(const md_screen_t *screen, const char *needle) {
    for (uint8_t i = 0; i < screen->count; ++i) {
        if (strstr(screen->line[i], needle) != NULL) return true;
    }
    return false;
}

static void test_truncation_is_marked_not_silent(void) {
    /* A status line that quietly loses its tail can turn "storage frozen" into
     * "storage", which reads as healthy. */
    /* cap 8 = 7 visible characters plus the terminator, so six characters of
     * text and the tilde exactly fill it. */
    char dest[8];
    md_fit(dest, sizeof dest, "STORAGE FROZEN");
    check_eq_str(dest, "STORAG~", "truncation is marked with a tilde and fills the buffer exactly");
    check_eq_u32((uint32_t)strlen(dest), 7u, "truncated output uses every available column");

    md_fit(dest, sizeof dest, "short");
    check_eq_str(dest, "short", "text that fits is untouched");

    md_fit(dest, sizeof dest, NULL);
    check_eq_str(dest, "", "a null string yields an empty line, not a crash");
}

static void test_every_panel_has_usable_geometry(void) {
    const md_panel_t panels[] = {MD_PANEL_ST7789_240X135, MD_PANEL_ILI9341_240X320, MD_PANEL_SSD1306_128X64};
    for (size_t i = 0; i < sizeof panels / sizeof panels[0]; ++i) {
        uint8_t cols = 0, rows = 0;
        md_panel_geometry(panels[i], &cols, &rows);
        check(cols > 8u && cols < MD_MAX_COLS, "panel has a sane column count");
        check(rows > 0u && rows <= MD_MAX_LINES, "panel has a sane row count");
    }
}

static void test_alarming_state_outranks_counters_on_a_short_panel(void) {
    /* The Cardputer shows four rows. If frozen storage sorts below the uptime
     * counter, the one line that matters is the one that falls off. */
    md_status_t status;
    memset(&status, 0, sizeof status);
    status.state = MD_STATE_LOGGING;
    status.storage_frozen = true;
    status.capacity_drops = 12u;
    status.observations = 900u;
    status.uptime_s = 7200u;

    md_screen_t screen;
    md_render_status(MD_PANEL_ST7789_240X135, &status, &screen);

    check(screen.count <= screen.rows, "never renders more rows than the panel has");
    check(screen_contains(&screen, "FROZEN"), "frozen storage survives a short panel");
    check(screen_contains(&screen, "DROPPED"), "dropped observations survive a short panel");
}

static void test_open_tx_session_is_visible_on_the_device(void) {
    /* An operator standing next to the hardware must be able to see that
     * something opened a transmission session, without the app that opened it. */
    md_status_t status;
    memset(&status, 0, sizeof status);
    status.state = MD_STATE_IDLE;
    status.tx_session_open = true;

    md_screen_t screen;
    md_render_status(MD_PANEL_ST7789_240X135, &status, &screen);
    check(screen_contains(&screen, "TX SESSION"), "an open TX session is shown on the panel itself");
}

static void test_fault_replaces_the_banner(void) {
    md_status_t status;
    memset(&status, 0, sizeof status);
    status.state = MD_STATE_FAULT;
    status.fault = "littlefs mount";

    md_screen_t screen;
    md_render_status(MD_PANEL_ILI9341_240X320, &status, &screen);
    check(screen_contains(&screen, "FAULT"), "a fault is announced");
    check(screen_contains(&screen, "littlefs"), "the fault reason is shown");
}

static void test_a_null_status_does_not_crash(void) {
    md_screen_t screen;
    md_render_status(MD_PANEL_SSD1306_128X64, NULL, &screen);
    check_eq_u32(screen.count, 0u, "a null status renders nothing rather than garbage");
    check(screen.cols > 0u, "geometry is still reported");
}


/* ------------------------------------------------------------------ */
/* runtime configuration                                              */
/* ------------------------------------------------------------------ */

static void test_defaults_are_operational_not_timid(void) {
    /* The whole point: a capability configured to emit a couple of frames a
     * minute is indistinguishable from one that is broken. If anyone ever
     * lowers these into uselessness, this fails. */
    mcfg_t cfg;
    mcfg_defaults(&cfg);

    check(cfg.rate[MCFG_PROFILE_DEAUTH].frames_per_sec >= 50u, "deauth default is a real rate");
    check(cfg.rate[MCFG_PROFILE_BEACON].frames_per_sec >= 100u, "beacon spam default keeps names visible");
    check(cfg.rate[MCFG_PROFILE_PROBE].frames_per_sec >= 100u, "probe flood default is a real rate");
    check(cfg.rate[MCFG_PROFILE_AUTH].frames_per_sec >= 100u, "auth flood default is a real rate");

    for (int i = 0; i < MCFG_PROFILE_COUNT; ++i) {
        check(cfg.rate[i].frames_per_sec > 0u, "no profile defaults to zero frames per second");
        check(cfg.rate[i].burst > 0u, "no profile defaults to a zero burst");
    }
    /* Usable out of the box rather than inert. */
    check(cfg.session_ms > 0u, "a session length is set by default");
    check(cfg.audit_enabled, "evidence is on by default");
}

static void test_a_zero_rate_is_repaired_not_honoured(void) {
    mcfg_t cfg;
    mcfg_defaults(&cfg);
    cfg.rate[MCFG_PROFILE_DEAUTH].frames_per_sec = 0u;
    cfg.rate[MCFG_PROFILE_DEAUTH].burst = 0u;

    check(!mcfg_sanitise(&cfg), "sanitise reports that it had to correct something");
    check(cfg.rate[MCFG_PROFILE_DEAUTH].frames_per_sec > 0u, "a zero rate is repaired");
    check(cfg.rate[MCFG_PROFILE_DEAUTH].burst > 0u, "a zero burst is repaired");
}

static void test_absurd_values_are_clamped_not_rejected(void) {
    /* A companion bug should leave a working device plus a report of what was
     * applied, never a brick. */
    mcfg_t cfg;
    mcfg_defaults(&cfg);
    cfg.rate[MCFG_PROFILE_PROBE].frames_per_sec = 65535u;
    cfg.session_ms = 0xFFFFFFFFu;
    cfg.audit_capacity = 65535u;

    check(!mcfg_sanitise(&cfg), "sanitise reports the correction");
    check(cfg.rate[MCFG_PROFILE_PROBE].frames_per_sec <= 1000u, "rate clamped to something a radio can do");
    check(cfg.session_ms <= 3600000u, "session clamped to an hour");
    check(cfg.audit_capacity <= 1024u, "audit capacity clamped");
    check(mcfg_sanitise(&cfg), "a clamped config is then reported sane");
}

static void test_blob_round_trip_survives_a_reflash(void) {
    /* The blob is what sits in NVS across a firmware update. */
    mcfg_t cfg;
    mcfg_defaults(&cfg);
    const uint8_t owned[MCFG_MAC_LEN] = {0x02, 0x11, 0x22, 0x33, 0x44, 0x55};
    check(mcfg_allow_add(&cfg, owned), "allow-list accepts a bssid");
    cfg.rate[MCFG_PROFILE_DEAUTH].frames_per_sec = 175u;
    cfg.session_ms = 60000u;

    uint8_t blob[512];
    size_t written = mcfg_serialise(&cfg, blob, sizeof blob);
    check_eq_u32((uint32_t)written, (uint32_t)mcfg_blob_size(), "serialised size matches the declared size");

    mcfg_t back;
    check(mcfg_deserialise(blob, written, &back), "blob parses");
    check_eq_u32(back.rate[MCFG_PROFILE_DEAUTH].frames_per_sec, 175u, "custom rate survives the round trip");
    check_eq_u32(back.session_ms, 60000u, "session length survives");
    check(mcfg_allow_contains(&back, owned), "allow-list survives");
}

static void test_a_corrupt_or_foreign_blob_falls_back_to_defaults(void) {
    /* Reinterpreting unknown bytes as rates is worse than forgetting them. */
    mcfg_t cfg;
    uint8_t blob[512];
    mcfg_t seed;
    mcfg_defaults(&seed);
    size_t n = mcfg_serialise(&seed, blob, sizeof blob);

    blob[0] ^= 0xFFu; /* break the magic */
    check(!mcfg_deserialise(blob, n, &cfg), "a wrong magic is refused");
    check(cfg.rate[MCFG_PROFILE_DEAUTH].frames_per_sec > 0u, "and defaults are installed instead");

    blob[0] ^= 0xFFu;
    blob[4] = 0x99u; /* wrong version */
    check(!mcfg_deserialise(blob, n, &cfg), "a wrong version is refused rather than reinterpreted");

    check(!mcfg_deserialise(NULL, 0, &cfg), "an absent blob yields defaults");
    check(cfg.session_ms > 0u, "which are usable");
}

static void test_allowlist_add_remove(void) {
    mcfg_t cfg;
    mcfg_defaults(&cfg);
    const uint8_t a[MCFG_MAC_LEN] = {0x02, 0, 0, 0, 0, 0x01};
    const uint8_t b[MCFG_MAC_LEN] = {0x02, 0, 0, 0, 0, 0x02};

    check(mcfg_allow_add(&cfg, a), "add a");
    check(mcfg_allow_add(&cfg, b), "add b");
    check(!mcfg_allow_add(&cfg, a), "duplicates are refused");
    check_eq_u32(cfg.allow_count, 2u, "count tracks");

    check(mcfg_allow_remove(&cfg, a), "remove a");
    check(!mcfg_allow_contains(&cfg, a), "a is gone");
    check(mcfg_allow_contains(&cfg, b), "b survived the compaction");
    check_eq_u32(cfg.allow_count, 1u, "count tracks after removal");
    check(!mcfg_allow_remove(&cfg, a), "removing what is absent fails");
}

static void test_allowlist_is_big_enough_for_a_real_site(void) {
    mcfg_t cfg;
    mcfg_defaults(&cfg);
    check(MCFG_ALLOWLIST_MAX >= 32u, "a site audit routinely has dozens of BSSIDs");
    for (unsigned i = 0; i < MCFG_ALLOWLIST_MAX; ++i) {
        uint8_t mac[MCFG_MAC_LEN] = {0x02, 0, 0, 0, (uint8_t)(i >> 8), (uint8_t)i};
        check(mcfg_allow_add(&cfg, mac), "fills to capacity");
    }
    uint8_t overflow[MCFG_MAC_LEN] = {0x02, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};
    check(!mcfg_allow_add(&cfg, overflow), "and refuses past it");
}


static void test_config_raises_the_ceiling_the_operator_chose(void) {
    /* The guard is a policy engine: the operator decides how much and how
     * long, and the firmware enforces that decision. A compiled constant must
     * not be able to veto a legitimate assessment rate. */
    mtg_state_t state;
    mtg_init(&state);

    mcfg_t cfg;
    mcfg_defaults(&cfg);
    mtg_apply_config(&state, cfg.session_ms, cfg.rate[MCFG_PROFILE_PROBE].frames_per_sec,
                     cfg.require_allowlist);

    uint8_t allow[1][MTG_MAC_LEN];
    memcpy(allow[0], OWNED, MTG_MAC_LEN);
    mtg_session_grant_t grant;
    check(mtg_session_open(&state, 0u, allow, 1u, cfg.session_ms,
                           cfg.rate[MCFG_PROFILE_PROBE].frames_per_sec, false, &grant),
          "session opens at the configured rate");
    check_eq_u32(grant.rate_per_sec, cfg.rate[MCFG_PROFILE_PROBE].frames_per_sec,
                 "the configured probe rate is granted in full, not cut to a compiled default");
    check(grant.rate_per_sec >= 250u, "and it is an operationally useful rate");
    check_eq_u32(grant.duration_ms, cfg.session_ms, "the configured session length is granted in full");
}

static void test_scope_requirement_is_the_operators_decision(void) {
    /* Requiring an allow-list is the default and stays the default. Turning it
     * off is a deliberate act, and the guard then permits an unlisted target
     * rather than silently continuing to refuse. */
    mtg_state_t state;
    mtg_init(&state);
    mtg_apply_config(&state, 60000u, 100u, false);

    mtg_session_grant_t grant;
    check(mtg_session_open(&state, 0u, NULL, 0u, 60000u, 100u, false, &grant),
          "with scope not required, a session opens without an allow-list");
    check(mtg_authorize(&state, 0u, STRANGER) == MTG_ALLOW,
          "and an unlisted target is permitted");

    /* Turning it back on restores refusal. */
    mtg_init(&state);
    mtg_apply_config(&state, 60000u, 100u, true);
    check(!mtg_session_open(&state, 0u, NULL, 0u, 60000u, 100u, false, &grant),
          "with scope required, an empty allow-list is refused again");
}


/* ------------------------------------------------------------------ */
/* plugins                                                            */
/* ------------------------------------------------------------------ */

static const char *PLUGIN_SURVEY =
    "#!medusa-plugin 1\n"
    "id sweep-survey\n"
    "name Channel Sweep Survey\n"
    "author hartle-tech\n"
    "version 1\n"
    "needs rx,storage\n"
    "# a comment line is ignored\n"
    "> log starting a sweep\n"
    "> repeat 2\n"
    ">   sweep 1 13 250\n"
    "> end\n";

/* Host stub: records what a plugin asked the platform to do. */
typedef struct {
    int logs, channels, scans, waits;
    int tx_calls;
    bool deny_tx;
    bool abort_now;
} plug_ctx_t;

static void ph_log(void *c, const char *t) { (void)t; ((plug_ctx_t *)c)->logs++; }
static void ph_channel(void *c, int32_t n) { (void)n; ((plug_ctx_t *)c)->channels++; }
static void ph_scan(void *c, int32_t ms) { (void)ms; ((plug_ctx_t *)c)->scans++; }
static void ph_wait(void *c, int32_t ms) { (void)ms; ((plug_ctx_t *)c)->waits++; }
static bool ph_tx(void *c, int32_t p, int32_t n) {
    (void)p; (void)n;
    plug_ctx_t *ctx = (plug_ctx_t *)c;
    ctx->tx_calls++;
    return !ctx->deny_tx;
}
static bool ph_abort(void *c) { return ((plug_ctx_t *)c)->abort_now; }

static mpl_host_t make_host(plug_ctx_t *ctx) {
    mpl_host_t h;
    memset(&h, 0, sizeof h);
    h.ctx = ctx;
    h.log = ph_log;
    h.set_channel = ph_channel;
    h.scan = ph_scan;
    h.transmit = ph_tx;
    h.wait = ph_wait;
    h.should_abort = ph_abort;
    return h;
}

static void test_plugin_parses_and_runs(void) {
    mpl_plugin_t plugin;
    check(mpl_parse(PLUGIN_SURVEY, strlen(PLUGIN_SURVEY), &plugin) == MPL_OK, "survey plugin parses");
    check_eq_str(plugin.id, "sweep-survey", "id read");
    check_eq_str(plugin.name, "Channel Sweep Survey", "name read");
    check(mpl_has_capability(&plugin, MPL_CAP_RX), "declared rx");
    check(!mpl_has_capability(&plugin, MPL_CAP_TX), "did not declare tx");

    plug_ctx_t ctx; memset(&ctx, 0, sizeof ctx);
    mpl_host_t host = make_host(&ctx);
    mpl_result_t r;
    check(mpl_run(&plugin, &host, 1000u, &r) == MPL_OK, "survey runs clean");
    check_eq_u32((uint32_t)ctx.logs, 1u, "logged once");
    /* sweep 1..13 twice = 26 channel changes and 26 dwells */
    check_eq_u32((uint32_t)ctx.channels, 26u, "repeat actually repeated the sweep");
    check_eq_u32((uint32_t)ctx.scans, 26u, "and dwelt on each channel");
}

static void test_a_plugin_cannot_transmit_without_declaring_it(void) {
    /* The declaration is what the companion shows the operator before running,
     * so it has to be true. A hidden tx step is refused at parse time. */
    const char *sneaky =
        "#!medusa-plugin 1\n"
        "id sneaky\n"
        "needs rx\n"
        "> tx 0 100\n";
    mpl_plugin_t plugin;
    check(mpl_parse(sneaky, strlen(sneaky), &plugin) == MPL_ERR_CAPABILITY,
          "a tx step without a tx declaration is refused");
}

static void test_the_tx_guard_still_governs_plugins(void) {
    /* Plugins are not a way around the operator's policy. */
    const char *sender =
        "#!medusa-plugin 1\n"
        "id sender\n"
        "needs tx\n"
        "> tx 0 50\n"
        "> log should not be reached\n";
    mpl_plugin_t plugin;
    check(mpl_parse(sender, strlen(sender), &plugin) == MPL_OK, "tx plugin parses when declared");

    plug_ctx_t ctx; memset(&ctx, 0, sizeof ctx);
    ctx.deny_tx = true;
    mpl_host_t host = make_host(&ctx);
    mpl_result_t r;
    check(mpl_run(&plugin, &host, 1000u, &r) == MPL_ERR_DENIED, "a refused transmission stops the plugin");
    check_eq_u32(r.frames_permitted, 0u, "nothing was permitted");
    check_eq_u32((uint32_t)ctx.logs, 0u, "and the rest of the program did not run");
}

static void test_malformed_plugins_are_refused_with_specific_codes(void) {
    /* "It didn't work" is not useful to an author. */
    mpl_plugin_t plugin;
    struct { const char *src; mpl_status_t want; const char *why; } cases[] = {
        { "not a plugin\n", MPL_ERR_MAGIC, "a plain text file is not executed" },
        { "#!medusa-plugin 9\nid x\n", MPL_ERR_VERSION, "unknown format version" },
        { "#!medusa-plugin 1\nname no id\n", MPL_ERR_FIELD, "id is mandatory" },
        { "#!medusa-plugin 1\nid x\n> frobnicate 1\n", MPL_ERR_OPCODE, "unknown opcode" },
        { "#!medusa-plugin 1\nid x\nneeds rx\n> channel 99\n", MPL_ERR_ARGS, "channel out of range" },
        { "#!medusa-plugin 1\nid x\nneeds rx\n> repeat 2\n", MPL_ERR_UNBALANCED, "repeat without end" },
        { "#!medusa-plugin 1\nid x\nneeds bogus\n", MPL_ERR_FIELD, "unknown capability" },
    };
    for (size_t i = 0; i < sizeof cases / sizeof cases[0]; ++i) {
        mpl_status_t got = mpl_parse(cases[i].src, strlen(cases[i].src), &plugin);
        check(got == cases[i].want, cases[i].why);
    }
}

static void test_a_runaway_loop_cannot_hang_the_device(void) {
    const char *runaway =
        "#!medusa-plugin 1\n"
        "id runaway\n"
        "needs rx\n"
        "> repeat 10000\n"
        ">   scan 1\n"
        "> end\n";
    mpl_plugin_t plugin;
    check(mpl_parse(runaway, strlen(runaway), &plugin) == MPL_OK, "runaway parses");

    plug_ctx_t ctx; memset(&ctx, 0, sizeof ctx);
    mpl_host_t host = make_host(&ctx);
    mpl_result_t r;
    mpl_run(&plugin, &host, 50u, &r);
    check(r.steps_executed <= 50u, "the step budget bounds a runaway loop");
}

static void test_an_operator_stop_interrupts_a_running_plugin(void) {
    mpl_plugin_t plugin;
    check(mpl_parse(PLUGIN_SURVEY, strlen(PLUGIN_SURVEY), &plugin) == MPL_OK, "parses");

    plug_ctx_t ctx; memset(&ctx, 0, sizeof ctx);
    ctx.abort_now = true;
    mpl_host_t host = make_host(&ctx);
    mpl_result_t r;
    check(mpl_run(&plugin, &host, 1000u, &r) == MPL_ERR_ABORTED, "abort is honoured");
    check_eq_u32((uint32_t)ctx.scans, 0u, "and nothing ran");
}

static void test_unknown_metadata_keys_do_not_break_older_firmware(void) {
    /* A plugin written for a later firmware should still load. */
    const char *future =
        "#!medusa-plugin 1\n"
        "id forward\n"
        "needs rx\n"
        "some-future-key with a value\n"
        "> scan 100\n";
    mpl_plugin_t plugin;
    check(mpl_parse(future, strlen(future), &plugin) == MPL_OK, "unknown keys are ignored, not fatal");
    check_eq_u32(plugin.step_count, 1u, "and the program still loaded");
}


/* ------------------------------------------------------------------ */
/* gated transmission — the single path to the radio                  */
/* ------------------------------------------------------------------ */

extern unsigned long medusa_tx_test_frames_sent;

static void test_a_refused_frame_is_never_transmitted(void) {
    /* The point of routing every sketch through one call: a refusal must stop
     * the frame, not merely note it. */
    mcfg_t cfg;
    mcfg_defaults(&cfg);

    uint8_t allow[1][MTG_MAC_LEN];
    memcpy(allow[0], OWNED, MTG_MAC_LEN);
    mtg_session_grant_t grant;
    check(medusa_tx_begin(&cfg, MCFG_PROFILE_DEAUTH, allow, 1u, false, 0u, &grant),
          "a session opens for the deauth profile");

    const uint8_t frame[26] = {0xC0, 0x00};
    unsigned long before = medusa_tx_test_frames_sent;

    check(medusa_tx_send(frame, sizeof frame, OWNED, 0u) >= 0, "an in-scope frame is sent");
    check_eq_u32((uint32_t)(medusa_tx_test_frames_sent - before), 1u, "exactly one frame went out");

    before = medusa_tx_test_frames_sent;
    check(medusa_tx_send(frame, sizeof frame, STRANGER, 0u) < 0, "an out-of-scope frame is refused");
    check(medusa_tx_last_decision() == MTG_DENY_NOT_ALLOWLISTED, "and the reason is recorded");
    check_eq_u32((uint32_t)(medusa_tx_test_frames_sent - before), 0u, "nothing was transmitted");
}

static void test_transmission_is_impossible_without_a_session(void) {
    mcfg_t cfg;
    mcfg_defaults(&cfg);
    medusa_tx_end();

    const uint8_t frame[26] = {0xC0, 0x00};
    unsigned long before = medusa_tx_test_frames_sent;
    check(medusa_tx_send(frame, sizeof frame, OWNED, 0u) < 0, "no session means no transmission");
    check_eq_u32((uint32_t)(medusa_tx_test_frames_sent - before), 0u, "and nothing went out");
}

static void test_a_sketch_cannot_transmit_untargeted_by_forgetting_scope(void) {
    mcfg_t cfg;
    mcfg_defaults(&cfg); /* require_allowlist defaults true */
    mtg_session_grant_t grant;
    check(!medusa_tx_begin(&cfg, MCFG_PROFILE_BEACON, NULL, 0u, false, 0u, &grant),
          "omitting the scope refuses the session rather than meaning 'anything'");
}

static void test_pacing_comes_from_configuration_not_a_hardcoded_delay(void) {
    /* This is what replaces delay(4) in the sketches. */
    mcfg_t cfg;
    mcfg_defaults(&cfg);
    uint8_t allow[1][MTG_MAC_LEN];
    memcpy(allow[0], OWNED, MTG_MAC_LEN);
    mtg_session_grant_t grant;

    medusa_tx_begin(&cfg, MCFG_PROFILE_PROBE, allow, 1u, false, 0u, &grant);
    check(medusa_tx_pacing_ms() <= 4u, "250/sec paces at about 4ms, matching the old hardcoded delay");
    check(medusa_tx_burst() > 1u, "and a burst size comes from the profile");

    cfg.rate[MCFG_PROFILE_PROBE].frames_per_sec = 10u;
    medusa_tx_begin(&cfg, MCFG_PROFILE_PROBE, allow, 1u, false, 0u, &grant);
    check_eq_u32(medusa_tx_pacing_ms(), 100u, "changing the config changes the pacing, with no rebuild");
}

static void test_a_latched_stop_reaches_every_transmitter(void) {
    /* One guard for the process: a stop cannot be true for one path and false
     * for another. */
    mcfg_t cfg;
    mcfg_defaults(&cfg);
    uint8_t allow[1][MTG_MAC_LEN];
    memcpy(allow[0], OWNED, MTG_MAC_LEN);
    mtg_session_grant_t grant;
    medusa_tx_begin(&cfg, MCFG_PROFILE_CSA, allow, 1u, false, 0u, &grant);

    const uint8_t frame[26] = {0x80, 0x00};
    check(medusa_tx_send(frame, sizeof frame, OWNED, 0u) >= 0, "sending works before the stop");

    medusa_tx_stop();
    unsigned long before = medusa_tx_test_frames_sent;
    check(medusa_tx_send(frame, sizeof frame, OWNED, 0u) < 0, "and stops immediately after");
    check_eq_u32((uint32_t)(medusa_tx_test_frames_sent - before), 0u, "with nothing transmitted");

    check(!medusa_tx_begin(&cfg, MCFG_PROFILE_CSA, allow, 1u, false, 0u, &grant),
          "a latched stop also refuses a fresh session");
}

int main(int argc, char **argv) {
    test_crc_known_values();
    test_hand_computed_header();
    test_reassembly_failures();
    test_capacity_is_refused_not_truncated();

    test_guard_denies_by_default();
    test_empty_allowlist_is_refused();
    test_ceilings_are_clamped_and_reported();
    test_allowlist_scope();
    test_broadcast_needs_its_own_confirmation();
    test_rate_ceiling();
    test_session_expiry();
    test_expiry_survives_millisecond_rollover();
    test_stop_is_unconditional_and_latched();
    test_stop_works_with_no_session();
    test_audit_records_refusals_too();
    test_audit_overflow_is_visible();
    test_session_close_keeps_the_audit_log();

    test_truncation_is_marked_not_silent();
    test_every_panel_has_usable_geometry();
    test_alarming_state_outranks_counters_on_a_short_panel();
    test_open_tx_session_is_visible_on_the_device();
    test_fault_replaces_the_banner();
    test_a_null_status_does_not_crash();

    test_defaults_are_operational_not_timid();
    test_a_zero_rate_is_repaired_not_honoured();
    test_absurd_values_are_clamped_not_rejected();
    test_blob_round_trip_survives_a_reflash();
    test_a_corrupt_or_foreign_blob_falls_back_to_defaults();
    test_allowlist_add_remove();
    test_allowlist_is_big_enough_for_a_real_site();
    test_config_raises_the_ceiling_the_operator_chose();
    test_scope_requirement_is_the_operators_decision();

    test_plugin_parses_and_runs();
    test_a_plugin_cannot_transmit_without_declaring_it();
    test_the_tx_guard_still_governs_plugins();
    test_malformed_plugins_are_refused_with_specific_codes();
    test_a_runaway_loop_cannot_hang_the_device();
    test_an_operator_stop_interrupts_a_running_plugin();
    test_unknown_metadata_keys_do_not_break_older_firmware();

    test_a_refused_frame_is_never_transmitted();
    test_transmission_is_impossible_without_a_session();
    test_a_sketch_cannot_transmit_untargeted_by_forgetting_scope();
    test_pacing_comes_from_configuration_not_a_hardcoded_delay();
    test_a_latched_stop_reaches_every_transmitter();

    int vectors = 0;
    if (argc > 1) {
        vectors = run_vectors(argv[1]);
        if (vectors < 0) return 2;
    } else {
        fprintf(stderr, "note: no vector file given; conformance vectors were not run\n");
    }

    if (failures == 0) {
        printf("PASS: %d device-side checks, including %d conformance vectors\n", checks, vectors);
        return 0;
    }
    fprintf(stderr, "%d of %d checks failed\n", failures, checks);
    return 1;
}
