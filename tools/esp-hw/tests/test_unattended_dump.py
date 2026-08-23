import os
import pty
import select
import sys
from pathlib import Path
import tempfile
import unittest


TOOLS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS_DIR))

import unattended_dump  # noqa: E402


HEADER = b"bssid,ssid,channel,rssi,seen,inventory_partial,capacity_drops\r\n"
PRIMARY_SOURCE = b"UNATT_DUMP_SOURCE\tprimary-csv\tpath=/medusa_log.csv\n"
TEMP_RECOVERY_SOURCE = b"UNATT_DUMP_SOURCE\ttemp-csv-read-only-recovery\tpath=/medusa_log.tmp\n"
DATABASE_RECOVERY_SOURCE = b"UNATT_DUMP_SOURCE\tdatabase-read-only\tgeneration=7\trecovery=unpaired-csv-artifact\n"
VOLATILE_RECOVERY_SOURCE = b"UNATT_DUMP_SOURCE\tvolatile-ram-read-only\tcommitted_generation=7\trecovery=db-csv-divergence\n"


class ScriptedStream:
    def __init__(self, lines):
        self.lines = list(lines)
        self.writes = []
        self.timeouts = []

    def write(self, data):
        self.writes.append(data)

    def readline(self, timeout):
        self.timeouts.append(timeout)
        if not self.lines:
            return None
        return self.lines.pop(0)


def successful_store(count=1, capacity_drops=0):
    return (
        f"UNATT_STORE\taps={count}\tdb=ok\tcsv=ok\t"
        f"capacity_drops={capacity_drops}\t"
        "path=/medusa_log.csv\r\n"
    ).encode()


def orphaned_store(count=0, capacity_drops=0, reason="orphaned-csv-preserved"):
    return (
        f"UNATT_STORE\taps={count}\tdb=error\tcsv=error\t"
        f"capacity_drops={capacity_drops}\treason={reason}\r\n"
    ).encode()


