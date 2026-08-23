#!/usr/bin/env bash
# Compile and run the device-side Companion Protocol and TX-guard host tests.
#
# This builds firmware source with a host compiler and runs it on the
# workstation. It does not flash, open a serial port, or touch a radio, and
# passing it is not authorisation to do any of those things.
#
#   bash tools/esp-hw/medusa_companion/verify.sh

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VECTORS="${1:-$DIR/../../companion/vectors/frames.tsv}"

[[ -f "$VECTORS" ]] || { printf 'FAIL: conformance vectors not found at %s\n' "$VECTORS" >&2; exit 2; }

BIN="$(mktemp -d)/medusa-companion-test"

# -Werror on purpose: this is firmware that parses input from a radio link, and
# a warning here is a defect report.
cc -std=c99 -Wall -Wextra -Werror -O2 \
    "$DIR/medusa_companion_protocol.c" \
    "$DIR/medusa_tx_guard.c" \
    "$DIR/medusa_display.c" \
    "$DIR/medusa_config.c" \
    "$DIR/medusa_plugin.c" \
    "$DIR/medusa_tx_common.c" \
    "$DIR/test/host_test.c" \
    -o "$BIN"

exec "$BIN" "$VECTORS"
