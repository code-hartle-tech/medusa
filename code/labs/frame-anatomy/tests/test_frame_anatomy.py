from __future__ import annotations

import ast
import io
from pathlib import Path
import re
import struct
import sys
import tempfile
import unittest
from contextlib import redirect_stdout


LAB_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB_DIR))

import frame_anatomy as fa  # noqa: E402


RX = "12:11:12:13:14:15"
TX = "22:21:22:23:24:25"
BSSID = "32:31:32:33:34:35"


class FrameControlTests(unittest.TestCase):
    def test_supported_subtype_bytes_and_bitfields(self) -> None:
        deauth = fa.pack_frame_control(
            frame_type=fa.TYPE_MANAGEMENT,
            subtype=fa.SUBTYPE_DEAUTHENTICATION,
        )
        action = fa.pack_frame_control(
            frame_type=fa.TYPE_MANAGEMENT, subtype=fa.SUBTYPE_ACTION
        )
        self.assertEqual(deauth, b"\xc0\x00")
        self.assertEqual(action, b"\xd0\x00")
        self.assertEqual(fa.decode_frame_control(deauth)["subtype"], 12)
        self.assertEqual(fa.decode_frame_control(action)["subtype"], 13)

    def test_every_frame_control_flag_occupies_its_bit(self) -> None:
        value = fa.frame_control_value(
            protocol_version=3,
            frame_type=2,
            subtype=8,
            to_ds=True,
            from_ds=True,
            more_fragments=True,
            retry=True,
            power_management=True,
            more_data=True,
            protected=True,
            order=True,
        )
        self.assertEqual(value, 0xFF8B)
        decoded = fa.decode_frame_control(value)
        self.assertEqual(decoded["protocol_version"], 3)
        self.assertEqual(decoded["type"], 2)
        self.assertEqual(decoded["subtype"], 8)
        for flag in fa._FC_FLAG_BITS:
            self.assertTrue(decoded[flag])

    def test_frame_control_bounds_are_checked(self) -> None:
        with self.assertRaises(fa.FrameAnatomyError):
            fa.frame_control_value(frame_type=4, subtype=0)
        with self.assertRaises(fa.FrameAnatomyError):
            fa.frame_control_value(frame_type=0, subtype=16)
        with self.assertRaises(fa.FrameAnatomyError):
            fa.frame_control_value(frame_type=0, subtype=0, retry=1)


