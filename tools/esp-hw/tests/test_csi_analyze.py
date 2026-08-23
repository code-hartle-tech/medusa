#!/usr/bin/env python3
"""Tests for the CSI analyser's core discrimination.

The claim this file exists to defend is narrow and specific: that the analyser
can tell MOTION from a SECOND TRANSMITTER. Both raise variance, so a detector
that only looked at variance would confuse them, and confusing them is fatal —
it would either cry rogue-AP every time somebody walked past, or stay silent
when an evil twin appeared.

These are synthetic streams with known ground truth. They test the decision
logic, NOT the radio. Nothing here says CSI works on hardware.
"""
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from medusa_csi_analyze import run, l1_distance, profile_from_hex  # noqa: E402

# 128 subcarriers: below RICH_LAYOUT_MIN the analyser deliberately declines to
# score position at all, because legacy-LTF profiles were measured to carry no
# position information. Synthetic tests must exercise a layout that is scored.
NSUB = 128


def to_hex(profile):
    return "".join(f"{max(0, min(255, int(v))):02x}" for v in profile)


def line(seq, mac, profile, motion=0, rssi=-58, frames=40):
    return f"CSI\t{seq}\t{mac}\t{rssi}\t{NSUB}\t{frames}\t{motion}\t{to_hex(profile)}\n"


def base_profile(rng, seed_shift=0):
    # A plausible channel: power varies smoothly across the band with a couple
    # of nulls, rather than being flat.
    return [
        120 + seed_shift + int(30 * ((i % 17) - 8) / 8.0) + rng.randint(-1, 1)
        for i in range(NSUB)
    ]


