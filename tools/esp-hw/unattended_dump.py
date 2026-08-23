#!/usr/bin/env python3
"""Retrieve a Medusa unattended inventory over its USB serial console.

The firmware protocol is deliberately small: persist the current passive
inventory with ``FLUSH``, wait for a successful ``UNATT_STORE`` response, then
request ``DUMP`` and save only the CSV payload enclosed by the firmware's
markers. Every dump must identify its exact, validated source before the CSV
begins. If and only if FLUSH reports the firmware's exact
``orphaned-csv-preserved`` recovery guard, the helper accepts a preserved CSV
candidate, a read-only database rendering, or an explicitly non-persisted
volatile-RAM rendering and reports which one supplied the download. This helper
has no erase or initialization command.

Only the Python standard library is used so the script works on stock macOS
and Linux installations.
"""

from __future__ import annotations

import argparse
import csv
import io
import os
from pathlib import Path
import re
import select
import sys
import tempfile
import termios
import time
from dataclasses import dataclass
from typing import Callable, Mapping, Optional, Union


BAUD_RATE = 115200
CSV_HEADER = [
    "bssid", "ssid", "channel", "rssi", "seen",
    "inventory_partial", "capacity_drops",
]
BEGIN_PREFIX = "UNATT_CSV_BEGIN\t"
END_MARKER = "UNATT_CSV_END"
MAX_LINE_BYTES = 64 * 1024
MAX_PAYLOAD_BYTES = 16 * 1024 * 1024
MAX_INVENTORY_ROWS = 120
MAX_UINT32 = 0xFFFFFFFF
ORPHAN_RECOVERY_REASON = "orphaned-csv-preserved"
DATABASE_RECOVERY_REASON = "database-read-only"
VOLATILE_RECOVERY_REASON = "volatile-ram-read-only"
SOURCE_PREFIX = "UNATT_DUMP_SOURCE\t"
CSV_DUMP_SOURCES = {
    "primary-csv": "/medusa_log.csv",
    "temp-csv-read-only-recovery": "/medusa_log.tmp",
    "backup-csv-read-only-recovery": "/medusa_log.bak",
}
DATABASE_RECOVERY_STATES = {
    "selected-db-metadata-mismatch",
    "unpaired-csv-artifact",
    "legacy-v2-metadata-unverified",
    "database-promotion-failed",
    "csv-promotion-failed",
}
VOLATILE_RECOVERY_STATES = {
    "csv-metadata-failed",
    "generation-exhausted",
    "db-csv-divergence",
    "database-write-failed",
}
BSSID_RE = re.compile(r"[0-9A-F]{2}(?::[0-9A-F]{2}){5}\Z")


class UnattendedDumpError(RuntimeError):
    """Base error for retrieval failures."""


class SerialTransportError(UnattendedDumpError):
    """The local serial device could not be opened or used."""


class ProtocolError(UnattendedDumpError):
    """The firmware response did not satisfy the unattended protocol."""


@dataclass(frozen=True)
class RetrievalResult:
    """Validated result returned by :func:`run_protocol`."""

    csv_text: str
    row_count: int
    capacity_drops: int
    store_fields: Mapping[str, str]
    recovery: Optional[str] = None
    source: Optional[str] = None

    @property
    def partial(self) -> bool:
        return self.capacity_drops > 0


Line = Union[bytes, str]
Clock = Callable[[], float]


