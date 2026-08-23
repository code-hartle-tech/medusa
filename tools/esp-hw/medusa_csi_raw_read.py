#!/usr/bin/env python3
"""Decode the raw CSI stream from medusa_csi_raw.

The sketch exports measurements and draws no conclusions. This turns them back
into complex channel estimates and — just as importantly — reports whether the
series is fit to transform at all.

WHY THE SAMPLING REPORT MATTERS MORE THAN THE DECODE.

Everything people want from raw CSI is spectral: breathing at 0.1-0.5 Hz,
motion, periodic interference. Every spectral method assumes evenly spaced
samples, and Wi-Fi frames do not arrive evenly. A run can decode perfectly and
still be worthless because:

  - the mean rate is below twice the frequency of interest, so the thing being
    looked for aliases to a different frequency and is reported confidently as
    something it is not;
  - the interval jitters, which smears a sharp peak into a hill and shifts it;
  - the firmware dropped frames, so the series has holes and every sample after
    a hole sits at the wrong time.

A tool that decoded cleanly and stayed quiet about all three would let someone
publish a breathing rate derived from aliased noise. So `sampling_report`
refuses to be optional, and `usable_for` states the highest frequency the run
can honestly support.
"""
from __future__ import annotations

import argparse
import base64
import json
import statistics
import sys
from dataclasses import dataclass, field


@dataclass
class RawRecord:
    seq: int
    us: int
    mac: str
    rssi: int
    rate: int
    sig_mode: int
    channel: int
    nsub: int
    iq: list[tuple[int, int]] = field(default_factory=list)   # (imag, real)

    @property
    def amplitude(self) -> list[float]:
        return [(im * im + re * re) ** 0.5 for im, re in self.iq]

    @property
    def phase(self) -> list[float]:
        """Radians, per subcarrier.

        This is the quantity medusa_csi throws away and the reason this path
        exists: a chest wall moving 4-12 mm rotates a reflected component by
        0.40-1.21 radians at 2.4 GHz. It is raw and wrapped — unwrapping and
        removing the carrier-frequency and sampling-time offsets is the
        consumer's job, and doing it here silently would hide a step that
        changes the answer.
        """
        from math import atan2
        return [atan2(im, re) for im, re in self.iq]


def decode_iq(payload: str) -> list[tuple[int, int]]:
    """base64 -> signed [imag, real] pairs.

    The driver's bytes are SIGNED. Reading them as unsigned turns every
    negative sample into a large positive one, which does not look like an
    error — it looks like a noisy channel.
    """
    raw = base64.b64decode(payload)
    vals = [v - 256 if v > 127 else v for v in raw]
    return [(vals[i], vals[i + 1]) for i in range(0, len(vals) - 1, 2)]


def parse_raw_line(line: str) -> RawRecord | None:
    if not line.startswith("CSIR\t"):
        return None
    p = line.rstrip("\n").split("\t")
    if len(p) < 10:
        return None
    try:
        rec = RawRecord(seq=int(p[1]), us=int(p[2]), mac=p[3], rssi=int(p[4]),
                        rate=int(p[5]), sig_mode=int(p[6]), channel=int(p[7]),
                        nsub=int(p[8]))
        rec.iq = decode_iq(p[9])
    except (ValueError, Exception):  # noqa: B014 - base64 raises binascii.Error
        return None
    return rec


def load(lines) -> tuple[list[RawRecord], dict]:
    records, info, drops = [], [], []
    for line in lines:
        if line.startswith("CSIRINFO\t"):
            info.append(line.strip().split("\t", 1)[1])
            continue
        if line.startswith("CSIRDROP\t"):
            p = line.strip().split("\t")
            if len(p) >= 3:
                drops.append({"dropped": int(p[1]), "received": int(p[2])})
            continue
        rec = parse_raw_line(line)
        if rec:
            records.append(rec)
    return records, {"info": info, "drops": drops[-1] if drops else None}


