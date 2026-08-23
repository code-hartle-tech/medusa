/*
 * Medusa Wi-Fi internals lab
 *
 * Research and educational firmware for hardware and networks the operator
 * owns, administers, or has written authorization to test. This build keeps
 * Espressif's stock raw-frame validator intact. It offers:
 *
 *   - metadata-only promiscuous receive counters;
 *   - one documented, benign vendor-specific action frame per command; and
 *   - one self-addressed unsupported-subtype probe per explicit command.
 *
 * It never accepts a target MAC, never loops raw transmissions, never stores
 * captured payloads, and never interposes private Wi-Fi-library symbols.
 */

#include <ctype.h>
#include <inttypes.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "esp_chip_info.h"
#include "esp_err.h"
#include "esp_event.h"
#include "esp_idf_version.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_system.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "nvs_flash.h"

enum {
    IEEE80211_HEADER_LEN = 24,
    ACTION_FRAME_LEN = 39,
    SELF_PROBE_FRAME_LEN = 26,
};

typedef struct {
    atomic_uint_least32_t rx_callbacks;
    atomic_uint_least32_t management;
    atomic_uint_least32_t unexpected_packet_type;
    atomic_uint_least32_t beacons;
    atomic_uint_least32_t probe_requests;
    atomic_uint_least32_t deauth_seen;
    atomic_uint_least32_t tx_callbacks;
    atomic_uint_least32_t tx_callback_success;
    atomic_uint_least32_t tx_callback_failure;
    atomic_int_least32_t last_rssi;
    atomic_uint_least32_t last_channel;
} lab_stats_t;

static const char *TAG = "medusa-lab";
static const uint8_t BROADCAST_MAC[6] = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff};
static uint8_t s_sta_mac[6];
static uint8_t s_action_frame[ACTION_FRAME_LEN];
static uint8_t s_self_probe_frame[SELF_PROBE_FRAME_LEN];
static lab_stats_t s_stats;
static uint8_t s_channel = CONFIG_MEDUSA_LAB_CHANNEL;

static void copy_mac(uint8_t *destination, const uint8_t source[6])
{
    memcpy(destination, source, 6);
}

static void build_management_header(uint8_t *frame,
                                    uint8_t frame_control_low,
                                    const uint8_t destination[6],
                                    const uint8_t source[6],
                                    const uint8_t bssid[6])
{
    memset(frame, 0, IEEE80211_HEADER_LEN);
    frame[0] = frame_control_low;
    frame[1] = 0x00;
    copy_mac(frame + 4, destination);
    copy_mac(frame + 10, source);
    copy_mac(frame + 16, bssid);
    /* Sequence control stays zero; en_sys_seq=true asks the driver to own it. */
}

static void build_action_frame(void)
{
    static const uint8_t marker[] = {'M', 'E', 'D', 'U', 'S', 'A', '-', 'L', 'A', 'B'};

    build_management_header(
        s_action_frame, 0xd0, BROADCAST_MAC, s_sta_mac, s_sta_mac);
    s_action_frame[24] = 127; /* Vendor-specific action category. */
    /* Unassigned three-octet lab placeholder in the standard OUI field. */
    s_action_frame[25] = 0x02;
    s_action_frame[26] = 0x00;
    s_action_frame[27] = 0x00;
    s_action_frame[28] = 0x01; /* Medusa lab action code. */
    memcpy(s_action_frame + 29, marker, sizeof(marker));
}

static void build_self_addressed_unsupported_probe(void)
{
    /*
     * Management subtype 0x0c is outside Espressif's documented raw-TX list.
     * All three addresses are this radio's own STA MAC: there is no external
     * target even if a future driver unexpectedly accepts the frame.
     */
    build_management_header(
        s_self_probe_frame, 0xc0, s_sta_mac, s_sta_mac, s_sta_mac);
    s_self_probe_frame[24] = 0x01; /* Reason code, little endian. */
    s_self_probe_frame[25] = 0x00;
}

static void promiscuous_rx_callback(void *buffer, wifi_promiscuous_pkt_type_t packet_type)
{
    const wifi_promiscuous_pkt_t *packet = (const wifi_promiscuous_pkt_t *)buffer;

    if (packet == NULL) {
        return;
    }

    atomic_fetch_add_explicit(&s_stats.rx_callbacks, 1, memory_order_relaxed);
    atomic_store_explicit(&s_stats.last_rssi, packet->rx_ctrl.rssi, memory_order_relaxed);
    atomic_store_explicit(&s_stats.last_channel, packet->rx_ctrl.channel, memory_order_relaxed);

    switch (packet_type) {
    case WIFI_PKT_MGMT: {
        atomic_fetch_add_explicit(&s_stats.management, 1, memory_order_relaxed);
        if (packet->rx_ctrl.sig_len < 1) {
            break;
        }
        const uint8_t subtype = (packet->payload[0] >> 4) & 0x0f;
        if (subtype == 0x08) {
            atomic_fetch_add_explicit(&s_stats.beacons, 1, memory_order_relaxed);
        } else if (subtype == 0x04) {
            atomic_fetch_add_explicit(&s_stats.probe_requests, 1, memory_order_relaxed);
        } else if (subtype == 0x0c) {
            atomic_fetch_add_explicit(&s_stats.deauth_seen, 1, memory_order_relaxed);
        }
        break;
    }
    default:
        atomic_fetch_add_explicit(&s_stats.unexpected_packet_type, 1, memory_order_relaxed);
        break;
    }
}