class FrameBuildDecodeTests(unittest.TestCase):
    def test_management_address_mapping(self) -> None:
        frame = fa.build_vendor_action_frame(
            receiver=RX,
            transmitter=TX,
            bssid=BSSID,
            oui="aa:bb:cc",
            vendor_content=b"\x01\x02",
        )
        self.assertEqual(frame[4:10], bytes.fromhex("121112131415"))
        self.assertEqual(frame[10:16], bytes.fromhex("222122232425"))
        self.assertEqual(frame[16:22], bytes.fromhex("323132333435"))

        addresses = fa.decode_frame(frame)["addresses"]
        self.assertEqual(addresses["receiver"], RX)
        self.assertEqual(addresses["destination"], RX)
        self.assertEqual(addresses["transmitter"], TX)
        self.assertEqual(addresses["source"], TX)
        self.assertEqual(addresses["bssid"], BSSID)

    def test_little_endian_duration_sequence_and_reason(self) -> None:
        frame = fa.build_deauthentication_frame(
            receiver=RX,
            transmitter=TX,
            bssid=BSSID,
            duration=0x1234,
            sequence_number=0xABC,
            fragment_number=0xD,
            reason_code=0x5678,
        )
        self.assertEqual(frame[2:4], b"\x34\x12")
        self.assertEqual(frame[22:24], b"\xcd\xab")
        self.assertEqual(frame[24:26], b"\x78\x56")

        decoded = fa.decode_deauthentication_frame(frame)
        self.assertEqual(decoded["duration"], 0x1234)
        self.assertEqual(decoded["sequence_control"]["sequence_number"], 0xABC)
        self.assertEqual(decoded["sequence_control"]["fragment_number"], 0xD)
        self.assertEqual(decoded["body"]["reason_code"], 0x5678)

    def test_deauthentication_frame_omits_fcs(self) -> None:
        frame = fa.build_deauthentication_frame(
            receiver=RX, transmitter=TX, bssid=BSSID, reason_code=3
        )
        self.assertEqual(len(frame), fa.MAC_HEADER_LENGTH + 2)
        self.assertEqual(frame[-2:], b"\x03\x00")
        self.assertFalse(fa.decode_frame(frame)["fcs_included"])
        with self.assertRaises(fa.FrameAnatomyError):
            fa.decode_deauthentication_frame(frame + b"\x00\x00\x00\x00")

    def test_readme_deauthentication_specimen_is_exact(self) -> None:
        frame = fa.build_deauthentication_frame(
            receiver="02:00:00:00:00:01",
            transmitter="02:00:00:00:00:02",
            bssid="02:00:00:00:00:03",
            duration=0,
            sequence_number=291,
            fragment_number=4,
            reason_code=2,
        )
        readme = (LAB_DIR / "README.md").read_text(encoding="utf-8")
        match = re.search(
            r"The reproducible FCS-free result is:\s*```text\s*"
            r"([0-9a-f]+)\s*```",
            readme,
        )
        self.assertIsNotNone(match, "README worked specimen is missing")
        self.assertEqual(frame.hex(), match.group(1))

    def test_vendor_action_shape_and_fcs_contract(self) -> None:
        content = b"lesson"
        frame = fa.build_vendor_action_frame(
            receiver=RX,
            transmitter=TX,
            bssid=BSSID,
            oui="aa-bb-cc",
            vendor_content=content,
        )
        self.assertEqual(len(frame), fa.MAC_HEADER_LENGTH + 4 + len(content))
        self.assertEqual(frame[24:28], b"\x7f\xaa\xbb\xcc")
        decoded = fa.decode_vendor_action_frame(frame)
        self.assertFalse(decoded["fcs_included"])
        self.assertEqual(decoded["body"]["category"], 127)
        self.assertEqual(decoded["body"]["oui"], "aa:bb:cc")
        self.assertEqual(decoded["body"]["vendor_content_hex"], content.hex())

    def test_field_and_frame_bounds(self) -> None:
        common = dict(receiver=RX, transmitter=TX, bssid=BSSID)
        for field, value in (
            ("duration", 65536),
            ("sequence_number", 4096),
            ("fragment_number", 16),
            ("reason_code", 65536),
        ):
            with self.subTest(field=field), self.assertRaises(fa.FrameAnatomyError):
                arguments = {**common, "reason_code": 1, field: value}
                fa.build_deauthentication_frame(**arguments)

        with self.assertRaises(fa.FrameAnatomyError):
            fa.build_deauthentication_frame(
                receiver="00:11:22:33:44", transmitter=TX, bssid=BSSID, reason_code=1
            )
        with self.assertRaises(fa.FrameAnatomyError):
            fa.build_vendor_action_frame(
                **common,
                oui="aa:bb:cc",
                vendor_content=b"x" * (fa.MAX_VENDOR_CONTENT_LENGTH + 1),
            )
        with self.assertRaises(fa.FrameAnatomyError):
            fa.decode_frame(b"\xc0")
        with self.assertRaises(fa.FrameAnatomyError):
            fa.decode_vendor_action_frame(
                b"\xd0\x00" + b"\x00" * (fa.MAX_FRAME_LENGTH - 1)
            )

    def test_decoder_rejects_other_types_subtypes_and_categories(self) -> None:
        unsupported_subtype = fa.pack_frame_control(frame_type=0, subtype=8) + b"\0" * 22
        with self.assertRaises(fa.FrameAnatomyError):
            fa.decode_frame(unsupported_subtype)

        non_management = fa.pack_frame_control(frame_type=2, subtype=0) + b"\0" * 22
        with self.assertRaises(fa.FrameAnatomyError):
            fa.decode_frame(non_management)

        action = bytearray(
            fa.build_vendor_action_frame(
                receiver=RX,
                transmitter=TX,
                bssid=BSSID,
                oui="aa:bb:cc",
            )
        )
        action[24] = 4
        with self.assertRaises(fa.FrameAnatomyError):
            fa.decode_frame(action)


class PcapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.frame = fa.build_deauthentication_frame(
            receiver=RX, transmitter=TX, bssid=BSSID, reason_code=7
        )

    def test_global_header_and_record_are_ieee80211_little_endian(self) -> None:
        pcap = fa.build_pcap([self.frame], timestamps=[1.25])
        self.assertEqual(pcap[:4], b"\xd4\xc3\xb2\xa1")
        global_header = struct.unpack("<IHHiIII", pcap[:24])
        self.assertEqual(
            global_header,
            (
                fa.PCAP_MAGIC_USEC,
                2,
                4,
                0,
                0,
                fa.MAX_FRAME_LENGTH,
                fa.LINKTYPE_IEEE802_11,
            ),
        )
        record_header = struct.unpack("<IIII", pcap[24:40])
        self.assertEqual(record_header, (1, 250000, len(self.frame), len(self.frame)))
        self.assertEqual(pcap[40:], self.frame)

    def test_multiple_records_round_trip_without_fcs(self) -> None:
        action = fa.build_vendor_action_frame(
            receiver=RX,
            transmitter=TX,
            bssid=BSSID,
            oui="aa:bb:cc",
            vendor_content=b"offline",
        )
        decoded = fa.decode_pcap(
            fa.build_pcap([self.frame, action], timestamps=[0, 2.000003])
        )
        self.assertEqual(decoded["linktype"], 105)
        self.assertEqual(len(decoded["records"]), 2)
        self.assertEqual(decoded["records"][0]["frame_hex"], self.frame.hex())
        self.assertFalse(decoded["records"][0]["decoded"]["fcs_included"])
        self.assertEqual(decoded["records"][1]["decoded"]["kind"], "vendor-specific action")
        self.assertEqual(decoded["records"][1]["timestamp_microseconds"], 3)

    def test_write_and_read_pcap_use_regular_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "lesson.pcap"
            fa.write_pcap(path, [self.frame], timestamps=[10])
            self.assertEqual(
                fa.read_pcap(path)["records"][0]["decoded"]["kind"],
                "deauthentication",
            )

    def test_pcap_bounds_and_truncation_are_rejected(self) -> None:
        with self.assertRaises(fa.PcapError):
            fa.build_pcap([self.frame], snaplen=len(self.frame) - 1)
        with self.assertRaises(fa.PcapError):
            fa.build_pcap([self.frame], timestamps=[])
        with self.assertRaises(fa.PcapError):
            fa.build_pcap([self.frame], timestamps=[-1])
        valid = fa.build_pcap([self.frame])
        with self.assertRaises(fa.PcapError):
            fa.decode_pcap(valid[:-1])


class CliAndOfflineBoundaryTests(unittest.TestCase):
    def test_cli_build_and_hex_decode(self) -> None:
        stream = io.StringIO()
        with redirect_stdout(stream):
            status = fa.main(
                [
                    "build-deauth",
                    "--receiver",
                    RX,
                    "--transmitter",
                    TX,
                    "--bssid",
                    BSSID,
                    "--reason-code",
                    "3",
                ]
            )
        self.assertEqual(status, 0)
        built = __import__("json").loads(stream.getvalue())

        stream = io.StringIO()
        with redirect_stdout(stream):
            status = fa.main(["decode", "--hex", built["frame_hex"]])
        self.assertEqual(status, 0)
        decoded = __import__("json").loads(stream.getvalue())
        self.assertEqual(decoded["decoded"]["kind"], "deauthentication")

    def test_module_has_no_operational_io_imports(self) -> None:
        tree = ast.parse((LAB_DIR / "frame_anatomy.py").read_text(encoding="utf-8"))
        imported_roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".")[0])
        forbidden = {
            "socket",
            "subprocess",
            "serial",
            "scapy",
            "pcap",
            "wifi",
            "espidf",
        }
        self.assertTrue(imported_roots.isdisjoint(forbidden), imported_roots & forbidden)


if __name__ == "__main__":
    unittest.main()
