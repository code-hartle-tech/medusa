#!/usr/bin/env bash
# Build and inspect Xtensa ELF files only. This script has no flash, serial,
# Wi-Fi, network, or host-privilege operations.

set -euo pipefail

LAB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$LAB_DIR/src"
BUILD_DIR="$LAB_DIR/build"

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

pass() {
    printf 'PASS: %s\n' "$*"
}

find_compiler() {
    if [[ -n "${XTENSA_CC:-}" ]]; then
        [[ -x "$XTENSA_CC" ]] || fail "XTENSA_CC is not executable: $XTENSA_CC"
        printf '%s\n' "$XTENSA_CC"
        return
    fi

    if command -v xtensa-esp32s3-elf-gcc >/dev/null 2>&1; then
        command -v xtensa-esp32s3-elf-gcc
        return
    fi

    local roots=()
    if [[ -n "${ARDUINO_DATA_DIR:-}" ]]; then
        roots+=("$ARDUINO_DATA_DIR")
    else
        roots+=("$HOME/Library/Arduino15/packages" "$HOME/.arduino15/packages")
    fi
    local candidate="" root
    for root in "${roots[@]}"; do
        [[ -d "$root" ]] || continue
        candidate+="$(find "$root" -type f -name xtensa-esp32s3-elf-gcc \
            -path '*/bin/xtensa-esp32s3-elf-gcc' 2>/dev/null || true)"$'\n'
    done
    candidate="$(printf '%s' "$candidate" | sed '/^$/d' | sort | tail -n 1)"
    [[ -n "$candidate" ]] || fail \
        "xtensa-esp32s3-elf-gcc not found; put it on PATH or set XTENSA_CC"
    printf '%s\n' "$candidate"
}

find_lx106_compiler() {
    if command -v xtensa-lx106-elf-gcc >/dev/null 2>&1; then
        command -v xtensa-lx106-elf-gcc
        return
    fi

    local roots=()
    if [[ -n "${ARDUINO_DATA_DIR:-}" ]]; then
        roots+=("$ARDUINO_DATA_DIR")
    else
        roots+=("$HOME/Library/Arduino15/packages" "$HOME/.arduino15/packages")
    fi
    local candidate="" root
    for root in "${roots[@]}"; do
        [[ -d "$root" ]] || continue
        candidate+="$(find "$root" -type f -name xtensa-lx106-elf-gcc \
            -path '*/bin/xtensa-lx106-elf-gcc' 2>/dev/null || true)"$'\n'
    done
    candidate="$(printf '%s' "$candidate" | sed '/^$/d' | sort | tail -n 1)"
    [[ -n "$candidate" ]] || return 1
    printf '%s\n' "$candidate"
}

CC="$(find_compiler)"
TOOL_BIN="$(cd "$(dirname "$CC")" && pwd)"
NM="$TOOL_BIN/xtensa-esp32s3-elf-nm"
OBJDUMP="$TOOL_BIN/xtensa-esp32s3-elf-objdump"
AR="$TOOL_BIN/xtensa-esp32s3-elf-ar"

[[ -x "$NM" ]] || fail "matching nm not found: $NM"
[[ -x "$OBJDUMP" ]] || fail "matching objdump not found: $OBJDUMP"
[[ -x "$AR" ]] || fail "matching ar not found: $AR"
mkdir -p "$BUILD_DIR"
rm -f \
    "$BUILD_DIR/abi_probe_lx106.o" \
    "$BUILD_DIR/abi-probe-lx106.objdump.txt" \
    "$BUILD_DIR/abi-sum4-lx106-call0.txt" \
    "$BUILD_DIR/abi-callsite-lx106-call0.txt"

REQUIRE_LX106="${REQUIRE_LX106:-0}"
[[ "$REQUIRE_LX106" == 0 || "$REQUIRE_LX106" == 1 ]] || \
    fail 'REQUIRE_LX106 must be 0 or 1'
if [[ -n "${XTENSA_LX106_CC:-}" ]]; then
    [[ -x "$XTENSA_LX106_CC" ]] || \
        fail "XTENSA_LX106_CC is not executable: $XTENSA_LX106_CC"
    LX106_CC="$XTENSA_LX106_CC"
