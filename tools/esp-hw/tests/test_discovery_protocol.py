import contextlib
import io
import json
import subprocess
import sys
from pathlib import Path
import types
import unittest
from unittest import mock


TOOLS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS_DIR))

import esp_build  # noqa: E402


def encoded(text):
    return text.encode("utf-8").hex()


class DiscoveryProtocolParserTests(unittest.TestCase):
    def test_apscan_decodes_control_characters_without_forging_records(self):
        hostile = "Cafe\tAP2\nAPSCAN_END"
        output = "\n".join([
            "APSCAN_BEGIN\t2",
            f"AP2\t02:00:00:00:00:01\t6\t-42\t{encoded(hostile)}",
            "APSCAN_END\t2\tOK\t1",
        ])

        aps, complete = esp_build._parse_apscan_output(output)

        self.assertTrue(complete)
        self.assertEqual(len(aps), 1)
        self.assertEqual(aps[0]["ssid"], hostile)

    def test_apscan_ignores_legacy_unescaped_records(self):
        output = "\n".join([
            "APSCAN_BEGIN\t2",
            "AP\tAA:BB:CC:DD:EE:FF\t1\t-1\tforged",
            "APSCAN_END\t2\tOK\t0",
        ])

        aps, complete = esp_build._parse_apscan_output(output)

        self.assertTrue(complete)
        self.assertEqual(aps, [])

    def test_apscan_requires_valid_begin_end_and_record_count(self):
        record = f"AP2\t02:00:00:00:00:01\t6\t-42\t{encoded('Lab')}"

        self.assertEqual(esp_build._parse_apscan_output(record), ([], False))
        self.assertEqual(
            esp_build._parse_apscan_output("APSCAN_BEGIN\t2\n" + record + "\nAPSCAN_END\t2\tOK\t2"),
            ([], False),
        )

    def test_apscan_allows_a_completed_zero_network_survey(self):
        self.assertEqual(
            esp_build._parse_apscan_output("APSCAN_BEGIN\t2\nAPSCAN_END\t2\tOK\t0\n"),
            ([], True),
        )

    def test_blescan_decodes_control_characters_and_company_id(self):
        hostile = "Beacon\tBLE2\nBLESCAN_END\t2\tOK\t99"
        output = "\n".join([
            "BLESCAN_BEGIN\t2",
            f"BLE2\t02:00:00:00:00:02\t-30\t{encoded(hostile)}\t4c00",
            "BLESCAN_END\t2\tOK\t1",
        ])

        devices, complete = esp_build._parse_blescan_output(output)

        self.assertTrue(complete)
        self.assertEqual(devices, [{
            "addr": "02:00:00:00:00:02",
            "rssi": -30,
            "name": hostile,
            "vendor": "Apple",
            "company_id": 0x004C,
            "manufacturer": "4c00",
        }])

    def test_blescan_preserves_the_raw_advertisement_payload(self):
        """Deriving a vendor and discarding the payload loses the only evidence
        of what was actually advertised, which is the thing being looked at."""
        payload = "4c0002151122334455667788"
        output = "\n".join([
            "BLESCAN_BEGIN\t2",
            f"BLE2\t02:00:00:00:00:07\t-44\t\t{payload}",
            "BLESCAN_END\t2\tOK\t1",
        ])

        devices, complete = esp_build._parse_blescan_output(output)

        self.assertTrue(complete)
        self.assertEqual(devices[0]["manufacturer"], payload)
        self.assertEqual(devices[0]["company_id"], 0x004C)
        self.assertEqual(devices[0]["vendor"], "Apple")

    def test_blescan_without_manufacturer_data_reports_no_company(self):
        output = "\n".join([
            "BLESCAN_BEGIN\t2",
            "BLE2\t02:00:00:00:00:08\t-60\t\t",
            "BLESCAN_END\t2\tOK\t1",
        ])

        devices, _ = esp_build._parse_blescan_output(output)

        self.assertEqual(devices[0]["manufacturer"], "")
        self.assertIsNone(devices[0]["company_id"])
        self.assertEqual(devices[0]["vendor"], "")

    def test_blescan_requires_a_valid_complete_cycle_but_allows_zero_devices(self):
        self.assertEqual(esp_build._parse_blescan_output("BLESCAN_END\t2\tOK\t0"), ([], False))
        self.assertEqual(
            esp_build._parse_blescan_output("BLESCAN_BEGIN\t2\nBLESCAN_END\t2\tOK\t0"),
            ([], True),
        )
        self.assertEqual(
            esp_build._parse_blescan_output("BLESCAN_BEGIN\t2\nBLESCAN_END\t2\tERROR\tstart-failed"),
            ([], False),
        )
        malformed = "BLESCAN_BEGIN\t2\nBLE2\tbad\t-1\t00\t\nBLESCAN_END\t2\tOK\t1"
        self.assertEqual(esp_build._parse_blescan_output(malformed), ([], False))

    def test_recon_uses_escaped_ssids_and_requires_a_counted_cycle(self):
        hostile = "Cafe\tRECON2\nRECON_END"
        row = f"RECON2\t02:00:00:00:00:03\t6\t-51\tWPA2-PSK\tcapable\t-\tCCMP\t{encoded(hostile)}"

        aps, complete = esp_build._parse_recon_output(
            "RECON_BEGIN\t2\n" + row + "\nRECON_END\t2\tOK\t1\n"
        )

        self.assertTrue(complete)
        self.assertEqual(aps[0]["ssid"], hostile)
        self.assertIn("pmf", aps[0]["findings"])
        self.assertEqual(
            esp_build._parse_recon_output("RECON_BEGIN\t2\n" + row + "\nRECON_END\t2\tOK\t2"),
            ([], False),
        )

    def test_client_scan_requires_markers_but_allows_zero_clients(self):
        self.assertEqual(
            esp_build._parse_clientscan_output("CLIENTS_BEGIN\t2\nCLIENTS_END\t2\tOK\t0"),
            ([], True),
        )
        self.assertEqual(
            esp_build._parse_clientscan_output("CLIENTS_END\t2\tOK\t0"),
            ([], False),
        )

    def test_configuration_findings_are_defensive_and_evidence_led(self):
        findings = esp_build._configuration_findings({
            "auth": "WPA2-PSK", "pmf": "none", "wps": True, "cipher": "CCMP",
        })

        self.assertEqual(set(findings), {"authentication", "pmf", "wps", "cipher"})
        rendered = json.dumps(findings).lower()
        for offensive_claim in ("knock", "crack", "evil twin", "creds", "mitm", "pixie", "auth flood", "attack applies"):
            with self.subTest(offensive_claim=offensive_claim):
                self.assertNotIn(offensive_claim, rendered)


