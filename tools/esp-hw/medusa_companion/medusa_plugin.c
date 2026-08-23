/*
 * Medusa plugins — parser and step machine. See medusa_plugin.h.
 */

#include "medusa_plugin.h"

#include <stdlib.h>
#include <string.h>

const char *mpl_status_code(mpl_status_t status) {
    switch (status) {
        case MPL_OK:              return "ok";
        case MPL_ERR_MAGIC:       return "plugin.magic";
        case MPL_ERR_VERSION:     return "plugin.version";
        case MPL_ERR_FIELD:       return "plugin.field";
        case MPL_ERR_OPCODE:      return "plugin.opcode";
        case MPL_ERR_ARGS:        return "plugin.args";
        case MPL_ERR_OVERFLOW:    return "plugin.overflow";
        case MPL_ERR_UNBALANCED:  return "plugin.unbalanced";
        case MPL_ERR_NEST:        return "plugin.nest";
        case MPL_ERR_CAPABILITY:  return "plugin.capability";
        case MPL_ERR_DENIED:      return "plugin.denied";
        case MPL_ERR_ABORTED:     return "plugin.aborted";
    }
    return "plugin.unknown";
}

const char *mpl_opcode_name(mpl_opcode_t op) {
    switch (op) {
        case MPL_OP_LOG:     return "log";
        case MPL_OP_CHANNEL: return "channel";
        case MPL_OP_SWEEP:   return "sweep";
        case MPL_OP_SCAN:    return "scan";
        case MPL_OP_TX:      return "tx";
        case MPL_OP_WAIT:    return "wait";
        case MPL_OP_REPEAT:  return "repeat";
        case MPL_OP_END:     return "end";
        case MPL_OP_COUNT:   break;
    }
    return "?";
}

/* ------------------------------------------------------------------ */
/* small text helpers — no allocation, no locale                       */
/* ------------------------------------------------------------------ */

static bool is_space(char c) { return c == ' ' || c == '\t' || c == '\r'; }

static const char *skip_space(const char *p, const char *end) {
    while (p < end && is_space(*p)) p++;
    return p;
}

/* Copy a whitespace-delimited token. Returns the position after it. */
static const char *take_token(const char *p, const char *end, char *out, size_t cap) {
    p = skip_space(p, end);
    size_t n = 0;
    while (p < end && !is_space(*p) && *p != '\n') {
        if (n + 1u < cap) out[n++] = *p;
        p++;
    }
    out[n] = '\0';
    return p;
}

/* Copy the rest of the line, trimmed. Used for free text such as log lines. */
static void take_rest(const char *p, const char *end, char *out, size_t cap) {
    p = skip_space(p, end);
    size_t n = 0;
    while (p < end && *p != '\n') {
        if (n + 1u < cap) out[n++] = *p;
        p++;
    }
    while (n > 0 && is_space(out[n - 1u])) n--;
    out[n] = '\0';
}

static bool parse_int(const char *text, int32_t *out) {
    if (text == NULL || *text == '\0') return false;
    char *stop = NULL;
    long value = strtol(text, &stop, 10);
    if (stop == text || (stop != NULL && *stop != '\0')) return false;
    *out = (int32_t)value;
    return true;
}

static void copy_field(char *dest, size_t cap, const char *src) {
    size_t n = strlen(src);
    if (n >= cap) n = cap - 1u;
    memcpy(dest, src, n);
    dest[n] = '\0';
}

/* ------------------------------------------------------------------ */
/* parser                                                              */
/* ------------------------------------------------------------------ */

static mpl_status_t parse_capability_list(const char *text, uint32_t *out) {
    /* Comma separated: rx,tx,storage */
    uint32_t caps = 0;
    const char *p = text;
    while (*p) {
        char token[16];
        size_t n = 0;
        while (*p && *p != ',') {
            if (n + 1u < sizeof token) token[n++] = *p;
            p++;
        }
        token[n] = '\0';
        if (*p == ',') p++;
        if (token[0] == '\0') continue;
        if (strcmp(token, "rx") == 0) caps |= MPL_CAP_RX;
        else if (strcmp(token, "tx") == 0) caps |= MPL_CAP_TX;
        else if (strcmp(token, "storage") == 0) caps |= MPL_CAP_STORAGE;
        else return MPL_ERR_FIELD;
    }
    *out = caps;
    return MPL_OK;
}