#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(6, 0, 0)
static void raw_tx_done_callback(const esp_80211_tx_info_t *tx_info)
{
    if (tx_info == NULL) {
        return;
    }

    atomic_fetch_add_explicit(&s_stats.tx_callbacks, 1, memory_order_relaxed);
    if (tx_info->tx_status == WIFI_SEND_SUCCESS) {
        atomic_fetch_add_explicit(&s_stats.tx_callback_success, 1, memory_order_relaxed);
    } else {
        atomic_fetch_add_explicit(&s_stats.tx_callback_failure, 1, memory_order_relaxed);
    }
    /* Completion status is still not a monitor capture and therefore is not
     * proof that another radio observed the expected bytes. */
}
#endif

static void print_help(void)
{
    puts("Commands:");
    puts("  help                         show this text");
    puts("  status                       print counters and proof boundaries");
    puts("  channel <1-13>               change the fixed 2.4 GHz channel");
    puts("  tx-action                    send one documented benign action frame");
    puts("  probe-stock-gate SELF        submit one self-addressed unsupported subtype");
    puts("");
    puts("No command accepts a target MAC. There are no transmit loops.");
}

static void print_status(void)
{
    wifi_country_t country = {0};
    esp_err_t country_result = esp_wifi_get_country(&country);

    printf("target=%s idf=%s channel=%u sta_mac=" MACSTR "\n",
           CONFIG_IDF_TARGET,
           esp_get_idf_version(),
           s_channel,
           MAC2STR(s_sta_mac));
    if (country_result == ESP_OK) {
        printf("country=%.2s start_channel=%u channel_count=%u policy=%d\n",
               country.cc,
               country.schan,
               country.nchan,
               country.policy);
    }
    printf("rx callbacks=%" PRIuLEAST32 " management=%" PRIuLEAST32
           " unexpected_packet_type=%" PRIuLEAST32 "\n",
           atomic_load_explicit(&s_stats.rx_callbacks, memory_order_relaxed),
           atomic_load_explicit(&s_stats.management, memory_order_relaxed),
           atomic_load_explicit(&s_stats.unexpected_packet_type, memory_order_relaxed));
    printf("mgmt beacons=%" PRIuLEAST32 " probe_requests=%" PRIuLEAST32
           " deauth_observed=%" PRIuLEAST32 " last_rssi=%" PRIdLEAST32
           " last_channel=%" PRIuLEAST32 "\n",
           atomic_load_explicit(&s_stats.beacons, memory_order_relaxed),
           atomic_load_explicit(&s_stats.probe_requests, memory_order_relaxed),
           atomic_load_explicit(&s_stats.deauth_seen, memory_order_relaxed),
           atomic_load_explicit(&s_stats.last_rssi, memory_order_relaxed),
           atomic_load_explicit(&s_stats.last_channel, memory_order_relaxed));
#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(6, 0, 0)
    printf("tx driver_callbacks=%" PRIuLEAST32 " success=%" PRIuLEAST32
           " failure=%" PRIuLEAST32 "\n",
           atomic_load_explicit(&s_stats.tx_callbacks, memory_order_relaxed),
           atomic_load_explicit(&s_stats.tx_callback_success, memory_order_relaxed),
           atomic_load_explicit(&s_stats.tx_callback_failure, memory_order_relaxed));
#else
    puts("tx driver_callback=unavailable (requires ESP-IDF 6.0 or newer)");
#endif
    puts("proof: API return != driver callback != second-radio over-air capture != receiver action");
}

static void print_tx_result(const char *experiment, esp_err_t result)
{
    printf("%s: esp_wifi_80211_tx -> %s (0x%x)\n",
           experiment,
           esp_err_to_name(result),
           (unsigned int)result);
    puts("This return value is queue/validation evidence, not over-air proof.");
}

static void run_benign_action_experiment(void)
{
    build_action_frame();
    const esp_err_t result = esp_wifi_80211_tx(
        WIFI_IF_STA, s_action_frame, sizeof(s_action_frame), true);
    print_tx_result("benign action frame", result);
}

static void run_stock_gate_experiment(void)
{
    build_self_addressed_unsupported_probe();
    const esp_err_t result = esp_wifi_80211_tx(
        WIFI_IF_STA, s_self_probe_frame, sizeof(s_self_probe_frame), true);
    print_tx_result("self-addressed unsupported-subtype probe", result);
    if (result == ESP_OK) {
        puts("Unexpected acceptance: stop here and verify with a second monitor radio before drawing conclusions.");
    } else if (result == ESP_ERR_INVALID_ARG) {
        puts("ESP_ERR_INVALID_ARG is consistent with the stock subtype gate because this probe fixes the other public arguments.");
    } else {
        puts("Inconclusive: this error can reflect interface, state, memory, or another environmental precondition.");
    }
}