class DiscoveryRuntimeProofTests(unittest.TestCase):
    def run_survey(self, function, reader_stdout, reader_rc=0):
        args = types.SimpleNamespace(port="/dev/test", chip="esp32-c3", seconds=10)
        completed = subprocess.CompletedProcess([], reader_rc, stdout=reader_stdout, stderr="")
        output = io.StringIO()
        with mock.patch.object(esp_build.H, "resolve_port", return_value=args.port), \
             mock.patch.object(esp_build, "_resolve_chip", return_value="esp32-c3"), \
             mock.patch.object(esp_build, "build", return_value=0), \
             mock.patch.object(esp_build, "flash", return_value=0), \
             mock.patch.object(esp_build.time, "sleep"), \
             mock.patch.object(esp_build.subprocess, "run", return_value=completed), \
             contextlib.redirect_stdout(output):
            rc = function(args)
        marker = "APSCAN_JSON " if function is esp_build.scan_aps else "BLESCAN_JSON "
        payload_line = next(line for line in output.getvalue().splitlines() if line.startswith(marker))
        return rc, json.loads(payload_line[len(marker):])

    def test_apscan_does_not_claim_success_from_build_and_flash_only(self):
        rc, payload = self.run_survey(esp_build.scan_aps, "booted but no scan markers\n")

        self.assertEqual(rc, 1)
        self.assertTrue(payload["flashed"])
        self.assertFalse(payload["complete"])
        self.assertEqual(payload["aps"], [])

    def test_blescan_requires_reader_success_even_with_complete_markers(self):
        rc, payload = self.run_survey(
            esp_build.ble_scan,
            "BLESCAN_BEGIN\t2\nBLESCAN_END\t2\tOK\t0\n",
            reader_rc=1,
        )

        self.assertEqual(rc, 1)
        self.assertTrue(payload["flashed"])
        self.assertFalse(payload["complete"])
        self.assertEqual(payload["devices"], [])

    def test_zero_result_surveys_are_successful_when_runtime_markers_are_valid(self):
        ap_rc, ap_payload = self.run_survey(
            esp_build.scan_aps,
            "APSCAN_BEGIN\t2\nAPSCAN_END\t2\tOK\t0\n",
        )
        ble_rc, ble_payload = self.run_survey(
            esp_build.ble_scan,
            "BLESCAN_BEGIN\t2\nBLESCAN_END\t2\tOK\t0\n",
        )

        self.assertEqual(ap_rc, 0)
        self.assertTrue(ap_payload["complete"])
        self.assertEqual(ble_rc, 0)
        self.assertTrue(ble_payload["complete"])

    def test_ap_and_ble_build_flash_pairs_use_bound_unique_directories(self):
        def invoke(function):
            calls = []
            args = types.SimpleNamespace(port="/dev/test", chip="esp32-c3", seconds=10)

            def fake_build(ns):
                calls.append(("build", ns.out))
                return 0

            def fake_flash(ns):
                calls.append(("flash", ns.build_dir))
                return 1

            with mock.patch.object(esp_build.H, "resolve_port", return_value=args.port), \
                 mock.patch.object(esp_build, "_resolve_chip", return_value="esp32-c3"), \
                 mock.patch.object(esp_build, "build", side_effect=fake_build), \
                 mock.patch.object(esp_build, "flash", side_effect=fake_flash):
                self.assertEqual(function(args), 1)
            self.assertEqual(calls[0][1], calls[1][1])
            return calls[0][1]

        for function in (esp_build.scan_aps, esp_build.ble_scan):
            with self.subTest(function=function.__name__):
                self.assertNotEqual(invoke(function), invoke(function))


