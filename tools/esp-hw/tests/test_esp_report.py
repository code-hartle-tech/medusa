import csv
import io
import sys
from pathlib import Path
import unittest


TOOLS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS_DIR))

import esp_report  # noqa: E402


BASE_AP = {
    "bssid": "02:00:00:00:00:01",
    "channel": 6,
    "rssi": -42,
    "count": 3,
}


def parse_wigle(text):
    return list(csv.reader(io.StringIO(text, newline="")))


def parse_airodump(text):
    return list(csv.reader(io.StringIO(text, newline=""), skipinitialspace=True))


class SpreadsheetSafetyTests(unittest.TestCase):
    def test_formula_leading_ssids_are_neutralized_in_both_csv_formats(self):
        for prefix in ("=", "+", "-", "@"):
            with self.subTest(prefix=prefix):
                ssid = prefix + "SUM(1,1)"
                row = {**BASE_AP, "ssid": ssid, "auth": "WPA2-PSK"}

                wigle = parse_wigle(esp_report.wigle_csv([row], now=0))
                airodump = parse_airodump(esp_report.airodump_csv([row], now=0))

                self.assertEqual(wigle[2][1], "'" + ssid)
                self.assertEqual(airodump[1][13], "'" + ssid)
                # ID-length describes the over-the-air SSID, not the safety
                # apostrophe added only to the exported spreadsheet cell.
                self.assertEqual(int(airodump[1][12]), len(ssid))

    def test_airodump_quotes_commas_newlines_and_quotes_without_column_drift(self):
        ssid = 'Lab, "east"\nsecond floor'
        row = {**BASE_AP, "ssid": ssid, "auth": "WPA2-PSK"}

        parsed = parse_airodump(esp_report.airodump_csv([row], now=0))

        self.assertEqual(len(parsed), 2)
        self.assertEqual(len(parsed[0]), 15)
        self.assertEqual(len(parsed[1]), 15)
        self.assertEqual(parsed[1][13], ssid)
        self.assertEqual(parsed[1][14], "")

    def test_non_formula_ssid_is_unchanged(self):
        row = {**BASE_AP, "ssid": "ordinary network", "auth": "WPA2-PSK"}

        self.assertEqual(parse_wigle(esp_report.wigle_csv([row], now=0))[2][1], row["ssid"])
        self.assertEqual(parse_airodump(esp_report.airodump_csv([row], now=0))[1][13], row["ssid"])


class UnknownSecurityTests(unittest.TestCase):
    def test_missing_auth_is_unknown_not_open(self):
        severity, label, finding = esp_report._risk({**BASE_AP, "ssid": "scan only"})

        self.assertEqual(severity, 0)
        self.assertEqual(label, "unknown")
        self.assertNotIn("readable", finding.lower())
        self.assertEqual(esp_report._authmode({}), "[UNKNOWN][ESS]")

    def test_missing_pmf_is_unknown_not_equivalent_to_not_advertised(self):
        severity, label, finding = esp_report._risk({"auth": "WPA2-PSK"})

        self.assertEqual(severity, 0)
        self.assertEqual(label, "unknown")
        self.assertIn("was not collected", finding)
        self.assertNotIn("were not advertised", finding)

    def test_explicit_open_auth_remains_a_critical_finding(self):
        severity, label, finding = esp_report._risk({"auth": "OPEN"})

        self.assertEqual(severity, 3)
        self.assertEqual(label, "critical")
        self.assertIn("no link-layer encryption", finding.lower())
        self.assertEqual(esp_report._authmode({"auth": "OPEN"}), "[ESS]")

    def test_posture_and_airodump_render_missing_auth_as_unknown(self):
        row = {**BASE_AP, "ssid": "scan only"}

        report = esp_report.posture_html([row], now=0)
        airodump = parse_airodump(esp_report.airodump_csv([row], now=0))

        self.assertIn(">unknown<", report)
        self.assertIn(">UNKNOWN<", report)
        self.assertIn(">unknown</td><td>unknown</td><td>UNKNOWN<", report)
        self.assertIn("0</b> critical", report)
        self.assertNotIn("all traffic readable", report.lower())
        self.assertEqual(airodump[1][5], "UNKNOWN")

    def test_posture_findings_do_not_claim_unobserved_exploit_outcomes(self):
        rows = [
            {"auth": "OPEN"},
            {"auth": "WEP"},
            {"auth": "WPA2-PSK", "wps": True},
            {"auth": "WPA2-PSK", "pmf": "none"},
            {"auth": "WPA3-SAE", "pmf": "required"},
        ]

        findings = " ".join(esp_report._risk(row)[2] for row in rows).lower()

        for unsupported_claim in ("all traffic readable", "pixie", "psk recovery", "deauth", "handshake", "crackable", "capture works"):
            with self.subTest(unsupported_claim=unsupported_claim):
                self.assertNotIn(unsupported_claim, findings)


