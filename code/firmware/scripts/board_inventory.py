#!/usr/bin/env python3
"""Read-only ESP serial-port inventory helper.

Without --port it lists local macOS serial candidates. With --port it asks the
installed esptool to identify the attached chip. It never flashes or erases,
but esptool can reset the board into and out of its ROM bootloader.
"""

from __future__ import annotations

import argparse
import glob
import json
import shutil
import subprocess
import sys


def serial_candidates() -> list[str]:
    ignored_fragments = (
        "Bluetooth-Incoming-Port",
        "debug-console",
        "Buds",
        "soundcore",
        "Hi-X",
    )
    return [
        path
        for path in sorted(glob.glob("/dev/cu.*"))
        if not any(fragment in path for fragment in ignored_fragments)
    ]


def find_esptool(explicit: str | None) -> str | None:
    if explicit:
        return explicit
    return shutil.which("esptool") or shutil.which("esptool.py")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", help="serial port to inspect, for example /dev/cu.usbmodemDEVICE")
    parser.add_argument("--esptool", help="explicit esptool or esptool.py executable")
    parser.add_argument("--json", action="store_true", help="print port candidates as JSON")
    args = parser.parse_args()

    candidates = serial_candidates()
    if args.json:
        print(json.dumps({"serial_candidates": candidates}, indent=2))
    elif candidates:
        print("Serial candidates:")
        for candidate in candidates:
            print(f"  {candidate}")
    else:
        print("No likely USB serial port is currently attached.")

    if not args.port:
        return 0

    tool = find_esptool(args.esptool)
    if tool is None:
        print("esptool was not found; install it or pass --esptool", file=sys.stderr)
        return 2

    print(f"\nRead-only chip identification on {args.port}:")
    completed = subprocess.run(
        [tool, "--port", args.port, "chip-id"],
        check=False,
        text=True,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