class AdjacentRuntimeProofTests(unittest.TestCase):
    def run_helper(self, function, stdout, reader_rc=0):
        args = types.SimpleNamespace(
            port="/dev/test", chip="esp32-c3", channel=6, seconds=10,
            bssid="02:00:00:00:00:01",
        )
        completed = subprocess.CompletedProcess([], reader_rc, stdout=stdout, stderr="")
        output = io.StringIO()
        patches = [
            mock.patch.object(esp_build.H, "resolve_port", return_value=args.port),
            mock.patch.object(esp_build, "_resolve_chip", return_value="esp32-c3"),
            mock.patch.object(esp_build, "build", return_value=0),
            mock.patch.object(esp_build, "flash", return_value=0),
            mock.patch.object(esp_build.time, "sleep"),
            mock.patch.object(esp_build.subprocess, "run", return_value=completed),
            mock.patch.object(esp_build.tempfile, "mkdtemp", return_value="/tmp/medusa-test-runtime"),
            mock.patch.object(esp_build, "write_pcap"),
        ]
        with contextlib.ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            with contextlib.redirect_stdout(output):
                rc = function(args)
        prefixes = {
            esp_build.recon: "RECON_JSON ",
            esp_build.scan_clients: "CLIENTS_JSON ",
            esp_build.sniff: "SNIFF_JSON ",
            esp_build.monitor: "MONITOR_JSON ",
        }
        prefix = prefixes[function]
        payload_line = next(line for line in output.getvalue().splitlines() if line.startswith(prefix))
        return rc, json.loads(payload_line[len(prefix):])

    def test_helpers_do_not_claim_success_from_compile_and_flash_only(self):
        for function in (esp_build.recon, esp_build.scan_clients, esp_build.sniff, esp_build.monitor):
            with self.subTest(function=function.__name__):
                rc, payload = self.run_helper(function, "firmware booted without runtime proof\n")
                self.assertEqual(rc, 1)
                self.assertTrue(payload["flashed"])
                self.assertFalse(payload["complete"])

    def test_zero_observation_runtime_cycles_are_valid(self):
        cases = (
            (esp_build.recon, "RECON_BEGIN\t2\nRECON_END\t2\tOK\t0\n"),
            (esp_build.scan_clients, "CLIENTS_BEGIN\t2\nCLIENTS_END\t2\tOK\t0\n"),
            (esp_build.sniff, "STAT\tseen=0 sent=0 dropped=0\n"),
            (esp_build.monitor, "MON\t6\t0\t0\t0\t0\t0\t-128\n"),
        )
        for function, stdout in cases:
            with self.subTest(function=function.__name__):
                rc, payload = self.run_helper(function, stdout)
                self.assertEqual(rc, 0)
                self.assertTrue(payload["complete"])

    def test_firmware_initialization_errors_never_become_runtime_proof(self):
        cases = (
            (esp_build.recon, "RECON_END\t2\tERROR\tinit-rc-0x3001\n"),
            (esp_build.scan_clients, "CLIENTS_END\t2\tERROR\twifi-mode\n"),
            (esp_build.sniff, "SNIFFER_ERROR\tinit-rc=0x3001\n"),
            (esp_build.monitor, "MONITOR_ERROR\twifi-mode\n"),
        )
        for function, stdout in cases:
            with self.subTest(function=function.__name__):
                rc, payload = self.run_helper(function, stdout)
                self.assertEqual(rc, 1)
                self.assertTrue(payload["flashed"])
                self.assertFalse(payload["complete"])


if __name__ == "__main__":
    unittest.main()
