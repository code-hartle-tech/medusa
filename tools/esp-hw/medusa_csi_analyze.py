#!/usr/bin/env python3
"""Analyse the CSI stream from medusa_csi.

The sketch measures; this decides what the measurements mean. Two questions:

MOTION — is something moving in the space?
    The channel response wobbles around one stable shape.

POSITION — is more than one physical radio claiming this BSSID?
    The channel response has two stable shapes and alternates between them.

DISTINGUISHING THE TWO IS THE WHOLE PROBLEM, and it is why this is not simply a
variance threshold. Motion and an evil twin both raise variance. What separates
them is the *shape* of the distribution:

    motion      one cluster, wide      — the profile drifts and returns
    evil twin   two clusters, tight    — the profile jumps between two
                                         signatures, each internally stable

A naive "variance is high, therefore rogue AP" detector would fire every time
somebody walked past, which would make it useless in exactly the environment it
is meant for. So this clusters first and reports the geometry.

Usage:
    python3 medusa_csi_analyze.py --port /dev/cu.usbmodemXXXX --seconds 60
    python3 medusa_csi_analyze.py --replay capture.tsv
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from collections import Counter
from statistics import median


#: Intervals a transmitter must contribute before any verdict is issued about
#: it. Below this, the honest report is that it was heard, not assessed.
MIN_INTERVALS_FOR_VERDICT = 10

#: Minimum profile length before position scoring is attempted at all.
#:
#: MEASURED. 64-subcarrier profiles come from legacy-LTF frames and carry too
#: little structure to fingerprint a position: across three constructed twins
#: and three honest APs at that layout, the scores were 2.30-3.58 and 2.36-3.64
#: respectively — completely interleaved, no signal whatsoever. Richer layouts
#: (128+, which include HT-LTF) at least separate partially. Scoring a legacy
#: profile would be generating a number from nothing.
RICH_LAYOUT_MIN = 128

#: Separation score above which two signatures are reported as two spatial
#: sources.
#:
#: VALIDATED ON BOTH SIDES AGAINST REAL HARDWARE CSI.
#:
#: Six evil twins were constructed by taking pairs of genuinely different
#: access points and relabelling both to one BSSID — real physics, real rooms,
#: real radios, with only the identity string changed, which is exactly and
#: only what an evil twin changes. See medusa_csi_twin_eval.py, whose own null
#: control (one AP merged with itself) passes 0/6.
#:
#: At this value, on the rich layouts where scoring is attempted:
#:
#:     twins detected            3/3
#:       including one pair separated by 0.1 dB of RSSI, where signal
#:       strength carries no information at all and every rival's
#:       approach is blind by construction
#:     legacy-layout twins       0/3   (not scored; see RICH_LAYOUT_MIN)
#:     honest APs flagged        1/7
#:
#: THE ONE FLAG IS NOT A FALSE POSITIVE IN THE INTERESTING SENSE. That access
#: point is independently shown to be multi-source by the null control: split
#: it in half and merge it with itself and it still separates. It genuinely
#: transmits one BSSID from more than one spatial source, which is what mesh
#: backhaul, a repeater, or an antenna-switching AP does. The measurement is
#: right; it is the inference that would be wrong.
#:
#: WHICH IS WHY THIS IS NOT A ROGUE-AP DETECTOR AND MUST NOT BE SOLD AS ONE.
#: What it detects is "more than one spatial source under one BSSID". An evil
#: twin is one cause of that. Ordinary, legitimate infrastructure is a much
#: more common cause. The finding is therefore POSITION_ANOMALY — an
#: observation needing context — and never a verdict about intent.
DEFAULT_SPLIT_MULTIPLIER = 5.0


def profile_from_hex(text: str) -> list[int]:
    """One byte per subcarrier."""
    return [int(text[i:i + 2], 16) for i in range(0, len(text) - 1, 2)]


def l1_distance(a: list[int], b: list[int]) -> float:
    """Mean absolute difference per subcarrier.

    L1 rather than Euclidean on purpose: a single wild subcarrier — which does
    happen, from interference in one part of the band — should not dominate the
    verdict the way squaring would let it.
    """
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    return sum(abs(a[i] - b[i]) for i in range(n)) / n


@dataclass
class Cluster:
    """A candidate physical transmitter position."""
    centroid: list[int]
    count: int = 1
    members: list[int] = field(default_factory=list)   # sequence numbers

    def absorb(self, profile: list[int], seq: int) -> None:
        # Running mean, so the centroid tracks slow legitimate drift (furniture
        # moved, temperature) without a second pass over the data.
        n = min(len(self.centroid), len(profile))
        self.count += 1
        for i in range(n):
            self.centroid[i] += (profile[i] - self.centroid[i]) / self.count
        self.members.append(seq)


class BssidTracker:
    """Clusters the profiles seen for one BSSID.

    Two rules, both learned from hardware rather than from reasoning:

    1. COMPARE ONLY LIKE WITH LIKE. One radio emits 64-, 128- and
       192-subcarrier profiles depending on each frame's rate and format.
       Those are different measurements, not different positions. See
       _cluster().

    2. JUDGE BY RATIO, NOT DISTANCE. No absolute distance separates "a second
       transmitter" from "one transmitter in a moving room", because a single
       radio's interval-to-interval movement is larger than the gap between
       two radios' long-run averages. What does separate them is how far apart
       two fitted clusters sit relative to how spread out each one is
       internally. See _cluster() for why the two obvious alternatives — a
       fixed distance, and a distance derived from the radio's own median
       movement — each fail, the second one precisely on the case that matters.

    `split_multiplier` is that ratio. It is calibrated on the false-positive
    side only; the true-positive side has never been measured. See
    DEFAULT_SPLIT_MULTIPLIER.
    """

    def __init__(self, bssid: str, split_multiplier: float):
        self.bssid = bssid
        self.split_multiplier = split_multiplier
        self.samples: list[tuple[int, list[int]]] = []
        self.clusters: list[Cluster] = []
        self.motion_samples: list[int] = []
        self.rssi_samples: list[int] = []
        self.frames = 0
        self.wobble = 0.0
        self.effective_threshold = 0.0
        self.separation_score = 0.0
        self.layout = 0
        self.layouts_seen: dict[int, int] = {}
        self.samples_discarded = 0

    def add(self, seq: int, profile: list[int], motion: int | None, rssi: int | None) -> None:
        self.frames += 1
        self.samples.append((seq, profile))
        if motion is not None:
            self.motion_samples.append(motion)
        if rssi is not None:
            self.rssi_samples.append(rssi)

    @staticmethod
    def _centroid(profiles: list[list[int]]) -> list[float]:
        n = min(len(p) for p in profiles)
        return [sum(p[i] for p in profiles) / len(profiles) for i in range(n)]

    @staticmethod
    def _spread(profiles: list[list[int]], centroid: list[float]) -> float:
        c = [int(v) for v in centroid]
        return sum(l1_distance(c, p) for p in profiles) / len(profiles)

    def _cluster(self) -> None:
        """Split into at most two positions, then ask whether the split earned
        its keep.

        WHY NOT A DISTANCE THRESHOLD, GREEDY OR ADAPTIVE.

        A greedy "further than X from any existing cluster starts a new one"
        needs X, and no single X works: measured on hardware, the gap between
        two radios' long-run average profiles (about 1.8) is far SMALLER than
        one radio's own interval-to-interval movement, so any X that catches a
        real twin also shatters an honest AP.

        Deriving X from the radio's own median consecutive-interval movement
        fixes that for a still channel and then fails on the case that matters
        most: an evil twin beaconing alternately with the AP it imitates makes
        *every* consecutive interval a jump between the two positions, so the
        "normal movement" estimate becomes the twin separation itself and the
        threshold rises to hide exactly what it was meant to find.

        What survives both is not a distance at all but a RATIO. Fit two
        clusters, then compare how far apart their centres are against how
        spread out each one is internally. Two positions look like two tight
        groups far apart; one position wobbling looks like a single diffuse
        group that a forced split cuts arbitrarily in half. The second case has
        a poor ratio no matter how large the absolute distances are, which is
        what makes this robust to a noisy room and to alternation alike.
        """
        if not self.samples:
            return

        # ONLY COMPARE LIKE WITH LIKE.
        #
        # The driver's CSI buffer length depends on which long training fields
        # the received frame carried, so one radio yields 64-, 128- and
        # 192-subcarrier profiles depending on the rate and format of each
        # frame. Those are different measurements, not different positions.
        #
        # This was found by hardware, not by reasoning: in a live capture the
        # only two access points reported as MULTIPLE_POSITIONS were precisely
        # the two whose profile lengths were mixed, while every AP with a
        # uniform length classified correctly. Because l1_distance compares
        # over the shorter of its two inputs, mixed lengths quietly compared
        # unlike quantities and produced a clean, confident, wrong answer —
        # and it would have fired on any busy AP, which is to say all of them.
        lengths = Counter(len(p) for _, p in self.samples)
        self.layout, kept = lengths.most_common(1)[0]
        self.layouts_seen = dict(lengths)
        self.samples_discarded = len(self.samples) - kept

        usable = [(s, p) for s, p in self.samples if len(p) == self.layout]
        profiles = [p for _, p in usable]
        seqs = [s for s, _ in usable]

        overall = self._centroid(profiles)
        self.wobble = self._spread(profiles, overall)

        if len(profiles) < 4:
            self.clusters = [Cluster(centroid=list(overall), count=len(profiles), members=seqs)]
            return

        # Seed the two candidate positions far apart: the sample furthest from
        # the overall centre, then the sample furthest from that one.
        oc = [int(v) for v in overall]
        a_idx = max(range(len(profiles)), key=lambda i: l1_distance(oc, profiles[i]))
        b_idx = max(range(len(profiles)), key=lambda i: l1_distance(profiles[a_idx], profiles[i]))
        ca = [float(v) for v in profiles[a_idx]]
        cb = [float(v) for v in profiles[b_idx]]

        group_a: list[int] = []
        group_b: list[int] = []
        for _ in range(12):
            group_a, group_b = [], []
            ia, ib = [int(v) for v in ca], [int(v) for v in cb]
            for i, p in enumerate(profiles):
                (group_a if l1_distance(ia, p) <= l1_distance(ib, p) else group_b).append(i)
            if not group_a or not group_b:
                break
            new_a = self._centroid([profiles[i] for i in group_a])
            new_b = self._centroid([profiles[i] for i in group_b])
            if new_a == ca and new_b == cb:
                break
            ca, cb = new_a, new_b

        if not group_a or not group_b:
            self.clusters = [Cluster(centroid=list(overall), count=len(profiles), members=seqs)]
            return

        pa = [profiles[i] for i in group_a]
        pb = [profiles[i] for i in group_b]

        # SCORE ALONG THE SEPARATING DIRECTION, NOT IN THE FULL SPACE.
        #
        # Comparing cluster separation against full-space spread was measured
        # against real data and does not discriminate at all: sweeping its
        # threshold moved twin detection and false alarms together, 6/6 twins
        # at 6/7 false alarms down to 0/6 at 0/7, with no operating point in
        # between. The statistic was useless, not merely mistuned.
        #
        # The reason is geometric. A profile has dozens of subcarriers, and
        # per-interval noise is spread across all of them, while the shift
        # between two positions lies along ONE direction — the whole profile
        # moves together, because it is the same room seen from somewhere else.
        # Measuring distance in the full space adds every noisy dimension to
        # the denominator and buries a real signal that only ever lived in one.
        #
        # So: take the direction between the two candidate centres, project
        # every profile onto it, and judge bimodality in that one dimension.
        # Noise orthogonal to the shift — which is most of it — drops out.
        direction = [ca[i] - cb[i] for i in range(min(len(ca), len(cb)))]
        norm = sum(d * d for d in direction) ** 0.5
        if norm < 1e-9:
            self.clusters = [Cluster(centroid=list(overall), count=len(profiles), members=seqs)]
            return

        def project(p):
            return sum(p[i] * direction[i] for i in range(len(direction))) / norm

        proj_a = [project(p) for p in pa]
        proj_b = [project(p) for p in pb]
        mean_a = sum(proj_a) / len(proj_a)
        mean_b = sum(proj_b) / len(proj_b)

        def variance(vals, m):
            return sum((v - m) ** 2 for v in vals) / max(1, len(vals) - 1)

        # Pooled within-group scatter along that same direction. This is an
        # effect size: how far apart the two groups sit measured in units of
        # their own spread.
        pooled = ((variance(proj_a, mean_a) * (len(proj_a) - 1)
                   + variance(proj_b, mean_b) * (len(proj_b) - 1))
                  / max(1, len(proj_a) + len(proj_b) - 2)) ** 0.5
        pooled = max(pooled, 1e-6)

        sep = abs(mean_a - mean_b)
        self.separation_score = sep / pooled
        self.effective_threshold = self.split_multiplier

        if self.separation_score > self.split_multiplier:
            self.clusters = [
                Cluster(centroid=list(ca), count=len(pa), members=[seqs[i] for i in group_a]),
                Cluster(centroid=list(cb), count=len(pb), members=[seqs[i] for i in group_b]),
            ]
        else:
            self.clusters = [Cluster(centroid=list(overall), count=len(profiles), members=seqs)]

    def verdict(self) -> dict:
        self._cluster()
        # Only clusters with real support count. A single stray reading is
        # noise, not a second access point, and treating it as one is how a
        # detector earns a reputation for crying wolf.
        min_support = max(3, self.frames // 20)
        solid = [c for c in self.clusters if c.count >= min_support]

        motion_mean = (sum(self.motion_samples) / len(self.motion_samples)) if self.motion_samples else 0
        motion_peak = max(self.motion_samples) if self.motion_samples else 0

        separation = 0.0
        if len(solid) >= 2:
            solid.sort(key=lambda c: c.count, reverse=True)
            a = [int(v) for v in solid[0].centroid]
            b = [int(v) for v in solid[1].centroid]
            separation = l1_distance(a, b)

        # A verdict needs enough intervals to be a verdict. A transmitter heard
        # once produced exactly one profile, which is trivially "one stable
        # signature" and trivially has some deviation from a baseline built out
        # of itself — so the naive path labelled it MOTION with total
        # confidence. Passing traffic from a distant AP would then read as
        # somebody walking around.
        #
        # This is the same failure as the firmware reporting motion from two
        # packets: the arithmetic is fine, the sample is not, and only the
        # label survives into whatever someone reads later.
        # POSITION AND MOTION ARE SEPARATE QUESTIONS WITH SEPARATE EVIDENCE.
        #
        # Position is decided from profiles; motion is decided from the
        # firmware's per-interval motion figure, which it withholds when an
        # interval held too few frames to average. A quiet access point
        # therefore yields plenty of position evidence and no motion evidence.
        #
        # An earlier version required both and returned INSUFFICIENT_DATA
        # whenever motion was missing. That silently disabled evil-twin
        # detection on exactly the networks where it matters most — quiet ones
        # — and it showed up as 0/6 on the twin evaluation before it was
        # visible anywhere else.
        if self.frames < MIN_INTERVALS_FOR_VERDICT:
            why = (f"only {self.frames} interval(s); at least "
                   f"{MIN_INTERVALS_FOR_VERDICT} are needed. Watch it longer.")
            return {
                "bssid": self.bssid,
                "finding": "INSUFFICIENT_DATA",
                "detail": f"{why} Heard, not assessed.",
                "intervals": self.frames,
                "clusters_total": len(self.clusters),
                "clusters_supported": len(solid),
                "cluster_separation": 0.0,
                "motion_mean": round(motion_mean, 1),
                "motion_peak": motion_peak,
                "rssi_mean": round(sum(self.rssi_samples) / len(self.rssi_samples), 1) if self.rssi_samples else None,
            }

        if len(solid) >= 2 and self.layout >= RICH_LAYOUT_MIN:
            # DELIBERATELY NOT CALLED A DETECTION. Validation against real
            # hardware found the score distributions for honest APs and for
            # two-position mixtures OVERLAP — one ordinary access point scored
            # 23.4 while every constructed twin scored between 5.5 and 9.6.
            # There is no threshold that separates them, so a confident
            # "rogue AP" verdict here would be a guess wearing a label.
            #
            # What the number IS good for is change over time: an AP whose
            # score has sat at 4 for a week and reads 20 today has changed in a
            # way worth investigating, even though 20 in isolation means little.
            finding = "POSITION_ANOMALY"
            detail = (
                f"two channel signatures for one BSSID, separation score "
                f"{self.separation_score:.1f} (centroids {separation:.1f} apart). "
                f"NOT a rogue-AP finding: validation showed honest APs scoring as "
                f"high as 23.4 against twins at 5.5-9.6, so this cannot be read "
                f"absolutely. Compare it against this BSSID's own history — a "
                f"change is the signal, the level is not."
            )
        elif not self.motion_samples:
            # One position, and no basis to say anything about movement. Named
            # distinctly so it cannot be mistaken for a quiet room: the room
            # was never measured.
            finding = "STABLE_POSITION"
            detail = (
                f"one position, motion not assessed. {self.frames} intervals held "
                f"enough frames to fingerprint but none held enough to average a "
                f"motion figure — this transmitter is too quiet to sense movement "
                f"against. It needs traffic, not more time."
            )
        elif motion_peak > 150:
            finding = "MOTION"
            detail = (
                f"single stable signature, peak deviation {motion_peak}. "
                f"The channel moved and returned — consistent with movement in "
                f"the space rather than a second transmitter."
            )
        else:
            finding = "STABLE"
            detail = f"single stable signature, peak deviation {motion_peak}."

        return {
            "bssid": self.bssid,
            "finding": finding,
            "detail": detail,
            "intervals": self.frames,
            "clusters_total": len(self.clusters),
            "clusters_supported": len(solid),
            "cluster_separation": round(separation, 2),
            "wobble": round(self.wobble, 2),
            "separation_score": round(self.separation_score, 2),
            "layout": self.layout,
            "layouts_seen": self.layouts_seen,
            "samples_discarded": self.samples_discarded,
            "threshold": round(self.effective_threshold, 2),
            "motion_mean": round(motion_mean, 1),
            "motion_peak": motion_peak,
            "rssi_mean": round(sum(self.rssi_samples) / len(self.rssi_samples), 1) if self.rssi_samples else None,
        }


def parse_line(line: str):
    """Return (seq, mac, rssi, nsub, frames, motion, profile) or None."""
    if not line.startswith("CSI\t"):
        return None
    parts = line.rstrip("\n").split("\t")
    if len(parts) < 8:
        return None
    _, seq, mac, rssi, nsub, frames, motion, prof = parts[:8]
    if mac == "-" or not prof or prof == "-":
        return None
    try:
        return (
            int(seq),
            mac,
            int(rssi) if rssi != "-" else None,
            int(nsub),
            int(frames),
            int(motion) if motion != "-" else None,
            profile_from_hex(prof),
        )
    except ValueError:
        return None


def run(lines, split_threshold: float, echo: bool) -> dict:
    trackers: dict[str, BssidTracker] = {}
    info: list[str] = []
    errors: list[str] = []

    for line in lines:
        if echo:
            sys.stderr.write(line if line.endswith("\n") else line + "\n")
        if line.startswith("CSIINFO\t"):
            info.append(line.strip().split("\t", 1)[1])
            continue
        if line.startswith("CSIERR\t"):
            errors.append(line.strip().split("\t", 1)[1])
            continue
        parsed = parse_line(line)
        if not parsed:
            continue
        seq, mac, rssi, _nsub, _frames, motion, profile = parsed
        if mac not in trackers:
            trackers[mac] = BssidTracker(mac, split_threshold)
        trackers[mac].add(seq, profile, motion, rssi)

    return {
        "info": info,
        "errors": errors,
        "transmitters": [t.verdict() for t in trackers.values()],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Analyse a medusa_csi stream.")
    ap.add_argument("--port", help="serial port to read live")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--replay", help="read a captured stream from a file instead")
    ap.add_argument("--split-threshold", type=float, default=DEFAULT_SPLIT_MULTIPLIER,
                    help=f"how many times a radio's own median wobble a profile must "
                         f"exceed to count as a different position "
                         f"(default {DEFAULT_SPLIT_MULTIPLIER})")
    ap.add_argument("--json", action="store_true", help="emit JSON only")
    ap.add_argument("--save", help="also write the raw stream here")
    args = ap.parse_args()

    if not args.port and not args.replay:
        ap.error("one of --port or --replay is required")

    saved = open(args.save, "w") if args.save else None

    def gen():
        if args.replay:
            with open(args.replay) as fh:
                yield from fh
            return
        try:
            import serial  # type: ignore
        except ImportError:
            print("pyserial is required for --port", file=sys.stderr)
            raise SystemExit(2)
        with serial.Serial(args.port, args.baud, timeout=1) as ser:
            deadline = time.time() + args.seconds
            while time.time() < deadline:
                raw = ser.readline()
                if not raw:
                    continue
                line = raw.decode("utf-8", "replace")
                if saved:
                    saved.write(line)
                    saved.flush()
                yield line

    result = run(gen(), args.split_threshold, echo=not args.json)
    if saved:
        saved.close()

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    print()
    for note in result["info"]:
        print(f"  info   {note}")
    for err in result["errors"]:
        print(f"  ERROR  {err}")
    if not result["transmitters"]:
        print("  no CSI observed — nothing to analyse")
        # An empty result is not a pass. Saying so explicitly keeps a quiet
        # channel from reading as a clean bill of health.
        return 1
    print()
    for t in result["transmitters"]:
        print(f"  {t['bssid']}  {t['finding']}")
        print(f"      {t['detail']}")
        print(f"      intervals={t['intervals']} clusters={t['clusters_supported']}"
              f"/{t['clusters_total']} motion(mean/peak)={t['motion_mean']}/{t['motion_peak']}"
              f" rssi={t['rssi_mean']}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