PSEUDO_ROWS = [
    {"bssid": "AA:BB:CC:11:22:33", "ssid": "Familia Silva 5G", "auth": "WPA2-PSK", "channel": 6, "rssi": -40},
    {"bssid": "AA:BB:CC:44:55:66", "ssid": "Familia Silva 5G", "auth": "OPEN", "channel": 1, "rssi": -70},
    {"bssid": "DE:EE:FF:77:88:99", "ssid": "", "auth": "WPA3-SAE", "channel": 11, "rssi": -55},
]


class PseudonymizerTests(unittest.TestCase):
    """A shareable export must not carry the identifiers the UI masks on screen."""

    def test_vendor_prefix_survives_but_device_octets_do_not(self):
        p = esp_report.Pseudonymizer()

        masked = p.bssid("AA:BB:CC:11:22:33")

        self.assertTrue(masked.startswith("AA:BB:CC:"), masked)
        self.assertNotIn("11:22:33", masked)
        # Still a syntactically valid MAC so downstream parsers keep working.
        self.assertRegex(masked, r"^(?:[0-9A-F]{2}:){5}[0-9A-F]{2}$")

    def test_same_input_is_stable_within_one_export(self):
        p = esp_report.Pseudonymizer()

        self.assertEqual(p.bssid("AA:BB:CC:11:22:33"), p.bssid("AA:BB:CC:11:22:33"))
        self.assertEqual(p.ssid("Familia Silva 5G"), p.ssid("Familia Silva 5G"))

    def test_distinct_inputs_stay_distinct_so_counts_survive(self):
        p = esp_report.Pseudonymizer()

        self.assertNotEqual(p.bssid("AA:BB:CC:11:22:33"), p.bssid("AA:BB:CC:44:55:66"))

    def test_two_exports_do_not_correlate(self):
        """Per-run salt: the same AP must not map to one token across reports."""
        first = esp_report.Pseudonymizer().bssid("AA:BB:CC:11:22:33")
        second = esp_report.Pseudonymizer().bssid("AA:BB:CC:11:22:33")

        self.assertNotEqual(first, second)

    def test_hidden_ssid_stays_empty_rather_than_inventing_a_name(self):
        p = esp_report.Pseudonymizer()

        self.assertEqual(p.ssid(""), "")
        self.assertEqual(p.ssid(None), "")


class PseudonymizedExportTests(unittest.TestCase):
    def test_wigle_export_carries_no_original_identifier(self):
        text = esp_report.wigle_csv(PSEUDO_ROWS, pseudonymizer=esp_report.Pseudonymizer())

        self.assertNotIn("Familia Silva", text)
        self.assertNotIn("11:22:33", text)
        self.assertNotIn("44:55:66", text)
        # Rows are still all present and parseable.
        self.assertEqual(len(parse_wigle(text)) - 2, len(PSEUDO_ROWS))

    def test_airodump_export_carries_no_original_identifier(self):
        text = esp_report.airodump_csv(PSEUDO_ROWS, pseudonymizer=esp_report.Pseudonymizer())

        self.assertNotIn("Familia Silva", text)
        self.assertNotIn("11:22:33", text)

    def test_posture_report_redacts_and_says_so(self):
        report = esp_report.posture_html(PSEUDO_ROWS, pseudonymizer=esp_report.Pseudonymizer())

        self.assertNotIn("Familia Silva", report)
        self.assertNotIn("11:22:33", report)
        # The reader must be told, or they will read pseudonyms as real values.
        self.assertIn("pseudonym", report.lower())

    def test_posture_findings_are_unchanged_by_redaction(self):
        """Redaction must not alter the security conclusions the report exists for."""
        plain = esp_report.posture_html(PSEUDO_ROWS)
        masked = esp_report.posture_html(PSEUDO_ROWS, pseudonymizer=esp_report.Pseudonymizer())

        for verdict in ("critical", "ok"):
            self.assertEqual(plain.count(f">{verdict}<"), masked.count(f">{verdict}<"))

    def test_default_export_is_unchanged(self):
        """Without the flag, output must be byte-identical to the previous behaviour."""
        self.assertIn("Familia Silva 5G", esp_report.wigle_csv(PSEUDO_ROWS))
        self.assertIn("AA:BB:CC:11:22:33", esp_report.wigle_csv(PSEUDO_ROWS))


if __name__ == "__main__":
    unittest.main()
