from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from verify_stock_validator import verify_map  # noqa: E402


STOCK = "/sdk/libnet80211.a(ieee80211_output.o)"
APP = ".pio/build/lab/main/libmain.a(app_override.o)"


def map_text(*sections: tuple[str, int, int, bool], xref_owner: str | None = None) -> str:
    lines = ["Linker script and memory map"]
    for owner, address, size, exports in sections:
        lines.extend(
            [
                " .text.ieee80211_raw_frame_sanity_check",
                f"                0x{address:016x}       0x{size:x} {owner}",
            ]
        )
        if exports:
            lines.append(
                f"                0x{address:016x}                "
                "ieee80211_raw_frame_sanity_check"
            )
        lines.append(" .text.next_function")
    if xref_owner is not None:
        lines.extend(
            [
                "Cross Reference Table",
                f"ieee80211_raw_frame_sanity_check                  {xref_owner}",
            ]
        )
    return "\n".join(lines) + "\n"


class StockValidatorMapTests(unittest.TestCase):
    def verify(self, contents: str) -> tuple[bool, str]:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "firmware.map"
            path.write_text(contents)
            return verify_map(path)

    def test_accepts_one_effective_stock_text_definition(self) -> None:
        ok, message = self.verify(map_text((STOCK, 0x42001000, 0x1CC, True), xref_owner=STOCK))
        self.assertTrue(ok, message)

    def test_rejects_literal_only_archive_presence(self) -> None:
        ok, _ = self.verify(
            ".literal.ieee80211_raw_frame_sanity_check\n"
            f"                0x0000000000000000        0x0 {STOCK}\n"
        )
        self.assertFalse(ok)

    def test_rejects_application_definition_even_when_stock_text_is_present(self) -> None:
        ok, message = self.verify(
            map_text(
                (APP, 0x42001000, 0x10, True),
                (STOCK, 0x42001010, 0x1CC, False),
                xref_owner=APP,
            )
        )
        self.assertFalse(ok)
        self.assertIn(APP, message)

    def test_rejects_conflicting_cross_reference_owner(self) -> None:
        ok, message = self.verify(
            map_text((STOCK, 0x42001000, 0x1CC, True), xref_owner=APP)
        )
        self.assertFalse(ok)
        self.assertIn("cross-reference owner", message)

    def test_rejects_zero_address_or_missing_export(self) -> None:
        for contents in (
            map_text((STOCK, 0, 0x1CC, True)),
            map_text((STOCK, 0x42001000, 0x1CC, False)),
        ):
            with self.subTest(contents=contents):
                ok, _ = self.verify(contents)
                self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
