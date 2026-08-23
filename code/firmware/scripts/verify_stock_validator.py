#!/usr/bin/env python3
"""Verify that built firmware resolves the private validator from libnet80211.

This is a build-evidence guard, not an API compatibility promise. The symbol is
private and may legitimately change in a future ESP-IDF release; if it does,
the script fails closed and asks for a fresh source/disassembly review.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
import sys


SYMBOL = "ieee80211_raw_frame_sanity_check"
STOCK_OWNER_FRAGMENT = "libnet80211.a(ieee80211_output.o)"
SECTION_RE = re.compile(
    rf"^\s*\.text\.{re.escape(SYMBOL)}"
    r"(?:\s+(0x[0-9a-fA-F]+)\s+(0x[0-9a-fA-F]+)\s+(.+?))?\s*$"
)
CONTRIBUTION_RE = re.compile(
    r"^\s*(0x[0-9a-fA-F]+)\s+(0x[0-9a-fA-F]+)\s+(.+?)\s*$"
)
DEFINITION_RE = re.compile(
    rf"^\s*(0x[0-9a-fA-F]+)\s+{re.escape(SYMBOL)}\s*$"
)
INPUT_SECTION_RE = re.compile(r"^\s*\.[A-Za-z0-9_]" )
XREF_RE = re.compile(rf"^{re.escape(SYMBOL)}\s+(.+?)\s*$")


@dataclass(frozen=True)
class TextContribution:
    address: int
    size: int
    owner: str
    exports_symbol: bool


def text_contributions(lines: list[str]) -> list[TextContribution]:
    """Extract function-section owners and whether each exports SYMBOL."""

    contributions: list[TextContribution] = []
    for index, line in enumerate(lines):
        section = SECTION_RE.match(line)
        if section is None:
            continue

        address_text, size_text, owner = section.groups()
        contribution_index = index
        if address_text is None:
            for candidate_index in range(index + 1, min(len(lines), index + 6)):
                candidate = CONTRIBUTION_RE.match(lines[candidate_index])
                if candidate is not None:
                    address_text, size_text, owner = candidate.groups()
                    contribution_index = candidate_index
                    break
        if address_text is None or size_text is None or owner is None:
            continue

        exports_symbol = False
        for candidate_index in range(contribution_index + 1, len(lines)):
            candidate_line = lines[candidate_index]
            if INPUT_SECTION_RE.match(candidate_line):
                break
            if DEFINITION_RE.match(candidate_line):
                exports_symbol = True
                break

        contributions.append(
            TextContribution(
                address=int(address_text, 16),
                size=int(size_text, 16),
                owner=owner,
                exports_symbol=exports_symbol,
            )
        )
    return contributions


def verify_map(path: Path) -> tuple[bool, str]:
    lines = path.read_text(errors="replace").splitlines()
    contributions = text_contributions(lines)
    effective = [
        contribution
        for contribution in contributions
        if contribution.address != 0
        and contribution.size != 0
        and contribution.exports_symbol
    ]

    if len(effective) != 1:
        return False, (
            f"{path}: expected exactly one nonzero text contribution exporting "
            f"{SYMBOL}, found {len(effective)}"
        )

    owner = effective[0].owner
    if STOCK_OWNER_FRAGMENT not in owner:
        return False, f"{path}: effective {SYMBOL} is owned by {owner}"

    # When GNU ld emits a cross-reference table, its first symbol row names the
    # selected definition. Treat a conflicting owner as an independent failure.
    xref_owners = [match.group(1) for line in lines if (match := XREF_RE.match(line))]
    if xref_owners and STOCK_OWNER_FRAGMENT not in xref_owners[0]:
        return False, f"{path}: cross-reference owner is {xref_owners[0]}"

    return True, f"{path}: effective validator text is owned by {STOCK_OWNER_FRAGMENT}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "maps",
        nargs="*",
        type=Path,
        help="application link-map paths (default: .pio/build/*/*.map)",
    )
    args = parser.parse_args()

    maps = args.maps or sorted(Path(".pio/build").glob("*/*.map"))
    if not maps:
        print("No firmware maps found. Build first with: pio run", file=sys.stderr)
        return 2

    failures = 0
    for path in maps:
        ok, message = verify_map(path)
        print(("PASS " if ok else "FAIL ") + message)
        failures += not ok
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
