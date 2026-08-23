# Xtensa ESP32-S3 linker and windowed-ABI lab

This is a **non-radio, static-analysis lab**. It compiles tiny arithmetic C
functions and inspects their ELF files; it contains no 802.11 frame data, Wi-Fi
API calls, flashing, serial access, or executable host payload. Its only
outputs are Xtensa object files, ELF files, linker maps, symbol tables, and
disassembly text.

Medusa is research and educational software for use on networks the operator
owns, administers, or has explicit written permission to test. Use against
networks or devices without authorization is illegal in most jurisdictions.
HARTLE.TECH publishes this work for security researchers, network operators
auditing their own infrastructure, educators, and capture-the-flag
participants — not for unauthorized intrusion.

Preserve the repository [`NOTICE`](../../../NOTICE) unchanged and surface the
credit “Medusa — © HARTLE.TECH” wherever attribution for third-party
components is displayed.

## Run it

The script locates `xtensa-esp32s3-elf-gcc` on `PATH` or in an Arduino package
directory. Set `XTENSA_CC=/absolute/path/to/xtensa-esp32s3-elf-gcc` to select a
specific installation.

```sh
./build-verify.sh
```

The script never runs or flashes the produced code. It fails unless
machine-readable evidence from `nm`, linker maps, and `objdump` proves every
claim below. Generated evidence lives under `build/`.

If the ESP8266 Arduino toolchain is installed, the script also detects
`xtensa-lx106-elf-gcc` and runs the optional call0 comparison described below.
Select a specific compiler or require that comparison with:

```sh
XTENSA_LX106_CC=/absolute/path/to/xtensa-lx106-elf-gcc \
REQUIRE_LX106=1 \
./build-verify.sh
```

Without that compiler, the S3 experiments still run and the script reports an
explicit `SKIP`; it never downloads or installs a toolchain itself.

## Experiment 1: the ordinary vendor baseline

[`src/vendor_validator.c`](src/vendor_validator.c) defines `lab_validator` in
the ordinary way. A non-weak, globally visible function appears as uppercase
`T` in `nm`: a **strong symbol in a text section**. Its unique
`vendor_validator_entry` alias shares the same address, letting the verifier
identify that body without executing it.

This baseline matters because neither C, Arduino, nor ESP-IDF normally permits
two strong definitions of the same global function. The default duplicate-link
attempt is expected to fail, and its diagnostic is saved in
`build/duplicate-without-muldefs.stderr.txt`.

## Experiment 2: duplicate strong definitions and `-z muldefs`

[`src/app_override.c`](src/app_override.c) supplies another strong
`lab_validator`. The special GNU ld option `-z muldefs` (equivalent in purpose
to `--allow-multiple-definition`) suppresses the normal duplicate-definition
error. GNU ld uses the **first definition encountered**, so object order is now
behavior:

```text
app_override.o  vendor_validator.o  -Wl,-z,muldefs
       ^ first strong definition wins
```

The proof is not a source-level guess:

- `muldefs-app-first.map` records the app object before the vendor object.
- `muldefs-app-first.nm.txt` shows `lab_validator` at exactly the address of
  `app_validator_entry`, not `vendor_validator_entry`.
- `muldefs-same-object-call.txt` proves that the call compiled in
  `vendor_validator.o` lands on the earlier application definition, while both
  implementations remain present for inspection.

This recreates the linker mechanism behind an application-first override
pattern without using any RF function. It also explains why copying the same C
definition into a conventional Arduino or IDF build can fail: without the
unusual link option it is a multiple-definition error; with different archive
extraction or link ordering, a different body can win.

## Experiment 3: when a static-archive member enters the link

The real Wi-Fi library is a static archive, so object extraction is a separate
step from symbol selection. This lab creates `libvendor_demo.a` with one member
that contains both `vendor_public_entry` and `archive_validator`, mirroring the
important object-file relationship without using any Wi-Fi names or behavior.

Three links expose the sequence:

1. `archive_app_override.o` plus the archive has no undefined reference to the
   vendor public entry. The archive member is not extracted and there is no
   duplicate.
2. Adding `archive_public_caller.o` creates that undefined reference. GNU ld
   extracts the one member, which also introduces its strong validator; the
   ordinary link now fails with a duplicate-definition error.
3. Repeating the pulled link with application-first `-z muldefs` succeeds. The
   selected validator is the application body, and disassembly proves that the
   call compiled inside the extracted vendor member lands there too.

