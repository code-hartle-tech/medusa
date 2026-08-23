#!/usr/bin/env python3
"""
Medusa Companion Protocol v1 — reference codec.

Specification: wiki/design/companion-protocol.md
Conformance vectors: tools/companion/vectors/frames.json

This is the reference implementation. The TypeScript, Swift, Kotlin, and
firmware codecs are written against the same vectors, so a disagreement between
any two of them is a test failure rather than a field report.

Pure stdlib, no hardware, no I/O. The transport hands us bytes; what those
bytes travelled over is not this module's concern.
"""

import json
import zlib

VERSION = 1

# 12-byte header: version, flags, msg_id, frag_index, total_len, crc32.
HEADER_LEN = 12
FLAG_LAST = 0x01

# u16 fields cap one message at 64 KiB. Deliberate: a device with tens of
# kilobytes of usable RAM must never be asked to buffer an unbounded
# reassembly. Bulk data uses chunked store.read events instead.
MAX_PAYLOAD = 0xFFFF
MAX_FRAGMENTS = 0xFFFF


class ProtocolError(Exception):
    """A frame or envelope that cannot be trusted. Carries a stable code."""

    def __init__(self, code, message):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def crc32(payload):
    """CRC-32/ISO-HDLC over the complete reassembled payload."""
    return zlib.crc32(payload) & 0xFFFFFFFF


def encode_frames(payload, msg_id, max_frame_len):
    """Split one payload into wire frames.

    `max_frame_len` is the transport's usable bytes per frame — for BLE that is
    ATT_MTU minus 3, not the MTU itself. A caller that passes the raw MTU
    produces frames the peer silently truncates, so the header must fit with at
    least one payload byte to spare or this refuses to encode.
    """
    if not isinstance(payload, (bytes, bytearray)):
        raise ProtocolError("proto.payload", "payload must be bytes")
    payload = bytes(payload)
    if len(payload) > MAX_PAYLOAD:
        raise ProtocolError("proto.length", f"payload {len(payload)} exceeds {MAX_PAYLOAD}")
    if max_frame_len <= HEADER_LEN:
        raise ProtocolError("proto.mtu", f"frame length {max_frame_len} leaves no room for payload")
    if not 0 <= msg_id <= 0xFFFF:
        raise ProtocolError("proto.msgid", "msg_id must fit in u16")

    body = max_frame_len - HEADER_LEN
    # An empty payload is still one frame: the peer must see the message.
    chunks = [payload[i:i + body] for i in range(0, len(payload), body)] or [b""]
    if len(chunks) > MAX_FRAGMENTS:
        raise ProtocolError("proto.fragments", "payload needs more fragments than u16 allows")

    checksum = crc32(payload)
    frames = []
    for index, chunk in enumerate(chunks):
        flags = FLAG_LAST if index == len(chunks) - 1 else 0
        header = (
            bytes([VERSION, flags])
            + msg_id.to_bytes(2, "little")
            + index.to_bytes(2, "little")
            + len(payload).to_bytes(2, "little")
            + checksum.to_bytes(4, "little")
        )
        frames.append(header + chunk)
    return frames


def decode_header(frame):
    """Parse one frame header. Raises rather than returning a partial view."""
    if len(frame) < HEADER_LEN:
        raise ProtocolError("proto.short", f"frame is {len(frame)} bytes, need at least {HEADER_LEN}")
    version = frame[0]
    if version != VERSION:
        raise ProtocolError("proto.version", f"unsupported protocol version {version}")
    return {
        "version": version,
        "last": bool(frame[1] & FLAG_LAST),
        "msg_id": int.from_bytes(frame[2:4], "little"),
        "frag_index": int.from_bytes(frame[4:6], "little"),
        "total_len": int.from_bytes(frame[6:8], "little"),
        "crc32": int.from_bytes(frame[8:12], "little"),
        "body": frame[HEADER_LEN:],
    }


class Reassembler:
    """Collects fragments into payloads.

    Holds at most one incomplete message. A fragment carrying a new msg_id
    abandons whatever was in progress, which bounds device memory to a single
    reassembly buffer and makes a lost tail self-healing rather than a leak.
    """

    def __init__(self):
        self._msg_id = None
        self._parts = []
        self._expect_index = 0
        self._total_len = 0
        self._crc32 = 0

    def reset(self):
        self._msg_id = None
        self._parts = []
        self._expect_index = 0

    def push(self, frame):
        """Feed one frame. Returns a complete payload, or None if more needed."""
        head = decode_header(frame)

        if head["msg_id"] != self._msg_id:
            # New message: drop any partial one rather than splicing two.
            if head["frag_index"] != 0:
                # A mid-message fragment whose start we never saw. Ignore it
                # instead of reassembling something that will fail CRC anyway.
                self.reset()
                raise ProtocolError("proto.orphan", "first fragment of a message must have index 0")
            self._msg_id = head["msg_id"]
            self._parts = []
            self._expect_index = 0
            self._total_len = head["total_len"]
            self._crc32 = head["crc32"]

        if head["frag_index"] != self._expect_index:
            self.reset()
            raise ProtocolError("proto.order", "fragments must arrive in order")

        # A peer that changes its mind about length or checksum mid-message is
        # either buggy or splicing; either way the reassembly is worthless.
        if head["total_len"] != self._total_len or head["crc32"] != self._crc32:
            self.reset()
            raise ProtocolError("proto.mismatch", "fragment disagrees about total length or checksum")

        self._parts.append(head["body"])
        self._expect_index += 1

        if not head["last"]:
            if sum(len(p) for p in self._parts) > self._total_len:
                self.reset()
                raise ProtocolError("proto.length", "fragments exceed declared total length")
            return None

        payload = b"".join(self._parts)
        expected_len, expected_crc = self._total_len, self._crc32
        self.reset()

        if len(payload) != expected_len:
            raise ProtocolError("proto.length", f"reassembled {len(payload)} bytes, header declared {expected_len}")
        if crc32(payload) != expected_crc:
            raise ProtocolError("proto.crc", "checksum mismatch on reassembled payload")
        return payload


# --------------------------------------------------------------------------- #
# envelope
# --------------------------------------------------------------------------- #
ENVELOPE_TYPES = ("cmd", "evt", "err", "ack")


def encode_envelope(kind, op, envelope_id, args=None):
    if kind not in ENVELOPE_TYPES:
        raise ProtocolError("proto.type", f"unknown envelope type {kind!r}")
    body = {"v": VERSION, "id": envelope_id, "t": kind, "op": op}
    if args is not None:
        body["a"] = args
    # separators: no incidental whitespace on a link where every byte is a
    # fragment boundary risk.
    return json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")


def decode_envelope(payload):
    try:
        body = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("proto.json", f"payload is not valid JSON: {exc}") from exc
    if not isinstance(body, dict):
        raise ProtocolError("proto.json", "envelope must be a JSON object")
    if body.get("v") != VERSION:
        raise ProtocolError("proto.version", f"unsupported envelope version {body.get('v')!r}")
    if body.get("t") not in ENVELOPE_TYPES:
        raise ProtocolError("proto.type", f"unknown envelope type {body.get('t')!r}")
    if not isinstance(body.get("op"), str) or not body["op"]:
        raise ProtocolError("proto.op", "envelope needs a non-empty op")
    if not isinstance(body.get("id"), str) or not body["id"]:
        raise ProtocolError("proto.id", "envelope needs a non-empty correlation id")
    return body


def error_envelope(envelope_id, code, message, op="error"):
    return encode_envelope("err", op, envelope_id, {"code": code, "message": message})