else
    LX106_CC="$(find_lx106_compiler || true)"
fi

COMMON_CFLAGS=(
    -std=c11
    -Os
    -ffreestanding
    -fno-builtin
    -fno-inline
    -fno-ipa-cp
    -fno-ipa-sra
    -fno-optimize-sibling-calls
    -ffunction-sections
    -fdata-sections
    -Wall
    -Wextra
    -Werror
)

LINK_BASE=(
    -nostdlib
    -nostartfiles
    -nodefaultlibs
)

printf 'Toolchain: '
"$CC" --version | sed -n '1p'

"$CC" "${COMMON_CFLAGS[@]}" -c "$SRC_DIR/vendor_validator.c" \
    -o "$BUILD_DIR/vendor_validator.o"
"$CC" "${COMMON_CFLAGS[@]}" -c "$SRC_DIR/app_override.c" \
    -o "$BUILD_DIR/app_override.o"
"$CC" "${COMMON_CFLAGS[@]}" -c "$SRC_DIR/external_call.c" \
    -o "$BUILD_DIR/external_call.o"
"$CC" "${COMMON_CFLAGS[@]}" -c "$SRC_DIR/wrapper.c" \
    -o "$BUILD_DIR/wrapper.o"
"$CC" "${COMMON_CFLAGS[@]}" -c "$SRC_DIR/abi_probe.c" \
    -o "$BUILD_DIR/abi_probe.o"
"$CC" "${COMMON_CFLAGS[@]}" -c "$SRC_DIR/archive_vendor_member.c" \
    -o "$BUILD_DIR/archive_vendor_member.o"
"$CC" "${COMMON_CFLAGS[@]}" -c "$SRC_DIR/archive_app_override.c" \
    -o "$BUILD_DIR/archive_app_override.o"
"$CC" "${COMMON_CFLAGS[@]}" -c "$SRC_DIR/archive_public_caller.c" \
    -o "$BUILD_DIR/archive_public_caller.o"
rm -f "$BUILD_DIR/libvendor_demo.a"
"$AR" rcs "$BUILD_DIR/libvendor_demo.a" \
    "$BUILD_DIR/archive_vendor_member.o"

LX106_RAN=0
if [[ -n "$LX106_CC" ]]; then
    LX106_TOOL_BIN="$(cd "$(dirname "$LX106_CC")" && pwd)"
    LX106_OBJDUMP="$LX106_TOOL_BIN/xtensa-lx106-elf-objdump"
    [[ -x "$LX106_OBJDUMP" ]] || \
        fail "matching LX106 objdump not found: $LX106_OBJDUMP"
    printf 'Optional LX106 toolchain: '
    "$LX106_CC" --version | sed -n '1p'
    "$LX106_CC" "${COMMON_CFLAGS[@]}" -c "$SRC_DIR/abi_probe.c" \
        -o "$BUILD_DIR/abi_probe_lx106.o"
    "$LX106_OBJDUMP" -dr "$BUILD_DIR/abi_probe_lx106.o" \
        >"$BUILD_DIR/abi-probe-lx106.objdump.txt"
    LX106_RAN=1
elif [[ "$REQUIRE_LX106" == 1 ]]; then
    fail 'xtensa-lx106-elf-gcc is required but was not found'
else
    printf '%s\n' \
        'SKIP: optional LX106 call0 comparison (set XTENSA_LX106_CC to enable)'
fi

# 1. Vendor-only baseline: one ordinary strong definition.
"$CC" "${LINK_BASE[@]}" \
    -Wl,-e,same_object_call \
    -Wl,-Map,"$BUILD_DIR/baseline.map" \
    "$BUILD_DIR/vendor_validator.o" \
    -o "$BUILD_DIR/baseline.elf"

# Duplicate strong symbols are normally an error. Keep the diagnostic as a
# teaching artifact, then perform the intentional -z muldefs experiment.
if "$CC" "${LINK_BASE[@]}" \
    -Wl,-e,lab_validator \
    "$BUILD_DIR/app_override.o" "$BUILD_DIR/vendor_validator.o" \
    -o "$BUILD_DIR/duplicate-without-muldefs.elf" \
    >"$BUILD_DIR/duplicate-without-muldefs.stdout.txt" \
    2>"$BUILD_DIR/duplicate-without-muldefs.stderr.txt"; then
    fail "duplicate strong definitions linked without -z muldefs"
