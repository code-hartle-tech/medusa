#!/usr/bin/env python3
"""Tests for the respiration analyser.

Ground truth is known here because the signal is constructed: a sinusoid at a
chosen rate is impressed on synthetic CSI amplitudes and the analyser must
recover that rate. This validates the ESTIMATOR. It says nothing about whether
a real chest produces a detectable signal on real hardware — that needs a
person breathing at a counted rate, and is a separate experiment.

The tests that matter most are the refusals. An estimator that reports a
plausible number from noise, or from a series too slow to carry the band, is
worse than one that reports nothing, because the number is indistinguishable
from a real result once it is written down.
"""
import math
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from medusa_csi_raw_read import RawRecord  # noqa: E402
from medusa_csi_vitals import analyse, false_alarm_probability, lomb_scargle  # noqa: E402

NSUB = 32


def make_records(bpm, seconds=90, rate_hz=20.0, jitter=0.0, depth=5.0,
                 noise=1.0, seed=7, nsub=NSUB, base=50.0, affected=None):
    """Synthetic CSI with a breathing oscillation on the amplitudes.

    `affected` limits the modulation to a subset of subcarriers, since a real
    chest reflection does not perturb every subcarrier equally.
    """
    rng = random.Random(seed)
    f = bpm / 60.0
    affected = range(nsub) if affected is None else affected
    phases = [rng.uniform(0, 2 * math.pi) for _ in range(nsub)]

    recs, t, us, seq = [], 0.0, 0, 0
    step = 1.0 / rate_hz
    while t < seconds:
        iq = []
        for i in range(nsub):
            amp = base
            if i in affected:
                amp += depth * math.sin(2 * math.pi * f * t + phases[i])
            amp += rng.gauss(0, noise)
            theta = rng.uniform(0, 2 * math.pi)
            iq.append((int(round(amp * math.sin(theta))), int(round(amp * math.cos(theta)))))
        recs.append(RawRecord(seq=seq, us=int(us), mac="02:00:5E:10:00:01", rssi=-55,
                              rate=11, sig_mode=1, channel=9, nsub=nsub, iq=iq))
        seq += 1
        dt = step * (1.0 + rng.uniform(-jitter, jitter)) if jitter else step
        t += dt
        us += int(dt * 1e6)
    return recs


class TestRecovery(unittest.TestCase):
    def test_recovers_a_known_rate(self):
        recs = make_records(bpm=15.0)
        out = analyse(recs)
        self.assertEqual(out["result"], "BREATHING")
        self.assertAlmostEqual(out["bpm"], 15.0, delta=1.0)

    def test_recovers_a_different_rate(self):
        """Guards against anything that always answers the same number."""
        out = analyse(make_records(bpm=24.0, seed=11))
        self.assertEqual(out["result"], "BREATHING")
        self.assertAlmostEqual(out["bpm"], 24.0, delta=1.0)

    def test_recovers_under_realistic_jitter(self):
        """Measured jitter on this hardware runs to 0.4 of the mean interval.
        Lomb-Scargle uses the real timestamps, so it should barely care."""
        out = analyse(make_records(bpm=18.0, jitter=0.5, seed=3))
        self.assertEqual(out["result"], "BREATHING")
        self.assertAlmostEqual(out["bpm"], 18.0, delta=1.5)

    def test_recovers_when_only_some_subcarriers_are_affected(self):
        out = analyse(make_records(bpm=12.0, affected=set(range(0, NSUB, 2)), seed=5))
        self.assertEqual(out["result"], "BREATHING")
        self.assertAlmostEqual(out["bpm"], 12.0, delta=1.0)

    def test_works_on_the_phase_diff_signal(self):
        out = analyse(make_records(bpm=20.0, seed=9), signal="phase-diff")
        self.assertIn(out["result"], ("BREATHING", "WEAK", "NO_PERIODIC_SIGNAL"))


