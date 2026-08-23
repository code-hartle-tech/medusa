# Medusa devkit

Write capabilities for Medusa without touching firmware, without a toolchain,
and without reflashing anything.

```bash
./medusa-plugin new my-survey      # scaffold something that already runs
./medusa-plugin lint my-survey.medusa   # validate + see exactly what it will do
./medusa-plugin examples           # steal from the bundled ones
```

The only dependency is a C compiler, and only because the validator is built
from the firmware's own parser.

## A plugin in ten lines

```
#!medusa-plugin 1
id site-survey
name Site Survey Sweep
author you
version 1
needs rx,storage

> log starting
> repeat 6
>   sweep 1 13 400
> end
```

`lint` runs it against a printing host, so you see the loop expanded, every
channel change, and — if it transmits — exactly how many frames it would ask
for. Nothing is transmitted and no hardware is needed.

## Steps

| Step | What it does |
|---|---|
| `log <text>` | Note something in the run log |
| `channel <1-14>` | Park on one channel |
| `sweep <from> <to> <ms>` | Hop a range, dwelling on each |
| `scan <ms>` | Listen where you are |
| `wait <ms>` | Do nothing |
| `tx <profile> <count>` | Transmit — needs `tx`, passes the guard |
| `repeat <n>` … `end` | Loop, up to four deep |

## What you declare, you get — and only that

`needs` is not documentation. A plugin that does not declare `tx` **cannot
contain a `tx` step**; the parser refuses it. That matters because the
companion shows your `needs` line to the operator before running you, and a
declaration nobody enforces is just a promise.

Transmission is never a way around the operator's policy. Every `tx` step goes
through the same firmware guard as everything else, so their session, scope,
rate ceiling and stop button all apply to your plugin exactly as they apply to
built-in capabilities. If they refuse, your program stops rather than carrying
on with a premise that no longer holds.

## Why a recipe rather than a compiled app

Flipper's ecosystem grew on loadable native apps. On an ESP32 — tens of
kilobytes of usable RAM, no MMU, no process isolation — a loadable native blob
is not an app, it is unbounded code with full hardware access. There is no
sandbox to put it in.

A step program gets the important parts anyway:

- **Safe by construction.** There is no opcode for "write arbitrary memory".
  The worst a bad plugin can do is waste time.
- **Reviewable before it runs.** The companion renders your steps. Nobody
  installs a binary and hopes.
- **Portable.** The same plugin runs on any Medusa, no build step, no ABI.
- **Shareable.** It is a text file. Paste it in a message.

## Ideas we would like to see built

Not a roadmap — a list of things the primitives already support:

- **Coverage maps** — sweep and dwell patterns tuned for walking a floor
- **Change detection** — a long watch that flags what appeared since last run
- **Regression suites** — run the same survey monthly and diff the posture
- **Recovery tests** — pressure and quiet windows, watching what comes back
- **Handover studies** — dwell on adjacent channels to watch clients move
- **Conference-mode watch** — dense-airspace sweeps with short dwells

## Sharing

A plugin is one text file with no dependencies, so sharing is copy and paste.
If you build something good, open an issue — we would like a community
catalogue, and the format was chosen partly because it makes one possible.

## Roadmap for the devkit itself

- A `catalogue` command to install a shared plugin by name
- Signed plugins, so a catalogue entry can prove who wrote it
- Richer host services: GPS position, display output, storage queries
- A simulator that replays a captured environment, so a plugin can be
  developed against real data with no radio at all

## Under the hood

`medusa-plugin lint` compiles
`../tools/esp-hw/medusa_companion/medusa_plugin.c` — the firmware's parser and
step machine — into a local binary and runs your file through it. A separate
host-side validator would drift from the device within a release or two, and
then the tool telling you "this is fine" would be the one that is wrong.
