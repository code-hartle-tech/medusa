#!/usr/bin/env python3
"""Tests for overlap-aware channel scoring.

The claim being defended: a channel's own occupancy is NOT the right ranking
key in the 2.4 GHz band. Channels are 22 MHz wide on 5 MHz spacing, so a
channel that is silent itself can be unusable because its neighbours are
shouting. Ranking on own-occupancy is the mistake router auto-select makes,
and it sends people to a channel that turns out no better.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from esp_build import score_channels, _overlap_weight  # noqa: E402


def row(ch, airtime=0, frames=0, retries=0, networks=0):
    return {"channel": ch, "frames": frames, "airtime_us": airtime,
            "retries": retries, "networks": networks, "dwell_ms": 3600}


class TestOverlapWeight(unittest.TestCase):
    def test_same_channel_is_full_weight(self):
        self.assertEqual(_overlap_weight(6, 6), 1.0)

    def test_five_apart_do_not_overlap(self):
        # Why 1/6/11 is the classic non-overlapping set.
        self.assertEqual(_overlap_weight(1, 6), 0.0)
        self.assertEqual(_overlap_weight(6, 11), 0.0)

    def test_adjacent_overlaps_heavily(self):
        self.assertAlmostEqual(_overlap_weight(6, 7), 0.8)

    def test_weight_is_symmetric(self):
        self.assertEqual(_overlap_weight(3, 7), _overlap_weight(7, 3))


class TestScoring(unittest.TestCase):
    def test_a_silent_channel_between_busy_ones_is_not_recommended(self):
        """The whole point. Channel 6 has zero traffic of its own and is a
        terrible choice, because 5 and 7 are saturated."""
        rows = [row(1, airtime=0), row(5, airtime=900_000),
                row(6, airtime=0), row(7, airtime=900_000), row(11, airtime=0)]
        scored = score_channels(rows)
        by_ch = {r["channel"]: r["interference_us"] for r in scored}

        # 6 sits between both offenders and takes the full brunt of each.
        self.assertNotEqual(scored[0]["channel"], 6)
        self.assertIn(scored[0]["channel"], (1, 11))
        # 1 and 11 are each four channels from one offender and out of range of
        # the other, so they tie exactly. Asserting a particular winner would be
        # asserting list order, not physics.
        self.assertEqual(by_ch[1], by_ch[11])
        self.assertGreater(by_ch[6], by_ch[1])

    def test_own_occupancy_alone_would_have_picked_the_wrong_channel(self):
        """Guards the specific regression: ranking by airtime_us."""
        rows = [row(6, airtime=0), row(5, airtime=900_000),
                row(7, airtime=900_000), row(11, airtime=1_000)]
        naive = sorted(rows, key=lambda r: r["airtime_us"])[0]["channel"]
        smart = score_channels(rows)[0]["channel"]
        self.assertEqual(naive, 6)
        self.assertEqual(smart, 11)
        self.assertNotEqual(naive, smart)

    def test_overlapping_networks_counts_neighbours_not_just_self(self):
        rows = [row(1, networks=2), row(3, networks=3), row(11, networks=9)]
        scored = {r["channel"]: r for r in score_channels(rows)}
        # 1 and 3 overlap each other; 11 is far from both.
        self.assertEqual(scored[1]["overlapping_networks"], 5)
        self.assertEqual(scored[11]["overlapping_networks"], 9)

    def test_retry_share_is_a_fraction_not_a_count(self):
        rows = [row(6, frames=200, retries=50)]
        self.assertEqual(score_channels(rows)[0]["retry_share"], 0.25)

    def test_no_frames_does_not_divide_by_zero(self):
        self.assertEqual(score_channels([row(6, frames=0, retries=0)])[0]["retry_share"], 0.0)

    def test_ranking_is_stable_and_complete(self):
        rows = [row(c, airtime=c * 1000) for c in range(1, 14)]
        scored = score_channels(rows)
        self.assertEqual(len(scored), 13)
        vals = [r["interference_us"] for r in scored]
        self.assertEqual(vals, sorted(vals))


if __name__ == "__main__":
    unittest.main(verbosity=2)