static mpl_status_t parse_step(const char *p, const char *end, mpl_step_t *step) {
    char word[24];
    p = take_token(p, end, word, sizeof word);
    memset(step, 0, sizeof(*step));

    if (strcmp(word, "log") == 0) {
        step->op = MPL_OP_LOG;
        take_rest(p, end, step->text, sizeof step->text);
        return MPL_OK;
    }

    char a[24] = {0}, b[24] = {0}, c[24] = {0};
    p = take_token(p, end, a, sizeof a);
    p = take_token(p, end, b, sizeof b);
    take_token(p, end, c, sizeof c);

    if (strcmp(word, "channel") == 0) {
        step->op = MPL_OP_CHANNEL;
        if (!parse_int(a, &step->a)) return MPL_ERR_ARGS;
        /* 2.4 GHz channel space. 14 exists only in some regions and the
         * configured regulatory domain remains authoritative at runtime. */
        if (step->a < 1 || step->a > 14) return MPL_ERR_ARGS;
        return MPL_OK;
    }
    if (strcmp(word, "sweep") == 0) {
        step->op = MPL_OP_SWEEP;
        if (!parse_int(a, &step->a) || !parse_int(b, &step->b) || !parse_int(c, &step->c)) return MPL_ERR_ARGS;
        if (step->a < 1 || step->a > 14 || step->b < step->a || step->b > 14) return MPL_ERR_ARGS;
        if (step->c < 1 || step->c > 60000) return MPL_ERR_ARGS;
        return MPL_OK;
    }
    if (strcmp(word, "scan") == 0) {
        step->op = MPL_OP_SCAN;
        if (!parse_int(a, &step->a)) return MPL_ERR_ARGS;
        if (step->a < 1 || step->a > 600000) return MPL_ERR_ARGS;
        return MPL_OK;
    }
    if (strcmp(word, "tx") == 0) {
        step->op = MPL_OP_TX;
        if (!parse_int(a, &step->a) || !parse_int(b, &step->b)) return MPL_ERR_ARGS;
        if (step->a < 0 || step->a > 15) return MPL_ERR_ARGS;
        if (step->b < 1 || step->b > 100000) return MPL_ERR_ARGS;
        return MPL_OK;
    }
    if (strcmp(word, "wait") == 0) {
        step->op = MPL_OP_WAIT;
        if (!parse_int(a, &step->a)) return MPL_ERR_ARGS;
        if (step->a < 0 || step->a > 600000) return MPL_ERR_ARGS;
        return MPL_OK;
    }
    if (strcmp(word, "repeat") == 0) {
        step->op = MPL_OP_REPEAT;
        if (!parse_int(a, &step->a)) return MPL_ERR_ARGS;
        if (step->a < 1 || step->a > 10000) return MPL_ERR_ARGS;
        return MPL_OK;
    }
    if (strcmp(word, "end") == 0) {
        step->op = MPL_OP_END;
        return MPL_OK;
    }
    return MPL_ERR_OPCODE;
}

mpl_status_t mpl_parse(const char *source, size_t len, mpl_plugin_t *out) {
    if (source == NULL || out == NULL) return MPL_ERR_FIELD;
    memset(out, 0, sizeof(*out));

    const char *p = source;
    const char *end = source + len;

    /* The first line identifies the format and its version. Refusing anything
     * else keeps an unrelated text file from being executed as a program. */
    if ((size_t)(end - p) < strlen(MPL_MAGIC) || strncmp(p, MPL_MAGIC, strlen(MPL_MAGIC)) != 0) {
        return MPL_ERR_MAGIC;
    }
    p += strlen(MPL_MAGIC);
    char version_text[16];
    p = take_token(p, end, version_text, sizeof version_text);
    int32_t version = 0;
    if (!parse_int(version_text, &version)) return MPL_ERR_VERSION;
    if ((uint32_t)version != MPL_FORMAT_VERSION) return MPL_ERR_VERSION;

    bool have_id = false;
    int nest = 0;

    while (p < end) {
        while (p < end && *p != '\n') p++;   /* finish the current line */
        if (p < end) p++;                     /* step over the newline  */
        if (p >= end) break;

        const char *line = skip_space(p, end);
        const char *line_end = line;
        while (line_end < end && *line_end != '\n') line_end++;

        if (line >= line_end) { p = line_end; continue; }      /* blank */
        if (*line == '#') { p = line_end; continue; }          /* comment */

        if (*line == '>') {
            /* A step. */
            if (out->step_count >= MPL_MAX_STEPS) return MPL_ERR_OVERFLOW;
            mpl_step_t step;
            mpl_status_t status = parse_step(line + 1, line_end, &step);
            if (status != MPL_OK) return status;

            if (step.op == MPL_OP_REPEAT) {
                if (++nest > (int)MPL_MAX_NEST) return MPL_ERR_NEST;
            } else if (step.op == MPL_OP_END) {
                if (--nest < 0) return MPL_ERR_UNBALANCED;
            } else if (step.op == MPL_OP_TX) {
                /* A plugin that never declared tx cannot contain one. The
                 * declaration is what the companion shows the operator, so it
                 * has to be true. */
                if ((out->capabilities & MPL_CAP_TX) == 0u) return MPL_ERR_CAPABILITY;
            } else if (step.op == MPL_OP_SCAN || step.op == MPL_OP_SWEEP) {
                if ((out->capabilities & MPL_CAP_RX) == 0u) return MPL_ERR_CAPABILITY;
            }

            out->step[out->step_count++] = step;
            p = line_end;
            continue;
        }

        /* Otherwise a metadata field: `key value`. */
        char key[24];
        const char *after = take_token(line, line_end, key, sizeof key);
        char value[MPL_MAX_NAME];
        take_rest(after, line_end, value, sizeof value);

        if (strcmp(key, "id") == 0) {
            if (value[0] == '\0') return MPL_ERR_FIELD;
            copy_field(out->id, sizeof out->id, value);
            have_id = true;
        } else if (strcmp(key, "name") == 0) {
            copy_field(out->name, sizeof out->name, value);
        } else if (strcmp(key, "author") == 0) {
            copy_field(out->author, sizeof out->author, value);
        } else if (strcmp(key, "version") == 0) {
            int32_t v = 0;
            if (!parse_int(value, &v) || v < 0 || v > 65535) return MPL_ERR_FIELD;
            out->version = (uint16_t)v;
        } else if (strcmp(key, "needs") == 0) {
            uint32_t caps = 0;
            mpl_status_t status = parse_capability_list(value, &caps);
            if (status != MPL_OK) return status;
            out->capabilities = caps;
        } else {
            /* Unknown keys are ignored rather than fatal, so a plugin written
             * for a later firmware still loads on an older one. */
        }
        p = line_end;
    }

    if (!have_id) return MPL_ERR_FIELD;
    if (nest != 0) return MPL_ERR_UNBALANCED;
    return MPL_OK;
}

