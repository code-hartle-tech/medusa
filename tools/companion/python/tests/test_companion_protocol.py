import binascii
import json
import sys
import unittest
import zlib
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS_DIR))

import medusa_companion as mc  # noqa: E402

VECTORS = TOOLS_DIR.parent / "vectors" / "frames.json"


class HandComputedFrameTests(unittest.TestCase):
    """The header layout, checked against bytes worked out by hand.

    Generating vectors from the codec and then testing the codec against them
    proves only that it is self-consistent. These expectations are written out
    independently so a change in the layout has to break something.
    """

    def test_single_frame_header_is_exactly_the_documented_bytes(self):
        payload = b"hi"
        # crc32 of b"hi" from an independent implementation.
        expected_crc = zlib.crc32(b"hi") & 0xFFFFFFFF
        self.assertEqual(expected_crc, 0xD8932AAC)

        frames = mc.encode_frames(payload, msg_id=1, max_frame_len=64)

        self.assertEqual(len(frames), 1)
        self.assertEqual(
            binascii.hexlify(frames[0]).decode(),
            # ver flags msg_id frag_idx total_len crc32(LE)   payload
            "01" "01" "0100" "0000" "0200" "ac2a93d8" + "6869",
        )

    def test_header_is_twelve_bytes(self):
        frames = mc.encode_frames(b"", msg_id=0, max_frame_len=32)
        self.assertEqual(len(frames[0]), mc.HEADER_LEN)
        self.assertEqual(mc.HEADER_LEN, 12)

    def test_an_empty_payload_still_produces_one_frame(self):
        """Silence and 'no data' must be distinguishable on the wire."""
        frames = mc.encode_frames(b"", msg_id=7, max_frame_len=32)
        self.assertEqual(len(frames), 1)
        self.assertIsNotNone(mc.Reassembler().push(frames[0]))


class FragmentationTests(unittest.TestCase):
    def test_ble_sized_frames_split_and_rejoin(self):
        # ATT_MTU 247 leaves 244 usable, minus the 12-byte header = 232.
        payload = bytes(range(256)) * 4  # 1024 bytes
        frames = mc.encode_frames(payload, msg_id=42, max_frame_len=244)

        self.assertEqual(len(frames), 5)  # ceil(1024 / 232)
        for frame in frames[:-1]:
            self.assertEqual(len(frame), 244)
            self.assertFalse(mc.decode_header(frame)["last"])
        self.assertTrue(mc.decode_header(frames[-1])["last"])

        r = mc.Reassembler()
        self.assertIsNone(r.push(frames[0]))
        for frame in frames[1:-1]:
            self.assertIsNone(r.push(frame))
        self.assertEqual(r.push(frames[-1]), payload)

    def test_default_mtu_of_23_still_works(self):
        """Before MTU negotiation a peer gets 20 usable bytes: 8 of payload."""
        payload = b"medusa companion protocol v1"
        frames = mc.encode_frames(payload, msg_id=3, max_frame_len=20)

        self.assertTrue(all(len(f) <= 20 for f in frames))
        r = mc.Reassembler()
        out = [r.push(f) for f in frames]
        self.assertEqual(out[-1], payload)

    def test_a_frame_length_that_leaves_no_payload_room_is_refused(self):
        with self.assertRaises(mc.ProtocolError) as ctx:
            mc.encode_frames(b"x", msg_id=1, max_frame_len=mc.HEADER_LEN)
        self.assertEqual(ctx.exception.code, "proto.mtu")


