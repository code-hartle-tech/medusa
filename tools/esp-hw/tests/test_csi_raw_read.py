#!/usr/bin/env python3
"""Tests for the raw CSI decoder.

The whole point of the raw path is that nothing is lost between the driver and
the consumer. So the tests that matter are the ones that would catch loss:

  - base64 round-trips every possible int8 value, including the negatives
  - signed bytes stay signed (reading them unsigned does not look like an
    error, it looks like a noisy channel)
  - phase is recoverable, since phase is the entire reason this path exists
  - the sampling report refuses runs that cannot support the analysis people
    will want to run on them
"""
import base64
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from medusa_csi_raw_read import (  # noqa: E402
    RawRecord,
    decode_iq,
    layout_groups,
    load,
    parse_raw_line,
    sampling_report,
)

B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"


def sketch_b64(vals):
    """Mirror of the encoder in medusa_csi_raw.ino, so the test exercises the
    same transform the firmware performs rather than assuming they agree."""
    data = bytes((v & 0xFF) for v in vals)
    out = []
    for i in range(0, len(data), 3):
        a = data[i]
        b = data[i + 1] if i + 1 < len(data) else 0
        c = data[i + 2] if i + 2 < len(data) else 0
        v = (a << 16) | (b << 8) | c
        out.append(B64[(v >> 18) & 0x3F])
        out.append(B64[(v >> 12) & 0x3F])
        out.append(B64[(v >> 6) & 0x3F] if i + 1 < len(data) else "=")
        out.append(B64[v & 0x3F] if i + 2 < len(data) else "=")
    return "".join(out)


def line(seq, us, mac="02:00:5E:10:00:01", vals=None, rssi=-58, nsub=None):
    vals = vals if vals is not None else [1, 2, 3, 4]
    n = nsub if nsub is not None else len(vals) // 2
    return f"CSIR\t{seq}\t{us}\t{mac}\t{rssi}\t11\t1\t9\t{n}\t{sketch_b64(vals)}\n"


class TestLosslessness(unittest.TestCase):
    def test_every_int8_value_round_trips(self):
        """The claim the raw path is built on. If this fails, nothing
        downstream means anything."""
        vals = list(range(-128, 128))
        encoded = sketch_b64(vals)
        decoded = decode_iq(encoded)
        flat = [v for pair in decoded for v in pair]
        self.assertEqual(flat, vals)

    def test_the_sketch_encoder_matches_standard_base64(self):
        """Guards against the firmware and the host drifting apart."""
        vals = [-128, -1, 0, 1, 127, 64, -64, 33, 99]
        mine = sketch_b64(vals)
        std = base64.b64encode(bytes((v & 0xFF) for v in vals)).decode()
        self.assertEqual(mine, std)

    def test_negative_bytes_are_not_read_as_large_positives(self):
        """0xFF is -1, not 255. Getting this wrong produces a plausible-looking
        signal rather than an obvious error."""
        decoded = decode_iq(sketch_b64([-1, -128]))
        self.assertEqual(decoded, [(-1, -128)])

    def test_odd_byte_count_does_not_fabricate_a_pair(self):
        decoded = decode_iq(sketch_b64([5, 6, 7]))
        self.assertEqual(decoded, [(5, 6)])