static void set_lab_channel(const char *argument)
{
    char *end = NULL;
    const long requested = strtol(argument, &end, 10);

    while (end != NULL && isspace((unsigned char)*end)) {
        ++end;
    }
    if (argument == end || end == NULL || *end != '\0' || requested < 1 || requested > 13) {
        puts("channel expects one integer in the range 1..13");
        return;
    }

    const esp_err_t result = esp_wifi_set_channel((uint8_t)requested, WIFI_SECOND_CHAN_NONE);
    if (result == ESP_OK) {
        s_channel = (uint8_t)requested;
    }
    printf("channel: %s (0x%x), current=%u\n",
           esp_err_to_name(result),
           (unsigned int)result,
           s_channel);
}

static void process_command(const char *line)
{
    if (strcmp(line, "help") == 0) {
        print_help();
    } else if (strcmp(line, "status") == 0) {
        print_status();
    } else if (strcmp(line, "tx-action") == 0) {
        run_benign_action_experiment();
    } else if (strcmp(line, "probe-stock-gate SELF") == 0) {
        run_stock_gate_experiment();
    } else if (strncmp(line, "channel ", 8) == 0) {
        set_lab_channel(line + 8);
    } else if (line[0] != '\0') {
        puts("unknown command; type 'help'");
    }
}

static void command_loop(void)
{
    char line[96];
    size_t length = 0;
    bool discarding_overlong_line = false;
    bool swallow_lf_after_cr = false;

    print_help();
    fputs("medusa-lab> ", stdout);
    fflush(stdout);

    for (;;) {
        const int character = fgetc(stdin);
        if (character == EOF) {
            /* The default ESP-IDF console VFS is non-blocking. Poll at a
             * bounded rate instead of treating temporary emptiness as EOF. */
            clearerr(stdin);
            vTaskDelay(pdMS_TO_TICKS(10));
            continue;
        }

        if (character == '\n' && swallow_lf_after_cr) {
            swallow_lf_after_cr = false;
            continue;
        }

        if (character == '\r' || character == '\n') {
            swallow_lf_after_cr = character == '\r';
            if (discarding_overlong_line) {
                puts("command too long; line discarded");
            } else {
                line[length] = '\0';
                process_command(line);
            }
            length = 0;
            discarding_overlong_line = false;
            fputs("medusa-lab> ", stdout);
            fflush(stdout);
            continue;
        }

        swallow_lf_after_cr = false;
        if (character == '\b' || character == 0x7f) {
            if (!discarding_overlong_line && length > 0) {
                --length;
            }
            continue;
        }

        if (discarding_overlong_line) {
            continue;
        }
        if (length + 1 >= sizeof(line)) {
            discarding_overlong_line = true;
            continue;
        }
        line[length++] = (char)character;
    }
}

static void initialize_nvs(void)
{
    const esp_err_t result = nvs_flash_init();
    if (result != ESP_OK) {
        ESP_LOGE(TAG,
                 "NVS init failed (%s); this lab will not erase persistent data automatically",
                 esp_err_to_name(result));
    }
    ESP_ERROR_CHECK(result);
}

static void initialize_wifi_lab(void)
{
    initialize_nvs();
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());

    wifi_init_config_t configuration = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&configuration));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_country_code(CONFIG_MEDUSA_LAB_COUNTRY_CODE, false));
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_ERROR_CHECK(esp_wifi_get_mac(WIFI_IF_STA, s_sta_mac));
    ESP_ERROR_CHECK(esp_wifi_set_channel(s_channel, WIFI_SECOND_CHAN_NONE));

    const wifi_promiscuous_filter_t filter = {
        .filter_mask = WIFI_PROMIS_FILTER_MASK_MGMT,
    };
    ESP_ERROR_CHECK(esp_wifi_set_promiscuous_filter(&filter));
    ESP_ERROR_CHECK(esp_wifi_set_promiscuous_rx_cb(promiscuous_rx_callback));
    ESP_ERROR_CHECK(esp_wifi_set_promiscuous(true));
#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(6, 0, 0)
    ESP_ERROR_CHECK(esp_wifi_register_80211_tx_cb(raw_tx_done_callback));
#endif
}

void app_main(void)
{
    setvbuf(stdin, NULL, _IONBF, 0);
    setvbuf(stdout, NULL, _IONBF, 0);

    esp_chip_info_t chip;
    esp_chip_info(&chip);

    initialize_wifi_lab();

    ESP_LOGI(TAG,
             "ready: target=%s, cores=%d, revision=%d, IDF=%s, country=%.2s, channel=%u",
             CONFIG_IDF_TARGET,
             chip.cores,
             chip.revision,
             esp_get_idf_version(),
             CONFIG_MEDUSA_LAB_COUNTRY_CODE,
             s_channel);
    ESP_LOGI(TAG, "stock Espressif raw-frame validator is intentionally intact");
    command_loop();
}