class ReassemblyFailureTests(unittest.TestCase):
    """Every one of these is a case where accepting the data would be worse."""

    def setUp(self):
        self.payload = b"a" * 600
        self.frames = mc.encode_frames(self.payload, msg_id=9, max_frame_len=244)
        self.assertGreater(len(self.frames), 1)

    def test_corrupted_payload_fails_the_checksum(self):
        r = mc.Reassembler()
        r.push(self.frames[0])
        tampered = bytearray(self.frames[1])
        tampered[mc.HEADER_LEN] ^= 0xFF
        with self.assertRaises(mc.ProtocolError) as ctx:
            for frame in [bytes(tampered)] + self.frames[2:]:
                r.push(frame)
        self.assertEqual(ctx.exception.code, "proto.crc")

    def test_out_of_order_fragments_are_refused(self):
        r = mc.Reassembler()
        r.push(self.frames[0])
        with self.assertRaises(mc.ProtocolError) as ctx:
            r.push(self.frames[2])
        self.assertEqual(ctx.exception.code, "proto.order")

    def test_a_mid_message_fragment_with_no_start_is_refused(self):
        with self.assertRaises(mc.ProtocolError) as ctx:
            mc.Reassembler().push(self.frames[1])
        self.assertEqual(ctx.exception.code, "proto.orphan")

    def test_a_new_message_abandons_an_incomplete_one(self):
        """The device holds one reassembly buffer; this is what bounds it."""
        r = mc.Reassembler()
        r.push(self.frames[0])

        other = b"second message"
        other_frames = mc.encode_frames(other, msg_id=10, max_frame_len=244)
        self.assertEqual(r.push(other_frames[0]), other)

    def test_a_truncated_frame_is_refused(self):
        with self.assertRaises(mc.ProtocolError) as ctx:
            mc.decode_header(self.frames[0][:8])
        self.assertEqual(ctx.exception.code, "proto.short")

    def test_an_unknown_protocol_version_is_never_guessed_at(self):
        frame = bytearray(self.frames[0])
        frame[0] = 0x02
        with self.assertRaises(mc.ProtocolError) as ctx:
            mc.decode_header(bytes(frame))
        self.assertEqual(ctx.exception.code, "proto.version")

    def test_fragments_disagreeing_about_the_message_are_refused(self):
        r = mc.Reassembler()
        r.push(self.frames[0])
        spliced = bytearray(self.frames[1])
        spliced[8] ^= 0xFF  # different crc32 in the header
        with self.assertRaises(mc.ProtocolError) as ctx:
            r.push(bytes(spliced))
        self.assertEqual(ctx.exception.code, "proto.mismatch")


class EnvelopeTests(unittest.TestCase):
    def test_round_trip(self):
        raw = mc.encode_envelope("cmd", "scan.wifi.start", "abc", {"channels": [1, 6, 11]})
        body = mc.decode_envelope(raw)
        self.assertEqual(body["t"], "cmd")
        self.assertEqual(body["op"], "scan.wifi.start")
        self.assertEqual(body["a"]["channels"], [1, 6, 11])

    def test_encoding_is_deterministic(self):
        """Two encoders must produce identical bytes for identical content."""
        a = mc.encode_envelope("cmd", "status.get", "1", {"b": 2, "a": 1})
        b = mc.encode_envelope("cmd", "status.get", "1", {"a": 1, "b": 2})
        self.assertEqual(a, b)
        self.assertNotIn(b" ", a)

    def test_the_seed_specs_phone_supplied_timestamp_is_gone(self):
        """The device has no RTC; a phone clock stamped on device observations
        would look authoritative and would not be."""
        body = mc.decode_envelope(mc.encode_envelope("evt", "scan.wifi.ap", "1", {}))
        self.assertNotIn("ts", body)

    def test_malformed_envelopes_are_refused_with_stable_codes(self):
        cases = [
            (b"not json", "proto.json"),
            (b"[]", "proto.json"),
            (b'{"v":2,"id":"1","t":"cmd","op":"x"}', "proto.version"),
            (b'{"v":1,"id":"1","t":"nope","op":"x"}', "proto.type"),
            (b'{"v":1,"id":"1","t":"cmd"}', "proto.op"),
            (b'{"v":1,"t":"cmd","op":"x"}', "proto.id"),
        ]
        for raw, code in cases:
            with self.subTest(raw=raw):
                with self.assertRaises(mc.ProtocolError) as ctx:
                    mc.decode_envelope(raw)
                self.assertEqual(ctx.exception.code, code)

    def test_unknown_argument_fields_are_tolerated(self):
        """Additive changes must not require a protocol version bump."""
        raw = b'{"v":1,"id":"1","t":"cmd","op":"status.get","a":{"future":true}}'
        self.assertEqual(mc.decode_envelope(raw)["a"]["future"], True)


class VectorFileTests(unittest.TestCase):
    """The cross-language contract. Swift and TypeScript run this same file."""

    @classmethod
    def setUpClass(cls):
        if not VECTORS.exists():
            raise unittest.SkipTest(f"vector file not generated yet: {VECTORS}")
        cls.vectors = json.loads(VECTORS.read_text())

    def test_encode_vectors_match(self):
        for case in self.vectors["encode"]:
            with self.subTest(name=case["name"]):
                frames = mc.encode_frames(
                    bytes.fromhex(case["payload_hex"]),
                    msg_id=case["msg_id"],
                    max_frame_len=case["max_frame_len"],
                )
                self.assertEqual([f.hex() for f in frames], case["frames_hex"])

    def test_decode_failure_vectors_match(self):
        for case in self.vectors["decode_errors"]:
            with self.subTest(name=case["name"]):
                r = mc.Reassembler()
                with self.assertRaises(mc.ProtocolError) as ctx:
                    for frame_hex in case["frames_hex"]:
                        r.push(bytes.fromhex(frame_hex))
                self.assertEqual(ctx.exception.code, case["code"])


if __name__ == "__main__":
    unittest.main()
