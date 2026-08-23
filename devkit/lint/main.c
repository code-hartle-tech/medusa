/*
 * medusa-plugin-lint — validate and dry-run a Medusa plugin.
 *
 * Built from the SAME parser and step machine the firmware runs
 * (tools/esp-hw/medusa_companion/medusa_plugin.c), not a reimplementation of
 * the grammar. A separate host-side validator would drift from the device
 * within a release or two, and then the tool that says "your plugin is fine"
 * would be the one lying to you.
 *
 * Dry-run executes the program against a printing host, so an author sees
 * exactly what their plugin would do — including how many frames it would ask
 * to transmit — without any hardware present and without transmitting.
 */

#include "../../tools/esp-hw/medusa_companion/medusa_plugin.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    int indent;
    int quiet;
    unsigned long frames;
} sim_t;

static void pad(const sim_t *s) {
    for (int i = 0; i < s->indent; ++i) fputs("  ", stdout);
}

static void sim_log(void *c, const char *text) {
    sim_t *s = c;
    if (s->quiet) return;
    pad(s); printf("log      %s\n", text);
}
static void sim_channel(void *c, int32_t n) {
    sim_t *s = c;
    if (s->quiet) return;
    pad(s); printf("channel  %d\n", n);
}
static void sim_scan(void *c, int32_t ms) {
    sim_t *s = c;
    if (s->quiet) return;
    pad(s); printf("scan     %d ms\n", ms);
}
static void sim_wait(void *c, int32_t ms) {
    sim_t *s = c;
    if (s->quiet) return;
    pad(s); printf("wait     %d ms\n", ms);
}
static bool sim_tx(void *c, int32_t profile, int32_t count) {
    sim_t *s = c;
    s->frames += (unsigned long)count;
    if (!s->quiet) {
        pad(s);
        printf("tx       profile %d x%d  (on hardware this passes the TX guard)\n", profile, count);
    }
    return true; /* a dry run never refuses; the device decides for real */
}

static const char *cap_list(uint32_t caps, char *out, size_t cap) {
    out[0] = '\0';
    if (caps & MPL_CAP_RX) strncat(out, "rx ", cap - strlen(out) - 1);
    if (caps & MPL_CAP_TX) strncat(out, "tx ", cap - strlen(out) - 1);
    if (caps & MPL_CAP_STORAGE) strncat(out, "storage ", cap - strlen(out) - 1);
    if (out[0] == '\0') strncat(out, "(none)", cap - 1);
    return out;
}

static int usage(void) {
    fputs("usage: medusa-plugin-lint <file.medusa> [--quiet]\n", stderr);
    return 2;
}

int main(int argc, char **argv) {
    if (argc < 2) return usage();
    const char *path = argv[1];
    int quiet = (argc > 2 && strcmp(argv[2], "--quiet") == 0);

    FILE *f = fopen(path, "rb");
    if (!f) { fprintf(stderr, "cannot open %s\n", path); return 2; }
    static char src[64 * 1024];
    size_t len = fread(src, 1, sizeof src - 1, f);
    fclose(f);
    src[len] = '\0';

    mpl_plugin_t plugin;
    mpl_status_t status = mpl_parse(src, len, &plugin);
    if (status != MPL_OK) {
        printf("INVALID  %s\n", path);
        printf("  reason: %s\n", mpl_status_code(status));
        return 1;
    }

    char caps[64];
    printf("VALID    %s\n", path);
    printf("  id       %s\n", plugin.id);
    printf("  name     %s\n", plugin.name[0] ? plugin.name : "(unnamed)");
    printf("  author   %s\n", plugin.author[0] ? plugin.author : "(anonymous)");
    printf("  version  %u\n", (unsigned)plugin.version);
    printf("  needs    %s\n", cap_list(plugin.capabilities, caps, sizeof caps));
    printf("  steps    %u\n", (unsigned)plugin.step_count);

    if (!quiet) printf("\n  dry run (nothing is transmitted):\n");
    sim_t sim = { .indent = 2, .quiet = quiet, .frames = 0 };
    mpl_host_t host;
    memset(&host, 0, sizeof host);
    host.ctx = &sim;
    host.log = sim_log;
    host.set_channel = sim_channel;
    host.scan = sim_scan;
    host.transmit = sim_tx;
    host.wait = sim_wait;

    mpl_result_t result;
    mpl_status_t run = mpl_run(&plugin, &host, 2000u, &result);

    printf("\n  executed %u steps", (unsigned)result.steps_executed);
    if (result.frames_requested) printf(", would request %lu frames", (unsigned long)result.frames_requested);
    printf("\n");
    if (run != MPL_OK) {
        printf("  run stopped: %s at step %u\n", mpl_status_code(run), (unsigned)result.failed_step);
        return 1;
    }
    if (result.steps_executed >= 2000u) {
        printf("  note: hit the dry-run step budget; on device this is bounded too\n");
    }
    return 0;
}
