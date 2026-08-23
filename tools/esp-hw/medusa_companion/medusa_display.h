/*
 * Medusa status display — panel-agnostic layout.
 *
 * For the autonomous-sensor variant, which has a screen and no phone attached.
 * The panels under consideration are known and small:
 *
 *   ST7789V2   240x135  (M5Stack Cardputer, an ESP32-S3)
 *   ILI9341    240x320  (ESP32-2432S028R "CYD", a classic ESP32-WROOM-32)
 *   SSD1306    128x64   (the usual small OLED on an unattended build)
 *
 * Neither of the first two is a new architecture: Medusa already builds for
 * ESP32-S3 and for classic ESP32. See
 * wiki/research/2026-08-21-continuation-candidates.md — the cost here is
 * display and input, not a chip lane.
 *
 * WHAT THIS MODULE IS: the decision about what text appears, in what order,
 * truncated how, for a given geometry. That is pure logic and it is tested on
 * the host. WHAT IT IS NOT: a panel driver. Pushing pixels is per-controller
 * and belongs behind this.
 *
 * The split exists because the interesting bugs are here — a status line that
 * silently truncates to something misleading is a correctness problem, not a
 * graphics one.
 *
 * Freestanding C99, no malloc.
 */

#ifndef MEDUSA_DISPLAY_H
#define MEDUSA_DISPLAY_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define MD_MAX_LINES 8u
#define MD_MAX_COLS 64u

typedef enum {
    MD_PANEL_ST7789_240X135 = 0, /* Cardputer */
    MD_PANEL_ILI9341_240X320,    /* CYD */
    MD_PANEL_SSD1306_128X64,     /* small OLED */
} md_panel_t;

typedef enum {
    MD_STATE_BOOTING = 0,
    MD_STATE_IDLE,
    MD_STATE_LOGGING,
    MD_STATE_STORAGE_FULL,
    MD_STATE_FAULT,
} md_state_t;

typedef struct {
    md_state_t state;
    uint32_t uptime_s;
    uint32_t observations;   /* rows recorded this run */
    uint16_t storage_pct;    /* 0..100 */
    bool storage_frozen;     /* a runtime storage failure froze new writes */
    uint32_t capacity_drops; /* observations discarded after capacity */
    bool tx_session_open;    /* an active-transmission session is open */
    const char *fault;       /* NULL unless state is MD_STATE_FAULT */
} md_status_t;

typedef struct {
    char line[MD_MAX_LINES][MD_MAX_COLS];
    uint8_t count;
    uint8_t cols; /* character columns this panel fits */
    uint8_t rows; /* text rows this panel fits */
} md_screen_t;

/** Character grid a panel provides, for the built-in font. */
void md_panel_geometry(md_panel_t panel, uint8_t *cols, uint8_t *rows);

/**
 * Compose the status screen for a panel.
 *
 * Ordering is by importance, not by convenience, because a small panel shows
 * only the first few rows: anything that means "this sensor is not recording
 * what you think it is" sorts above routine counters.
 */
void md_render_status(md_panel_t panel, const md_status_t *status, md_screen_t *out);

/**
 * Truncate `text` into `dest` of `cap` bytes (including the terminator).
 *
 * Marks truncation with a trailing '~' rather than cutting silently. A status
 * line that quietly loses its tail can turn "storage frozen" into "storage",
 * which reads as fine.
 */
void md_fit(char *dest, size_t cap, const char *text);

#endif /* MEDUSA_DISPLAY_H */
