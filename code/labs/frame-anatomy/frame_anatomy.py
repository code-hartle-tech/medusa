#!/usr/bin/env python3
"""Offline IEEE 802.11 management-frame anatomy helpers.

This module only transforms bytes and regular files.  It has no socket, radio,
serial, subprocess, ESP-IDF, or packet-injection integration.  Frames produced
here omit the four-byte Frame Check Sequence (FCS), as is common when a driver
or capture interface owns FCS handling.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import struct
from typing import Any, Iterable, Sequence


PROTOCOL_VERSION = 0
TYPE_MANAGEMENT = 0
SUBTYPE_DEAUTHENTICATION = 12
SUBTYPE_ACTION = 13
VENDOR_SPECIFIC_ACTION_CATEGORY = 127

MAC_HEADER_LENGTH = 24
DEAUTHENTICATION_BODY_LENGTH = 2
VENDOR_ACTION_PREFIX_LENGTH = 4  # category (1) + OUI (3)

# A deliberately conservative lab guard.  This is an input-safety bound, not a
# claim that every PHY/standard revision accepts a frame of this size.
MAX_FRAME_LENGTH = 4095
MAX_VENDOR_CONTENT_LENGTH = (
    MAX_FRAME_LENGTH - MAC_HEADER_LENGTH - VENDOR_ACTION_PREFIX_LENGTH
)

PCAP_MAGIC_USEC = 0xA1B2C3D4
PCAP_VERSION_MAJOR = 2
PCAP_VERSION_MINOR = 4
LINKTYPE_IEEE802_11 = 105
PCAP_GLOBAL_HEADER_LENGTH = 24
PCAP_RECORD_HEADER_LENGTH = 16

_FC_FLAG_BITS = {
    "to_ds": 8,
    "from_ds": 9,
    "more_fragments": 10,
    "retry": 11,
    "power_management": 12,
    "more_data": 13,
    "protected": 14,
    "order": 15,
}


class FrameAnatomyError(ValueError):
    """Raised when a frame or field is outside this lab's supported subset."""


class PcapError(FrameAnatomyError):
    """Raised when a PCAP is malformed or uses an unsupported encoding."""