class TestDiscrimination(unittest.TestCase):
    def setUp(self):
        self.rng = random.Random(20260822)
        # Locally-administered fixture addresses. Never a real BSSID from a
        # real survey: a router's BSSID is geolocatable through public
        # wardriving databases, so committing one to a public repository
        # publishes where that network lives.
        self.mac = "02:00:5E:10:00:01"

    def _run(self, lines, threshold=12.0):
        return run(lines, threshold, echo=False)

    def test_stable_channel_reports_stable(self):
        base = base_profile(self.rng)
        lines = [line(i, self.mac, [v + self.rng.randint(-1, 1) for v in base], motion=self.rng.randint(0, 20))
                 for i in range(40)]
        result = self._run(lines)
        t = result["transmitters"][0]
        self.assertEqual(t["finding"], "STABLE")
        self.assertEqual(t["clusters_supported"], 1)

    def test_motion_is_not_reported_as_a_second_transmitter(self):
        """The false-positive that would make this feature useless."""
        base = base_profile(self.rng)
        lines = []
        for i in range(60):
            # Wander around the same shape and come back, the way a body
            # crossing the room perturbs multipath.
            drift = int(8 * ((i % 10) - 5) / 5.0)
            prof = [v + drift + self.rng.randint(-2, 2) for v in base]
            lines.append(line(i, self.mac, prof, motion=200 + self.rng.randint(0, 80)))
        t = self._run(lines)["transmitters"][0]
        self.assertEqual(t["finding"], "MOTION")
        self.assertEqual(t["clusters_supported"], 1,
                         "movement must not be reported as a second position")

    def test_two_positions_are_detected(self):
        """Two radios claiming one BSSID from different places."""
        a = base_profile(self.rng)
        # A genuinely different position: a different multipath shape, not just
        # a level shift, since an attacker at a different distance also differs
        # in profile shape.
        b = [a[i] + (40 if (i // 4) % 2 == 0 else -35) for i in range(NSUB)]
        lines = []
        for i in range(60):
            src = a if i % 2 == 0 else b
            lines.append(line(i, self.mac, [v + self.rng.randint(-2, 2) for v in src], motion=self.rng.randint(0, 30)))
        t = self._run(lines)["transmitters"][0]
        self.assertEqual(t["finding"], "POSITION_ANOMALY")
        self.assertGreaterEqual(t["clusters_supported"], 2)
        self.assertGreater(t["cluster_separation"], 12.0)

    def test_single_stray_reading_is_not_a_second_transmitter(self):
        """One outlier is noise. Treating it as an AP is how a detector
        earns a reputation for crying wolf."""
        base = base_profile(self.rng)
        lines = [line(i, self.mac, [v + self.rng.randint(-1, 1) for v in base], motion=5)
                 for i in range(60)]
        outlier = [v + 90 for v in base]
        lines.insert(30, line(999, self.mac, outlier, motion=800))
        t = self._run(lines)["transmitters"][0]
        self.assertEqual(t["clusters_supported"], 1)
        self.assertNotEqual(t["finding"], "POSITION_ANOMALY")

    def test_separate_bssids_tracked_separately(self):
        a = base_profile(self.rng)
        b = base_profile(self.rng, seed_shift=60)
        lines = []
        for i in range(30):
            lines.append(line(i, "02:00:5E:10:00:01", [v + self.rng.randint(-1, 1) for v in a]))
            lines.append(line(i, "02:00:5E:10:00:02", [v + self.rng.randint(-1, 1) for v in b]))
        result = self._run(lines)
        self.assertEqual(len(result["transmitters"]), 2)
        for t in result["transmitters"]:
            self.assertEqual(t["clusters_supported"], 1)


class TestEvidenceThresholds(unittest.TestCase):
    """A verdict needs enough sample to be a verdict."""

    def setUp(self):
        self.rng = random.Random(7)
        self.mac = "02:00:5E:10:00:01"

    def test_a_transmitter_heard_once_is_not_judged(self):
        """Observed live: a distant AP contributing one interval was labelled
        MOTION with full confidence, because one profile is trivially one
        stable cluster that trivially differs from a baseline made of itself."""
        base = base_profile(self.rng)
        result = run([line(0, self.mac, base, motion=278)], 12.0, echo=False)
        t = result["transmitters"][0]
        self.assertEqual(t["finding"], "INSUFFICIENT_DATA")
        self.assertEqual(t["intervals"], 1)

    def test_verdict_appears_once_there_is_enough_sample(self):
        base = base_profile(self.rng)
        lines = [line(i, self.mac, [v + self.rng.randint(-1, 1) for v in base], motion=5)
                 for i in range(20)]
        t = run(lines, 12.0, echo=False)["transmitters"][0]
        self.assertNotEqual(t["finding"], "INSUFFICIENT_DATA")

    def test_position_is_assessed_even_when_motion_cannot_be(self):
        """Position and motion have separate evidence and separate answers.

        The firmware withholds a motion figure when an interval held too few
        frames to average. Those intervals still carry a perfectly good
        profile. Requiring motion evidence before answering the POSITION
        question silently disabled twin detection on quiet networks — which is
        where it matters most — and showed up as 0/6 on the twin evaluation
        before it was visible anywhere else."""
        base = base_profile(self.rng)
        lines = [f"CSI\t{i}\t{self.mac}\t-60\t{NSUB}\t2\t-\t{to_hex(base)}\n" for i in range(20)]
        t = run(lines, 12.0, echo=False)["transmitters"][0]
        self.assertEqual(t["finding"], "STABLE_POSITION")
        self.assertNotEqual(t["finding"], "INSUFFICIENT_DATA")


class TestLayoutGate(unittest.TestCase):
    """Legacy-LTF profiles are not scored for position at all.

    Measured on hardware: at 64 subcarriers, three constructed twins scored
    2.30-3.58 and three honest single-radio APs scored 2.36-3.64. Completely
    interleaved — the layout carries no position information whatsoever, so
    producing a score from it would be manufacturing a number from nothing.
    """

    def setUp(self):
        self.rng = random.Random(11)
        self.mac = "02:00:5E:10:00:01"

    def _two_position_lines(self, nsub):
        a = [120 + int(30 * ((i % 17) - 8) / 8.0) for i in range(nsub)]
        b = [a[i] + (40 if (i // 4) % 2 == 0 else -35) for i in range(nsub)]
        lines = []
        for i in range(60):
            src = a if i % 2 == 0 else b
            prof = [v + self.rng.randint(-2, 2) for v in src]
            hexed = "".join(f"{max(0, min(255, v)):02x}" for v in prof)
            lines.append(f"CSI\t{i}\t{self.mac}\t-58\t{nsub}\t40\t10\t{hexed}\n")
        return lines

    def test_rich_layout_is_scored(self):
        t = run(self._two_position_lines(128), 12.0, echo=False)["transmitters"][0]
        self.assertEqual(t["finding"], "POSITION_ANOMALY")

    def test_legacy_layout_is_not_scored_even_when_bimodal(self):
        """Identical two-position data at 64 subcarriers must NOT be reported.
        Refusing to answer beats answering from evidence known to be empty."""
        t = run(self._two_position_lines(64), 12.0, echo=False)["transmitters"][0]
        self.assertNotEqual(t["finding"], "POSITION_ANOMALY")
        self.assertEqual(t["layout"], 64)


class TestParsing(unittest.TestCase):
    def test_empty_interval_lines_are_ignored(self):
        """The sketch emits a placeholder when it heard nothing. It must not
        become a data point."""
        lines = ["CSI\t0\t-\t-\t0\t0\t-\t-\n"] * 10
        result = run(lines, 12.0, echo=False)
        self.assertEqual(result["transmitters"], [])

    def test_errors_and_info_are_surfaced(self):
        lines = [
            "CSIINFO\tchannel=9\n",
            "CSIERR\tcsi-enable-258 (core may lack CONFIG_ESP32_WIFI_CSI_ENABLED)\n",
        ]
        result = run(lines, 12.0, echo=False)
        self.assertIn("channel=9", result["info"])
        self.assertEqual(len(result["errors"]), 1)

    def test_l1_handles_length_mismatch(self):
        self.assertEqual(l1_distance([10, 20], [10, 20, 30]), 0.0)

    def test_profile_hex_roundtrip(self):
        self.assertEqual(profile_from_hex("00ff10"), [0, 255, 16])


if __name__ == "__main__":
    unittest.main(verbosity=2)
