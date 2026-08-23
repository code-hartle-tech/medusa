from __future__ import annotations

from pathlib import Path
import sys
import unittest


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from inspect_stock_tx import EvidenceError, verify_trace  # noqa: E402


TX = """
4203be50 <esp_wifi_80211_tx>:
4203be56: 20d550 or a13, a5, a5
4203be59: 20c440 or a12, a4, a4
4203be5c: 20b330 or a11, a3, a3
4203be5f: 02ad mov.n a10, a2
4203be61: ffe4e5 call8 4203bcb0 <ieee80211_raw_frame_sanity_check>
4203be64: 0a6d mov.n a6, a10
4203be66: 0eaa56 bnez a10, 4203bf54 <esp_wifi_80211_tx+0x104>
"""

VALIDATOR = """
4203bcb0 <ieee80211_raw_frame_sanity_check>:
4203bd38: 000382 l8ui a8, a3, 0
4203bd3b: ce0c movi.n a14, 12
4203bd3d: 0f7c movi.n a15, -16
4203bd3f: 10e8e0 and a14, a8, a14
4203bd42: 10f8f0 and a15, a8, a15
4203bcfc: 02a122 movi a2, 0x102
4203be28: 02a122 movi a2, 0x102
4203be38: 020c movi.n a2, 0
"""


class StockTxTraceTests(unittest.TestCase):
    def test_accepts_the_pinned_instruction_shape(self) -> None:
        evidence = verify_trace(TX, VALIDATOR)
        self.assertIn("call8", evidence.gate_call)
        self.assertEqual(len(evidence.argument_staging), 4)
        self.assertEqual(len(evidence.invalid_arg_returns), 2)

    def test_accepts_mov_or_or_argument_staging(self) -> None:
        alternate = (
            TX.replace("or a13, a5, a5", "mov.n a13, a5")
            .replace("or a12, a4, a4", "mov.n a12, a4")
            .replace("or a11, a3, a3", "mov.n a11, a3")
        )
        verify_trace(alternate, VALIDATOR)

    def test_rejects_a_call_to_the_wrong_function(self) -> None:
        with self.assertRaises(EvidenceError):
            verify_trace(TX.replace("ieee80211_raw_frame_sanity_check", "other"), VALIDATOR)

    def test_rejects_argument_overwrite_before_call(self) -> None:
        overwritten = TX.replace(
            "4203be61: ffe4e5 call8",
            "4203be60: 07ad mov.n a10, a7\n4203be61: ffe4e5 call8",
        )
        with self.assertRaises(EvidenceError):
            verify_trace(overwritten, VALIDATOR)

    def test_rejects_result_overwrite_before_branch(self) -> None:
        overwritten = TX.replace(
            "4203be64: 0a6d mov.n a6, a10",
            "4203be64: 07ad mov.n a10, a7",
        )
        with self.assertRaises(EvidenceError):
            verify_trace(overwritten, VALIDATOR)

    def test_rejects_missing_frame_control_masks(self) -> None:
        with self.assertRaises(EvidenceError):
            verify_trace(TX, VALIDATOR.replace("movi.n a15, -16", "nop"))

    def test_rejects_frame_control_overwrite_before_masks(self) -> None:
        overwritten = VALIDATOR.replace(
            "4203bd3b: ce0c movi.n a14, 12",
            "4203bd39: 078d mov.n a8, a7\n4203bd3b: ce0c movi.n a14, 12",
        )
        with self.assertRaises(EvidenceError):
            verify_trace(TX, overwritten)

    def test_rejects_mask_constant_loaded_after_use(self) -> None:
        reordered = VALIDATOR.replace(
            "4203bd3b: ce0c movi.n a14, 12\n"
            "4203bd3d: 0f7c movi.n a15, -16\n"
            "4203bd3f: 10e8e0 and a14, a8, a14",
            "4203bd3b: 10e8e0 and a14, a8, a14\n"
            "4203bd3d: ce0c movi.n a14, 12\n"
            "4203bd3f: 0f7c movi.n a15, -16",
        )
        with self.assertRaises(EvidenceError):
            verify_trace(TX, reordered)

    def test_rejects_missing_invalid_arg_or_success_return(self) -> None:
        for altered in (
            VALIDATOR.replace("movi a2, 0x102", "movi a2, 7"),
            VALIDATOR.replace("movi.n a2, 0", "movi.n a2, 1"),
        ):
            with self.subTest(altered=altered):
                with self.assertRaises(EvidenceError):
                    verify_trace(TX, altered)


if __name__ == "__main__":
    unittest.main()