class TestRefusals(unittest.TestCase):
    """The tests that stop this producing confident nonsense."""

    def test_pure_noise_yields_no_signal(self):
        """The false-positive control. If this ever fails, every positive
        result in the tool is worthless."""
        recs = make_records(bpm=15.0, depth=0.0, noise=3.0, seed=42)
        out = analyse(recs)
        self.assertIn(out["result"], ("NO_PERIODIC_SIGNAL", "WEAK"))
        if out["result"] == "WEAK":
            self.assertLess(out["subcarriers_agreeing"], 8)

    def test_sampling_too_slow_is_refused_not_aliased(self):
        """1 Hz cannot carry 0.5 Hz. The wrong behaviour is to return a
        confident number for a frequency that folded."""
        recs = make_records(bpm=15.0, rate_hz=1.0, seconds=120)
        out = analyse(recs)
        self.assertEqual(out["result"], "REFUSED")
        self.assertIn("busier transmitter", out["reason"])

    def test_refusal_names_the_actual_fix(self):
        """Capturing for longer does not raise the sample rate. Saying so
        stops the obvious wrong response."""
        out = analyse(make_records(bpm=15.0, rate_hz=0.8, seconds=200))
        self.assertEqual(out["result"], "REFUSED")
        self.assertIn("not for longer", out["reason"])

    def test_too_few_records_is_refused(self):
        self.assertEqual(analyse([])["result"], "REFUSED")

    def test_a_single_loud_subcarrier_does_not_carry_a_verdict(self):
        """Consensus is the point: one subcarrier with a big peak is noise."""
        recs = make_records(bpm=15.0, depth=0.0, noise=2.0, seed=21,
                            affected=set())
        # impress a strong oscillation on exactly one subcarrier
        f = 15.0 / 60.0
        for r in recs:
            t = r.us / 1e6
            im, re = r.iq[0]
            amp = math.hypot(im, re) + 12.0 * math.sin(2 * math.pi * f * t)
            th = math.atan2(im, re)
            r.iq[0] = (int(round(amp * math.sin(th))), int(round(amp * math.cos(th))))
        out = analyse(recs, min_agree=8)
        self.assertNotEqual(out["result"], "BREATHING")


