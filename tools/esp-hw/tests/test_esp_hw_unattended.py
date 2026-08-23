import contextlib
import io
import json
import types
import unittest
from unittest import mock

import esp_hw
import unattended_dump


class EspHwUnattendedTests(unittest.TestCase):
    def test_json_propagates_orphan_recovery_truth(self):
        result = unattended_dump.RetrievalResult(
            csv_text="bssid,ssid,channel,rssi,seen,inventory_partial,capacity_drops\n",
            row_count=0,
            capacity_drops=0,
            store_fields={"reason": "orphaned-csv-preserved"},
            recovery="orphaned-csv-preserved",
            source="temp-csv-read-only-recovery",
        )
        args = types.SimpleNamespace(
            port="/dev/cu.test",
            out="/tmp/medusa-test-unattended.csv",
            timeout=5.0,
        )
        output = io.StringIO()
        with mock.patch.object(esp_hw, "resolve_port", return_value=args.port), \
             mock.patch.object(esp_hw.os.path, "exists", return_value=True), \
             mock.patch.object(
                 unattended_dump, "retrieve_to_file", return_value=result
             ), contextlib.redirect_stdout(output):
            self.assertEqual(esp_hw.cmd_unattended_dump(args), 0)

        json_line = next(
            line for line in output.getvalue().splitlines()
            if line.startswith("UNATTENDED_DUMP_JSON ")
        )
        payload = json.loads(json_line.split(" ", 1)[1])
        self.assertEqual(payload["recovery"], "orphaned-csv-preserved")
        self.assertEqual(payload["source"], "temp-csv-read-only-recovery")
        self.assertEqual(payload["rows"], 0)
        self.assertFalse(payload["partial"])

    def test_cli_labels_volatile_ram_recovery_as_non_persisted_read_only(self):
        result = unattended_dump.RetrievalResult(
            csv_text="bssid,ssid,channel,rssi,seen,inventory_partial,capacity_drops\n",
            row_count=0,
            capacity_drops=0,
            store_fields={"reason": "orphaned-csv-preserved"},
            recovery="volatile-ram-read-only",
            source="volatile-ram-read-only",
        )
        args = types.SimpleNamespace(
            port="/dev/cu.test",
            out="/tmp/medusa-test-unattended.csv",
            timeout=5.0,
        )
        output = io.StringIO()
        with mock.patch.object(esp_hw, "resolve_port", return_value=args.port), \
             mock.patch.object(esp_hw.os.path, "exists", return_value=True), \
             mock.patch.object(
                 unattended_dump, "retrieve_to_file", return_value=result
             ), contextlib.redirect_stdout(output):
            self.assertEqual(esp_hw.cmd_unattended_dump(args), 0)

        self.assertIn("rendered from non-persisted volatile RAM", output.getvalue())
        self.assertIn("readback did not clear recovery artifacts", output.getvalue())
        self.assertNotIn("device storage unchanged", output.getvalue())


if __name__ == "__main__":
    unittest.main()