bool mpl_has_capability(const mpl_plugin_t *plugin, mpl_capability_t cap) {
    return plugin != NULL && (plugin->capabilities & (uint32_t)cap) != 0u;
}

/* ------------------------------------------------------------------ */
/* step machine                                                        */
/* ------------------------------------------------------------------ */

typedef struct {
    uint16_t start_index; /* step after the repeat */
    int32_t remaining;
} mpl_frame_t;

mpl_status_t mpl_run(const mpl_plugin_t *plugin, const mpl_host_t *host,
                     uint32_t max_steps, mpl_result_t *result) {
    mpl_result_t local;
    memset(&local, 0, sizeof local);
    local.status = MPL_OK;

    if (plugin == NULL) {
        local.status = MPL_ERR_FIELD;
        if (result) *result = local;
        return local.status;
    }

    mpl_frame_t stack[MPL_MAX_NEST];
    int depth = 0;
    uint32_t budget = max_steps ? max_steps : 100000u;

    for (uint16_t pc = 0; pc < plugin->step_count; ++pc) {
        if (budget-- == 0u) {
            /* Not an error: a bounded run that hit its ceiling. Reported so an
             * author can see their loop was longer than the budget allowed. */
            break;
        }
        if (host && host->should_abort && host->should_abort(host->ctx)) {
            local.status = MPL_ERR_ABORTED;
            local.failed_step = pc;
            break;
        }

        const mpl_step_t *s = &plugin->step[pc];
        local.steps_executed++;

        switch (s->op) {
            case MPL_OP_LOG:
                if (host && host->log) host->log(host->ctx, s->text);
                break;
            case MPL_OP_CHANNEL:
                if (host && host->set_channel) host->set_channel(host->ctx, s->a);
                break;
            case MPL_OP_SWEEP:
                for (int32_t ch = s->a; ch <= s->b; ++ch) {
                    if (host && host->set_channel) host->set_channel(host->ctx, ch);
                    if (host && host->scan) host->scan(host->ctx, s->c);
                }
                break;
            case MPL_OP_SCAN:
                if (host && host->scan) host->scan(host->ctx, s->a);
                break;
            case MPL_OP_TX:
                local.frames_requested += (uint32_t)s->b;
                if (host && host->transmit) {
                    if (!host->transmit(host->ctx, s->a, s->b)) {
                        /* The guard said no. Stop rather than continue with the
                         * rest of a program whose premise no longer holds. */
                        local.status = MPL_ERR_DENIED;
                        local.failed_step = pc;
                        pc = plugin->step_count; /* terminate the loop */
                        continue;
                    }
                    local.frames_permitted += (uint32_t)s->b;
                }
                break;
            case MPL_OP_WAIT:
                if (host && host->wait) host->wait(host->ctx, s->a);
                break;
            case MPL_OP_REPEAT:
                if (depth >= (int)MPL_MAX_NEST) {
                    local.status = MPL_ERR_NEST;
                    local.failed_step = pc;
                    pc = plugin->step_count;
                    continue;
                }
                stack[depth].start_index = (uint16_t)(pc + 1u);
                stack[depth].remaining = s->a;
                depth++;
                break;
            case MPL_OP_END:
                if (depth == 0) {
                    local.status = MPL_ERR_UNBALANCED;
                    local.failed_step = pc;
                    pc = plugin->step_count;
                    continue;
                }
                if (--stack[depth - 1].remaining > 0) {
                    pc = (uint16_t)(stack[depth - 1].start_index - 1u); /* ++pc lands on start */
                } else {
                    depth--;
                }
                break;
            case MPL_OP_COUNT:
            default:
                local.status = MPL_ERR_OPCODE;
                local.failed_step = pc;
                pc = plugin->step_count;
                continue;
        }
    }

    if (result) *result = local;
    return local.status;
}
