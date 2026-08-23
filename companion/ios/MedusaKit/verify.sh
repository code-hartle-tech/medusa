#!/usr/bin/env bash
# Compile and run the MedusaKit Companion Protocol conformance checks.
#
# Prefers `swift test`, which is the real test suite. Falls back to compiling
# the conformance runner directly with swiftc, because SwiftPM's manifest
# compilation fails on a machine that has only Command Line Tools rather than
# full Xcode — PackageDescription does not link — and the codec should still be
# verifiable there.
#
# No network, no simulator, no device. This checks bytes.

set -euo pipefail

KIT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VECTORS="${1:-$KIT_DIR/../../../tools/companion/vectors/frames.json}"

[[ -f "$VECTORS" ]] || { printf 'FAIL: vectors not found at %s\n' "$VECTORS" >&2; exit 2; }

if swift package describe >/dev/null 2>&1; then
    echo "==> SwiftPM available; running the XCTest suite"
    cp "$VECTORS" "$KIT_DIR/Tests/MedusaKitTests/frames.json"
    exec swift test --package-path "$KIT_DIR"
fi

echo "==> SwiftPM manifest unavailable (Command Line Tools only); compiling the conformance runner directly"
BIN="$(mktemp -d)/medusakit-conformance"
swiftc -O \
    "$KIT_DIR/Sources/MedusaKit/CompanionProtocol.swift" \
    "$KIT_DIR/Conformance/main.swift" \
    -o "$BIN"
exec "$BIN" "$VECTORS"