def sampling_report(records: list[RawRecord]) -> dict:
    """Describe the time base, and say what it can and cannot support."""
    if len(records) < 3:
        return {"usable": False, "reason": f"only {len(records)} records"}

    gaps_us = [b.us - a.us for a, b in zip(records, records[1:])]
    gaps_us = [g for g in gaps_us if g > 0]
    if not gaps_us:
        return {"usable": False, "reason": "no positive time deltas — check the timebase"}

    mean_gap = statistics.mean(gaps_us)
    median_gap = statistics.median(gaps_us)
    rate_hz = 1e6 / mean_gap
    jitter = statistics.pstdev(gaps_us) / mean_gap if mean_gap else 0.0
    span_s = (records[-1].us - records[0].us) / 1e6

    # Nyquist against the mean rate, with the usual practical derating: a peak
    # estimated from a series sampled at barely twice its frequency is not
    # something to quote.
    nyquist = rate_hz / 2.0
    comfortable = rate_hz / 5.0

    # Frequency resolution is set by the observation span, not the rate. Sixty
    # seconds gives 0.017 Hz, which is one breath per minute.
    resolution_hz = (1.0 / span_s) if span_s > 0 else float("inf")

    return {
        "usable": True,
        "records": len(records),
        "span_s": round(span_s, 2),
        "rate_hz": round(rate_hz, 2),
        "median_gap_ms": round(median_gap / 1000.0, 2),
        "jitter_fraction": round(jitter, 3),
        "nyquist_hz": round(nyquist, 3),
        "comfortable_hz": round(comfortable, 3),
        "resolution_hz": round(resolution_hz, 4),
        "resolution_bpm": round(resolution_hz * 60, 2),
        "breathing_supported": comfortable >= 0.5,
        "max_bpm_supported": round(comfortable * 60, 1),
    }


def layout_groups(records: list[RawRecord]) -> dict[int, list[RawRecord]]:
    """Split by subcarrier count.

    One radio emits 64-, 128- and 192-subcarrier records depending on each
    frame's format. Those are different measurements of different things, and
    stacking them into one matrix silently compares unlike quantities — the
    same trap that produced false rogue-AP findings in the summarised pipeline.
    """
    out: dict[int, list[RawRecord]] = {}
    for r in records:
        out.setdefault(len(r.iq), []).append(r)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Decode and characterise a raw CSI stream.")
    ap.add_argument("--capture", required=True, help="a medusa_csi_raw stream")
    ap.add_argument("--mac", help="restrict to one transmitter")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    with open(args.capture) as fh:
        records, meta = load(fh)
    if args.mac:
        records = [r for r in records if r.mac.upper() == args.mac.upper()]

    groups = layout_groups(records)
    per_layout = {}
    for nsub, recs in sorted(groups.items()):
        by_mac: dict[str, int] = {}
        for r in recs:
            by_mac[r.mac] = by_mac.get(r.mac, 0) + 1
        per_layout[nsub] = {"records": len(recs), "sampling": sampling_report(recs),
                            "transmitters": by_mac}

    result = {"total_records": len(records), "info": meta["info"],
              "drops": meta["drops"], "layouts": per_layout}

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    print()
    for note in meta["info"]:
        print(f"  info   {note}")
    if meta["drops"] and meta["drops"]["dropped"]:
        d = meta["drops"]
        pct = 100.0 * d["dropped"] / max(1, d["received"])
        print(f"  WARN   {d['dropped']} of {d['received']} frames dropped ({pct:.1f}%) — "
              f"the series has holes and is not evenly sampled")
    print(f"\n  {len(records)} records")
    for nsub, g in per_layout.items():
        s = g["sampling"]
        print(f"\n  layout {nsub} subcarriers — {g['records']} records")
        if not s.get("usable"):
            print(f"      unusable: {s.get('reason')}")
            continue
        print(f"      span {s['span_s']}s  rate {s['rate_hz']} Hz  "
              f"median gap {s['median_gap_ms']} ms  jitter {s['jitter_fraction']}")
        print(f"      resolution {s['resolution_hz']} Hz ({s['resolution_bpm']} bpm)")
        verdict = ("supports breathing (to %.0f bpm)" % s["max_bpm_supported"]
                   if s["breathing_supported"]
                   else "TOO SLOW for breathing — would alias; needs more traffic")
        print(f"      {verdict}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