def _uint(name: str, value: int, bits: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise FrameAnatomyError(f"{name} must be an integer")
    upper = (1 << bits) - 1
    if not 0 <= value <= upper:
        raise FrameAnatomyError(f"{name} must be between 0 and {upper}")
    return value


def _bytes(name: str, value: bytes | bytearray | memoryview) -> bytes:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise FrameAnatomyError(f"{name} must be bytes-like")
    return bytes(value)


def parse_mac(value: str | bytes | bytearray | memoryview) -> bytes:
    """Return a six-byte MAC address from bytes or colon/hyphen notation."""

    if isinstance(value, (bytes, bytearray, memoryview)):
        result = bytes(value)
    elif isinstance(value, str):
        compact = value.replace(":", "").replace("-", "")
        if len(compact) != 12:
            raise FrameAnatomyError("MAC address must contain exactly 6 octets")
        try:
            result = bytes.fromhex(compact)
        except ValueError as exc:
            raise FrameAnatomyError("MAC address contains non-hex characters") from exc
    else:
        raise FrameAnatomyError("MAC address must be text or bytes-like")
    if len(result) != 6:
        raise FrameAnatomyError("MAC address must contain exactly 6 octets")
    return result


def format_mac(value: bytes | bytearray | memoryview) -> str:
    address = parse_mac(value)
    return ":".join(f"{octet:02x}" for octet in address)


def parse_oui(value: str | bytes | bytearray | memoryview) -> bytes:
    """Return a three-byte Organizationally Unique Identifier."""

    if isinstance(value, (bytes, bytearray, memoryview)):
        result = bytes(value)
    elif isinstance(value, str):
        compact = value.replace(":", "").replace("-", "")
        if len(compact) != 6:
            raise FrameAnatomyError("OUI must contain exactly 3 octets")
        try:
            result = bytes.fromhex(compact)
        except ValueError as exc:
            raise FrameAnatomyError("OUI contains non-hex characters") from exc
    else:
        raise FrameAnatomyError("OUI must be text or bytes-like")
    if len(result) != 3:
        raise FrameAnatomyError("OUI must contain exactly 3 octets")
    return result


def format_oui(value: bytes | bytearray | memoryview) -> str:
    oui = parse_oui(value)
    return ":".join(f"{octet:02x}" for octet in oui)


def frame_control_value(
    *,
    frame_type: int,
    subtype: int,
    protocol_version: int = PROTOCOL_VERSION,
    to_ds: bool = False,
    from_ds: bool = False,
    more_fragments: bool = False,
    retry: bool = False,
    power_management: bool = False,
    more_data: bool = False,
    protected: bool = False,
    order: bool = False,
) -> int:
    """Assemble the 16-bit IEEE 802.11 Frame Control field."""

    value = _uint("protocol_version", protocol_version, 2)
    value |= _uint("frame_type", frame_type, 2) << 2
    value |= _uint("subtype", subtype, 4) << 4
    flags = {
        "to_ds": to_ds,
        "from_ds": from_ds,
        "more_fragments": more_fragments,
        "retry": retry,
        "power_management": power_management,
        "more_data": more_data,
        "protected": protected,
        "order": order,
    }
    for name, enabled in flags.items():
        if not isinstance(enabled, bool):
            raise FrameAnatomyError(f"{name} must be a boolean")
        if enabled:
            value |= 1 << _FC_FLAG_BITS[name]
    return value


def pack_frame_control(**fields: Any) -> bytes:
    """Pack :func:`frame_control_value` in 802.11 little-endian byte order."""

    return struct.pack("<H", frame_control_value(**fields))


def decode_frame_control(value: int | bytes | bytearray | memoryview) -> dict[str, Any]:
    """Decode a Frame Control integer or its two little-endian bytes."""

    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        if len(raw) != 2:
            raise FrameAnatomyError("Frame Control must be exactly 2 bytes")
        number = struct.unpack("<H", raw)[0]
    else:
        number = _uint("frame_control", value, 16)

    result: dict[str, Any] = {
        "raw": number,
        "raw_hex_le": struct.pack("<H", number).hex(),
        "protocol_version": number & 0x3,
        "type": (number >> 2) & 0x3,
        "subtype": (number >> 4) & 0xF,
    }
    result.update(
        {name: bool(number & (1 << bit)) for name, bit in _FC_FLAG_BITS.items()}
    )
    return result


def pack_sequence_control(*, sequence_number: int = 0, fragment_number: int = 0) -> bytes:
    """Pack sequence (12 bits) and fragment (4 bits) in little-endian order."""

    sequence = _uint("sequence_number", sequence_number, 12)
    fragment = _uint("fragment_number", fragment_number, 4)
    return struct.pack("<H", (sequence << 4) | fragment)


def _management_header(
    *,
    subtype: int,
    receiver: str | bytes | bytearray | memoryview,
    transmitter: str | bytes | bytearray | memoryview,
    bssid: str | bytes | bytearray | memoryview,
    duration: int,
    sequence_number: int,
    fragment_number: int,
) -> bytes:
    # For a management frame with To DS = From DS = 0:
    # Address 1 = receiver/destination, Address 2 = transmitter/source,
    # Address 3 = BSSID.
    return b"".join(
        (
            pack_frame_control(frame_type=TYPE_MANAGEMENT, subtype=subtype),
            struct.pack("<H", _uint("duration", duration, 16)),
            parse_mac(receiver),
            parse_mac(transmitter),
            parse_mac(bssid),
            pack_sequence_control(
                sequence_number=sequence_number, fragment_number=fragment_number
            ),
        )
    )


def build_deauthentication_frame(
    *,
    receiver: str | bytes | bytearray | memoryview,
    transmitter: str | bytes | bytearray | memoryview,
    bssid: str | bytes | bytearray | memoryview,
    reason_code: int,
    duration: int = 0,
    sequence_number: int = 0,
    fragment_number: int = 0,
) -> bytes:
    """Build an FCS-free deauthentication frame for offline inspection."""

    header = _management_header(
        subtype=SUBTYPE_DEAUTHENTICATION,
        receiver=receiver,
        transmitter=transmitter,
        bssid=bssid,
        duration=duration,
        sequence_number=sequence_number,
        fragment_number=fragment_number,
    )
    body = struct.pack("<H", _uint("reason_code", reason_code, 16))
    return header + body


def build_vendor_action_frame(
    *,
    receiver: str | bytes | bytearray | memoryview,
    transmitter: str | bytes | bytearray | memoryview,
    bssid: str | bytes | bytearray | memoryview,
    oui: str | bytes | bytearray | memoryview,
    vendor_content: bytes | bytearray | memoryview = b"",
    duration: int = 0,
    sequence_number: int = 0,
    fragment_number: int = 0,
) -> bytes:
    """Build an FCS-free category-127 vendor-specific action frame."""

    content = _bytes("vendor_content", vendor_content)
    if len(content) > MAX_VENDOR_CONTENT_LENGTH:
        raise FrameAnatomyError(
            f"vendor_content exceeds lab bound of {MAX_VENDOR_CONTENT_LENGTH} bytes"
        )
    header = _management_header(
        subtype=SUBTYPE_ACTION,
        receiver=receiver,
        transmitter=transmitter,
        bssid=bssid,
        duration=duration,
        sequence_number=sequence_number,
        fragment_number=fragment_number,
    )
    body = bytes((VENDOR_SPECIFIC_ACTION_CATEGORY,)) + parse_oui(oui) + content
    return header + body


def _decode_management_header(frame: bytes) -> dict[str, Any]:
    if len(frame) < MAC_HEADER_LENGTH:
        raise FrameAnatomyError(
            f"management frame is shorter than {MAC_HEADER_LENGTH}-byte header"
        )
    if len(frame) > MAX_FRAME_LENGTH:
        raise FrameAnatomyError(
            f"frame exceeds lab bound of {MAX_FRAME_LENGTH} bytes"
        )

    control = decode_frame_control(frame[0:2])
    if control["protocol_version"] != PROTOCOL_VERSION:
        raise FrameAnatomyError("only protocol version 0 is supported")
    if control["type"] != TYPE_MANAGEMENT:
        raise FrameAnatomyError("only management frames are supported")
    if control["to_ds"] or control["from_ds"]:
        raise FrameAnatomyError("management frames must have To DS = From DS = 0")

    sequence_control = struct.unpack_from("<H", frame, 22)[0]
    return {
        "length": len(frame),
        "fcs_included": False,
        "frame_control": control,
        "duration": struct.unpack_from("<H", frame, 2)[0],
        "addresses": {
            "address_1": format_mac(frame[4:10]),
            "address_1_role": "receiver/destination",
            "receiver": format_mac(frame[4:10]),
            "destination": format_mac(frame[4:10]),
            "address_2": format_mac(frame[10:16]),
            "address_2_role": "transmitter/source",
            "transmitter": format_mac(frame[10:16]),
            "source": format_mac(frame[10:16]),
            "address_3": format_mac(frame[16:22]),
            "address_3_role": "bssid",
            "bssid": format_mac(frame[16:22]),
        },
        "sequence_control": {
            "raw": sequence_control,
            "raw_hex_le": frame[22:24].hex(),
            "sequence_number": sequence_control >> 4,
            "fragment_number": sequence_control & 0xF,
        },
    }


def decode_deauthentication_frame(
    value: bytes | bytearray | memoryview,
) -> dict[str, Any]:
    """Decode exactly one FCS-free deauthentication frame."""

    frame = _bytes("frame", value)
    expected = MAC_HEADER_LENGTH + DEAUTHENTICATION_BODY_LENGTH
    if len(frame) != expected:
        raise FrameAnatomyError(
            f"FCS-free deauthentication frame must be exactly {expected} bytes"
        )
    decoded = _decode_management_header(frame)
    if decoded["frame_control"]["subtype"] != SUBTYPE_DEAUTHENTICATION:
        raise FrameAnatomyError("frame is not a deauthentication subtype")
    decoded["kind"] = "deauthentication"
    decoded["body"] = {
        "reason_code": struct.unpack_from("<H", frame, MAC_HEADER_LENGTH)[0],
        "reason_code_hex_le": frame[MAC_HEADER_LENGTH:expected].hex(),
    }
    return decoded


def decode_vendor_action_frame(
    value: bytes | bytearray | memoryview,
) -> dict[str, Any]:
    """Decode one FCS-free category-127 vendor-specific action frame."""

    frame = _bytes("frame", value)
    minimum = MAC_HEADER_LENGTH + VENDOR_ACTION_PREFIX_LENGTH
    if len(frame) < minimum:
        raise FrameAnatomyError(
            f"vendor-specific action frame must be at least {minimum} bytes"
        )
    decoded = _decode_management_header(frame)
    if decoded["frame_control"]["subtype"] != SUBTYPE_ACTION:
        raise FrameAnatomyError("frame is not an action subtype")
    if frame[MAC_HEADER_LENGTH] != VENDOR_SPECIFIC_ACTION_CATEGORY:
        raise FrameAnatomyError("only category-127 vendor-specific action is supported")
    decoded["kind"] = "vendor-specific action"
    decoded["body"] = {
        "category": frame[MAC_HEADER_LENGTH],
        "oui": format_oui(frame[MAC_HEADER_LENGTH + 1 : MAC_HEADER_LENGTH + 4]),
        "vendor_content_hex": frame[MAC_HEADER_LENGTH + 4 :].hex(),
        "vendor_content_length": len(frame) - minimum,
    }
    return decoded


def decode_frame(value: bytes | bytearray | memoryview) -> dict[str, Any]:
    """Decode one frame from the two management subtypes this lab supports."""

    frame = _bytes("frame", value)
    if len(frame) < 2:
        raise FrameAnatomyError("frame is too short to contain Frame Control")
    control = decode_frame_control(frame[:2])
    if control["type"] != TYPE_MANAGEMENT:
        raise FrameAnatomyError("only management frames are supported")
    if control["subtype"] == SUBTYPE_DEAUTHENTICATION:
        return decode_deauthentication_frame(frame)
    if control["subtype"] == SUBTYPE_ACTION:
        return decode_vendor_action_frame(frame)
    raise FrameAnatomyError(
        f"unsupported management subtype {control['subtype']}; "
        "supported subtypes are deauthentication (12) and action (13)"
    )


def _normalise_frames(
    frames: Iterable[bytes | bytearray | memoryview], *, snaplen: int
) -> list[bytes]:
    frame_list = [_bytes("frame", frame) for frame in frames]
    for frame in frame_list:
        if not frame:
            raise PcapError("PCAP records may not contain an empty frame")
        if len(frame) > MAX_FRAME_LENGTH:
            raise PcapError(f"frame exceeds lab bound of {MAX_FRAME_LENGTH} bytes")
        if len(frame) > snaplen:
            raise PcapError(f"frame length {len(frame)} exceeds snaplen {snaplen}")
    return frame_list


def _timestamp_parts(timestamp: float | int) -> tuple[int, int]:
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
        raise PcapError("timestamp must be a finite non-negative number")
    numeric = float(timestamp)
    if not math.isfinite(numeric) or numeric < 0:
        raise PcapError("timestamp must be a finite non-negative number")
    seconds = math.floor(numeric)
    microseconds = round((numeric - seconds) * 1_000_000)
    if microseconds == 1_000_000:
        seconds += 1
        microseconds = 0
    _uint("timestamp seconds", seconds, 32)
    return seconds, microseconds


def build_pcap(
    frames: Iterable[bytes | bytearray | memoryview],
    *,
    timestamps: Iterable[float | int] | None = None,
    snaplen: int = MAX_FRAME_LENGTH,
) -> bytes:
    """Encode FCS-free raw 802.11 frames as little-endian microsecond PCAP."""

    snaplen = _uint("snaplen", snaplen, 32)
    if snaplen == 0:
        raise PcapError("snaplen must be greater than zero")
    frame_list = _normalise_frames(frames, snaplen=snaplen)
    if timestamps is None:
        timestamp_list: list[float | int] = [0] * len(frame_list)
    else:
        timestamp_list = list(timestamps)
        if len(timestamp_list) != len(frame_list):
            raise PcapError("timestamps must have exactly one entry per frame")

    output = bytearray(
        struct.pack(
            "<IHHiIII",
            PCAP_MAGIC_USEC,
            PCAP_VERSION_MAJOR,
            PCAP_VERSION_MINOR,
            0,
            0,
            snaplen,
            LINKTYPE_IEEE802_11,
        )
    )
    for frame, timestamp in zip(frame_list, timestamp_list):
        seconds, microseconds = _timestamp_parts(timestamp)
        output.extend(
            struct.pack("<IIII", seconds, microseconds, len(frame), len(frame))
        )
        output.extend(frame)
    return bytes(output)


def write_pcap(
    path: str | Path,
    frames: Iterable[bytes | bytearray | memoryview],
    *,
    timestamps: Iterable[float | int] | None = None,
    snaplen: int = MAX_FRAME_LENGTH,
) -> None:
    """Write a PCAP to an ordinary file; no capture interface is involved."""

    Path(path).write_bytes(build_pcap(frames, timestamps=timestamps, snaplen=snaplen))


def decode_pcap(value: bytes | bytearray | memoryview) -> dict[str, Any]:
    """Decode this lab's little-endian raw-802.11 PCAP subset."""

    data = _bytes("pcap", value)
    if len(data) < PCAP_GLOBAL_HEADER_LENGTH:
        raise PcapError("PCAP is shorter than its 24-byte global header")
    magic, major, minor, thiszone, sigfigs, snaplen, network = struct.unpack_from(
        "<IHHiIII", data, 0
    )
    if magic != PCAP_MAGIC_USEC:
        raise PcapError("only little-endian microsecond PCAP is supported")
    if (major, minor) != (PCAP_VERSION_MAJOR, PCAP_VERSION_MINOR):
        raise PcapError(f"unsupported PCAP version {major}.{minor}")
    if network != LINKTYPE_IEEE802_11:
        raise PcapError(
            f"unsupported PCAP linktype {network}; expected {LINKTYPE_IEEE802_11}"
        )
    if snaplen == 0:
        raise PcapError("PCAP snaplen must be greater than zero")

    records: list[dict[str, Any]] = []
    offset = PCAP_GLOBAL_HEADER_LENGTH
    while offset < len(data):
        if len(data) - offset < PCAP_RECORD_HEADER_LENGTH:
            raise PcapError("truncated PCAP record header")
        seconds, microseconds, included, original = struct.unpack_from(
            "<IIII", data, offset
        )
        offset += PCAP_RECORD_HEADER_LENGTH
        if microseconds >= 1_000_000:
            raise PcapError("PCAP microseconds field is outside 0..999999")
        if included > snaplen:
            raise PcapError("PCAP included length exceeds snaplen")
        if included > original:
            raise PcapError("PCAP included length exceeds original length")
        if included != original:
            raise PcapError("truncated frame records are not supported by this lab")
        end = offset + included
        if end > len(data):
            raise PcapError("truncated PCAP frame data")
        frame = data[offset:end]
        records.append(
            {
                "timestamp_seconds": seconds,
                "timestamp_microseconds": microseconds,
                "included_length": included,
                "original_length": original,
                "frame_hex": frame.hex(),
                "decoded": decode_frame(frame),
            }
        )
        offset = end

    return {
        "format": "pcap",
        "byte_order": "little-endian",
        "timestamp_resolution": "microseconds",
        "version": f"{major}.{minor}",
        "thiszone": thiszone,
        "sigfigs": sigfigs,
        "snaplen": snaplen,
        "linktype": network,
        "linktype_name": "LINKTYPE_IEEE802_11",
        "records": records,
    }


def read_pcap(path: str | Path) -> dict[str, Any]:
    return decode_pcap(Path(path).read_bytes())


def _hex_bytes(name: str, value: str) -> bytes:
    compact = "".join(value.split()).replace(":", "")
    if len(compact) % 2:
        raise FrameAnatomyError(f"{name} must contain an even number of hex digits")
    try:
        return bytes.fromhex(compact)
    except ValueError as exc:
        raise FrameAnatomyError(f"{name} contains non-hex characters") from exc


def _add_management_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--receiver", required=True, help="Address 1 (RA/DA)")
    parser.add_argument("--transmitter", required=True, help="Address 2 (TA/SA)")
    parser.add_argument("--bssid", required=True, help="Address 3 (BSSID)")
    parser.add_argument("--duration", type=int, default=0)
    parser.add_argument("--sequence", type=int, default=0, dest="sequence_number")
    parser.add_argument("--fragment", type=int, default=0, dest="fragment_number")
    parser.add_argument("--raw-out", type=Path, help="write FCS-free raw bytes")
    parser.add_argument(
        "--pcap-out", type=Path, help="write LINKTYPE_IEEE802_11 PCAP"
    )
    parser.add_argument(
        "--timestamp",
        type=float,
        default=0,
        help="PCAP record timestamp (default: reproducible epoch 0)",
    )


def _cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build and inspect two IEEE 802.11 frame shapes offline.",
        epilog="This program cannot capture or transmit. Use only with authorized lab data.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    deauth = commands.add_parser(
        "build-deauth", help="build an offline deauthentication-frame specimen"
    )
    _add_management_arguments(deauth)
    deauth.add_argument("--reason-code", type=int, required=True)

    action = commands.add_parser(
        "build-action", help="build a category-127 vendor-action specimen"
    )
    _add_management_arguments(action)
    action.add_argument("--oui", required=True, help="three-octet vendor OUI")
    action.add_argument(
        "--vendor-content-hex", default="", help="opaque vendor bytes as hex"
    )

    decode = commands.add_parser("decode", help="decode a raw frame or lab PCAP")
    decode.add_argument("source", help="file path, or hex text with --hex")
    decode.add_argument(
        "--hex", action="store_true", dest="source_is_hex", help="source is raw hex"
    )
    return parser


