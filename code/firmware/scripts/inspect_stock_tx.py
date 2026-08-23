#!/usr/bin/env python3
"""Verify and explain the stock Xtensa raw-TX validation edge in an ELF.

This script only invokes objdump and reads an existing firmware image. It does
not modify the ELF, initialize Wi-Fi, flash hardware, or transmit anything.
The checked instruction shapes are intentionally version-pinned archaeology,
not a private Espressif API contract.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys


VALIDATOR = "ieee80211_raw_frame_sanity_check"
PUBLIC_TX = "esp_wifi_80211_tx"


class EvidenceError(RuntimeError):
    """Raised when disassembly does not prove the expected stock call edge."""


@dataclass(frozen=True)
class TraceEvidence:
    argument_staging: tuple[str, ...]
    gate_call: str
    rejection_branch: str
    frame_control_load: str
    type_mask: tuple[str, ...]
    subtype_mask: tuple[str, ...]
    invalid_arg_returns: tuple[str, ...]
    success_return: str

    def as_dict(self) -> dict[str, object]:
        return {
            "argument_staging": list(self.argument_staging),
            "gate_call": self.gate_call,
            "rejection_branch": self.rejection_branch,
            "frame_control_load": self.frame_control_load,
            "type_mask": list(self.type_mask),
            "subtype_mask": list(self.subtype_mask),
            "invalid_arg_returns": list(self.invalid_arg_returns),
            "success_return": self.success_return,
        }


def instruction_lines(text: str) -> list[str]:
    return [
        line.strip()
        for line in text.splitlines()
        if re.match(r"^\s*[0-9a-fA-F]+:\s+[0-9a-fA-F]+\s+", line)
    ]


def one_line(lines: list[str], pattern: str, description: str) -> str:
    matches = [line for line in lines if re.search(pattern, line)]
    if len(matches) != 1:
        raise EvidenceError(
            f"expected exactly one {description}, found {len(matches)}"
        )
    return matches[0]


def one_or_more(lines: list[str], pattern: str, description: str) -> tuple[str, ...]:
    matches = tuple(line for line in lines if re.search(pattern, line))
    if not matches:
        raise EvidenceError(f"missing {description}")
    return matches


def verify_trace(tx_text: str, validator_text: str) -> TraceEvidence:
    tx = instruction_lines(tx_text)
    validator = instruction_lines(validator_text)
    if not tx or not validator:
        raise EvidenceError("objdump output did not contain both functions")

    gate_call = one_line(
        tx,
        rf"\bcall8\b.*<{re.escape(VALIDATOR)}>",
        "call8 to the stock validator",
    )
    gate_index = tx.index(gate_call)
    # These version-pinned blobs stage all four outgoing arguments in the four
    # instructions immediately before CALL8. Restricting the match to that
    # final window matters: finding an earlier correct move is not proof if a
    # destination register is overwritten before the call.
    staging_window = tx[max(0, gate_index - 4) : gate_index]
    if len(staging_window) != 4:
        raise EvidenceError("missing four-instruction argument-staging window")
    immediately_after_gate = tx[gate_index + 1 : gate_index + 3]
    if len(immediately_after_gate) != 2:
        raise EvidenceError("missing two-instruction result-check window")
    staging = (
        one_line(staging_window, r"\b(?:mov\.n\s+a10,\s*a2|or\s+a10,\s*a2,\s*a2)\b", "final ifx staging into a10"),
        one_line(staging_window, r"\b(?:mov\.n\s+a11,\s*a3|or\s+a11,\s*a3,\s*a3)\b", "final buffer staging into a11"),
        one_line(staging_window, r"\b(?:mov\.n\s+a12,\s*a4|or\s+a12,\s*a4,\s*a4)\b", "final length staging into a12"),
        one_line(staging_window, r"\b(?:mov\.n\s+a13,\s*a5|or\s+a13,\s*a5,\s*a5)\b", "final sequence flag staging into a13"),
    )
    result_copy = one_line(
        immediately_after_gate,
        r"\b(?:mov\.n\s+a6,\s*a10|or\s+a6,\s*a10,\s*a10)\b",
        "validator-result copy from a10",
    )
    rejection = one_line(
        immediately_after_gate,
        r"\bbnez(?:\.n)?\s+a10,",
        "nonzero-result rejection branch",
    )
    if immediately_after_gate != [result_copy, rejection]:
        raise EvidenceError("validator result is not copied and tested contiguously")

    frame_control = one_line(
        validator,
        r"\bl8ui\s+a8,\s*a3,\s*0\b",
        "frame-control byte load from buffer[0]",
    )
    frame_control_index = validator.index(frame_control)
    type_load_pattern = r"\bmovi(?:\.n)?\s+a14,\s*(?:12|0xc)\b"
    type_and_pattern = r"\band\s+a14,\s*a8,\s*a14\b"
    subtype_load_pattern = r"\bmovi(?:\.n)?\s+a15,\s*(?:-16|0xfffffff0)\b"
    subtype_and_pattern = r"\band\s+a15,\s*a8,\s*a15\b"
    required_mask_patterns = (
        type_load_pattern,
        type_and_pattern,
        subtype_load_pattern,
        subtype_and_pattern,
    )
    mask_windows = []
    for start in range(max(0, frame_control_index - 4), frame_control_index + 1):
        window = validator[start : start + 5]
        if (
            len(window) == 5
            and frame_control in window
            and all(
                sum(bool(re.search(pattern, line)) for line in window) == 1
                for pattern in required_mask_patterns
            )
        ):
            mask_windows.append(window)
    if len(mask_windows) != 1:
        raise EvidenceError(
            "expected exactly one contiguous five-instruction Frame Control "
            f"mask window, found {len(mask_windows)}"
        )
    mask_window = mask_windows[0]
    type_mask = (
        one_line(
            mask_window,
            type_load_pattern,
            "0x0c type mask load",
        ),
        one_line(
            mask_window,
            type_and_pattern,
            "type-bit extraction",
        ),
    )
    subtype_mask = (
        one_line(
            mask_window,
            subtype_load_pattern,
            "0xf0 subtype mask load",
        ),
        one_line(
            mask_window,
            subtype_and_pattern,
            "subtype-bit extraction",
        ),
    )
    ordered_dependencies = (
        (frame_control, type_mask[1]),
        (frame_control, subtype_mask[1]),
        (type_mask[0], type_mask[1]),
        (subtype_mask[0], subtype_mask[1]),
    )
    if any(
        mask_window.index(producer) >= mask_window.index(consumer)
        for producer, consumer in ordered_dependencies
    ):
        raise EvidenceError("Frame Control mask operands are not loaded before use")
    invalid_returns = one_or_more(
        validator,
        r"\bmovi\s+a2,\s*0x102\b",
        "ESP_ERR_INVALID_ARG return path",
    )
    success = one_line(
        validator,
        r"\bmovi\.n\s+a2,\s*0\b",
        "zero success return",
    )

    return TraceEvidence(
        argument_staging=staging,
        gate_call=gate_call,
        rejection_branch=rejection,
        frame_control_load=frame_control,
        type_mask=type_mask,
        subtype_mask=subtype_mask,
        invalid_arg_returns=invalid_returns,
        success_return=success,
    )


def find_objdump(explicit: Path | None) -> Path:
    if explicit is not None:
        if not explicit.is_file():
            raise EvidenceError(f"objdump not found: {explicit}")
        return explicit

    on_path = shutil.which("xtensa-esp32s3-elf-objdump")
    if on_path:
        return Path(on_path)

    package_roots = (
        Path.home() / "Library" / "Arduino15" / "packages",
        Path.home() / ".arduino15" / "packages",
    )
    candidates = sorted(
        candidate
        for package_root in package_roots
        for candidate in package_root.glob(
            "*/tools/xtensa-esp32s3-elf-gcc/*/bin/xtensa-esp32s3-elf-objdump"
        )
    )
    if candidates:
        return candidates[-1]
    raise EvidenceError(
        "xtensa-esp32s3-elf-objdump not found; pass --objdump /absolute/path"
    )


def disassemble(objdump: Path, elf: Path, symbol: str) -> str:
    result = subprocess.run(
        [str(objdump), "-d", f"--disassemble={symbol}", str(elf)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise EvidenceError(
            f"objdump failed for {symbol}: {(result.stderr or result.stdout).strip()}"
        )
    if f"<{symbol}>:" not in result.stdout:
        raise EvidenceError(f"ELF does not expose {symbol}")
    return result.stdout


def print_lesson(evidence: TraceEvidence) -> None:
    print("PASS: final ELF contains the stock raw-TX validation edge\n")
    print("1. Public-function arguments become outgoing call8 registers:")
    names = ("ifx", "buffer", "len", "en_sys_seq")
    destinations = ("a10", "a11", "a12", "a13")
    for name, destination, line in zip(names, destinations, evidence.argument_staging):
        print(f"   {name:10s} -> {destination}: {line}")
    print("\n2. The vendor TX function calls the checker, then rejects nonzero a10:")
    print(f"   {evidence.gate_call}")
    print(f"   {evidence.rejection_branch}")
    print("\n3. In the checker, the rotated a3 is buffer; byte zero is Frame Control:")
    print(f"   {evidence.frame_control_load}")
    for line in evidence.type_mask:
        print(f"   {line}")
    for line in evidence.subtype_mask:
        print(f"   {line}")
    print("\n4. Observed return paths in this pinned blob:")
    for line in evidence.invalid_arg_returns:
        print(f"   reject: {line}  # 0x102 = ESP_ERR_INVALID_ARG")
    print(f"   accept: {evidence.success_return}")
    print(
        "\nThe masks prove type/subtype extraction. Branch meaning remains "
        "version-pinned binary archaeology, not a supported private API."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("elf", type=Path, help="built ESP32-S3 firmware ELF")
    parser.add_argument("--objdump", type=Path, help="matching Xtensa objdump")
    parser.add_argument("--json", action="store_true", help="emit evidence as JSON")
    args = parser.parse_args()

    try:
        if not args.elf.is_file():
            raise EvidenceError(f"ELF not found: {args.elf}")
        objdump = find_objdump(args.objdump)
        evidence = verify_trace(
            disassemble(objdump, args.elf, PUBLIC_TX),
            disassemble(objdump, args.elf, VALIDATOR),
        )
    except EvidenceError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(evidence.as_dict(), indent=2))
    else:
        print_lesson(evidence)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
