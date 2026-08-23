#!/usr/bin/env python3
"""Walk through the respiration validation, one prompt at a time.

The protocol has four runs, a traffic generator that must stay up throughout,
and a couple of choices that are easy to get wrong. Handing someone four
commands to track while they are also trying to breathe to a metronome is a
good way to get a bad experiment, so this drives the whole thing and asks for
nothing but Enter.

It starts the traffic itself, runs each capture in order, and prints one
summary at the end including whatever failed.

Full protocol and reasoning:
    wiki/research/2026-08-23-respiration-validation-protocol.md
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))

BOLD, DIM, GREEN, YELLOW, RED, RESET = (
    "\033[1m", "\033[2m", "\033[32m", "\033[33m", "\033[31m", "\033[0m")


def say(msg="", colour=""):
    print(f"{colour}{msg}{RESET}" if colour else msg, flush=True)


def wait(prompt, countdown=15):
    """Wait for the operator, by Enter if there is a terminal and by clock if
    there is not.

    Not every way of launching this gives the process a TTY — Claude Code's
    `!` prefix does not, and there input() sees EOF immediately and the whole
    run aborts before the first capture. Falling back to a countdown keeps it
    usable everywhere, and a countdown is arguably better anyway: it tells the
    subject exactly how long they have to settle.
    """
    say(f"\n{BOLD}{prompt}{RESET}")
    if sys.stdin.isatty():
        try:
            input("  Press Enter when ready… ")
            return
        except (EOFError, KeyboardInterrupt):
            say("\naborted", RED)
            raise SystemExit(1)
    say(f"  no terminal attached — starting in {countdown}s. Get into position.", DIM)
    for remaining in range(countdown, 0, -1):
        if remaining <= 5 or remaining % 5 == 0:
            say(f"    {remaining}…", DIM)
        time.sleep(1)


def pace(bpm, stop_event):
    """Print inhale/exhale so the subject has something to follow without
    needing a second app open.

    Runs on its own thread because the capture blocks for two minutes, and a
    metronome that only starts once the capture has finished would be worse
    than none.
    """
    period = 60.0 / bpm
    say(f"\n  Breathe with this — one full breath every {period:.1f}s.", DIM)
    while not stop_event.is_set():
        say(f"  {BOLD}in …{RESET}", GREEN)
        if stop_event.wait(period / 2):
            break
        say(f"  {BOLD}    … out{RESET}", YELLOW)
        if stop_event.wait(period / 2):
            break


def default_gateway():
    """Discover this machine's gateway rather than shipping someone's.

    A hardcoded LAN address is both wrong on every other network and a small
    privacy leak in a public repository — the mirror's own scanner rejected an
    earlier version of this file for exactly that.
    """
    for cmd, key in ((["route", "-n", "get", "default"], "gateway:"),
                     (["ip", "route", "show", "default"], "via")):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        for token in out.split():
            if token.count(".") == 3 and all(p.isdigit() for p in token.split(".")):
                return token
        for line in out.splitlines():
            if key in line:
                parts = line.split()
                if parts:
                    return parts[-1]
    return None


class NoTraffic:
    """Active mode already generates its own traffic from the board."""

    def __enter__(self):
        say("  active mode: the board generates its own traffic", DIM)
        return self

    def __exit__(self, *exc):
        return False


class Traffic:
    """Keeps the target access point busy for the whole session.

    Without this the only frames are beacons, which measured 0.4-1.2 Hz here —
    below what breathing needs, so every run would be refused.
    """

    def __init__(self, gateway):
        self.gateway = gateway
        self.proc = None

    def __enter__(self):
        say(f"  starting traffic to {self.gateway} …", DIM)
        self.proc = subprocess.Popen(
            ["ping", "-i", "0.02", self.gateway],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.5)
        if self.proc.poll() is not None:
            say(f"  could not ping {self.gateway} — runs will probably be refused", RED)
        return self

    def __exit__(self, *exc):
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGINT)
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        say("  traffic stopped", DIM)


def capture(args, label, out_path, expect=None, seconds=120, pace_bpm=None):
    cmd = ["python3", os.path.join(HERE, "esp_hw.py"), "vitals",
           "--port", args.port, "--channel", str(args.channel),
           "--seconds", str(seconds), "--save", out_path]
    if args.chip:
        cmd += ["--chip", args.chip]
    if args.ssid:
        cmd += ["--ssid", args.ssid]
        if args.password:
            cmd += ["--password", args.password]
    elif args.bssid:
        cmd += ["--bssid", args.bssid]
    if expect is not None:
        cmd += ["--expect", str(expect)]

    say(f"\n  capturing {label} ({seconds}s)…", DIM)
    stop = threading.Event()
    pacer = None
    if pace_bpm:
        pacer = threading.Thread(target=pace, args=(pace_bpm, stop), daemon=True)
        pacer.start()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
    finally:
        stop.set()
        if pacer:
            pacer.join(timeout=2)
    payload = None
    for line in (r.stdout or "").splitlines():
        if line.startswith("VITALS_JSON "):
            try:
                payload = json.loads(line[len("VITALS_JSON "):])
            except ValueError:
                payload = None
    if payload is None:
        tail = (r.stdout or "").strip().splitlines()[-3:] or ["no output"]
        say("  run failed:", RED)
        for t in tail:
            say(f"    {t}", RED)
        return {"label": label, "result": "FAILED", "path": out_path}

    payload["label"] = label
    payload["path"] = out_path
    res = payload.get("result")
    colour = {"BREATHING": GREEN, "REFUSED": RED}.get(res, YELLOW)
    detail = payload.get("bpm")
    say(f"  {res}" + (f" — {detail} bpm" if detail is not None else ""), colour)
    if "error_bpm" in payload:
        e = payload["error_bpm"]
        say(f"  error vs your counted rate: {e:+.2f} bpm",
            GREEN if abs(e) <= 2 else YELLOW)
    return payload


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", default="/dev/cu.usbmodem1101")
    ap.add_argument("--chip", default="esp32-c3")
    ap.add_argument("--channel", type=int, default=9)
    ap.add_argument("--bssid", help="transmitter to sense against; omit to auto-select")
    ap.add_argument("--gateway", help="what to ping for traffic (default: this "
                                      "machine's own default gateway). Ignored "
                                      "in active mode, which makes its own.")
    ap.add_argument("--ssid", help="ACTIVE MODE: the board joins this network and "
                                   "pings it, setting its own sample rate")
    ap.add_argument("--password", help="password for --ssid (never logged)")
    ap.add_argument("--outdir", default=".", help="where to write the four captures")
    ap.add_argument("--seconds", type=int, default=120)
    ap.add_argument("--runs", default="ABCD",
                    help="which runs to do, e.g. B for a single paced run. "
                         "Doing fewer is a smoke test, not a validation: the "
                         "evidence comes from B and C tracking two DIFFERENT "
                         "chosen rates, with A proving the room was quiet.")
    args = ap.parse_args()
    wanted = set(args.runs.upper())


    # Active mode generates its own traffic from the board, so the host-side
    # ping is redundant — and pinging from a machine that may not even be on
    # the same network would just be noise.
    if not args.ssid and not args.gateway:
        args.gateway = default_gateway()
        if not args.gateway:
            say("could not determine the default gateway; pass --gateway", RED)
            return 2

    os.makedirs(args.outdir, exist_ok=True)
    p = lambda name: os.path.join(args.outdir, name)  # noqa: E731

    n = len(wanted & set("ABCD"))
    say(f"\n{BOLD}Respiration validation — {n} run(s), "
        f"about {n * (args.seconds + 40) // 60} minutes.{RESET}")
    say(f"Each run captures for {args.seconds}s. You will be told when to move.\n", DIM)
    say("Sit 0.5-2 m from the board, ideally between it and the access point.", DIM)
    say("Stay still: a shifting arm moves far more than a breathing chest.", DIM)

    results = []
    traffic = Traffic(args.gateway) if args.gateway else NoTraffic()
    with traffic:
        if "A" in wanted:
            wait("RUN A — the control. LEAVE THE ROOM after pressing Enter.")
            results.append(capture(args, "A: empty room", p("runA-empty.tsv"),
                                   seconds=args.seconds))

        a = results[-1] if results else {}
        if a.get("result") == "BREATHING":
            say("\n  Something in the empty room is oscillating at breathing rate "
                f"({a.get('bpm')} bpm).", RED)
            say("  A fan, a pump, or a draught. Every later run is confounded until "
                "it is found.", RED)
            say("  Continuing anyway, but treat the rest with suspicion.", YELLOW)

        if "B" in wanted:
            wait("RUN B — sit down. You will breathe at 12 breaths/min (one every 5s).")
            results.append(capture(args, "B: 12 bpm counted", p("runB-12bpm.tsv"),
                                   expect=12, seconds=args.seconds, pace_bpm=12))

        if "C" in wanted:
            wait("RUN C — same seat. Now 20 breaths/min (one every 3s). "
                 "This is the run that matters most.")
            results.append(capture(args, "C: 20 bpm counted", p("runC-20bpm.tsv"),
                                   expect=20, seconds=args.seconds, pace_bpm=20))

        if "D" in wanted:
            wait("RUN D — breathe normally, then HOLD your breath for ~30s "
                 "about 40s in, then resume.")
            results.append(capture(args, "D: breath hold", p("runD-hold.tsv"),
                                   seconds=args.seconds))

    say(f"\n{BOLD}Summary{RESET}")
    for r in results:
        res = r.get("result", "?")
        line = f"  {r['label']:<22} {res}"
        if r.get("bpm") is not None:
            line += f"  {r['bpm']} bpm"
        if "error_bpm" in r:
            line += f"  (error {r['error_bpm']:+.2f})"
        colour = {"BREATHING": GREEN, "REFUSED": RED, "FAILED": RED}.get(res, YELLOW)
        say(line, colour)

    b = next((r for r in results if r["label"].startswith("B")), {})
    c = next((r for r in results if r["label"].startswith("C")), {})
    say("")
    if b.get("result") == "BREATHING" and c.get("result") == "BREATHING":
        if abs(b.get("error_bpm", 99)) <= 3 and abs(c.get("error_bpm", 99)) <= 3:
            say("  Both counted runs tracked their chosen rates. That is the result.", GREEN)
        else:
            say("  Both runs detected breathing but the rates are off. Worth a repeat.", YELLOW)
    else:
        say("  Not a clean result. The captures are saved and can be re-analysed "
            "without the hardware:", YELLOW)
        say(f"    python3 {os.path.join(HERE, 'medusa_csi_vitals.py')} "
            f"--capture {p('runB-12bpm.tsv')} --expect 12 --signal phase-diff", DIM)

    say(f"\n  captures written to {os.path.abspath(args.outdir)}\n", DIM)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
