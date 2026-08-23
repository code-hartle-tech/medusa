#!/usr/bin/env python3
"""Measure the evil-twin detector on BOTH sides, using real hardware CSI.

WHY THIS EXISTS.

The detector was calibrated only against false positives: real access points,
each a single radio, none of which should ever be flagged. That establishes it
does not cry wolf and says nothing whatever about whether it catches anything.
A detector with the threshold set to infinity passes that test perfectly.

Measuring the other side needs an evil twin, and the obvious way to get one —
stand up a rogue AP cloning a BSSID — is an active transmission this project
does not do casually, and would still only produce ONE twin at one distance.

THE CONSTRUCTION.

An evil twin is two radios in different physical places claiming one identity.
Ordinary neighbouring access points are already two radios in different
physical places; they differ only in that they are honest about it. So a twin
can be built from real measurements by taking the CSI of two genuinely
different APs and relabelling both to a single BSSID.

Nothing about the physics is synthesised. Every profile is a real measurement
of a real radio at a real position, through the real room, recorded by the real
part. The ONLY thing changed is the identity string attached to it — which is
precisely, and exclusively, what an evil twin changes.

WHAT IT PROVES AND WHAT IT DOES NOT.

Proves: given two physically distinct transmitters presented under one
identity, the detector separates them (or does not), at a measured rate.

Does NOT prove: that an attacker who parks very close to the AP being imitated
is detectable. Detection must degrade as the two positions converge, and no
amount of relabelling can manufacture the near-field case. The pair distances
here are whatever the surrounding radios happened to offer, and are reported so
the result is read as "detected at THESE separations", never as "detects evil
twins".

Usage:
    python3 medusa_csi_twin_eval.py --capture run.tsv
    python3 medusa_csi_twin_eval.py --capture run.tsv --sweep
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from collections import defaultdict

from medusa_csi_analyze import (
    DEFAULT_SPLIT_MULTIPLIER,
    MIN_INTERVALS_FOR_VERDICT,
    BssidTracker,
    l1_distance,
    parse_line,
)

TWIN_BSSID = "02:00:5E:7W:1N:00".replace("W", "0").replace("N", "0")  # 02:00:5E:70:10:00


def load(path: str) -> dict[str, list[tuple]]:
    """Group parsed rows by source BSSID."""
    rows: dict[str, list[tuple]] = defaultdict(list)
    with open(path) as fh:
        for line in fh:
            p = parse_line(line)
            if not p:
                continue
            seq, mac, rssi, nsub, frames, motion, profile = p
            rows[mac].append((seq, profile, motion, rssi))
    return rows


def layout_of(samples) -> int:
    counts: dict[int, int] = defaultdict(int)
    for _, profile, _, _ in samples:
        counts[len(profile)] += 1
    return max(counts, key=counts.get)


def same_layout(samples, layout) -> list[tuple]:
    return [s for s in samples if len(s[1]) == layout]


def judge(samples, multiplier: float) -> dict:
    t = BssidTracker(TWIN_BSSID, multiplier)
    for seq, profile, motion, rssi in samples:
        # A merged BSSID would have one firmware baseline rather than two, so
        # the per-source motion figures are not what the firmware would emit.
        # They are carried through only because motion gates INSUFFICIENT_DATA;
        # the position decision reads profiles, never motion.
        t.add(seq, profile, motion, rssi)
    return t.verdict()


def interleave(a: list[tuple], b: list[tuple]) -> list[tuple]:
    """Alternate the two sources, as a twin beaconing alongside the real AP."""
    out = []
    for i in range(max(len(a), len(b))):
        if i < len(a):
            out.append((i * 2, a[i][1], a[i][2], a[i][3]))
        if i < len(b):
            out.append((i * 2 + 1, b[i][1], b[i][2], b[i][3]))
    return out


def evaluate(path: str, multiplier: float) -> dict:
    rows = load(path)
    usable = {}
    for mac, samples in rows.items():
        lay = layout_of(samples)
        kept = same_layout(samples, lay)
        if len(kept) >= MIN_INTERVALS_FOR_VERDICT:
            usable[mac] = (lay, kept)

    # ---- negative controls: each real AP alone must NOT be flagged ----
    negatives = []
    for mac, (lay, samples) in usable.items():
        v = judge(samples, multiplier)
        negatives.append({
            "bssid": mac, "layout": lay, "intervals": len(samples),
            "finding": v["finding"],
            "false_positive": v["finding"] == "POSITION_ANOMALY",
        })

    # ---- positives: two real positions under one identity ----
    positives = []
    for (ma, (la, sa)), (mb, (lb, sb)) in itertools.combinations(usable.items(), 2):
        if la != lb:
            continue  # different layouts are not comparable; see the analyser
        merged = interleave(sa, sb)
        v = judge(merged, multiplier)
        ca = BssidTracker._centroid([p for _, p, _, _ in sa])
        cb = BssidTracker._centroid([p for _, p, _, _ in sb])
        true_sep = l1_distance([int(x) for x in ca], [int(x) for x in cb])
        rssi_a = [r for _, _, _, r in sa if r is not None]
        rssi_b = [r for _, _, _, r in sb if r is not None]
        d_rssi = abs(sum(rssi_a) / len(rssi_a) - sum(rssi_b) / len(rssi_b)) if rssi_a and rssi_b else None
        positives.append({
            "pair": [ma, mb], "layout": la, "intervals": len(merged),
            "true_separation": round(true_sep, 2),
            "rssi_delta_db": round(d_rssi, 1) if d_rssi is not None else None,
            "finding": v["finding"],
            "detected": v["finding"] == "POSITION_ANOMALY",
        })

    tp = sum(1 for p in positives if p["detected"])
    fp = sum(1 for n in negatives if n["false_positive"])
    return {
        "multiplier": multiplier,
        "negatives": negatives,
        "positives": positives,
        "true_positives": tp,
        "positives_total": len(positives),
        "false_positives": fp,
        "negatives_total": len(negatives),
    }


def self_check(path: str, multiplier: float) -> dict:
    """The harness's own null control.

    Split a single access point's intervals into two halves and merge them back
    under one identity, exactly as the twin construction does — except both
    halves came from the SAME radio at the SAME position.

    A twin built this way is not a twin, so any detection here is the harness
    manufacturing a positive out of its own construction rather than out of the
    physics. If this fails, the twin results mean nothing and no conclusion may
    be drawn from them in either direction.
    """
    rows = load(path)
    results = []
    for mac, samples in rows.items():
        lay = layout_of(samples)
        kept = same_layout(samples, lay)
        if len(kept) < 2 * MIN_INTERVALS_FOR_VERDICT:
            continue

        # An access point that already reads as multi-source on its own is not
        # a valid null case. Some infrastructure genuinely transmits one BSSID
        # from more than one place — mesh backhaul, a repeater, an AP switching
        # antennas — and for such a radio "two spatial sources" is the correct
        # answer, so counting it as a harness fault would be blaming the
        # measurement for being right.
        alone = judge(kept, multiplier)
        if alone["finding"] == "POSITION_ANOMALY":
            results.append({
                "bssid": mac, "layout": lay, "intervals": len(kept),
                "finding": alone["finding"], "spurious": False,
                "excluded": "already multi-source when analysed alone",
            })
            continue
        # SPLIT BY PARITY, NOT BY TIME. Halving the capture chronologically
        # makes the two "sources" the early room and the late room, so any slow
        # drift over the run — someone shifting on a sofa, a door opening — is
        # handed to the detector as a position difference. Even and odd
        # intervals interleave through the whole capture instead, so both
        # halves see the same conditions and the only remaining difference is
        # noise, which is exactly what a null control should contain.
        merged = interleave(kept[0::2], kept[1::2])
        v = judge(merged, multiplier)
        results.append({
            "bssid": mac, "layout": lay, "intervals": len(merged),
            "finding": v["finding"],
            "spurious": v["finding"] == "POSITION_ANOMALY",
            "excluded": None,
        })
    valid = [r for r in results if not r["excluded"]]
    return {"cases": results, "spurious": sum(1 for r in valid if r["spurious"]),
            "total": len(valid), "excluded": sum(1 for r in results if r["excluded"])}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--capture", required=True, help="a medusa_csi stream (--save output)")
    ap.add_argument("--multiplier", type=float, default=DEFAULT_SPLIT_MULTIPLIER)
    ap.add_argument("--sweep", action="store_true",
                    help="sweep the multiplier and print both error rates at each value")
    ap.add_argument("--self-check", action="store_true",
                    help="null control: split each AP in half and merge it with itself; "
                         "any detection here means the harness manufactures positives")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.self_check:
        r = self_check(args.capture, args.multiplier)
        if args.json:
            print(json.dumps(r, indent=2)); return 0
        print(f"\nNULL CONTROL — one AP split in half and merged with itself")
        for c in r["cases"]:
            mark = ("excluded: " + c["excluded"]) if c.get("excluded") else (
                   "SPURIOUS DETECTION" if c["spurious"] else "ok")
            print(f"  {c['bssid']}  layout={c['layout']:<4} n={c['intervals']:<4} {c['finding']:<18} {mark}")
        print(f"\n  {r['spurious']}/{r['total']} spurious detections "
              f"({'harness is sound' if not r['spurious'] else 'HARNESS IS UNSOUND — twin results mean nothing'})\n")
        return 1 if r["spurious"] else 0

    if args.sweep:
        table = []
        for m in [1, 2, 3, 4, 5, 6, 8, 10, 12, 16, 20, 30]:
            r = evaluate(args.capture, float(m))
            table.append({"multiplier": m,
                          "detected": r["true_positives"], "of": r["positives_total"],
                          "false_alarms": r["false_positives"], "of_neg": r["negatives_total"]})
        if args.json:
            print(json.dumps(table, indent=2))
            return 0
        print(f"\n{'mult':>5}  {'twins detected':>16}  {'false alarms':>14}")
        for t in table:
            print(f"{t['multiplier']:>5}  {t['detected']:>7}/{t['of']:<8}  {t['false_alarms']:>6}/{t['of_neg']:<7}")
        print("\nA usable operating point needs high detection AND zero false alarms.")
        print("If no row achieves both, the detector cannot separate these cases and")
        print("that is the finding — not something to tune away.\n")
        return 0

    r = evaluate(args.capture, args.multiplier)
    if args.json:
        print(json.dumps(r, indent=2))
        return 0

    print(f"\nmultiplier {r['multiplier']}")
    print(f"\nNEGATIVE CONTROLS — real single-radio APs, none should be flagged")
    for n in r["negatives"]:
        mark = "FALSE ALARM" if n["false_positive"] else "ok"
        print(f"  {n['bssid']}  layout={n['layout']:<4} n={n['intervals']:<4} {n['finding']:<18} {mark}")
    print(f"\nPOSITIVES — two real positions under one identity")
    for p in r["positives"]:
        mark = "DETECTED" if p["detected"] else "MISSED"
        print(f"  {p['pair'][0]} + {p['pair'][1]}")
        print(f"      layout={p['layout']} n={p['intervals']} true_separation={p['true_separation']} "
              f"rssi_delta={p['rssi_delta_db']}dB  -> {p['finding']}  {mark}")
    print(f"\n  detected {r['true_positives']}/{r['positives_total']} twins, "
          f"{r['false_positives']}/{r['negatives_total']} false alarms\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