class ProtocolTests(unittest.TestCase):
    def test_retrieves_only_csv_between_markers(self):
        stream = ScriptedStream(
            [
                b"UNATT_STATUS\tch=6\taps=1\r\n",
                successful_store(),
                b"boot chatter after flush\n",
                PRIMARY_SOURCE,
                b"UNATT_CSV_BEGIN\t1\r\n",
                HEADER,
                b'AA:BB:CC:DD:EE:FF,"Lab, AP",6,-42,9,no,0\r\n',
                b"UNATT_CSV_END\r\n",
                b"trailing chatter must not be captured\n",
            ]
        )

        result = unattended_dump.run_protocol(stream)

        self.assertEqual(stream.writes, [b"FLUSH\n", b"DUMP\n"])
        self.assertNotIn(b"CLEAR", b"".join(stream.writes))
        self.assertEqual(result.row_count, 1)
        self.assertEqual(
            result.csv_text,
            'bssid,ssid,channel,rssi,seen,inventory_partial,capacity_drops\n'
            'AA:BB:CC:DD:EE:FF,"Lab, AP",6,-42,9,no,0\n',
        )
        self.assertEqual(result.store_fields["aps"], "1")

    def test_accepts_an_empty_inventory(self):
        stream = ScriptedStream(
            [
                successful_store(0),
                PRIMARY_SOURCE,
                b"UNATT_CSV_BEGIN\t0\n",
                HEADER,
                b"UNATT_CSV_END\n",
            ]
        )

        result = unattended_dump.run_protocol(stream)

        self.assertEqual(result.row_count, 0)
        self.assertEqual(result.csv_text, HEADER.decode().replace("\r\n", "\n"))

    def test_rejects_failed_store_without_sending_dump(self):
        stream = ScriptedStream(
            [b"UNATT_STORE\taps=3\tdb=ok\tcsv=error\tcapacity_drops=0\tpath=/medusa_log.csv\n"]
        )

        with self.assertRaisesRegex(unattended_dump.ProtocolError, "could not persist"):
            unattended_dump.run_protocol(stream)

        self.assertEqual(stream.writes, [b"FLUSH\n"])

    def test_recovers_only_the_exact_firmware_preserved_orphan_csv(self):
        stream = ScriptedStream(
            [
                # These failed-snapshot counters are deliberately stale. The
                # preserved CSV is the only source of recovery metadata.
                orphaned_store(count=0, capacity_drops=0),
                TEMP_RECOVERY_SOURCE,
                b"UNATT_CSV_BEGIN\t1\n",
                HEADER,
                b'AA:BB:CC:DD:EE:FF,"Recovered",6,-42,9,yes,4\n',
                b"UNATT_CSV_END\n",
            ]
        )

        result = unattended_dump.run_protocol(stream)

        self.assertEqual(stream.writes, [b"FLUSH\n", b"DUMP\n"])
        self.assertNotIn(b"CLEAR", b"".join(stream.writes))
        self.assertNotIn(b"INIT", b"".join(stream.writes))
        self.assertEqual(result.row_count, 1)
        self.assertEqual(result.capacity_drops, 4)
        self.assertTrue(result.partial)
        self.assertEqual(result.recovery, "orphaned-csv-preserved")
        self.assertEqual(result.source, "temp-csv-read-only-recovery")
        self.assertEqual(
            result.store_fields["reason"], "orphaned-csv-preserved"
        )

    def test_labels_read_only_database_recovery_separately_from_csv(self):
        stream = ScriptedStream(
            [
                orphaned_store(count=1, capacity_drops=4),
                DATABASE_RECOVERY_SOURCE,
                b"UNATT_CSV_BEGIN\t1\n",
                HEADER,
                b'AA:BB:CC:DD:EE:FF,"Database row",6,-42,9,yes,4\n',
                b"UNATT_CSV_END\n",
            ]
        )

        result = unattended_dump.run_protocol(stream)

        self.assertEqual(result.recovery, "database-read-only")
        self.assertEqual(result.source, "database-read-only")
        self.assertEqual(result.row_count, 1)
        self.assertEqual(result.capacity_drops, 4)

    def test_volatile_ram_recovery_allows_store_and_dump_counts_to_differ(self):
        stream = ScriptedStream(
            [
                orphaned_store(count=0, capacity_drops=0),
                VOLATILE_RECOVERY_SOURCE,
                b"UNATT_CSV_BEGIN\t1\n",
                HEADER,
                b'AA:BB:CC:DD:EE:FF,"Not persisted",6,-42,9,yes,4\n',
                b"UNATT_CSV_END\n",
            ]
        )

        result = unattended_dump.run_protocol(stream)

        self.assertEqual(result.recovery, "volatile-ram-read-only")
        self.assertEqual(result.source, "volatile-ram-read-only")
        self.assertEqual(result.row_count, 1)
        self.assertEqual(result.capacity_drops, 4)

    def test_requires_one_exact_source_before_csv_begin(self):
        missing = ScriptedStream(
            [successful_store(0), b"UNATT_CSV_BEGIN\t0\n"]
        )
        with self.assertRaisesRegex(
            unattended_dump.ProtocolError, "before dump source"
        ):
            unattended_dump.run_protocol(missing)

        duplicate = ScriptedStream(
            [successful_store(0), PRIMARY_SOURCE, PRIMARY_SOURCE]
        )
        with self.assertRaisesRegex(
            unattended_dump.ProtocolError, "duplicate UNATT_DUMP_SOURCE"
        ):
            unattended_dump.run_protocol(duplicate)

    def test_rejects_unknown_or_mismatched_source_metadata(self):
        invalid_sources = (
            b"UNATT_DUMP_SOURCE\tprimary-csv\tpath=/medusa_log.tmp\n",
            b"UNATT_DUMP_SOURCE\tdatabase-read-only\tgeneration=-1\trecovery=unpaired-csv-artifact\n",
            b"UNATT_DUMP_SOURCE\tdatabase-read-only\tgeneration=00\trecovery=unpaired-csv-artifact\n",
            b"UNATT_DUMP_SOURCE\tdatabase-read-only\tgeneration=7\trecovery=unknown\n",
            b"UNATT_DUMP_SOURCE\tvolatile-ram-read-only\tcommitted_generation=7\trecovery=wrong\n",
            b"UNATT_DUMP_SOURCE\tvolatile-ram-read-only\tcommitted_generation=00\trecovery=db-csv-divergence\n",
            b"UNATT_DUMP_SOURCE\tprimary-db-read-only\tpath=/medusa_inventory.bin\n",
        )
        for source in invalid_sources:
            with self.subTest(source=source):
                stream = ScriptedStream([orphaned_store(), source])
                with self.assertRaises(unattended_dump.ProtocolError):
                    unattended_dump.run_protocol(stream)

    def test_successful_store_must_dump_the_primary_csv(self):
        stream = ScriptedStream(
            [
                successful_store(0),
                DATABASE_RECOVERY_SOURCE,
                b"UNATT_CSV_BEGIN\t0\n",
                HEADER,
                b"UNATT_CSV_END\n",
            ]
        )

        with self.assertRaisesRegex(
            unattended_dump.ProtocolError, "unexpected dump source"
        ):
            unattended_dump.run_protocol(stream)

    def test_rejects_other_failed_store_reasons_without_sending_dump(self):
        for reason in (
            "orphaned-csv",
            "storage-unavailable",
            "orphaned-csv-preserved-extra",
        ):
            with self.subTest(reason=reason):
                stream = ScriptedStream([orphaned_store(reason=reason)])
                with self.assertRaisesRegex(
                    unattended_dump.ProtocolError, "could not persist"
                ):
                    unattended_dump.run_protocol(stream)
                self.assertEqual(stream.writes, [b"FLUSH\n"])

    def test_rejects_inconsistent_orphan_reason_without_sending_dump(self):
        stream = ScriptedStream(
            [
                b"UNATT_STORE\taps=1\tdb=ok\tcsv=error\tcapacity_drops=0\t"
                b"reason=orphaned-csv-preserved\n"
            ]
        )

        with self.assertRaisesRegex(
            unattended_dump.ProtocolError, "inconsistent orphan"
        ):
            unattended_dump.run_protocol(stream)

        self.assertEqual(stream.writes, [b"FLUSH\n"])

    def test_rejects_invalid_orphan_csv_after_read_only_dump(self):
        stream = ScriptedStream(
            [
                orphaned_store(),
                TEMP_RECOVERY_SOURCE,
                b"UNATT_CSV_BEGIN\t1\n",
                HEADER,
                b'AA:BB:CC:DD:EE:FF,"Recovered",6,-42,9,no,4\n',
                b"UNATT_CSV_END\n",
            ]
        )

        with self.assertRaisesRegex(
            unattended_dump.ProtocolError, "capacity metadata"
        ):
            unattended_dump.run_protocol(stream)

        self.assertEqual(stream.writes, [b"FLUSH\n", b"DUMP\n"])
        self.assertNotIn(b"CLEAR", b"".join(stream.writes))
        self.assertNotIn(b"INIT", b"".join(stream.writes))

    def test_marks_a_capacity_limited_inventory_partial(self):
        stream = ScriptedStream(
            [
                successful_store(1, capacity_drops=4),
                PRIMARY_SOURCE,
                b"UNATT_CSV_BEGIN\t1\n",
                HEADER,
                b'AA:BB:CC:DD:EE:FF,"Lab",1,-20,1,yes,4\n',
                b"UNATT_CSV_END\n",
            ]
        )

        result = unattended_dump.run_protocol(stream)

        self.assertTrue(result.partial)
        self.assertEqual(result.capacity_drops, 4)

    def test_rejects_inconsistent_capacity_metadata(self):
        stream = ScriptedStream(
            [
                successful_store(1, capacity_drops=4),
                PRIMARY_SOURCE,
                b"UNATT_CSV_BEGIN\t1\n",
                HEADER,
                b'AA:BB:CC:DD:EE:FF,"Lab",1,-20,1,no,0\n',
                b"UNATT_CSV_END\n",
            ]
        )

        with self.assertRaisesRegex(
            unattended_dump.ProtocolError, "capacity metadata"
        ):
            unattended_dump.run_protocol(stream)

    def test_rejects_unexpected_header(self):
        stream = ScriptedStream(
            [
                successful_store(),
                PRIMARY_SOURCE,
                b"UNATT_CSV_BEGIN\t1\n",
                b"ssid,bssid,channel,rssi,seen\n",
                b"Lab,AA:BB:CC:DD:EE:FF,1,-20,1\n",
                b"UNATT_CSV_END\n",
            ]
        )

        with self.assertRaisesRegex(unattended_dump.ProtocolError, "unexpected CSV header"):
            unattended_dump.run_protocol(stream)

    def test_rejects_row_count_mismatch(self):
        stream = ScriptedStream(
            [
                successful_store(2),
                PRIMARY_SOURCE,
                b"UNATT_CSV_BEGIN\t2\n",
                HEADER,
                b'AA:BB:CC:DD:EE:FF,"Lab",1,-20,1,no,0\n',
                b"UNATT_CSV_END\n",
            ]
        )

        with self.assertRaisesRegex(unattended_dump.ProtocolError, "row count"):
            unattended_dump.run_protocol(stream)

    def test_rejects_store_and_dump_count_mismatch(self):
        stream = ScriptedStream(
            [
                successful_store(2),
                PRIMARY_SOURCE,
                b"UNATT_CSV_BEGIN\t1\n",
                HEADER,
                b'AA:BB:CC:DD:EE:FF,"Lab",1,-20,1,no,0\n',
                b"UNATT_CSV_END\n",
            ]
        )

        with self.assertRaisesRegex(
            unattended_dump.ProtocolError, "persisted row count"
        ):
            unattended_dump.run_protocol(stream)

    def test_rejects_malformed_csv_row(self):
        stream = ScriptedStream(
            [
                successful_store(),
                PRIMARY_SOURCE,
                b"UNATT_CSV_BEGIN\t1\n",
                HEADER,
                b"AA:BB:CC:DD:EE:FF,Lab,1,-20,1,no\n",
                b"UNATT_CSV_END\n",
            ]
        )

        with self.assertRaisesRegex(unattended_dump.ProtocolError, "columns"):
            unattended_dump.run_protocol(stream)

    def test_rejects_rows_outside_firmware_inventory_invariants(self):
        invalid_rows = (
            (b'aa:BB:CC:DD:EE:FF,"Lab",1,-20,1,no,0\n', "BSSID"),
            (b'AA:BB:CC:DD:EE:FF,"Lab",0,-20,1,no,0\n', "channel"),
            (b'AA:BB:CC:DD:EE:FF,"Lab",15,-20,1,no,0\n', "channel"),
            (b'AA:BB:CC:DD:EE:FF,"Lab",01,-20,1,no,0\n', "channel"),
            (b'AA:BB:CC:DD:EE:FF,"Lab",1,-129,1,no,0\n', "RSSI"),
            (b'AA:BB:CC:DD:EE:FF,"Lab",1,1,1,no,0\n', "RSSI"),
            (b'AA:BB:CC:DD:EE:FF,"Lab",1,-20,0,no,0\n', "seen count"),
            (b'AA:BB:CC:DD:EE:FF,"Lab",1,-20,4294967296,no,0\n', "seen count"),
            (b'AA:BB:CC:DD:EE:FF,"Lab",1,-20,01,no,0\n', "seen count"),
            (b'AA:BB:CC:DD:EE:FF,"=SUM(1)",1,-20,1,no,0\n', "formula"),
            (b'AA:BB:CC:DD:EE:FF,"Lab\tSSID",1,-20,1,no,0\n', "unescaped SSID"),
            (b'AA:BB:CC:DD:EE:FF,"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",1,-20,1,no,0\n', "oversized SSID"),
        )
        for row, message in invalid_rows:
            with self.subTest(row=row):
                stream = ScriptedStream(
                    [
                        successful_store(),
                        PRIMARY_SOURCE,
                        b"UNATT_CSV_BEGIN\t1\n",
                        HEADER,
                        row,
                        b"UNATT_CSV_END\n",
                    ]
                )
                with self.assertRaisesRegex(unattended_dump.ProtocolError, message):
                    unattended_dump.run_protocol(stream)

    def test_accepts_formula_neutralization_and_bounded_hex_escapes(self):
        escaped_ssid = "\\x01" * 32
        for ssid in ("'=SUM(1;2)", escaped_ssid):
            with self.subTest(ssid=ssid):
                row = (
                    f'AA:BB:CC:DD:EE:FF,"{ssid}",1,-20,1,no,0\n'
                ).encode()
                stream = ScriptedStream(
                    [
                        successful_store(),
                        PRIMARY_SOURCE,
                        b"UNATT_CSV_BEGIN\t1\n",
                        HEADER,
                        row,
                        b"UNATT_CSV_END\n",
                    ]
                )
                result = unattended_dump.run_protocol(stream)
                self.assertEqual(result.row_count, 1)

    def test_rejects_duplicate_bssids(self):
        row = b'AA:BB:CC:DD:EE:FF,"Lab",1,-20,1,no,0\n'
        stream = ScriptedStream(
            [
                successful_store(2),
                PRIMARY_SOURCE,
                b"UNATT_CSV_BEGIN\t2\n",
                HEADER,
                row,
                row,
                b"UNATT_CSV_END\n",
            ]
        )

        with self.assertRaisesRegex(unattended_dump.ProtocolError, "repeats a BSSID"):
            unattended_dump.run_protocol(stream)

    def test_rejects_end_marker_before_begin(self):
        stream = ScriptedStream([successful_store(), PRIMARY_SOURCE, b"UNATT_CSV_END\n"])

        with self.assertRaisesRegex(unattended_dump.ProtocolError, "before begin"):
            unattended_dump.run_protocol(stream)

    def test_rejects_nested_begin_marker(self):
        stream = ScriptedStream(
            [
                successful_store(),
                PRIMARY_SOURCE,
                b"UNATT_CSV_BEGIN\t0\n",
                b"UNATT_CSV_BEGIN\t0\n",
            ]
        )

        with self.assertRaisesRegex(unattended_dump.ProtocolError, "nested"):
            unattended_dump.run_protocol(stream)

    def test_times_out_if_store_never_arrives(self):
        stream = ScriptedStream([b"UNATT_STATUS\tch=1\taps=0\n"])

        with self.assertRaisesRegex(unattended_dump.ProtocolError, "UNATT_STORE"):
            unattended_dump.run_protocol(stream)

        self.assertEqual(stream.writes, [b"FLUSH\n"])

    def test_rejects_non_utf8_serial_text(self):
        stream = ScriptedStream([b"\xff\n"])

        with self.assertRaisesRegex(unattended_dump.ProtocolError, "non-UTF-8"):
            unattended_dump.run_protocol(stream)


