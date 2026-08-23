/*
 * Medusa plugins — user-authored capabilities without reflashing.
 *
 * WHY A RECIPE AND NOT NATIVE CODE
 *
 * Flipper's community grew around loadable native apps. That model does not
 * transfer to an ESP32 with tens of kilobytes of usable RAM, no MMU and no
 * process isolation: a loadable native blob there is not an app, it is
 * arbitrary code with full hardware access and no way to contain it.
 *
 * So a Medusa plugin is a short program over primitives the firmware already
 * implements — hop, dwell, scan, transmit, wait, repeat, log. That turns out
 * to cover most of what a competitor "mode" actually is, because most modes
 * are a loop over exactly those primitives with different parameters. It also
 * gets three properties native loading cannot:
 *
 *   - Safe by construction. There is no opcode for "write arbitrary memory",
 *     so a bad plugin cannot corrupt the device, only waste its time.
 *   - Small. A program is a bounded array of fixed-size steps, no malloc.
 *   - Inspectable. The companion can render a plugin's steps and show exactly
 *     what it will do before it runs. Nobody has to trust a binary.
 *
 * TRANSMISSION IS NOT A LOOPHOLE. A `tx` step calls the same
 * mtg_authorize() every built-in path uses, so plugins inherit the operator's
 * session, scope, rate and stop policy for free. A plugin cannot transmit
 * something the operator's own configuration would refuse.
 *
 * Freestanding C99, no malloc, no I/O. The VM calls out through a host table,
 * so the whole thing runs and is tested on a workstation.
 */

#ifndef MEDUSA_PLUGIN_H
#define MEDUSA_PLUGIN_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define MPL_MAGIC "#!medusa-plugin"
#define MPL_FORMAT_VERSION 1u

#define MPL_MAX_STEPS 64u
#define MPL_MAX_ID 32u
#define MPL_MAX_NAME 48u
#define MPL_MAX_AUTHOR 32u
#define MPL_MAX_TEXT 48u
#define MPL_MAX_NEST 4u

/* What a plugin is allowed to touch. Declared up front in the manifest so the
 * companion can show it before running, and so a plugin that never declared
 * `tx` can never reach a transmit step. */
typedef enum {
    MPL_CAP_RX = 1u << 0,      /* passive receive: scan, sniff */
    MPL_CAP_TX = 1u << 1,      /* transmission, still subject to the TX guard */
    MPL_CAP_STORAGE = 1u << 2, /* write findings to local storage */
} mpl_capability_t;

typedef enum {
    MPL_OP_LOG = 0,   /* log <text>                         */
    MPL_OP_CHANNEL,   /* channel <n>                        */
    MPL_OP_SWEEP,     /* sweep <from> <to> <dwell_ms>       */
    MPL_OP_SCAN,      /* scan <ms>                          */
    MPL_OP_TX,        /* tx <profile> <count>               */
    MPL_OP_WAIT,      /* wait <ms>                          */
    MPL_OP_REPEAT,    /* repeat <n>                         */
    MPL_OP_END,       /* end                                */
    MPL_OP_COUNT
} mpl_opcode_t;

typedef struct {
    mpl_opcode_t op;
    int32_t a;
    int32_t b;
    int32_t c;
    char text[MPL_MAX_TEXT];
} mpl_step_t;

typedef struct {
    char id[MPL_MAX_ID];
    char name[MPL_MAX_NAME];
    char author[MPL_MAX_AUTHOR];
    uint16_t version;
    uint32_t capabilities; /* bitmask of mpl_capability_t */
    mpl_step_t step[MPL_MAX_STEPS];
    uint16_t step_count;
} mpl_plugin_t;

typedef enum {
    MPL_OK = 0,
    MPL_ERR_MAGIC,       /* plugin.magic     — not a Medusa plugin        */
    MPL_ERR_VERSION,     /* plugin.version   — unknown format version     */
    MPL_ERR_FIELD,       /* plugin.field     — missing or malformed field */
    MPL_ERR_OPCODE,      /* plugin.opcode    — unknown step               */
    MPL_ERR_ARGS,        /* plugin.args      — bad arguments for a step   */
    MPL_ERR_OVERFLOW,    /* plugin.overflow  — too many steps             */
    MPL_ERR_UNBALANCED,  /* plugin.unbalanced— repeat without end         */
    MPL_ERR_NEST,        /* plugin.nest      — nested too deeply          */
    MPL_ERR_CAPABILITY,  /* plugin.capability— step needs an undeclared cap */
    MPL_ERR_DENIED,      /* plugin.denied    — the TX guard refused         */
    MPL_ERR_ABORTED,     /* plugin.aborted   — host asked it to stop        */
} mpl_status_t;

const char *mpl_status_code(mpl_status_t status);
const char *mpl_opcode_name(mpl_opcode_t op);

/**
 * Parse plugin source into a validated program.
 *
 * Validation is total: an accepted plugin has known opcodes, in-range
 * arguments, balanced repeat/end, declared capabilities covering every step it
 * contains, and a bounded length. Anything else is refused with a specific
 * code, because "it didn't work" is not a useful thing to show an author.
 */
mpl_status_t mpl_parse(const char *source, size_t len, mpl_plugin_t *out);

/** True if the plugin declared the capability. */
bool mpl_has_capability(const mpl_plugin_t *plugin, mpl_capability_t cap);

/* Host services. The VM never touches hardware itself; supplying these is what
 * a platform does. Any of them may be NULL, in which case the step is a no-op
 * that still advances — useful for a dry run that shows what would happen. */
typedef struct {
    void *ctx;
    void (*log)(void *ctx, const char *text);
    void (*set_channel)(void *ctx, int32_t channel);
    void (*scan)(void *ctx, int32_t ms);
    /* Returns true if the transmission was permitted. The implementation is
     * expected to consult the TX guard; the VM treats false as a hard stop. */
    bool (*transmit)(void *ctx, int32_t profile, int32_t count);
    void (*wait)(void *ctx, int32_t ms);
    /* Polled between steps so a long plugin can be interrupted. */
    bool (*should_abort)(void *ctx);
} mpl_host_t;

typedef struct {
    uint16_t steps_executed;
    uint32_t frames_requested;
    uint32_t frames_permitted;
    mpl_status_t status;
    uint16_t failed_step; /* index of the step that stopped the run */
} mpl_result_t;

/**
 * Run a parsed plugin to completion.
 *
 * Bounded by construction: `max_steps` caps total executed steps so a
 * `repeat 60000` cannot hang the device, and the host's should_abort is polled
 * between steps so an operator stop is honoured mid-run.
 */
mpl_status_t mpl_run(const mpl_plugin_t *plugin, const mpl_host_t *host,
                     uint32_t max_steps, mpl_result_t *result);

#ifdef __cplusplus
}
#endif

#endif /* MEDUSA_PLUGIN_H */
