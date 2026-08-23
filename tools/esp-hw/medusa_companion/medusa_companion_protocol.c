/*
 * Medusa Companion Protocol v1 — device-side codec.
 * See medusa_companion_protocol.h and wiki/design/companion-protocol.md.
 */

#include "medusa_companion_protocol.h"

#include <string.h>

const char *mc_status_code(mc_status_t status) {
    switch (status) {
        case MC_OK:            return "ok";
        case MC_ERR_SHORT:     return "proto.short";
        case MC_ERR_VERSION:   return "proto.version";
        case MC_ERR_ORPHAN:    return "proto.orphan";
        case MC_ERR_ORDER:     return "proto.order";
        case MC_ERR_MISMATCH:  return "proto.mismatch";
        case MC_ERR_LENGTH:    return "proto.length";
        case MC_ERR_CRC:       return "proto.crc";
        case MC_ERR_CAPACITY:  return "proto.capacity";
        case MC_ERR_MTU:       return "proto.mtu";
    }
    return "proto.unknown";
}

uint32_t mc_crc32(const uint8_t *data, size_t len) {
    /* Computed rather than tabulated: a 1 KiB table is real memory on a device
     * with tens of kilobytes, and this runs over frames, not megabytes. */
    uint32_t crc = 0xFFFFFFFFu;
    for (size_t i = 0; i < len; ++i) {
        crc ^= (uint32_t)data[i];
        for (int bit = 0; bit < 8; ++bit) {
            /* Reflected polynomial, the same one zlib.crc32 uses. */
            crc = (crc & 1u) ? ((crc >> 1) ^ 0xEDB88320u) : (crc >> 1);
        }
    }
    return crc ^ 0xFFFFFFFFu;
}

static uint16_t read_u16(const uint8_t *p) {
    return (uint16_t)((uint16_t)p[0] | ((uint16_t)p[1] << 8));
}

static uint32_t read_u32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static void write_u16(uint8_t *p, uint16_t value) {
    p[0] = (uint8_t)(value & 0xFFu);
    p[1] = (uint8_t)((value >> 8) & 0xFFu);
}

static void write_u32(uint8_t *p, uint32_t value) {
    p[0] = (uint8_t)(value & 0xFFu);
    p[1] = (uint8_t)((value >> 8) & 0xFFu);
    p[2] = (uint8_t)((value >> 16) & 0xFFu);
    p[3] = (uint8_t)((value >> 24) & 0xFFu);
}

mc_status_t mc_decode_header(const uint8_t *frame, size_t frame_len, mc_frame_header_t *out) {
    if (frame == NULL || out == NULL || frame_len < MC_HEADER_LEN) return MC_ERR_SHORT;
    if (frame[0] != MC_VERSION) return MC_ERR_VERSION;

    out->version = frame[0];
    out->last = (frame[1] & MC_FLAG_LAST) != 0u;
    out->msg_id = read_u16(&frame[2]);
    out->frag_index = read_u16(&frame[4]);
    out->total_len = read_u16(&frame[6]);
    out->crc32 = read_u32(&frame[8]);
    out->body = frame + MC_HEADER_LEN;
    out->body_len = frame_len - MC_HEADER_LEN;
    return MC_OK;
}

mc_status_t mc_encode_frame(const uint8_t *payload, size_t payload_len, uint16_t msg_id,
                            size_t max_frame_len, uint16_t index,
                            uint8_t *out, size_t out_cap, size_t *out_len, uint16_t *frame_count) {
    if (payload_len > MC_MAX_PAYLOAD) return MC_ERR_LENGTH;
    if (max_frame_len <= MC_HEADER_LEN) return MC_ERR_MTU;

    const size_t body = max_frame_len - MC_HEADER_LEN;
    /* An empty payload is still one frame: the peer must see the message. */
    const size_t count = (payload_len == 0u) ? 1u : ((payload_len + body - 1u) / body);
    if (frame_count != NULL) *frame_count = (uint16_t)count;
    if (index >= count) return MC_ERR_ORDER;

    const size_t offset = (size_t)index * body;
    size_t chunk = payload_len - offset;
    if (chunk > body) chunk = body;

    if (out_cap < MC_HEADER_LEN + chunk) return MC_ERR_CAPACITY;

    out[0] = (uint8_t)MC_VERSION;
    out[1] = (index + 1u == count) ? (uint8_t)MC_FLAG_LAST : 0u;
    write_u16(&out[2], msg_id);
    write_u16(&out[4], index);
    write_u16(&out[6], (uint16_t)payload_len);
    write_u32(&out[8], mc_crc32(payload, payload_len));
    if (chunk > 0u) memcpy(out + MC_HEADER_LEN, payload + offset, chunk);

    if (out_len != NULL) *out_len = MC_HEADER_LEN + chunk;
    return MC_OK;
}

void mc_reassembler_reset(mc_reassembler_t *state) {
    if (state == NULL) return;
    state->used = 0u;
    state->msg_id = 0u;
    state->expect_index = 0u;
    state->total_len = 0u;
    state->crc32 = 0u;
    state->active = false;
}

mc_status_t mc_reassembler_push(mc_reassembler_t *state, const uint8_t *frame, size_t frame_len,
                                size_t *payload_len) {
    if (state == NULL || payload_len == NULL) return MC_ERR_SHORT;
    *payload_len = 0u;

    mc_frame_header_t head;
    mc_status_t status = mc_decode_header(frame, frame_len, &head);
    if (status != MC_OK) return status;

    if (!state->active || head.msg_id != state->msg_id) {
        /* A new message abandons an incomplete one. That is what bounds this
         * device to a single reassembly buffer, and it makes a lost tail
         * self-healing rather than a permanent wedge. */
        if (head.frag_index != 0u) {
            mc_reassembler_reset(state);
            return MC_ERR_ORPHAN;
        }
        if (head.total_len > MC_REASSEMBLY_CAPACITY) {
            /* Refuse rather than truncate. A silently truncated payload would
             * fail the checksum anyway, but with a misleading code. */
            mc_reassembler_reset(state);
            return MC_ERR_CAPACITY;
        }
        state->active = true;
        state->msg_id = head.msg_id;
        state->used = 0u;
        state->expect_index = 0u;
        state->total_len = head.total_len;
        state->crc32 = head.crc32;
    }

    if (head.frag_index != state->expect_index) {
        mc_reassembler_reset(state);
        return MC_ERR_ORDER;
    }
    if (head.total_len != state->total_len || head.crc32 != state->crc32) {
        mc_reassembler_reset(state);
        return MC_ERR_MISMATCH;
    }
    if (state->used + head.body_len > state->total_len) {
        mc_reassembler_reset(state);
        return MC_ERR_LENGTH;
    }

    if (head.body_len > 0u) memcpy(state->buffer + state->used, head.body, head.body_len);
    state->used += head.body_len;
    state->expect_index++;

    if (!head.last) return MC_OK;

    if (state->used != state->total_len) {
        mc_reassembler_reset(state);
        return MC_ERR_LENGTH;
    }
    if (mc_crc32(state->buffer, state->used) != state->crc32) {
        mc_reassembler_reset(state);
        return MC_ERR_CRC;
    }

    *payload_len = state->used;
    /* Leave the payload in place for the caller, but end the message so the
     * next frame starts cleanly. */
    state->active = false;
    state->expect_index = 0u;
    return MC_OK;
}
