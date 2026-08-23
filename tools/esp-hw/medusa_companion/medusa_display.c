/*
 * Medusa status display — panel-agnostic layout. See medusa_display.h.
 */

#include "medusa_display.h"

#include <stdio.h>
#include <string.h>

void md_panel_geometry(md_panel_t panel, uint8_t *cols, uint8_t *rows) {
    /* Character cells for the built-in 6x8 font at the scale each panel can
     * carry legibly at arm's length. */
    uint8_t c = 20u, r = 4u;
    switch (panel) {
        case MD_PANEL_ST7789_240X135: c = 20u; r = 4u; break;  /* 240/12, 135/32 */
        case MD_PANEL_ILI9341_240X320: c = 20u; r = 8u; break; /* taller panel  */
        case MD_PANEL_SSD1306_128X64:  c = 21u; r = 6u; break; /* 128/6, 64/10  */
    }
    if (cols != NULL) *cols = c;
    if (rows != NULL) *rows = r;
}

void md_fit(char *dest, size_t cap, const char *text) {
    if (dest == NULL || cap == 0u) return;
    if (text == NULL) {
        dest[0] = '\0';
        return;
    }
    size_t len = strlen(text);
    if (len < cap) {
        memcpy(dest, text, len + 1u);
        return;
    }
    /* Mark the cut. A silently truncated status line can turn "storage frozen"
     * into "storage", which reads as healthy. */
    if (cap == 1u) {
        dest[0] = '\0';
        return;
    }
    memcpy(dest, text, cap - 2u);
    dest[cap - 2u] = '~';
    dest[cap - 1u] = '\0';
}

static const char *state_word(md_state_t state) {
    switch (state) {
        case MD_STATE_BOOTING:      return "BOOTING";
        case MD_STATE_IDLE:         return "IDLE";
        case MD_STATE_LOGGING:      return "LOGGING";
        case MD_STATE_STORAGE_FULL: return "STORAGE FULL";
        case MD_STATE_FAULT:        return "FAULT";
    }
    return "UNKNOWN";
}

static void push(md_screen_t *screen, const char *text) {
    if (screen->count >= screen->rows || screen->count >= MD_MAX_LINES) return;
    size_t cap = (size_t)screen->cols + 1u;
    if (cap > MD_MAX_COLS) cap = MD_MAX_COLS;
    md_fit(screen->line[screen->count], cap, text);
    screen->count++;
}

void md_render_status(md_panel_t panel, const md_status_t *status, md_screen_t *out) {
    if (out == NULL) return;
    memset(out, 0, sizeof(*out));
    md_panel_geometry(panel, &out->cols, &out->rows);
    if (status == NULL) return;

    char buffer[MD_MAX_COLS * 2u];

    /* Row order is by importance, because a 4-row panel shows only the first
     * four. Anything meaning "this sensor is not recording what you think" has
     * to sort above routine counters. */

    if (status->state == MD_STATE_FAULT) {
        snprintf(buffer, sizeof buffer, "FAULT");
        push(out, buffer);
        snprintf(buffer, sizeof buffer, "%s", status->fault != NULL ? status->fault : "unspecified");
        push(out, buffer);
    } else {
        snprintf(buffer, sizeof buffer, "MEDUSA %s", state_word(status->state));
        push(out, buffer);
    }

    /* Frozen storage outranks everything else that is not a fault: the sensor
     * looks alive and is silently keeping nothing. */
    if (status->storage_frozen) {
        push(out, "STORAGE FROZEN");
    }

    /* Dropped observations mean the record is incomplete. Say so before the
     * count, so a partial number is never read as a total. */
    if (status->capacity_drops > 0u) {
        snprintf(buffer, sizeof buffer, "DROPPED %lu", (unsigned long)status->capacity_drops);
        push(out, buffer);
    }

    /* An open transmission session must be visible on the device itself, not
     * only in whatever app opened it. */
    if (status->tx_session_open) {
        push(out, "TX SESSION OPEN");
    }

    snprintf(buffer, sizeof buffer, "seen %lu", (unsigned long)status->observations);
    push(out, buffer);

    snprintf(buffer, sizeof buffer, "disk %u%%", (unsigned)(status->storage_pct > 100u ? 100u : status->storage_pct));
    push(out, buffer);

    const uint32_t hours = status->uptime_s / 3600u;
    const uint32_t minutes = (status->uptime_s % 3600u) / 60u;
    snprintf(buffer, sizeof buffer, "up %luh%02lum", (unsigned long)hours, (unsigned long)minutes);
    push(out, buffer);
}
