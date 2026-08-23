/*
 * medusa_txgate — the gated transmission path.
 *
 * LAWFUL USE ONLY. For radios and networks you own, administer, or have
 * explicit written permission to assess.
 *
 * WHY THIS SKETCH EXISTS
 *
 * The threat model requires that any product transmission ship with a
 * firmware-enforced per-session enable, a BSSID allow-list, rate and duration
 * ceilings, an audit log, separate confirmation for broadcast, and a
 * persistent stop. Every other TX sketch in this tree calls
 * esp_wifi_80211_tx() directly in a loop with none of that. This one is the
 * reference for how a transmitting path is supposed to be built: the radio is
 * unreachable except through mtg_authorize(), and a refusal is not an error
 * path bolted on afterwards — it is the default.
 *
 * The guard itself is tools/esp-hw/medusa_companion/medusa_tx_guard.c,
 * symlinked in rather than copied so there is exactly one implementation, and
 * it carries 115 host-side checks. See wiki/design/threat-model.md.
 *
 * WHAT IT TRANSMITS
 *
 * One vendor-specific action frame (subtype 13) per authorised call, unicast
 * to the allow-listed target, carrying a plainly visible "MEDUSA-LAB" marker
 * so anything that captures it can identify the source and intent. It is not a
 * denial-of-service frame: no deauthentication, no disassociation, no channel
 * switch, no beacon flood. A single marked frame is enough to prove the gate
 * works end to end, and nothing more is needed for that.
 *
 * On a stock Espressif build esp_wifi_80211_tx() rejects management subtypes
 * with 0x102 (ESP_ERR_INVALID_ARG). That is expected and is reported honestly
 * rather than hidden: the gate's decision and the radio's decision are two
 * different facts and this sketch prints both.
 *
 * COMMANDS (115200 baud, newline terminated)
 *
 *   STATUS                    session state and audit depth
 *   SESSION OPEN <bssid> [duration_ms] [rate_per_sec]
 *   SESSION CLOSE
 *   TX <bssid>                attempt one authorised transmission
 *   STOP                      latch the persistent stop
 *   CLEAR STOP                release it (deliberately a separate act)
 *   AUDIT                     dump the on-device decision log
 */

#include <Arduino.h>
#include <WiFi.h>
#include <esp_wifi.h>

#include "medusa_tx_guard.h"

static mtg_state_t guard;
static uint8_t frame[128];

static bool parse_mac(const char *text, uint8_t out[6]) {
    unsigned v[6];
    if (sscanf(text, "%x:%x:%x:%x:%x:%x", &v[0], &v[1], &v[2], &v[3], &v[4], &v[5]) != 6) return false;
    for (int i = 0; i < 6; ++i) {
        if (v[i] > 0xFF) return false;
        out[i] = (uint8_t)v[i];
    }
    return true;
}