class TestRecord(unittest.TestCase):
    def test_phase_is_recovered(self):
        """Phase is the quantity the summarised path discards and the reason
        this one exists."""
        r = RawRecord(seq=0, us=0, mac="x", rssi=-50, rate=11, sig_mode=1,
                      channel=9, nsub=2, iq=[(0, 10), (10, 0), (-10, 0)])
        ph = r.phase
        self.assertAlmostEqual(ph[0], 0.0, places=6)              # imag 0, real +
        self.assertAlmostEqual(ph[1], math.pi / 2, places=6)      # imag +, real 0
        self.assertAlmostEqual(abs(ph[2]), math.pi / 2, places=6)

    def test_amplitude_matches_magnitude(self):
        r = RawRecord(seq=0, us=0, mac="x", rssi=-50, rate=11, sig_mode=1,
                      channel=9, nsub=1, iq=[(3, 4)])
        self.assertAlmostEqual(r.amplitude[0], 5.0, places=6)

    def test_parse_round_trips_a_full_line(self):
        rec = parse_raw_line(line(7, 123456, vals=[-5, 6, -7, 8]))
        self.assertIsNotNone(rec)
        self.assertEqual(rec.seq, 7)
        self.assertEqual(rec.us, 123456)
        self.assertEqual(rec.iq, [(-5, 6), (-7, 8)])

    def test_malformed_lines_are_rejected_not_guessed(self):
        self.assertIsNone(parse_raw_line("CSIR\t1\t2\n"))
        self.assertIsNone(parse_raw_line("garbage\n"))
        self.assertIsNone(parse_raw_line("CSIRINFO\tchannel=9\n"))


class TestSamplingReport(unittest.TestCase):
    """The report exists so nobody transforms a series that cannot support it."""

    def _recs(self, gaps_us, nsub=64):
        recs, t = [], 0
        vals = [1, 2] * nsub
        for i, g in enumerate([0] + list(gaps_us)):
            t += g
            recs.append(parse_raw_line(line(i, t, vals=vals, nsub=nsub)))
        return recs

    def test_fast_even_sampling_supports_breathing(self):
        # 20 Hz, perfectly even.
        r = sampling_report(self._recs([50_000] * 60))
        self.assertTrue(r["usable"])
        self.assertAlmostEqual(r["rate_hz"], 20.0, places=1)
        self.assertTrue(r["breathing_supported"])

    def test_slow_sampling_is_flagged_as_unable_to_support_breathing(self):
        """1 Hz is where the summarised pipeline sat, and it is below what
        breathing needs — the failure this whole path exists to fix."""
        r = sampling_report(self._recs([1_000_000] * 30))
        self.assertTrue(r["usable"])
        self.assertAlmostEqual(r["rate_hz"], 1.0, places=1)
        self.assertFalse(r["breathing_supported"],
                         "1 Hz cannot carry a 0.5 Hz signal without aliasing")

    def test_jitter_is_reported(self):
        even = sampling_report(self._recs([50_000] * 40))
        uneven = sampling_report(self._recs([10_000, 90_000] * 20))
        self.assertLess(even["jitter_fraction"], 0.05)
        self.assertGreater(uneven["jitter_fraction"], 0.5)

    def test_resolution_follows_span_not_rate(self):
        """Frequency resolution is 1/span. Sampling faster for the same
        duration buys no resolution, which is the mistake people make when
        they crank the rate and shorten the run."""
        short_fast = sampling_report(self._recs([10_000] * 100))    # 1 s span
        long_slow = sampling_report(self._recs([100_000] * 100))    # 10 s span
        self.assertGreater(short_fast["rate_hz"], long_slow["rate_hz"])
        self.assertGreater(short_fast["resolution_hz"], long_slow["resolution_hz"])

    def test_too_few_records_is_refused(self):
        self.assertFalse(sampling_report([])["usable"])


class TestGrouping(unittest.TestCase):
    def test_layouts_are_kept_apart(self):
        lines = [line(i, i * 50_000, vals=[1, 2] * 64, nsub=64) for i in range(5)]
        lines += [line(i, i * 50_000, vals=[1, 2] * 128, nsub=128) for i in range(5)]
        recs, _ = load(lines)
        groups = layout_groups(recs)
        self.assertEqual(sorted(groups), [64, 128])

    def test_drop_reports_are_surfaced(self):
        recs, meta = load([line(0, 0), "CSIRDROP\t17\t400\n"])
        self.assertEqual(meta["drops"], {"dropped": 17, "received": 400})


if __name__ == "__main__":
    unittest.main(verbosity=2)