class TestEdgeRecheck(unittest.TestCase):
    """A peak against a band edge is suspect however strong the consensus is.

    Observed live: a real capture reported BREATHING at 7.0 bpm with 90 of 128
    subcarriers agreeing and a false-alarm probability of zero. Widening the
    band moved it to 3.6 bpm — it had been pinned to the 6 bpm edge all along
    and was slow drift with a ~17 second period. Statistical strength says the
    oscillation is real; it says nothing about it being inside the band asked
    for.
    """

    def test_drift_below_the_band_is_not_reported_as_breathing(self):
        """3 bpm is well under the 6 bpm floor and must not surface as a rate.

        Either refusal is correct and which one appears depends on how much of
        the out-of-band signal leaks upward: a clean synthetic sinusoid leaks
        almost nothing and simply yields no significant peak, while the real
        capture that motivated this had broadband drift that did leak, pinned
        against the edge, and needed the widening check to expose it. The
        requirement is that neither is ever called BREATHING.
        """
        recs = make_records(bpm=3.0, seconds=120, rate_hz=20.0, depth=6.0, seed=31)
        out = analyse(recs, band=(6 / 60.0, 36 / 60.0))
        self.assertIn(out["result"], ("EDGE_PEAK", "NO_PERIODIC_SIGNAL", "WEAK"))
        self.assertNotEqual(out["result"], "BREATHING")

    def test_broadband_drift_is_caught_by_the_widening_check(self):
        """Real drift is a random walk, not a clean tone.

        That matters: a pure sinusoid below the band leaks almost nothing
        upward and simply yields no peak, whereas a random walk has power at
        every low frequency, leaks into the band, and pins a strong peak
        against the floor. The second is what the live capture contained and
        what the widening check exists for.
        """
        rng = random.Random(99)
        recs = make_records(bpm=15.0, seconds=150, rate_hz=20.0, depth=0.0,
                            noise=0.6, seed=71)
        walk = 0.0
        for r in recs:
            walk += rng.gauss(0, 0.9)          # slow wandering baseline
            for i, (im, re) in enumerate(r.iq):
                amp = math.hypot(im, re) + walk
                th = math.atan2(im, re)
                r.iq[i] = (int(round(amp * math.sin(th))), int(round(amp * math.cos(th))))
        out = analyse(recs, band=(6 / 60.0, 36 / 60.0))
        self.assertNotEqual(out["result"], "BREATHING",
                            "a wandering baseline must never read as respiration")
        if out["result"] == "EDGE_PEAK":
            self.assertIn("moved_to_bpm", out)
            self.assertLess(out["moved_to_bpm"], out["bpm"])

    def test_a_signal_just_below_the_floor_is_a_known_ambiguity(self):
        """Documented limitation rather than a hidden one.

        A rate just under the floor cannot be separated from one at the floor,
        because the peak moves by less than the agreement tolerance when the
        band is widened. The check catches signals clearly outside the band,
        not ones straddling its edge — and 5 bpm is, after all, a real
        breathing rate; 6 was our choice of floor, not a law.
        """
        out = analyse(make_records(bpm=5.0, seconds=150, rate_hz=20.0,
                                   depth=8.0, noise=2.0, seed=13),
                      band=(6 / 60.0, 36 / 60.0))
        if out.get("bpm") is not None:
            self.assertLess(out["bpm"], 9.0,
                            "it may be reported, but never far from where it is")

    def test_a_rate_inside_the_band_survives_the_recheck(self):
        """The recheck must not eat genuine mid-band results."""
        out = analyse(make_records(bpm=15.0, seed=17), band=(6 / 60.0, 36 / 60.0))
        self.assertEqual(out["result"], "BREATHING")
        self.assertAlmostEqual(out["bpm"], 15.0, delta=1.5)

    def test_a_rate_near_but_genuinely_at_the_edge_is_kept(self):
        """A real 8 bpm signal sits close to the 6 bpm floor but does not move
        when the floor does, so it must be reported."""
        out = analyse(make_records(bpm=8.0, seconds=150, seed=23),
                      band=(6 / 60.0, 36 / 60.0))
        self.assertIn(out["result"], ("BREATHING", "WEAK"))
        self.assertAlmostEqual(out["bpm"], 8.0, delta=1.5)


class TestStatistics(unittest.TestCase):
    def test_lomb_scargle_finds_a_planted_tone(self):
        t = [i * 0.05 for i in range(1200)]
        y = [math.sin(2 * math.pi * 0.25 * ti) for ti in t]
        freqs = [0.1 + i * 0.005 for i in range(101)]
        p = lomb_scargle(t, y, freqs)
        peak = freqs[max(range(len(p)), key=lambda k: p[k])]
        self.assertAlmostEqual(peak, 0.25, delta=0.01)

    def test_flat_series_has_no_power(self):
        t = [i * 0.05 for i in range(100)]
        self.assertEqual(max(lomb_scargle(t, [3.0] * 100, [0.2, 0.3])), 0.0)

    def test_false_alarm_probability_is_monotonic_and_bounded(self):
        a = false_alarm_probability(2.0, 50)
        b = false_alarm_probability(20.0, 50)
        self.assertGreater(a, b)
        self.assertLessEqual(a, 1.0)
        self.assertGreaterEqual(b, 0.0)

    def test_more_trials_makes_the_same_peak_less_impressive(self):
        """Scanning more frequencies gives noise more chances to win."""
        few = false_alarm_probability(8.0, 10)
        many = false_alarm_probability(8.0, 1000)
        self.assertLess(few, many)


if __name__ == "__main__":
    unittest.main(verbosity=2)