fi
pass 'duplicate strong definitions are rejected by default'

# 2. GNU ld's -z muldefs keeps going and selects the first definition. The
# application object is deliberately earlier than the vendor object.
"$CC" "${LINK_BASE[@]}" \
    -Wl,-e,lab_validator \
    -Wl,-z,muldefs \
    -Wl,-Map,"$BUILD_DIR/muldefs-app-first.map" \
    "$BUILD_DIR/app_override.o" "$BUILD_DIR/vendor_validator.o" \
    -o "$BUILD_DIR/muldefs-app-first.elf"

# 3. --wrap only redirects undefined references. same_object_call() and the
# definition are in vendor_validator.o; separate_object_call() is not.
"$CC" "${LINK_BASE[@]}" \
    -Wl,-e,separate_object_call \
    -Wl,--wrap=lab_validator \
    -Wl,-Map,"$BUILD_DIR/wrap.map" \
    "$BUILD_DIR/vendor_validator.o" \
    "$BUILD_DIR/external_call.o" \
    "$BUILD_DIR/wrapper.o" \
    -o "$BUILD_DIR/wrap.elf"

# 4. Static-archive extraction. Merely placing libvendor_demo.a on the link
# line does not extract its member. Referencing vendor_public_entry does; that
# same member then contributes a second strong archive_validator definition.
"$CC" "${LINK_BASE[@]}" \
    -Wl,-e,archive_validator \
    -Wl,-Map,"$BUILD_DIR/archive-not-extracted.map" \
    "$BUILD_DIR/archive_app_override.o" \
    "$BUILD_DIR/libvendor_demo.a" \
    -o "$BUILD_DIR/archive-not-extracted.elf"

if "$CC" "${LINK_BASE[@]}" \
    -Wl,-e,archive_public_caller \
    "$BUILD_DIR/archive_app_override.o" \
    "$BUILD_DIR/archive_public_caller.o" \
    "$BUILD_DIR/libvendor_demo.a" \
    -o "$BUILD_DIR/archive-extracted-without-muldefs.elf" \
    >"$BUILD_DIR/archive-extracted-without-muldefs.stdout.txt" \
    2>"$BUILD_DIR/archive-extracted-without-muldefs.stderr.txt"; then
    fail 'extracted archive member duplicated a strong symbol without an error'
fi

"$CC" "${LINK_BASE[@]}" \
    -Wl,-e,archive_public_caller \
    -Wl,-z,muldefs \
    -Wl,-Map,"$BUILD_DIR/archive-extracted-muldefs.map" \
    "$BUILD_DIR/archive_app_override.o" \
    "$BUILD_DIR/archive_public_caller.o" \
    "$BUILD_DIR/libvendor_demo.a" \
    -o "$BUILD_DIR/archive-extracted-muldefs.elf"

for stem in baseline muldefs-app-first wrap archive-not-extracted archive-extracted-muldefs; do
    "$NM" -n "$BUILD_DIR/$stem.elf" >"$BUILD_DIR/$stem.nm.txt"
    "$OBJDUMP" -dr "$BUILD_DIR/$stem.elf" >"$BUILD_DIR/$stem.objdump.txt"
done
"$NM" -n "$BUILD_DIR/vendor_validator.o" \
    >"$BUILD_DIR/vendor-validator-object.nm.txt"
"$NM" -n "$BUILD_DIR/external_call.o" \
    >"$BUILD_DIR/external-call-object.nm.txt"
"$OBJDUMP" -dr "$BUILD_DIR/vendor_validator.o" \
    >"$BUILD_DIR/vendor-validator-object.objdump.txt"
"$OBJDUMP" -dr "$BUILD_DIR/external_call.o" \
    >"$BUILD_DIR/external-call-object.objdump.txt"
"$OBJDUMP" -dr "$BUILD_DIR/abi_probe.o" \
    >"$BUILD_DIR/abi-probe.objdump.txt"