class AtomicWriteTests(unittest.TestCase):
    def test_success_replaces_existing_file_without_temp_residue(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "inventory.csv"
            target.write_text("old\n", encoding="utf-8")

            unattended_dump.atomic_write_text(target, "new\n")

            self.assertEqual(target.read_text(encoding="utf-8"), "new\n")
            self.assertEqual(list(Path(directory).glob(".inventory.csv.*.tmp")), [])

    def test_validation_failure_leaves_existing_file_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "inventory.csv"
            target.write_text("trusted previous capture\n", encoding="utf-8")
            stream = ScriptedStream(
                [
                    successful_store(),
                    PRIMARY_SOURCE,
                    b"UNATT_CSV_BEGIN\t2\n",
                    HEADER,
                    b"UNATT_CSV_END\n",
                ]
            )

            with self.assertRaises(unattended_dump.ProtocolError):
                result = unattended_dump.run_protocol(stream)
                unattended_dump.atomic_write_text(target, result.csv_text)

            self.assertEqual(
                target.read_text(encoding="utf-8"), "trusted previous capture\n"
            )


class PosixSerialStreamTests(unittest.TestCase):
    def test_reads_partial_lines_and_writes_over_a_pseudo_terminal(self):
        master_fd, slave_fd = pty.openpty()
        slave_path = os.ttyname(slave_fd)
        try:
            with unattended_dump.PosixSerialStream(slave_path) as stream:
                os.write(master_fd, b"UNATT_")
                self.assertIsNone(stream.readline(0.01))
                os.write(master_fd, b"READY\r\n")
                self.assertEqual(stream.readline(1.0), b"UNATT_READY\r\n")

                stream.write(b"FLUSH\n")
                readable, _, _ = select.select([master_fd], [], [], 1.0)
                self.assertEqual(readable, [master_fd])
                self.assertEqual(os.read(master_fd, 64), b"FLUSH\n")
        finally:
            os.close(slave_fd)
            os.close(master_fd)


class ParserTests(unittest.TestCase):
    def test_begin_marker_is_exact_and_decimal(self):
        self.assertEqual(unattended_dump.parse_begin_marker("UNATT_CSV_BEGIN\t12"), 12)
        for invalid in (
            "UNATT_CSV_BEGIN",
            "UNATT_CSV_BEGIN 12",
            "UNATT_CSV_BEGIN\t-1",
            "UNATT_CSV_BEGIN\t00",
            "UNATT_CSV_BEGIN\t121",
            "UNATT_CSV_BEGIN\t1 extra",
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(unattended_dump.ProtocolError):
                    unattended_dump.parse_begin_marker(invalid)

    def test_source_accepts_each_exact_runtime_volatile_recovery_state(self):
        for recovery in (
            "csv-metadata-failed",
            "generation-exhausted",
            "db-csv-divergence",
            "database-write-failed",
        ):
            with self.subTest(recovery=recovery):
                source = (
                    "UNATT_DUMP_SOURCE\tvolatile-ram-read-only\t"
                    f"committed_generation=7\trecovery={recovery}"
                )
                self.assertEqual(
                    unattended_dump.parse_dump_source(source),
                    "volatile-ram-read-only",
                )

    def test_store_requires_unique_success_fields(self):
        for invalid in (
            "UNATT_STORE\taps=1\tdb=ok",
            "UNATT_STORE\taps=nope\tdb=ok\tcsv=ok",
            "UNATT_STORE\taps=1\taps=2\tdb=ok\tcsv=ok",
            "UNATT_STORE\taps=121\tdb=ok\tcsv=ok\tcapacity_drops=0",
            "UNATT_STORE\taps=1\tdb=ok\tcsv=ok\tcapacity_drops=4294967296",
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(unattended_dump.ProtocolError):
                    unattended_dump.parse_store_line(invalid)


if __name__ == "__main__":
    unittest.main()
