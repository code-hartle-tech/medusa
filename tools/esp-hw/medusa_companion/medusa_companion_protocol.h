/*
 * Medusa Companion Protocol v1 — device-side codec.
 *
 * Specification: wiki/design/companion-protocol.md
 * Conformance:   tools/companion/vectors/frames.tsv
 *
 * Freestanding C99. No Arduino, no ESP-IDF, no malloc — so the same
 * translation unit compiles into the sketch and into a host test binary, and
 * the bytes this device puts on the air are checked against the same vectors
 * the Python, TypeScript, Swift, and Kotlin codecs run.
 *
 * Every buffer is caller-owned and every length is checked. This code parses
 * input from a radio link, which is the least trustworthy input surface the
 * product has.
 */

#ifndef MEDUSA_COMPANION_PROTOCOL_H
#define MEDUSA_COMPANION_PROTOCOL_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* Arduino compiles a .ino as C++ while this stays C, so without this a
 * sketch links against mangled names that do not exist. */
#ifdef __cplusplus
extern "C" {
#endif

#define MC_VERSION 1u
#define MC_HEADER_LEN 12u
#define MC_FLAG_LAST 0x01u

/* u16 total_len caps a message at 64 KiB by design; the device reassembly
 * buffer below is far smaller than that and refuses anything larger. */
#define MC_MAX_PAYLOAD 0xFFFFu

/* What one device can hold in RAM for a single incomplete message. A phone can
 * ask for more than this; it gets told no rather than crashing the sensor. */
#ifndef MC_REASSEMBLY_CAPACITY
#define MC_REASSEMBLY_CAPACITY 2048u
#endif

typedef enum {
    MC_OK = 0,
    MC_ERR_SHORT,      /* proto.short    — frame smaller than the header */
    MC_ERR_VERSION,    /* proto.version  — a version we do not implement */
    MC_ERR_ORPHAN,     /* proto.orphan   — mid-message fragment, no start */
    MC_ERR_ORDER,      /* proto.order    — fragment out of sequence */
    MC_ERR_MISMATCH,   /* proto.mismatch — fragment disagrees about the message */
    MC_ERR_LENGTH,     /* proto.length   — declared/actual length disagreement */
    MC_ERR_CRC,        /* proto.crc      — checksum failed on reassembly */
    MC_ERR_CAPACITY,   /* proto.capacity — larger than this device will buffer */
    MC_ERR_MTU,        /* proto.mtu      — frame size leaves no room for payload */
} mc_status_t;

/** Stable dotted code for a status, for putting in an err envelope. */
const char *mc_status_code(mc_status_t status);

/** CRC-32/ISO-HDLC over a complete payload. */
uint32_t mc_crc32(const uint8_t *data, size_t len);

typedef struct {
    uint8_t version;
    bool last;
    uint16_t msg_id;
    uint16_t frag_index;
    uint16_t total_len;
    uint32_t crc32;
    const uint8_t *body;
    size_t body_len;
} mc_frame_header_t;

/** Parse a frame header. Does not copy; `body` points into `frame`. */
mc_status_t mc_decode_header(const uint8_t *frame, size_t frame_len, mc_frame_header_t *out);

/**
 * Write one fragment of `payload` into `out`.
 *
 * `max_frame_len` is the transport's usable bytes per frame — for BLE that is
 * ATT_MTU minus 3, not the MTU. Returns the number of frames the whole payload
 * needs via `frame_count`, and writes fragment `index` into `out`.
 */
mc_status_t mc_encode_frame(const uint8_t *payload, size_t payload_len, uint16_t msg_id,
                            size_t max_frame_len, uint16_t index,
                            uint8_t *out, size_t out_cap, size_t *out_len, uint16_t *frame_count);

typedef struct {
    uint8_t buffer[MC_REASSEMBLY_CAPACITY];
    size_t used;
    uint16_t msg_id;
    uint16_t expect_index;
    uint16_t total_len;
    uint32_t crc32;
    bool active;
} mc_reassembler_t;

void mc_reassembler_reset(mc_reassembler_t *state);

/**
 * Feed one frame.
 *
 * On MC_OK with `*payload_len > 0` a complete, checksum-verified payload is in
 * `state->buffer`. On MC_OK with `*payload_len == 0` more fragments are needed.
 * Any error resets the reassembler: a device must not hold a half-message that
 * a peer has already given up on.
 */
mc_status_t mc_reassembler_push(mc_reassembler_t *state, const uint8_t *frame, size_t frame_len,
                                size_t *payload_len);

#ifdef __cplusplus
}
#endif

#endif /* MEDUSA_COMPANION_PROTOCOL_H */
