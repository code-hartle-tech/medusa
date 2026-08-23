#!/usr/bin/env bash
#
# Run every Companion Protocol codec against the shared vectors.
#
# There are four implementations of one wire format — Python, TypeScript, Java
# and Swift — because the firmware, the web client, the Android app and the iOS
# app each need one. Four implementations of anything drift. The vectors in
# vectors/ are the contract, and this is the thing that notices when one of them
# stops honouring it.
#
# A missing toolchain is reported as SKIPPED, not as a pass. Swift is macOS-only
# and a JDK may not be installed, and a check that quietly reports success
# because it never ran is worse than no check — that failure mode has already
# cost this project a day elsewhere.
#
#   bash tools/companion/check-all-codecs.sh

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VECTORS="$HERE/vectors"
BUILD="${TMPDIR:-/tmp}/medusa-codecs"
mkdir -p "$BUILD"

RAN=0
FAILED=0
SKIPPED=0

pass() { printf '  \033[32mok\033[0m      %s\n' "$1"; RAN=$((RAN + 1)); }
fail() { printf '  \033[31mFAIL\033[0m    %s\n' "$1"; FAILED=$((FAILED + 1)); }
skip() { printf '  \033[2mskipped %s — %s\033[0m\n' "$1" "$2"; SKIPPED=$((SKIPPED + 1)); }

printf '\nCompanion Protocol — codec conformance\n'
printf '  vectors: %s\n\n' "$VECTORS"

# ---------------------------------------------------------------- python
if command -v python3 >/dev/null; then
  if out=$(cd "$HERE/python" && python3 tests/test_companion_protocol.py 2>&1); then
    pass "python   $(printf '%s' "$out" | grep -oE 'Ran [0-9]+ tests' | head -1)"
  else
    fail "python"
    printf '%s\n' "$out" | tail -5 | sed 's/^/          /'
  fi
else
  skip "python" "python3 not found"
fi

# ---------------------------------------------------------------- java
# macOS ships a javac/java SHIM that exists, is executable, and then reports
# "Unable to locate a Java Runtime". Testing for the file therefore proves
# nothing; the only reliable check is running it and seeing whether it works.
JAVAC=""
JAVA=""
for candidate in /opt/homebrew/opt/openjdk@17/bin "$(dirname "$(command -v javac 2>/dev/null || echo /nonexistent)")"; do
  if [[ -x "$candidate/javac" ]] && "$candidate/javac" -version >/dev/null 2>&1; then
    JAVAC="$candidate/javac"
    JAVA="$candidate/java"
    break
  fi
done
if [[ -n "$JAVAC" && -x "$JAVA" ]]; then
  if "$JAVAC" -d "$BUILD/java" "$HERE"/java/tech/hartle/medusa/companion/*.java 2>"$BUILD/javac.log"; then
    if out=$("$JAVA" -cp "$BUILD/java" tech.hartle.medusa.companion.ConformanceMain "$VECTORS" 2>&1); then
      pass "java     $out"
    else
      fail "java"
      printf '%s\n' "$out" | sed 's/^/          /'
    fi
  else
    fail "java (compile)"
    tail -5 "$BUILD/javac.log" | sed 's/^/          /'
  fi
else
  skip "java" "no working JDK (a javac that exists but cannot run is not one)"
fi

# ---------------------------------------------------------------- swift
if command -v swiftc >/dev/null; then
  if swiftc -O "$HERE/swift/CompanionProtocol.swift" "$HERE/swift/Conformance.swift" \
       -o "$BUILD/swift-conformance" 2>"$BUILD/swiftc.log"; then
    if out=$("$BUILD/swift-conformance" "$VECTORS" 2>&1); then
      pass "swift    $out"
    else
      fail "swift"
      printf '%s\n' "$out" | sed 's/^/          /'
    fi
  else
    fail "swift (compile)"
    tail -5 "$BUILD/swiftc.log" | sed 's/^/          /'
  fi
else
  skip "swift" "swiftc not found (macOS only)"
fi

# ---------------------------------------------------------------- typescript
# The client uses Node's built-in runner, not vitest — its package.json says
# `node --test`. Guessing the runner produced "no test suite found", which
# looks like a broken test rather than a wrong command.
CLIENT="$HERE/../esp-lab/client"
if command -v node >/dev/null; then
  if out=$(cd "$CLIENT" && node --test src/companion/protocol.test.ts 2>&1); then
    pass "typescript"
  else
    fail "typescript"
    printf '%s\n' "$out" | tail -8 | sed 's/^/          /'
  fi
else
  skip "typescript" "node not found"
fi

printf '\n  %d ran, %d failed, %d skipped\n\n' "$RAN" "$FAILED" "$SKIPPED"
[[ "$FAILED" -eq 0 ]] || exit 1
