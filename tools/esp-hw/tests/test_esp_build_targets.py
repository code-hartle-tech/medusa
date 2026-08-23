import types
import unittest
from unittest import mock

import esp_build


class DiscoveryBuildTargetTests(unittest.TestCase):
    def capture_build(self, fn, args):
        captured = []

        def fake_build(namespace):
            captured.append(namespace)
            return 1

        with mock.patch.object(esp_build.H, "resolve_port", return_value=args.port), \
             mock.patch.object(esp_build, "_resolve_chip", return_value="esp32-c3"), \
             mock.patch.object(esp_build, "build", side_effect=fake_build):
            self.assertEqual(fn(args), 1)
        self.assertEqual(len(captured), 1)
        return captured[0]

    def test_sniffer_passes_channel_into_locked_build(self):
        ns = self.capture_build(
            esp_build.sniff,
            types.SimpleNamespace(port="/dev/test", chip="esp32-c3", channel=9, seconds=5),
        )
        self.assertEqual(ns.channel, 9)
        self.assertIsNone(ns.bssid)

    def test_monitor_passes_channel_and_bssid_into_locked_build(self):
        ns = self.capture_build(
            esp_build.monitor,
            types.SimpleNamespace(
                port="/dev/test", chip="esp32-c3", channel=11,
                bssid="02:00:00:00:00:01", seconds=5,
            ),
        )
        self.assertEqual(ns.channel, 11)
        self.assertEqual(ns.bssid, "02:00:00:00:00:01")

    def test_recon_passes_optional_channel_into_locked_build(self):
        ns = self.capture_build(
            esp_build.recon,
            types.SimpleNamespace(port="/dev/test", chip="esp32-c3", channel=6),
        )
        self.assertEqual(ns.channel, 6)
        self.assertIsNone(ns.bssid)

    def test_client_scan_passes_target_into_locked_build(self):
        ns = self.capture_build(
            esp_build.scan_clients,
            types.SimpleNamespace(
                port="/dev/test", chip="esp32-c3", channel=1,
                bssid="02:00:00:00:00:02",
            ),
        )
        self.assertEqual(ns.channel, 1)
        self.assertEqual(ns.bssid, "02:00:00:00:00:02")


if __name__ == "__main__":
    unittest.main()