class PosixSerialStream:
    """Minimal non-blocking line stream for a POSIX serial device.

    The public ``write`` and ``readline`` methods also form the small interface
    accepted by :func:`run_protocol`, allowing tests or callers to inject a
    scripted stream without opening hardware.
    """

    def __init__(self, path: Union[str, os.PathLike[str]]) -> None:
        self.path = os.fspath(path)
        self._fd: Optional[int] = None
        self._original_attrs = None
        self._buffer = bytearray()

    def __enter__(self) -> "PosixSerialStream":
        self.open()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def open(self) -> None:
        if self._fd is not None:
            return
        try:
            fd = os.open(
                self.path,
                os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK,
            )
        except OSError as exc:
            raise SerialTransportError(
                f"cannot open serial device {self.path!r}: {exc}"
            ) from exc

        try:
            original = termios.tcgetattr(fd)
            attrs = list(original)
            # tcgetattr() nests the control-character list.  Copy it as well
            # so VMIN/VTIME changes do not corrupt the settings restored later.
            attrs[6] = list(original[6])
            attrs[0] = termios.IGNPAR
            attrs[1] = 0
            flow_control = getattr(termios, "CRTSCTS", 0)
            attrs[2] &= ~(
                termios.CSIZE | termios.PARENB | termios.CSTOPB | flow_control
            )
            attrs[2] |= termios.CS8 | termios.CREAD | termios.CLOCAL
            attrs[3] = 0
            attrs[4] = termios.B115200
            attrs[5] = termios.B115200
            attrs[6][termios.VMIN] = 0
            attrs[6][termios.VTIME] = 0
            termios.tcsetattr(fd, termios.TCSANOW, attrs)
            termios.tcflush(fd, termios.TCIFLUSH)
        except (OSError, termios.error) as exc:
            os.close(fd)
            raise SerialTransportError(
                f"cannot configure {self.path!r} at {BAUD_RATE} baud: {exc}"
            ) from exc

        self._fd = fd
        self._original_attrs = original
        self._buffer.clear()

    def close(self) -> None:
        fd = self._fd
        if fd is None:
            return
        self._fd = None
        try:
            if self._original_attrs is not None:
                termios.tcsetattr(fd, termios.TCSANOW, self._original_attrs)
        except (OSError, termios.error):
            # A USB device may have disappeared; closing the fd is still useful.
            pass
        finally:
            try:
                os.close(fd)
            except OSError:
                pass

    def write(self, data: bytes, timeout: float = 5.0) -> None:
        """Write all bytes, waiting for a non-blocking fd to become writable."""

        fd = self._require_fd()
        view = memoryview(data)
        deadline = time.monotonic() + timeout
        while view:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SerialTransportError(
                    f"timed out writing to serial device {self.path!r}"
                )
            try:
                _, writable, _ = select.select([], [fd], [], remaining)
            except (OSError, ValueError) as exc:
                raise SerialTransportError(
                    f"cannot wait for serial device {self.path!r}: {exc}"
                ) from exc
            if not writable:
                raise SerialTransportError(
                    f"timed out writing to serial device {self.path!r}"
                )
            try:
                written = os.write(fd, view)
            except (BlockingIOError, InterruptedError):
                continue
            except OSError as exc:
                raise SerialTransportError(
                    f"cannot write to serial device {self.path!r}: {exc}"
                ) from exc
            if written <= 0:
                raise SerialTransportError(
                    f"serial device {self.path!r} accepted no data"
                )
            view = view[written:]

    def readline(self, timeout: float) -> Optional[bytes]:
        """Return one newline-terminated line, or ``None`` on timeout."""

        fd = self._require_fd()
        deadline = time.monotonic() + max(timeout, 0.0)
        while True:
            newline = self._buffer.find(b"\n")
            if newline >= 0:
                line = bytes(self._buffer[: newline + 1])
                del self._buffer[: newline + 1]
                return line
            if len(self._buffer) > MAX_LINE_BYTES:
                raise ProtocolError(
                    f"serial line exceeded {MAX_LINE_BYTES} bytes"
                )

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                readable, _, _ = select.select([fd], [], [], remaining)
            except (OSError, ValueError) as exc:
                raise SerialTransportError(
                    f"cannot wait for serial device {self.path!r}: {exc}"
                ) from exc
            if not readable:
                return None
            try:
                chunk = os.read(fd, 4096)
            except (BlockingIOError, InterruptedError):
                continue
            except OSError as exc:
                raise SerialTransportError(
                    f"cannot read serial device {self.path!r}: {exc}"
                ) from exc
            if not chunk:
                raise SerialTransportError(
                    f"serial device {self.path!r} closed while reading"
                )
            self._buffer.extend(chunk)

    def _require_fd(self) -> int:
        if self._fd is None:
            raise SerialTransportError("serial device is not open")
        return self._fd


def _decode_line(raw_line: Line) -> str:
    if isinstance(raw_line, bytes):
        try:
            line = raw_line.decode("utf-8", "strict")
        except UnicodeDecodeError as exc:
            raise ProtocolError("firmware emitted non-UTF-8 serial text") from exc
    elif isinstance(raw_line, str):
        line = raw_line
    else:
        raise ProtocolError(
            f"serial stream returned unsupported line type {type(raw_line).__name__}"
        )
    if line.endswith("\n"):
        line = line[:-1]
    if line.endswith("\r"):
        line = line[:-1]
    return line


