#!/usr/bin/env bash
# Create the durable, pinned PlatformIO environment this lab builds with.
#
# Why this exists: verification runs kept reaching for a PlatformIO Core that
# lived under /tmp, which macOS clears. A build environment that disappears
# between sessions makes "the firmware compiles" unreproducible, so the venv
# gets a durable home and an exact pinned version instead.
#
# This script installs a Python package into a virtualenv. It does not build,
# flash, erase, or open a serial port, and it never touches an attached board.

set -euo pipefail

# Pinned to match code/firmware/README.md and the recorded proof ladder.
PIO_VERSION="6.1.19"

# PlatformIO Core 6.1.x does not support the newest CPython releases, and the
# system interpreter is the one the working environment was built with. Allow
# an override for hosts where /usr/bin/python3 is something else.
PYTHON_BIN="${MEDUSA_PIO_PYTHON:-/usr/bin/python3}"

# Durable, XDG-ish, and free of spaces — PlatformIO handles a space in its own
# path poorly enough that it is not worth risking.
VENV_DIR="${MEDUSA_PIO_HOME:-$HOME/.local/share/medusa/pio-venv}"

log() { printf '==> %s\n' "$*"; }
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

[[ -x "$PYTHON_BIN" ]] || fail "python interpreter not executable: $PYTHON_BIN"

log "interpreter: $PYTHON_BIN ($("$PYTHON_BIN" --version 2>&1))"
log "venv:        $VENV_DIR"
log "platformio:  $PIO_VERSION (pinned)"

if [[ ! -x "$VENV_DIR/bin/pio" ]]; then
    mkdir -p "$(dirname "$VENV_DIR")"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/python" -m pip install --quiet --upgrade pip
"$VENV_DIR/bin/python" -m pip install --quiet "platformio==${PIO_VERSION}"

# Assert the pin rather than trusting the install log. `pio --version` also
# emits an unrelated LibreSSL warning on stock macOS Python, so match loosely
# on the version string and fail closed if it is not the pinned one.
installed="$("$VENV_DIR/bin/pio" --version 2>/dev/null | tr -d '\r')"
case "$installed" in
    *"$PIO_VERSION"*) : ;;
    *) fail "expected PlatformIO $PIO_VERSION, got: ${installed:-<no output>}" ;;
esac

log "OK — $installed"
cat <<EOF

Use it without changing your shell profile:

    export PATH="$VENV_DIR/bin:\$PATH"
    cd code/firmware
    pio run -e esp32s3-idf44

Building is not authorization to flash. Every upload, storage
initialization, storage clear, and RF operation remains a separate,
explicitly approved step.
EOF