def _write_cli_outputs(args: argparse.Namespace, frame: bytes) -> None:
    if args.raw_out is not None:
        args.raw_out.write_bytes(frame)
    if args.pcap_out is not None:
        write_pcap(args.pcap_out, [frame], timestamps=[args.timestamp])


def main(argv: Sequence[str] | None = None) -> int:
    parser = _cli_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "build-deauth":
            frame = build_deauthentication_frame(
                receiver=args.receiver,
                transmitter=args.transmitter,
                bssid=args.bssid,
                reason_code=args.reason_code,
                duration=args.duration,
                sequence_number=args.sequence_number,
                fragment_number=args.fragment_number,
            )
            _write_cli_outputs(args, frame)
            result: dict[str, Any] = {
                "format": "raw IEEE 802.11 management frame (FCS omitted)",
                "frame_hex": frame.hex(),
                "decoded": decode_frame(frame),
            }
        elif args.command == "build-action":
            frame = build_vendor_action_frame(
                receiver=args.receiver,
                transmitter=args.transmitter,
                bssid=args.bssid,
                oui=args.oui,
                vendor_content=_hex_bytes(
                    "vendor_content_hex", args.vendor_content_hex
                ),
                duration=args.duration,
                sequence_number=args.sequence_number,
                fragment_number=args.fragment_number,
            )
            _write_cli_outputs(args, frame)
            result = {
                "format": "raw IEEE 802.11 management frame (FCS omitted)",
                "frame_hex": frame.hex(),
                "decoded": decode_frame(frame),
            }
        else:
            data = (
                _hex_bytes("source", args.source)
                if args.source_is_hex
                else Path(args.source).read_bytes()
            )
            if data.startswith(struct.pack("<I", PCAP_MAGIC_USEC)):
                result = decode_pcap(data)
            else:
                result = {
                    "format": "raw IEEE 802.11 management frame (FCS omitted)",
                    "frame_hex": data.hex(),
                    "decoded": decode_frame(data),
                }
    except (FrameAnatomyError, OSError) as exc:
        parser.error(str(exc))
        return 2

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