static void print_mac(const uint8_t mac[6]) {
    Serial.printf("%02X:%02X:%02X:%02X:%02X:%02X", mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
}

/* One vendor-specific action frame with a visible marker. */
static int build_marked_action(const uint8_t target[6], uint8_t *out) {
    static const char MARKER[] = "MEDUSA-LAB";
    uint8_t self[6];
    esp_wifi_get_mac(WIFI_IF_STA, self);

    int n = 0;
    out[n++] = 0xD0;  out[n++] = 0x00;              // action frame, no flags
    out[n++] = 0x00;  out[n++] = 0x00;              // duration
    memcpy(out + n, target, 6); n += 6;             // addr1 destination
    memcpy(out + n, self, 6);   n += 6;             // addr2 source (this radio)
    memcpy(out + n, target, 6); n += 6;             // addr3 bssid
    out[n++] = 0x00;  out[n++] = 0x00;              // sequence control
    out[n++] = 0x7F;                                // category: vendor specific
    out[n++] = 0xDE;  out[n++] = 0xAD;  out[n++] = 0xBE;  // an OUI-shaped tag
    memcpy(out + n, MARKER, sizeof(MARKER) - 1);
    n += (int)sizeof(MARKER) - 1;
    return n;
}

static void report(const char *label, mtg_decision_t decision, const uint8_t target[6]) {
    Serial.printf("TXGATE\t%s\tdecision=%s\ttarget=", label, mtg_decision_code(decision));
    print_mac(target);
    Serial.println();
}

static void cmd_status(void) {
    Serial.printf("TXGATE_STATUS\topen=%d\tstopped=%d\tallow=%u\tbroadcast_ok=%d\trate=%u\tduration_ms=%lu\taudit=%u\tdropped=%lu\n",
                  guard.open ? 1 : 0, guard.stopped ? 1 : 0, (unsigned)guard.allow_count,
                  guard.broadcast_confirmed ? 1 : 0, (unsigned)guard.rate_per_sec,
                  (unsigned long)guard.duration_ms, (unsigned)mtg_audit_count(&guard),
                  (unsigned long)guard.audit_dropped);
}

static void cmd_session_open(const char *args) {
    char mac_text[32] = {0};
    unsigned long duration = 0;
    unsigned rate = 0;
    int fields = sscanf(args, "%31s %lu %u", mac_text, &duration, &rate);
    if (fields < 1) { Serial.println("TXGATE_ERR\targ.usage\tSESSION OPEN <bssid> [duration_ms] [rate]"); return; }

    uint8_t allow[1][MTG_MAC_LEN];
    if (!parse_mac(mac_text, allow[0])) { Serial.println("TXGATE_ERR\targ.bssid\tunparseable"); return; }

    mtg_session_grant_t grant;
    if (!mtg_session_open(&guard, millis(), allow, 1, (uint32_t)duration, (uint16_t)rate, false, &grant)) {
        /* Refused: a latched stop, or an empty allow-list. Never silent. */
        Serial.printf("TXGATE_ERR\ttx.refused\tstopped=%d\n", guard.stopped ? 1 : 0);
        return;
    }
    /* The firmware clamps to its own ceilings and reports what is actually in
     * force, so an operator who asked for more can see they did not get it. */
    Serial.printf("TXGATE_SESSION\tgranted_duration_ms=%lu\tgranted_rate=%u\tallow=%u\tbroadcast=%d\n",
                  (unsigned long)grant.duration_ms, (unsigned)grant.rate_per_sec,
                  (unsigned)grant.allow_count, grant.broadcast_confirmed ? 1 : 0);
}

static void cmd_tx(const char *args) {
    uint8_t target[6];
    if (!parse_mac(args, target)) { Serial.println("TXGATE_ERR\targ.bssid\tunparseable"); return; }

    /* THE GATE. There is no path to the radio that does not pass here. */
    mtg_decision_t decision = mtg_authorize(&guard, millis(), target);
    if (decision != MTG_ALLOW) {
        report("refused", decision, target);
        return;
    }

    int len = build_marked_action(target, frame);
    esp_err_t rc = esp_wifi_80211_tx(WIFI_IF_STA, frame, len, true);

    /* Two different facts, reported separately: the gate permitted it, and
     * this is what the radio then did with it. */
    Serial.printf("TXGATE\tpermitted\tdecision=ok\ttarget=");
    print_mac(target);
    Serial.printf("\tbytes=%d\tradio_rc=0x%x\t%s\n", len, rc,
                  rc == 0x102 ? "(stock driver rejected the management subtype)"
                              : (rc == ESP_OK ? "(accepted by the driver)" : "(driver error)"));
}

static void cmd_audit(void) {
    uint16_t count = mtg_audit_count(&guard);
    Serial.printf("TXGATE_AUDIT\tentries=%u\tdropped=%lu\n", (unsigned)count, (unsigned long)guard.audit_dropped);
    for (uint16_t i = 0; i < count; ++i) {
        const mtg_audit_entry_t *entry = mtg_audit_at(&guard, i);
        if (!entry) continue;
        Serial.printf("TXGATE_AUDIT\t%u\tat_ms=%lu\tdecision=%s\ttarget=",
                      (unsigned)i, (unsigned long)entry->at_ms, mtg_decision_code(entry->decision));
        print_mac(entry->bssid);
        Serial.println();
    }
}

void setup() {
    Serial.begin(115200);
    delay(600);
    Serial.println("\n=== medusa_txgate ===");
    Serial.println("GATED transmission. LAWFUL USE ONLY - owned/authorized targets.");
    Serial.println("Every frame passes the firmware TX guard. No session = no transmission.");

    mtg_init(&guard);

    WiFi.mode(WIFI_STA);
    esp_wifi_start();
    esp_wifi_set_promiscuous(true);

    cmd_status();
    Serial.println("commands: STATUS | SESSION OPEN <bssid> [ms] [rate] | SESSION CLOSE | TX <bssid> | STOP | CLEAR STOP | AUDIT");
}

void loop() {
    static char line[128];
    static size_t used = 0;

    while (Serial.available()) {
        char c = (char)Serial.read();
        if (c == '\r') continue;
        if (c != '\n') {
            if (used + 1 < sizeof line) line[used++] = c;
            continue;
        }
        line[used] = '\0';
        used = 0;

        if (strcmp(line, "STATUS") == 0) {
            cmd_status();
        } else if (strncmp(line, "SESSION OPEN ", 13) == 0) {
            cmd_session_open(line + 13);
        } else if (strcmp(line, "SESSION CLOSE") == 0) {
            mtg_session_close(&guard);
            Serial.println("TXGATE_SESSION\tclosed");
        } else if (strncmp(line, "TX ", 3) == 0) {
            cmd_tx(line + 3);
        } else if (strcmp(line, "STOP") == 0) {
            /* Unconditional and latched, valid in any state. */
            mtg_stop(&guard);
            Serial.println("TXGATE_STOP\tlatched");
        } else if (strcmp(line, "CLEAR STOP") == 0) {
            mtg_clear_stop(&guard);
            Serial.println("TXGATE_STOP\tcleared");
        } else if (strcmp(line, "AUDIT") == 0) {
            cmd_audit();
        } else if (line[0] != '\0') {
            Serial.printf("TXGATE_ERR\targ.command\tunknown: %s\n", line);
        }
    }
    delay(10);
}