The verifier checks the maps, symbol addresses, expected failure diagnostic,
and final call destination. This is why “the archive is later on the link line”
does not mean every object inside it was present from the beginning: archive
members are pulled to satisfy unresolved symbols, at object-file granularity.

## Experiment 4: the limit of `--wrap`

`-Wl,--wrap=lab_validator` rewrites an **undefined reference** to
`lab_validator` as a reference to `__wrap_lab_validator`. It does not rewrite a
reference whose symbol is already defined by the object containing the call.

The two call sites make that boundary visible:

```text
vendor_validator.o                       external_call.o
  T lab_validator                          U lab_validator
  same_object_call -> lab_validator        separate_object_call -> lab_validator
             |                                         |
             +-- definition is in this object            +-- ld can --wrap it
```

`objdump -dr` shows that both call relocations name `lab_validator`, while `nm`
reveals the decisive difference: the vendor object has a strong `T` definition
and the separate caller has a `U` reference.
After linking, disassembly proves `same_object_call` still calls the original
body and `separate_object_call` calls `__wrap_lab_validator`.

The practical lesson is object-file granularity: `--wrap` is not a universal
runtime hook. Moving the caller into another translation unit creates the
undefined reference on which the linker feature operates.

## Reading the ESP32-S3 windowed ABI

ESP32-S3 uses the Xtensa LX7 **windowed** ABI. Register names in disassembly are
the current function's view of a rotating physical register file.

For a four-argument C function such as `abi_sum4(int, int, int, int)`:

| Callee view | Meaning |
| --- | --- |
| `a2` | argument 1; also the integer return-value register |
| `a3` | argument 2 |
| `a4` | argument 3 |
| `a5` | argument 4 |

At a `call8` call site, the caller places those outgoing values in its
`a10`–`a13`. The callee's `entry` performs the register-window rotation selected
by `call8`; the same physical registers then appear to the callee as `a2`–`a5`.
The callee puts an integer result in its `a2`. After the window is restored, the
caller sees that physical register as its `a10` and can move it to its own `a2`
when returning the result onward.

`retw.n` is the 16-bit code-density form of the windowed return instruction.
It reverses the window transition and returns via the saved windowed return
address. It is not the same ABI sequence as call0-style `ret.n`.

Inspect these focused reports after a successful run:

```sh
sed -n '1,120p' build/abi-sum4.txt
sed -n '1,160p' build/abi-callsite.txt
```

The verifier requires `abi_sum4` to visibly consume `a2`, `a3`, `a4`, and
`a5`, requires the call site to contain `call8`, and requires windowed
`retw.n` instructions. That is direct evidence from the installed Espressif
compiler rather than a hand-written assembly illustration.

## Optional comparison: the real ESP8266/L106 call0 ABI

The same `abi_probe.c` can be compiled with the ESP8266 Arduino core's
`xtensa-lx106-elf-gcc`. This is a separate compiler target, not an S3 compiler
flag. The official ESP8266 Arduino 3.1.2 package index pins the
`3.1.0-gcc10.3-e5f9fec` toolchain for this purpose.

The two real compiler outputs make the ABI boundary concrete:

| Operation | ESP32-S3 windowed ABI | ESP8266/L106 call0 ABI |
| --- | --- | --- |
| Caller argument registers | `a10`–`a13` before `call8` | `a2`–`a5` before `call0` |
| Callee argument registers | `a2`–`a5` after window rotation | the same `a2`–`a5`; no rotation |
| Return-address handling | `entry`/register window | caller saves and restores `a0` |
| Return instruction | `retw.n` | `ret.n` |

When enabled, the verifier writes focused
`abi-sum4-lx106-call0.txt` and `abi-callsite-lx106-call0.txt` reports. It
requires direct `a2`–`a5` argument setup, `call0`, and `ret.n`, and rejects
`entry`, `call8`, or `retw.n` in the L106 object. This proves the ABI contrast;
it does not identify an attached module, invoke an ESP8266 SDK function, or
exercise Wi-Fi.

[Official ESP8266 Arduino package index](https://arduino.esp8266.com/stable/package_esp8266com_index.json)

## What each evidence source answers

| Evidence | Question answered |
| --- | --- |
| `*.nm.txt` | Is a symbol strong/undefined, and which aliases share its address? |
| `*.map` | Which object files were linked, and in what input order? |
| `*.objdump.txt` | Which instructions and relocation/call destinations were emitted? |

Together they separate three easily conflated stages: what C declares, what an
object file leaves unresolved, and what the final linker chooses.
