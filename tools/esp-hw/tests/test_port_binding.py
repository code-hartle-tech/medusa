import subprocess
import sys
import os
from pathlib import Path
import types
import unittest
from unittest import mock


TOOLS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS_DIR))

import esp_build  # noqa: E402
import esp_hw  # noqa: E402


class ExactPortBindingTests(unittest.TestCase):
    def test_resolve_port_accepts_only_the_exact_existing_path(self):
        with mock.patch.object(esp_hw.os.path, "exists", side_effect=lambda path: path == "/dev/requested"):
            self.assertEqual(esp_hw.resolve_port("/dev/requested"), "/dev/requested")
            self.assertIsNone(esp_hw.resolve_port("/dev/missing"))
            self.assertIsNone(esp_hw.resolve_port(None))

    def test_flash_fails_before_upload_when_requested_port_is_missing(self):
        args = types.SimpleNamespace(
            port="/dev/requested", chip="esp32-c3", fqbn=None,
            build_dir="/tmp/build", bin=None, baud="460800",
        )
        with mock.patch.object(esp_build.H, "resolve_port", return_value=None), \
             mock.patch.object(esp_build.subprocess, "run") as run:
            self.assertEqual(esp_build.flash(args), 2)
        run.assert_not_called()

    def test_serial_reader_does_not_fall_back_from_a_missing_requested_path(self):
        missing = "/dev/medusa-test-port-that-does-not-exist"
        result = subprocess.run(
            [sys.executable, str(TOOLS_DIR / "read_serial.py"), missing, "0.01"],
            capture_output=True,
            text=True,
            timeout=2,
        )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")

    def test_usb_scan_probes_only_the_exact_requested_path(self):
        requested = "/dev/requested"
        with mock.patch.object(esp_hw.os.path, "exists", return_value=True), \
             mock.patch.object(
                 esp_hw,
                 "probe_esp",
                 return_value={
                     "chip": "ESP32-S3",
                     "mac": "aa:bb:cc:dd:ee:ff",
                     "detected": True,
                     "probe": "ok",
                 },
             ) as probe:
            result = esp_hw.scan_usb(probe=True, timeout=3, port=requested)

        self.assertEqual([entry["port"] for entry in result], [requested])
        probe.assert_called_once_with(requested, 3)

    def test_scan_cli_accepts_port_and_does_not_fall_back_when_it_is_missing(self):
        missing = "/dev/medusa-test-port-that-does-not-exist"
        result = subprocess.run(
            [
                sys.executable,
                str(TOOLS_DIR / "esp_hw.py"),
                "scan",
                "--usb",
                "--probe",
                "--json",
                "--port",
                missing,
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout.strip(), '{\n  "usb": []\n}')


class PublicCliSafetyTests(unittest.TestCase):
    def test_cli_help_builds_without_conflicting_subparsers_and_omits_legacy_lab(self):
        result = subprocess.run(
            [sys.executable, str(TOOLS_DIR / "esp_hw.py"), "--help"],
            capture_output=True,
            text=True,
            timeout=5,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("capture-wpa", result.stdout)
        self.assertNotRegex(result.stdout, r"(?m)^\s+lab\s")

    def test_active_reassociation_helper_fails_closed_without_explicit_enable(self):
        args = types.SimpleNamespace(port="/dev/requested")
        with mock.patch.dict(os.environ, {}, clear=False), \
             mock.patch.object(esp_build.H, "resolve_port") as resolve_port:
            os.environ.pop("MEDUSA_ACTIVE_LAB", None)
            self.assertEqual(esp_build.capture_wpa(args), 2)
        resolve_port.assert_not_called()


if __name__ == "__main__":
    unittest.main()