def parse_store_line(
    line: str, *, allow_orphan_recovery: bool = False
) -> Mapping[str, str]:
    """Parse and validate one ``UNATT_STORE`` response line."""

    parts = line.split("\t")
    if not parts or parts[0] != "UNATT_STORE":
        raise ProtocolError("expected an UNATT_STORE response")
    fields = {}
    for item in parts[1:]:
        if "=" not in item:
            raise ProtocolError(f"malformed UNATT_STORE field: {item!r}")
        key, value = item.split("=", 1)
        if not key or key in fields:
            raise ProtocolError(f"malformed UNATT_STORE field: {item!r}")
        fields[key] = value

    for required in ("aps", "db", "csv", "capacity_drops"):
        if required not in fields:
            raise ProtocolError(f"UNATT_STORE is missing {required!r}")
    aps = _parse_canonical_uint(
        fields["aps"], "UNATT_STORE aps", maximum=MAX_INVENTORY_ROWS
    )
    capacity_drops = _parse_canonical_uint(
        fields["capacity_drops"], "UNATT_STORE capacity_drops",
        maximum=MAX_UINT32,
    )
    orphan_recovery = (
        fields["db"] == "error"
        and fields["csv"] == "error"
        and fields.get("reason") == ORPHAN_RECOVERY_REASON
    )
    if fields.get("reason") == ORPHAN_RECOVERY_REASON and not orphan_recovery:
        raise ProtocolError("firmware reported inconsistent orphan recovery state")
    if fields["db"] != "ok" or fields["csv"] != "ok":
        if allow_orphan_recovery and orphan_recovery:
            return fields
        raise ProtocolError(
            "firmware could not persist the inventory "
            f"(db={fields['db']}, csv={fields['csv']})"
        )
    return fields


def parse_begin_marker(line: str) -> int:
    """Return the declared row count from an exact CSV begin marker."""

    if not line.startswith(BEGIN_PREFIX):
        raise ProtocolError("expected an UNATT_CSV_BEGIN marker")
    count_text = line[len(BEGIN_PREFIX) :]
    return _parse_canonical_uint(
        count_text, "UNATT_CSV_BEGIN row count", maximum=MAX_INVENTORY_ROWS
    )


def parse_dump_source(line: str) -> str:
    """Validate an exact ``DUMP`` source marker and return its safe label."""

    parts = line.split("\t")
    if len(parts) < 2 or parts[0] != "UNATT_DUMP_SOURCE":
        raise ProtocolError("expected an UNATT_DUMP_SOURCE marker")
    label = parts[1]
    fields = {}
    for item in parts[2:]:
        if "=" not in item:
            raise ProtocolError(f"malformed UNATT_DUMP_SOURCE field: {item!r}")
        key, value = item.split("=", 1)
        if not key or key in fields:
            raise ProtocolError(f"malformed UNATT_DUMP_SOURCE field: {item!r}")
        fields[key] = value

    if label in CSV_DUMP_SOURCES:
        if fields != {"path": CSV_DUMP_SOURCES[label]}:
            raise ProtocolError("UNATT_DUMP_SOURCE has an unexpected CSV path or fields")
        return label
    if label == DATABASE_RECOVERY_REASON:
        if set(fields) != {"generation", "recovery"}:
            raise ProtocolError("database dump source has unexpected fields")
        _parse_canonical_uint(
            fields["generation"], "database dump source generation",
            minimum=1, maximum=0xFFFFFFFFFFFFFFFF,
        )
        if fields["recovery"] not in DATABASE_RECOVERY_STATES:
            raise ProtocolError("database dump source has an invalid recovery state")
        return label
    if label == VOLATILE_RECOVERY_REASON:
        if set(fields) != {"committed_generation", "recovery"}:
            raise ProtocolError("volatile RAM dump source has unexpected fields")
        _parse_canonical_uint(
            fields["committed_generation"],
            "volatile RAM dump source committed generation",
            maximum=0xFFFFFFFFFFFFFFFF,
        )
        if fields["recovery"] not in VOLATILE_RECOVERY_STATES:
            raise ProtocolError("volatile RAM dump source has an invalid recovery state")
        return label
    raise ProtocolError(f"unsupported UNATT_DUMP_SOURCE label: {label!r}")


