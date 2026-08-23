#!/usr/bin/env python3
"""Tests for the twin-evaluation harness.

This harness produced the numbers that decide what the position feature may
claim, so it needs to be correct in its own right. The specific ways it could
lie:

  - manufacturing a positive from its own construction rather than the physics
    (guarded by the null control, tested here on synthetic data with known
    ground truth)
  - silently comparing incomparable layouts
  - drifting out of sync with the analyser's finding names, which already
    happened once: a rename left evaluate() testing for a label the analyser no
    longer emits, so every sweep reported zero detections regardless of input
"""
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import medusa_csi_analyze as A  # noqa: E402
import medusa_csi_twin_eval as T  # noqa: E402

NSUB = 128


def to_hex(profile):
    return "".join(f"{max(0, min(255, int(v))):02x}" for v in profile)


def write_capture(path, sources, intervals=40, nsub=NSUB, seed=4242):
    """sources: {bssid: base_profile}. Emits interleaved real-looking lines.

    The per-sample jitter must be INDEPENDENT per subcarrier. An earlier
    version used ((i * 7 + j) % 3 - 1), which shifts coherently across the
    whole profile as i advances — that is precisely the signature of a
    position change, so the "single position" fixture read as two positions
    and the null control silently had nothing left to test.
    """
    rng = random.Random(seed)
    with open(path, "w") as fh:
        for i in range(intervals):
            for mac, base in sources.items():
                prof = [v + rng.randint(-1, 1) for v in base]
                fh.write(f"CSI\t{i}\t{mac}\t-60\t{nsub}\t40\t10\t{to_hex(prof)}\n")


def flat(value, nsub=NSUB):
    return [value + int(20 * ((i % 13) - 6) / 6.0) for i in range(nsub)]


class TestHarnessSoundness(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_twin_tmp.tsv")

    def tearDown(self):
        if os.path.exists(self.tmp):
            os.remove(self.tmp)

    def test_null_control_finds_nothing_on_single_position_data(self):
        """One AP merged with itself must never read as two positions."""
        write_capture(self.tmp, {"02:00:5E:10:00:01": flat(120)}, intervals=60)
        r = T.self_check(self.tmp, A.DEFAULT_SPLIT_MULTIPLIER)
        self.assertEqual(r["spurious"], 0)
        self.assertGreater(r["total"], 0, "null control must actually run some cases")

    def test_two_distinct_positions_are_detected(self):
        a = flat(120)
        b = [a[i] + (45 if (i // 5) % 2 == 0 else -40) for i in range(NSUB)]
        write_capture(self.tmp, {"02:00:5E:10:00:01": a, "02:00:5E:10:00:02": b}, intervals=60)
        r = T.evaluate(self.tmp, A.DEFAULT_SPLIT_MULTIPLIER)
        self.assertEqual(len(r["positives"]), 1)
        self.assertTrue(r["positives"][0]["detected"])

    def test_two_nearly_identical_positions_are_not_detected(self):
        """Sanity in the other direction: two APs that look the same must not
        be reported as two positions just because they are two APs."""
        a = flat(120)
        b = [v + 1 for v in a]
        write_capture(self.tmp, {"02:00:5E:10:00:01": a, "02:00:5E:10:00:02": b}, intervals=60)
        r = T.evaluate(self.tmp, A.DEFAULT_SPLIT_MULTIPLIER)
        self.assertFalse(r["positives"][0]["detected"])

    def test_pairs_of_different_layouts_are_not_compared(self):
        path2 = self.tmp + ".2"
        try:
            write_capture(self.tmp, {"02:00:5E:10:00:01": flat(120)}, intervals=40, nsub=128)
            with open(self.tmp) as fh:
                head = fh.read()
            with open(path2, "w") as fh:
                fh.write(head)
                for i in range(40):
                    prof = flat(120, nsub=64)
                    fh.write(f"CSI\t{i}\t02:00:5E:10:00:02\t-60\t64\t40\t10\t{to_hex(prof)}\n")
            r = T.evaluate(path2, A.DEFAULT_SPLIT_MULTIPLIER)
            self.assertEqual(r["positives"], [],
                             "a 128-subcarrier AP and a 64-subcarrier AP are not comparable")
        finally:
            if os.path.exists(path2):
                os.remove(path2)


class TestLabelsStayInSync(unittest.TestCase):
    """The rename bug: evaluate() tested for a finding the analyser no longer
    emitted, so every sweep read zero detections no matter what went in. A
    silent zero is the worst possible failure for a validation harness — it
    looks exactly like an honest negative result."""

    def test_harness_looks_for_a_label_the_analyser_can_actually_emit(self):
        src = open(T.__file__).read()
        self.assertIn("POSITION_ANOMALY", src)
        analyser = open(A.__file__).read()
        self.assertIn('"POSITION_ANOMALY"', analyser)
        self.assertNotIn('== "MULTIPLE_POSITIONS"', src,
                         "harness still tests for the pre-rename label")


class TestInterleave(unittest.TestCase):
    def test_alternates_and_keeps_everything(self):
        a = [(0, [1], 5, -60), (1, [2], 5, -60)]
        b = [(0, [3], 5, -70), (1, [4], 5, -70)]
        out = T.interleave(a, b)
        self.assertEqual([p for _, p, _, _ in out], [[1], [3], [2], [4]])

    def test_uneven_lengths_keep_every_sample(self):
        a = [(i, [i], 5, -60) for i in range(5)]
        b = [(i, [i + 100], 5, -70) for i in range(2)]
        self.assertEqual(len(T.interleave(a, b)), 7)


if __name__ == "__main__":
    unittest.main(verbosity=2)