symbol_addr() {
    local report="$1"
    local symbol="$2"
    awk -v wanted="$symbol" '$3 == wanted { print $1; exit }' "$report"
}

require_symbol_type() {
    local report="$1"
    local type="$2"
    local symbol="$3"
    awk -v wanted_type="$type" -v wanted_symbol="$symbol" '
        ($2 == wanted_type && $3 == wanted_symbol) ||
        ($1 == wanted_type && $2 == wanted_symbol) { found=1 }
        END { exit !found }
    ' \
        "$report" || fail "$symbol is not type $type in $(basename "$report")"
}

require_same_address() {
    local report="$1"
    local left="$2"
    local right="$3"
    local left_addr right_addr
    left_addr="$(symbol_addr "$report" "$left")"
    right_addr="$(symbol_addr "$report" "$right")"
    [[ -n "$left_addr" && -n "$right_addr" ]] || \
        fail "missing $left or $right in $(basename "$report")"
    [[ "$left_addr" == "$right_addr" ]] || \
        fail "$left ($left_addr) != $right ($right_addr) in $(basename "$report")"
}

require_different_address() {
    local report="$1"
    local left="$2"
    local right="$3"
    local left_addr right_addr
    left_addr="$(symbol_addr "$report" "$left")"
    right_addr="$(symbol_addr "$report" "$right")"
    [[ -n "$left_addr" && -n "$right_addr" ]] || \
        fail "missing $left or $right in $(basename "$report")"
    [[ "$left_addr" != "$right_addr" ]] || \
        fail "$left unexpectedly equals $right in $(basename "$report")"
}

require_text() {
    local file="$1"
    local pattern="$2"
    local description="$3"
    grep -Eq -- "$pattern" "$file" || fail "$description"
}

reject_text() {
    local file="$1"
    local pattern="$2"
    local description="$3"
    if grep -Eq -- "$pattern" "$file"; then
        fail "$description"
    fi
}

first_map_line() {
    local map_file="$1"
    local object_name="$2"
    grep -n -m 1 -F "$object_name" "$map_file" | cut -d: -f1
}

extract_function() {
    local report="$1"
    local symbol="$2"
    awk -v label="<$symbol>:" '
        index($0, label) { in_function=1 }
        in_function && /^[[:space:]]*$/ { exit }
        in_function { print }
    ' "$report"
}

# nm: baseline function and its vendor-only alias are strong text symbols at
# the same address.
require_symbol_type "$BUILD_DIR/baseline.nm.txt" T lab_validator
require_symbol_type "$BUILD_DIR/baseline.nm.txt" T vendor_validator_entry
require_same_address "$BUILD_DIR/baseline.nm.txt" \
    lab_validator vendor_validator_entry
require_text "$BUILD_DIR/baseline.map" 'vendor_validator\.o' \
    'baseline map does not name vendor_validator.o'
pass 'baseline exposes the vendor validator as one strong T symbol'

# nm + map: -z muldefs selects the definition from the first input object.
require_symbol_type "$BUILD_DIR/muldefs-app-first.nm.txt" T lab_validator
require_same_address "$BUILD_DIR/muldefs-app-first.nm.txt" \
    lab_validator app_validator_entry
require_different_address "$BUILD_DIR/muldefs-app-first.nm.txt" \
    lab_validator vendor_validator_entry
extract_function "$BUILD_DIR/muldefs-app-first.objdump.txt" same_object_call \
    >"$BUILD_DIR/muldefs-same-object-call.txt"
require_text "$BUILD_DIR/muldefs-same-object-call.txt" \
    'call8.*<(lab_validator|app_validator_entry)>' \
    'same-object call did not resolve to the earlier app definition under -z muldefs'
app_map_line="$(first_map_line "$BUILD_DIR/muldefs-app-first.map" app_override.o)"
vendor_map_line="$(first_map_line "$BUILD_DIR/muldefs-app-first.map" vendor_validator.o)"
[[ -n "$app_map_line" && -n "$vendor_map_line" ]] || \
    fail 'muldefs map is missing one of the input objects'
(( app_map_line < vendor_map_line )) || \
    fail 'map does not show app_override.o before vendor_validator.o'