def _parse_canonical_uint(value: str, label: str, *, maximum: int,
                          minimum: int = 0) -> int:
    if (not value or not value.isascii() or not value.isdigit() or
            (len(value) > 1 and value.startswith("0"))):
        raise ProtocolError(f"{label} is not a canonical unsigned integer")
    number = int(value, 10)
    if number < minimum or number > maximum:
        raise ProtocolError(f"{label} is out of range")
    return number


def _parse_canonical_int(value: str, label: str, *, minimum: int,
                         maximum: int) -> int:
    digits = value[1:] if value.startswith("-") else value
    if (not digits or not digits.isascii() or not digits.isdigit() or
            (len(digits) > 1 and digits.startswith("0"))):
        raise ProtocolError(f"{label} is not a canonical integer")
    number = int(value, 10)
    if str(number) != value or number < minimum or number > maximum:
        raise ProtocolError(f"{label} is out of range")
    return number


def _validate_exported_ssid(ssid: str, row_index: int) -> None:
    if any(ord(char) < 0x20 or ord(char) > 0x7E for char in ssid):
        raise ProtocolError(
            f"CSV row {row_index} has an unescaped SSID byte"
        )
    if ssid.startswith(("=", "+", "-", "@")):
        raise ProtocolError(
            f"CSV row {row_index} has an unneutralized spreadsheet formula"
        )
    if len(ssid) > 129:
        raise ProtocolError(f"CSV row {row_index} has an oversized SSID")

    # A leading apostrophe before a formula byte is the firmware's spreadsheet
    # neutralizer and is not part of the original 32-byte SSID. Every canonical
    # uppercase \xNN sequence can represent one non-printable input byte. A
    # literal printable sequence is indistinguishable, so this computes the
    # smallest possible source length and still rejects anything the formatter
    # could not have emitted from a bounded SSID.
    offset = 1 if len(ssid) >= 2 and ssid[0] == "'" and ssid[1] in "=+-@" else 0
    source_bytes = 0
    while offset < len(ssid):
        if (offset + 3 < len(ssid) and ssid[offset:offset + 2] == "\\x" and
                all(char in "0123456789ABCDEF" for char in ssid[offset + 2:offset + 4])):
            offset += 4
        else:
            offset += 1
        source_bytes += 1
    if source_bytes > 32:
        raise ProtocolError(f"CSV row {row_index} has an oversized SSID")


