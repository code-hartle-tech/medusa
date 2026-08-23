#!/usr/bin/env python3
"""Respiration rate from raw CSI.

Reads a medusa_csi_raw capture and reports a breathing rate, or refuses.

THE PHYSICS, BRIEFLY. At 2.4 GHz the wavelength is 12.5 cm. A chest wall moves
4-12 mm during tidal breathing, and a signal reflecting off it travels that
twice, so the path length changes by 8-24 mm — 0.40 to 1.21 radians of phase
rotation. The received CSI is the sum of all multipath components, one of which
bounces off the chest, so the sum oscillates at the breathing frequency.

Detectability comes from the rhythm being SUSTAINED, not from the displacement
being large. Noise spreads across the spectrum; a periodic signal concentrates
into one bin. Sixty seconds of observation gives 0.017 Hz resolution — one
breath per minute — and that integration is what pulls a millimetre-scale
movement out of the noise.

WHY LOMB-SCARGLE AND NOT AN FFT.

Wi-Fi frames do not arrive on a clock. Measured jitter on this hardware runs to
0.4 of the mean interval and worse. The usual response is to interpolate onto a
uniform grid and run an FFT, but interpolation invents samples that were never
measured and quietly biases the spectrum — it suppresses power near Nyquist and
can manufacture structure that was not there.

The Lomb-Scargle periodogram is built for unevenly sampled series. It fits
sinusoids at each trial frequency using the ACTUAL timestamps, so nothing is
invented. It also has a known null distribution, which means this tool can
report a false-alarm probability rather than an adjective.

WHY CONSENSUS ACROSS SUBCARRIERS RATHER THAN ONE COMBINED SPECTRUM.

Breathing perturbs many subcarriers coherently; noise does not. Summing all
subcarriers into one spectrum would look more sensitive, but subcarriers are
correlated, so the combined significance would be overstated in a way that is
hard to bound. Instead each subcarrier is tested on its own and the tool
reports HOW MANY independently peak at the same frequency. Twenty subcarriers
agreeing to within a bin is strong evidence and is easy to reason about;
one subcarrier with a big peak is not.

WHAT IT WILL NOT DO.

- It refuses when the capture cannot support the band, rather than returning a
  number derived from aliased noise.
- It does not report heart rate. That displacement is 0.2-0.5 mm, some 20-30x
  weaker than breathing, and sits underneath the breathing harmonics. Claiming
  it from this data would be dishonest.
- A result here is "a periodic signal at N breaths per minute is present", not
  "a person is present". A fan, a pump, or a swaying plant on a 0.3 Hz cycle
  produces the same evidence.

Usage:
    python3 medusa_csi_vitals.py --capture raw.tsv
    python3 medusa_csi_vitals.py --capture raw.tsv --mac AA:BB:.. --json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass

from medusa_csi_raw_read import layout_groups, load, sampling_report

#: Breathing band. 6-36 breaths per minute covers resting adult through
#: exertion, with margin on both sides. Deliberately excludes anything below
#: 0.1 Hz, which is where slow environmental drift lives and would otherwise
#: dominate every spectrum.
DEFAULT_BAND_HZ = (0.1, 0.6)

#: How finely to sample the frequency axis, as a multiple of the natural
#: resolution 1/T. Oversampling does not add information — resolution is set by
#: the observation span — but it stops a true peak falling between trial
#: frequencies and being underestimated.
OVERSAMPLE = 8

#: A subcarrier must beat this false-alarm probability to be counted as having
#: found something. 1% per subcarrier, with the consensus requirement below
#: doing the real work of controlling false positives.
DEFAULT_MAX_FAP = 0.01

#: Subcarriers whose peaks must agree before a rate is reported. Breathing
#: moves many subcarriers together; noise does not.
DEFAULT_MIN_AGREE = 8

#: Agreement tolerance in breaths per minute. Two subcarriers pointing at 15.1
#: and 15.4 are agreeing.
AGREE_TOLERANCE_BPM = 1.5


@dataclass
class SubcarrierPeak:
    index: int
    freq_hz: float
    power: float
    fap: float


def lomb_scargle(t: list[float], y: list[float], freqs: list[float]) -> list[float]:
    """Classical normalised Lomb-Scargle periodogram.

    Returns power per trial frequency, normalised by the series variance so the
    null distribution is exponential with unit mean — which is what makes the
    false-alarm probability below meaningful.
    """
    n = len(y)
    if n < 4:
        return [0.0] * len(freqs)
    mean = sum(y) / n
    var = sum((v - mean) ** 2 for v in y) / (n - 1)
    if var <= 0:
        return [0.0] * len(freqs)

    dy = [v - mean for v in y]
    out = []
    for f in freqs:
        w = 2.0 * math.pi * f
        # The time offset tau makes the sine and cosine bases orthogonal on
        # THIS set of sample times. It is the step that makes the method valid
        # for uneven sampling rather than merely tolerant of it.
        s2 = sum(math.sin(2.0 * w * ti) for ti in t)
        c2 = sum(math.cos(2.0 * w * ti) for ti in t)
        tau = math.atan2(s2, c2) / (2.0 * w) if w else 0.0

        cc = ss = cy = sy = 0.0
        for ti, yi in zip(t, dy):
            a = w * (ti - tau)
            c, s = math.cos(a), math.sin(a)
            cc += c * c
            ss += s * s
            cy += yi * c
            sy += yi * s
        term_c = (cy * cy / cc) if cc > 1e-12 else 0.0
        term_s = (sy * sy / ss) if ss > 1e-12 else 0.0
        out.append(0.5 * (term_c + term_s) / var)
    return out


def false_alarm_probability(power: float, n_independent: float) -> float:
    """Probability of a peak this large arising from noise alone.

    Under the null of independent Gaussian noise a normalised Lomb-Scargle
    power is exponentially distributed, so a single trial exceeds P with
    probability exp(-P). Scanning many frequencies gives many chances, hence
    the correction for the number of INDEPENDENT trial frequencies — which is
    set by the observation span and bandwidth, not by how finely the axis was
    sampled. Using the oversampled count instead would overstate the correction
    and hide real signals.
    """
    if power <= 0:
        return 1.0
    single = math.exp(-power)
    if single * n_independent < 1e-9:
        return single * n_independent          # avoids catastrophic cancellation
    return 1.0 - (1.0 - single) ** n_independent


def detrend(t: list[float], y: list[float], window_s: float) -> list[float]:
    """Remove everything slower than the band by subtracting a moving mean.

    Lomb-Scargle subtracts the MEAN, which removes a constant, not a trend, so
    a wandering baseline reaches the periodogram intact. A room's slow wander
    has power at every low frequency and orders of magnitude more of it than a
    chest, and that power leaks up past the band edge.

    Measured on a live capture of someone breathing at a counted 12 bpm: the
    three strongest peaks were drift at 5.0, 6.2 and 7.2 bpm, with a genuine
    11.4 bpm respiration peak fourth and invisible to the search.

    The window comes from the band rather than being fixed, so widening the
    search widens what counts as slow. It is windowed in TIME, not in sample
    count, because at 40 Hz with jitter a fixed number of samples spans
    different durations in different places and would high-pass unevenly.
    """
    n = len(y)
    if n < 3 or window_s <= 0:
        return list(y)
    out = [0.0] * n
    lo = hi = 0
    run = 0.0
    half = window_s / 2.0
    for i in range(n):
        while hi < n and t[hi] <= t[i] + half:
            run += y[hi]
            hi += 1
        while t[lo] < t[i] - half:
            run -= y[lo]
            lo += 1
        count = hi - lo
        out[i] = y[i] - (run / count if count else y[i])
    return out


def series_for(records, index: int, signal: str) -> tuple[list[float], list[float]]:
    """Time base and one subcarrier's series.

    `amplitude` is the default and the safe choice on a single antenna. Raw CSI
    phase carries a large random per-packet offset from carrier and sampling
    frequency error; absolute phase is therefore meaningless and would produce
    a spectrum full of nothing. `phase-diff` removes the common part of that
    offset by differencing against a neighbouring subcarrier, which is the
    standard single-antenna workaround.
    """
    t, y = [], []
    for r in records:
        if index >= len(r.iq):
            continue
        t.append(r.us / 1e6)
        if signal == "phase-diff":
            j = index + 1 if index + 1 < len(r.iq) else index - 1
            im1, re1 = r.iq[index]
            im2, re2 = r.iq[j]
            d = math.atan2(im1, re1) - math.atan2(im2, re2)
            y.append(math.atan2(math.sin(d), math.cos(d)))   # wrap to (-pi, pi]
        else:
            im, re = r.iq[index]
            y.append(math.sqrt(im * im + re * re))
    if t:
        t0 = t[0]
        t = [ti - t0 for ti in t]
    return t, y


def analyse(records, band=DEFAULT_BAND_HZ, signal="amplitude",
            max_fap=DEFAULT_MAX_FAP, min_agree=DEFAULT_MIN_AGREE,
            _edge_recheck=True) -> dict:
    report = sampling_report(records)
    if not report.get("usable"):
        return {"result": "REFUSED", "reason": report.get("reason"), "sampling": report}

    # Refuse rather than alias. A rate produced from a series that cannot carry
    # the band is not a weak result, it is a wrong one, and it looks identical
    # to a good one.
    if report["comfortable_hz"] < band[1]:
        return {
            "result": "REFUSED",
            "reason": (f"sampling supports up to {report['max_bpm_supported']} bpm; "
                       f"the requested band needs {band[1] * 60:.0f} bpm. "
                       f"Capture against a busier transmitter, not for longer."),
            "sampling": report,
        }

    span = report["span_s"]
    df = 1.0 / (span * OVERSAMPLE)
    freqs = []
    f = band[0]
    while f <= band[1]:
        freqs.append(f)
        f += df
    # Independent trials are set by span and bandwidth, not by the oversampled grid.
    n_independent = max(1.0, span * (band[1] - band[0]))

    nsub = min(len(r.iq) for r in records)
    peaks: list[SubcarrierPeak] = []
    for i in range(nsub):
        t, y = series_for(records, i, signal)
        if len(t) < 16:
            continue
        # Corner AT the band floor, applied twice: one moving mean has a soft
        # rolloff and drift has far more power than the signal beneath it.
        w = 1.0 / band[0]
        y = detrend(t, y, w)
        y = detrend(t, y, w)
        power = lomb_scargle(t, y, freqs)
        best = max(range(len(power)), key=lambda k: power[k])
        fap = false_alarm_probability(power[best], n_independent)
        peaks.append(SubcarrierPeak(i, freqs[best], power[best], fap))

    significant = [p for p in peaks if p.fap <= max_fap]
    if not significant:
        return {"result": "NO_PERIODIC_SIGNAL", "sampling": report,
                "subcarriers_tested": len(peaks), "subcarriers_significant": 0,
                "detail": "no subcarrier showed a peak beyond chance in the band"}

    # Consensus: the frequency the most subcarriers independently agree on.
    best_cluster, best_members = None, []
    for cand in significant:
        members = [p for p in significant
                   if abs(p.freq_hz - cand.freq_hz) * 60 <= AGREE_TOLERANCE_BPM]
        if len(members) > len(best_members):
            best_cluster, best_members = cand, members

    bpm = (sum(p.freq_hz for p in best_members) / len(best_members)) * 60
    agree = len(best_members)
    result = "BREATHING" if agree >= min_agree else "WEAK"

    # A peak pinned against a band edge is usually the shoulder of something
    # OUTSIDE the band rather than a feature inside it — slow environmental
    # drift leaking up past the lower edge is the common case. The estimator
    # cannot see beyond where it was told to look, so it says so instead of
    # presenting an edge value as if it were a located peak.
    edge_tol_bpm = max(AGREE_TOLERANCE_BPM, report["resolution_bpm"])
    at_edge = None
    if bpm - band[0] * 60 <= edge_tol_bpm:
        at_edge = "lower"
    elif band[1] * 60 - bpm <= edge_tol_bpm:
        at_edge = "upper"
    # AN EDGE PEAK IS SUSPECT BECAUSE IT IS AT THE EDGE, NOT BECAUSE IT IS WEAK.
    #
    # A first version let a strong consensus override the edge warning, and a
    # real capture then reported BREATHING at 7.0 bpm with 90 of 128
    # subcarriers agreeing and a false-alarm probability of zero. Widening the
    # band moved that peak to 3.6 bpm — it had been pinned against the 6 bpm
    # edge the whole time, and was slow environmental drift with a ~17 second
    # period, not respiration. Statistical strength says the oscillation is
    # real; it says nothing about whether it is INSIDE the band you asked for.
    #
    # So the tool now runs the check itself rather than advising the reader to.
    # Widen the offending edge and look again: a peak that moves with the edge
    # was never in the band, and a peak that stays put is genuine.
    if at_edge and _edge_recheck:
        widened = ((band[0] / 3.0, band[1]) if at_edge == "lower"
                   else (band[0], band[1] * 2.0))
        probe = analyse(records, band=widened, signal=signal, max_fap=max_fap,
                        min_agree=min_agree, _edge_recheck=False)
        moved = probe.get("bpm")
        if moved is not None and abs(moved - bpm) > max(AGREE_TOLERANCE_BPM,
                                                       report["resolution_bpm"]):
            return {
                "result": "EDGE_PEAK",
                "bpm": round(bpm, 2),
                "moved_to_bpm": round(moved, 2),
                "subcarriers_agreeing": agree,
                "subcarriers_significant": len(significant),
                "subcarriers_tested": len(peaks),
                "resolution_bpm": report["resolution_bpm"],
                "signal": signal,
                "at_band_edge": at_edge,
                "sampling": report,
                "detail": (
                    f"peak sat on the {at_edge} edge at {bpm:.1f} bpm and moved to "
                    f"{moved:.1f} bpm when the band was widened — so it was never "
                    f"inside the band. This is "
                    f"{'slow drift' if at_edge == 'lower' else 'a faster process'}, "
                    f"not a rate, however many subcarriers agreed on it."
                ),
            }
    return {
        "result": result,
        "bpm": round(bpm, 2),
        "subcarriers_agreeing": agree,
        "subcarriers_significant": len(significant),
        "subcarriers_tested": len(peaks),
        "median_fap": round(sorted(p.fap for p in best_members)[len(best_members) // 2], 6),
        "resolution_bpm": report["resolution_bpm"],
        "signal": signal,
        "at_band_edge": at_edge,
        "sampling": report,
        "detail": (
            f"{agree} subcarriers independently peak at {bpm:.1f} bpm"
            if result == "BREATHING" else
            f"peak sits on the {at_edge} edge of the band at {bpm:.1f} bpm, so it is "
            f"probably the shoulder of something outside it "
            f"({'slow drift' if at_edge == 'lower' else 'a faster process'}) "
            f"rather than a rate. Widen the band to look, do not report this."
            if result == "EDGE_PEAK" else
            f"only {agree} subcarriers agree (need {min_agree}); "
            f"suggestive at {bpm:.1f} bpm but not enough evidence"
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Respiration rate from a raw CSI capture.")
    ap.add_argument("--capture", required=True)
    ap.add_argument("--mac", help="restrict to one transmitter (recommended)")
    ap.add_argument("--layout", type=int, help="subcarrier count to analyse")
    ap.add_argument("--signal", choices=["amplitude", "phase-diff"], default="amplitude")
    ap.add_argument("--min-bpm", type=float, default=DEFAULT_BAND_HZ[0] * 60)
    ap.add_argument("--max-bpm", type=float, default=DEFAULT_BAND_HZ[1] * 60)
    ap.add_argument("--min-agree", type=int, default=DEFAULT_MIN_AGREE)
    ap.add_argument("--expect", type=float,
                    help="ground-truth bpm; prints the error instead of just the estimate")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    with open(args.capture) as fh:
        records, meta = load(fh)
    if args.mac:
        records = [r for r in records if r.mac.upper() == args.mac.upper()]
    groups = layout_groups(records)
    if not groups:
        print("no records", file=sys.stderr)
        return 1
    layout = args.layout or max(groups, key=lambda k: len(groups[k]))
    recs = groups[layout]

    band = (args.min_bpm / 60.0, args.max_bpm / 60.0)
    out = analyse(recs, band=band, signal=args.signal, min_agree=args.min_agree)
    out["layout"] = layout
    out["records"] = len(recs)
    if meta.get("drops") and meta["drops"].get("dropped"):
        out["drops"] = meta["drops"]
    if args.expect is not None and out.get("bpm") is not None:
        out["expected_bpm"] = args.expect
        out["error_bpm"] = round(out["bpm"] - args.expect, 2)

    if args.json:
        print(json.dumps(out, indent=2))
        return 0

    s = out["sampling"]
    print(f"\n  layout {layout}: {out['records']} records, "
          f"{s.get('rate_hz')} Hz, span {s.get('span_s')}s, "
          f"resolution {s.get('resolution_bpm')} bpm")
    if out["result"] == "REFUSED":
        print(f"\n  REFUSED — {out['reason']}\n")
        return 1
    if out["result"] == "EDGE_PEAK":
        print(f"\n  EDGE PEAK — {out['detail']}\n")
        return 0
    if out["result"] == "NO_PERIODIC_SIGNAL":
        print(f"\n  NO PERIODIC SIGNAL — {out['detail']}")
        print(f"  ({out['subcarriers_tested']} subcarriers tested)\n")
        return 0
    print(f"\n  {out['result']}: {out['bpm']} bpm")
    print(f"      {out['detail']}")
    print(f"      significant subcarriers {out['subcarriers_significant']}"
          f"/{out['subcarriers_tested']}, median false-alarm probability {out['median_fap']}")
    if "error_bpm" in out:
        print(f"      ground truth {out['expected_bpm']} bpm -> error {out['error_bpm']:+.2f} bpm")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