pass '-z muldefs selects the earlier app definition, including the vendor-object call'

# Archive maps + symbols: no undefined public entry means no member extraction.
# Adding that one reference extracts the member and exposes its duplicate
# validator. Under -z muldefs, both the selected symbol and the call compiled
# inside the archive member resolve to the earlier application body.
reject_text "$BUILD_DIR/archive-not-extracted.map" \
    'libvendor_demo\.a\(archive_vendor_member\.o\)' \
    'vendor archive member was extracted without an undefined public reference'
reject_text "$BUILD_DIR/archive-not-extracted.nm.txt" \
    '[[:space:]](vendor_public_entry|vendor_archive_validator_entry)$' \
    'unextracted archive unexpectedly contributed vendor symbols'
require_same_address "$BUILD_DIR/archive-not-extracted.nm.txt" \
    archive_validator app_archive_validator_entry
require_text "$BUILD_DIR/archive-extracted-without-muldefs.stderr.txt" \
    'multiple definition of [`'"'"']?archive_validator' \
    'archive extraction did not produce the expected duplicate-symbol diagnostic'
require_text "$BUILD_DIR/archive-extracted-muldefs.map" \
    'libvendor_demo\.a\(archive_vendor_member\.o\)' \
    'pulled link map does not name the extracted vendor archive member'
require_same_address "$BUILD_DIR/archive-extracted-muldefs.nm.txt" \
    archive_validator app_archive_validator_entry
require_different_address "$BUILD_DIR/archive-extracted-muldefs.nm.txt" \
    archive_validator vendor_archive_validator_entry
extract_function "$BUILD_DIR/archive-extracted-muldefs.objdump.txt" \
    vendor_public_entry \
    >"$BUILD_DIR/archive-vendor-public-call.txt"
require_text "$BUILD_DIR/archive-vendor-public-call.txt" \
    'call8.*<(archive_validator|app_archive_validator_entry)>' \
    'archive-member call did not resolve to the earlier application definition'
pass 'public reference extracts one archive member; duplicate handling then follows link order'

# Object nm/objdump: both calls name the symbol in a relocation, but it is a
# strong definition (T) in the same object and genuinely undefined (U) in the
# separate caller. --wrap only substitutes the latter class of reference.
require_symbol_type "$BUILD_DIR/vendor-validator-object.nm.txt" T lab_validator
require_symbol_type "$BUILD_DIR/external-call-object.nm.txt" U lab_validator
require_text "$BUILD_DIR/vendor-validator-object.objdump.txt" \
    'R_XTENSA_[^[:space:]]+[[:space:]]+lab_validator' \
    'same-object relocation does not name lab_validator'
require_text "$BUILD_DIR/external-call-object.objdump.txt" \
    'R_XTENSA_[^[:space:]]+[[:space:]]+lab_validator' \
    'separate-object relocation does not name undefined lab_validator'

# Linked objdump: prove the two calls land on different destinations.
extract_function "$BUILD_DIR/wrap.objdump.txt" same_object_call \
    >"$BUILD_DIR/wrap-same-object-call.txt"
extract_function "$BUILD_DIR/wrap.objdump.txt" separate_object_call \
    >"$BUILD_DIR/wrap-separate-object-call.txt"
require_text "$BUILD_DIR/wrap-same-object-call.txt" \
    'call8.*<(lab_validator|vendor_validator_entry)>' \
    'same-object call did not land on the original validator'
require_text "$BUILD_DIR/wrap-separate-object-call.txt" \
    'call8.*<(__wrap_lab_validator|wrapper_entry)>' \
    'separate-object call was not redirected to the wrapper'
require_same_address "$BUILD_DIR/wrap.nm.txt" \
    lab_validator vendor_validator_entry
require_same_address "$BUILD_DIR/wrap.nm.txt" \
    __wrap_lab_validator wrapper_entry
require_different_address "$BUILD_DIR/wrap.nm.txt" \
    lab_validator __wrap_lab_validator
require_text "$BUILD_DIR/wrap.map" 'wrapper\.o' \
    'wrap map does not name wrapper.o'
pass '--wrap redirects the undefined external call but not the same-object call'