def validate_csv_payload(
    lines: list[str], expected_rows: int, capacity_drops: Optional[int]
) -> tuple[str, int]:
    """Validate the payload and return normalized text plus drop metadata.

    A normal snapshot supplies ``capacity_drops`` from its successful
    ``UNATT_STORE`` response and remains strict about matching it.  An orphan
    recovery passes ``None`` because the failed DB snapshot cannot vouch for
    the preserved CSV; in that case the value is derived from consistent CSV
    rows instead.
    """

    csv_text = "\n".join(lines) + "\n"
    if len(csv_text.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ProtocolError(
            f"CSV payload exceeded {MAX_PAYLOAD_BYTES} bytes"
        )
    try:
        rows = list(csv.reader(io.StringIO(csv_text, newline=""), strict=True))
    except csv.Error as exc:
        raise ProtocolError(f"firmware emitted invalid CSV: {exc}") from exc
    if not rows or rows[0] != CSV_HEADER:
        actual = rows[0] if rows else None
        raise ProtocolError(
            f"unexpected CSV header: expected {CSV_HEADER!r}, got {actual!r}"
        )
    data_rows = rows[1:]
    if len(data_rows) != expected_rows:
        raise ProtocolError(
            "CSV row count does not match marker: "
            f"expected {expected_rows}, got {len(data_rows)}"
        )
    row_capacity_drops = set()
    seen_bssids = set()
    for index, row in enumerate(data_rows, start=1):
        if len(row) != len(CSV_HEADER):
            raise ProtocolError(
                f"CSV row {index} has {len(row)} columns; "
                f"expected {len(CSV_HEADER)}"
            )
        if not BSSID_RE.fullmatch(row[0]):
            raise ProtocolError(f"CSV row {index} has an invalid BSSID")
        if row[0] in seen_bssids:
            raise ProtocolError(f"CSV row {index} repeats a BSSID")
        seen_bssids.add(row[0])
        _validate_exported_ssid(row[1], index)
        _parse_canonical_uint(
            row[2], f"CSV row {index} channel", minimum=1, maximum=14
        )
        _parse_canonical_int(
            row[3], f"CSV row {index} RSSI", minimum=-128, maximum=0
        )
        _parse_canonical_uint(
            row[4], f"CSV row {index} seen count",
            minimum=1, maximum=MAX_UINT32,
        )
        row_drops = _parse_canonical_uint(
            row[6], f"CSV row {index} capacity metadata",
            maximum=MAX_UINT32,
        )
        expected_partial = "yes" if row_drops > 0 else "no"
        if row[5] != expected_partial:
            raise ProtocolError(
                f"CSV row {index} has inconsistent capacity metadata"
            )
        row_capacity_drops.add(row_drops)
    if len(row_capacity_drops) > 1:
        raise ProtocolError("CSV rows have inconsistent capacity metadata")
    derived_capacity_drops = next(iter(row_capacity_drops), 0)
    if capacity_drops is not None and derived_capacity_drops != capacity_drops:
        raise ProtocolError("CSV rows have inconsistent capacity metadata")
    return csv_text, derived_capacity_drops


def _read_line(stream, deadline: float, clock: Clock, stage: str) -> str:
    remaining = deadline - clock()
    if remaining <= 0:
        raise ProtocolError(f"timed out waiting for {stage}")
    raw_line = stream.readline(remaining)
    if raw_line is None:
        raise ProtocolError(f"timed out waiting for {stage}")
    return _decode_line(raw_line)


def run_protocol(
    stream,
    timeout: float = 15.0,
    *,
    clock: Clock = time.monotonic,
) -> RetrievalResult:
    """Run the non-clearing retrieval protocol against an injected line stream.

    ``stream`` must provide ``write(bytes)`` and ``readline(timeout)``. The
    function sends ``FLUSH`` first and sends ``DUMP`` only after a normal
    successful store or the exact preserved-orphan recovery response.  It has
    no path that can send the firmware's destructive or initialization
    commands.
    """

    if timeout <= 0:
        raise ValueError("timeout must be greater than zero")

    stream.write(b"FLUSH\n")
    deadline = clock() + timeout
    store_fields: Optional[Mapping[str, str]] = None
    while store_fields is None:
        line = _read_line(stream, deadline, clock, "UNATT_STORE")
        if line == "UNATT_STORE" or line.startswith("UNATT_STORE\t"):
            store_fields = parse_store_line(
                line, allow_orphan_recovery=True
            )

    orphan_recovery = (
        store_fields["db"] == "error"
        and store_fields["csv"] == "error"
        and store_fields.get("reason") == ORPHAN_RECOVERY_REASON
    )

    stream.write(b"DUMP\n")
    deadline = clock() + timeout
    expected_rows: Optional[int] = None
    source: Optional[str] = None
    payload_lines: list[str] = []
    payload_size = 0
    while True:
        stage = (END_MARKER if expected_rows is not None else
                 "UNATT_CSV_BEGIN" if source is not None else "UNATT_DUMP_SOURCE")
        line = _read_line(stream, deadline, clock, stage)
        if expected_rows is None:
            if line == END_MARKER:
                raise ProtocolError("received UNATT_CSV_END before begin marker")
            if line == "UNATT_DUMP_SOURCE" or line.startswith(SOURCE_PREFIX):
                if source is not None:
                    raise ProtocolError("received a duplicate UNATT_DUMP_SOURCE marker")
                source = parse_dump_source(line)
                continue
            if line == "UNATT_CSV_BEGIN" or line.startswith(BEGIN_PREFIX):
                if source is None:
                    raise ProtocolError("received UNATT_CSV_BEGIN before dump source")
                expected_rows = parse_begin_marker(line)
            continue

        if line == END_MARKER:
            break
        if line == "UNATT_CSV_BEGIN" or line.startswith(BEGIN_PREFIX):
            raise ProtocolError("received a nested UNATT_CSV_BEGIN marker")
        if line == "UNATT_DUMP_SOURCE" or line.startswith(SOURCE_PREFIX):
            raise ProtocolError("received UNATT_DUMP_SOURCE inside CSV payload")
        payload_size += len(line.encode("utf-8")) + 1
        if payload_size > MAX_PAYLOAD_BYTES:
            raise ProtocolError(
                f"CSV payload exceeded {MAX_PAYLOAD_BYTES} bytes"
            )
        payload_lines.append(line)

    assert expected_rows is not None
    assert source is not None
    source_may_precede_store = orphan_recovery and (
        source in CSV_DUMP_SOURCES or source == VOLATILE_RECOVERY_REASON
    )
    stored_capacity_drops = None if source_may_precede_store else int(
        store_fields["capacity_drops"]
    )
    csv_text, capacity_drops = validate_csv_payload(
        payload_lines, expected_rows, stored_capacity_drops
    )
    stored_rows = int(store_fields["aps"])
    if not source_may_precede_store and stored_rows != expected_rows:
        raise ProtocolError(
            "persisted row count does not match CSV marker: "
            f"store reported {stored_rows}, marker reported {expected_rows}"
        )
    if orphan_recovery:
        recovery = (source if source in {DATABASE_RECOVERY_REASON, VOLATILE_RECOVERY_REASON}
                    else ORPHAN_RECOVERY_REASON)
    else:
        if source != "primary-csv":
            raise ProtocolError("successful store returned an unexpected dump source")
        recovery = None
    return RetrievalResult(
        csv_text, expected_rows, capacity_drops, store_fields, recovery, source
    )


def atomic_write_text(path: Union[str, os.PathLike[str]], text: str) -> None:
    """Replace ``path`` atomically with UTF-8 text from the same directory."""

    target = Path(path)
    parent = target.parent
    if not parent.is_dir():
        raise UnattendedDumpError(f"output directory does not exist: {parent}")

    fd = -1
    temp_name: Optional[str] = None
    try:
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=os.fspath(parent)
        )
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            fd = -1
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
        temp_name = None
    except OSError as exc:
        raise UnattendedDumpError(
            f"cannot atomically write output {os.fspath(target)!r}: {exc}"
        ) from exc
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        if temp_name is not None:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass


def retrieve_to_file(
    port: Union[str, os.PathLike[str]],
    output: Union[str, os.PathLike[str]],
    timeout: float = 15.0,
) -> RetrievalResult:
    """Retrieve, validate, and atomically store one unattended inventory."""

    with PosixSerialStream(port) as stream:
        result = run_protocol(stream, timeout=timeout)
    atomic_write_text(output, result.csv_text)
    return result


def _positive_timeout(value: str) -> float:
    try:
        timeout = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if timeout <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return timeout


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Retrieve a passive Medusa inventory over 115200-baud USB serial. "
            "The helper issues FLUSH and DUMP but no clear command; readback "
            "does not clear recovery artifacts."
        )
    )
    parser.add_argument(
        "port", help="exact detected serial device, such as /dev/cu.usbmodemDEVICE"
    )
    parser.add_argument("output", help="destination CSV path")
    parser.add_argument(
        "--timeout",
        type=_positive_timeout,
        default=15.0,
        metavar="SECONDS",
        help="maximum wait for each firmware response stage (default: 15)",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_argument_parser().parse_args(argv)
    try:
        result = retrieve_to_file(args.port, args.output, timeout=args.timeout)
    except (UnattendedDumpError, ValueError) as exc:
        print(f"unattended retrieval failed: {exc}", file=sys.stderr)
        return 1
    suffix = (
        f" (partial: {result.capacity_drops} observations dropped after capacity was reached)"
        if result.partial else ""
    )
    recovery = (
        " (recovered preserved CSV; readback did not clear recovery artifacts)"
        if result.recovery == ORPHAN_RECOVERY_REASON else ""
    )
    if result.recovery == DATABASE_RECOVERY_REASON:
        recovery = " (rendered from the validated database read-only; readback did not clear recovery artifacts)"
    if result.recovery == VOLATILE_RECOVERY_REASON:
        recovery = " (rendered from non-persisted volatile RAM; readback did not clear recovery artifacts)"
    print(
        f"saved {result.row_count} inventory rows to {args.output}"
        f"{suffix}{recovery}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