# ABI objdump: the callee consumes four integer arguments through a2..a5; the
# call site uses CALL8, and windowed functions return with RETW.N.
extract_function "$BUILD_DIR/abi-probe.objdump.txt" abi_sum4 \
    >"$BUILD_DIR/abi-sum4.txt"
extract_function "$BUILD_DIR/abi-probe.objdump.txt" abi_callsite \
    >"$BUILD_DIR/abi-callsite.txt"
for register in a2 a3 a4 a5; do
    require_text "$BUILD_DIR/abi-sum4.txt" "\\b$register\\b" \
        "abi_sum4 disassembly does not use $register"
done
for register in a10 a11 a12 a13; do
    require_text "$BUILD_DIR/abi-callsite.txt" "\\b$register\\b" \
        "windowed abi_callsite disassembly does not use $register"
done
require_text "$BUILD_DIR/abi-callsite.txt" '\bcall8\b' \
    'abi_callsite does not use call8'
require_text "$BUILD_DIR/abi-callsite.txt" \
    'R_XTENSA_[^[:space:]]+[[:space:]]+abi_sum4' \
    'abi_callsite call relocation does not target abi_sum4'
require_text "$BUILD_DIR/abi-probe.objdump.txt" '\bentry\b' \
    'windowed ABI probe does not contain entry'
require_text "$BUILD_DIR/abi-probe.objdump.txt" '\bretw\.n\b' \
    'ABI probe does not contain retw.n'
reject_text "$BUILD_DIR/abi-callsite.txt" '\bcall0\b' \
    'windowed abi_callsite unexpectedly contains call0'
pass 'objdump shows a2-a5 argument use, call8, and retw.n'

# Optional real ESP8266/L106 comparison: its compiler uses the call0 ABI. The
# caller passes the same four values directly in a2..a5, saves a0 itself, calls
# with CALL0, and returns with RET.N. No register-window ENTRY/RETW sequence is
# present. This proves an ABI contrast only; it exercises no ESP8266 SDK or RF.
if (( LX106_RAN )); then
    extract_function "$BUILD_DIR/abi-probe-lx106.objdump.txt" abi_sum4 \
        >"$BUILD_DIR/abi-sum4-lx106-call0.txt"
    extract_function "$BUILD_DIR/abi-probe-lx106.objdump.txt" abi_callsite \
        >"$BUILD_DIR/abi-callsite-lx106-call0.txt"
    for register in a2 a3 a4 a5; do
        require_text "$BUILD_DIR/abi-sum4-lx106-call0.txt" "\\b$register\\b" \
            "LX106 abi_sum4 disassembly does not use $register"
        require_text "$BUILD_DIR/abi-callsite-lx106-call0.txt" "\\b$register\\b" \
            "LX106 abi_callsite disassembly does not use $register"
    done
    require_text "$BUILD_DIR/abi-callsite-lx106-call0.txt" '\bcall0\b' \
        'LX106 abi_callsite does not use call0'
    require_text "$BUILD_DIR/abi-callsite-lx106-call0.txt" \
        'R_XTENSA_[^[:space:]]+[[:space:]]+abi_sum4' \
        'LX106 abi_callsite call relocation does not target abi_sum4'
    require_text "$BUILD_DIR/abi-callsite-lx106-call0.txt" \
        '\bs32i(\.n)?\s+a0,\s*a1,' \
        'LX106 caller does not save a0 on its stack frame'
    require_text "$BUILD_DIR/abi-callsite-lx106-call0.txt" \
        '\bl32i(\.n)?\s+a0,\s*a1,' \
        'LX106 caller does not restore a0 from its stack frame'
    require_text "$BUILD_DIR/abi-probe-lx106.objdump.txt" '\bret\.n\b' \
        'LX106 ABI probe does not contain ret.n'
    reject_text "$BUILD_DIR/abi-probe-lx106.objdump.txt" \
        '\b(entry|call8|retw\.n)\b' \
        'LX106 call0 probe unexpectedly contains a windowed-ABI instruction'
    pass 'LX106 comparison shows direct a2-a5 arguments, call0, and ret.n'
fi

printf '\nAll linker/ABI assertions passed. Reports are in:\n  %s\n' "$BUILD_DIR"
