from contextlib import closing, redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from astra.archive import Archive, SCHEMA_VERSION
from astra.memory_backup import create_snapshot, main, verify_snapshot


class MemoryBackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "source memory #1.sqlite"
        self.destination = self.root / "snapshot.sqlite"
        self.archive = Archive(self.source)

    def test_snapshot_recovery_preserves_all_records_metadata_and_sequence(self):
        first = self.archive.remember("A retained note")
        self.archive.edit(first["id"], "Approved revision", revision=1, source="Lab notebook")
        self.archive.remember("A fictional event", kind="fiction")
        deleted = self.archive.remember("Deleted entry")
        self.archive.forget(deleted["id"])
        original = self.archive.list_memories()
        result = create_snapshot(self.source, self.destination)
        self.assertEqual(
            {"schema_version": SCHEMA_VERSION, "memory_count": 2, "integrity": "ok"},
            result,
        )
        self.assertEqual(result, verify_snapshot(self.destination))
        recovered = Archive(self.destination)
        self.assertEqual(original, recovered.list_memories())
        self.assertEqual(deleted["id"] + 1, recovered.remember("After recovery")["id"])
        self.assertEqual(original, self.archive.list_memories())

    def test_snapshot_reads_committed_wal_data(self):
        with closing(sqlite3.connect(self.source)) as writer:
            writer.execute("PRAGMA journal_mode = WAL")
            writer.execute("BEGIN")
            writer.execute("SELECT COUNT(*) FROM memories").fetchone()
            memory = self.archive.remember("Committed while WAL is active")
            self.assertTrue(Path(str(self.source) + "-wal").exists())
            create_snapshot(self.source, self.destination)
        self.assertEqual([memory], Archive(self.destination).list_memories())

    def test_existing_destination_is_never_overwritten(self):
        self.destination.write_bytes(b"Keep this exact file")
        original = self.source.read_bytes()
        with self.assertRaises(FileExistsError):
            create_snapshot(self.source, self.destination)
        self.assertEqual(b"Keep this exact file", self.destination.read_bytes())
        self.assertEqual(original, self.source.read_bytes())
        with self.assertRaises(FileExistsError):
            create_snapshot(self.source, self.source)
        self.assertEqual(original, self.source.read_bytes())

    def test_missing_source_and_parent_are_not_created(self):
        missing = self.root / "missing.sqlite"
        with self.assertRaises(sqlite3.OperationalError):
            create_snapshot(missing, self.destination)
        self.assertFalse(missing.exists())
        self.assertFalse(self.destination.exists())
        with self.assertRaises(FileNotFoundError):
            create_snapshot(self.source, self.root / "missing" / "out.sqlite")

    def test_failed_verification_removes_only_new_destination(self):
        with patch("astra.memory_backup.verify_snapshot", side_effect=ValueError("Invalid snapshot")):
            with self.assertRaisesRegex(ValueError, "Invalid snapshot"):
                create_snapshot(self.source, self.destination)
        self.assertFalse(self.destination.exists())
        self.assertTrue(self.source.exists())
        self.assertEqual({"source memory #1.sqlite"}, {p.name for p in self.root.iterdir()})

    def test_copy_deadline_cleans_up_destination(self):
        with patch("astra.memory_backup.time.monotonic", side_effect=[0, 31]):
            with self.assertRaisesRegex(TimeoutError, "deadline"):
                create_snapshot(self.source, self.destination)
        self.assertFalse(self.destination.exists())
        self.assertEqual([], self.archive.list_memories())

    def test_verify_is_read_only_and_refuses_unsupported_schema(self):
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute("PRAGMA user_version = 999")
        original = self.source.read_bytes()
        with self.assertRaisesRegex(ValueError, "Expected memory schema"):
            verify_snapshot(self.source)
        self.assertEqual(original, self.source.read_bytes())
        with self.assertRaisesRegex(ValueError, "Expected memory schema"):
            create_snapshot(self.source, self.destination)
        self.assertFalse(self.destination.exists())
        self.assertEqual(original, self.source.read_bytes())

    def test_corruption_and_incomplete_schema_are_rejected(self):
        corrupt = self.root / "corrupt.sqlite"
        corrupt.write_bytes(b"Not a SQLite database")
        with self.assertRaises(sqlite3.DatabaseError):
            verify_snapshot(corrupt)
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute("ALTER TABLE memories RENAME TO not_memories")
        with self.assertRaisesRegex(ValueError, "Incomplete memory schema"):
            verify_snapshot(self.source)

    def test_verify_missing_file_does_not_create_it(self):
        with self.assertRaises(sqlite3.OperationalError):
            verify_snapshot(self.destination)
        self.assertFalse(self.destination.exists())

    @unittest.skipIf(os.name == "nt", "POSIX mode assertions")
    def test_snapshot_requests_private_file_mode(self):
        create_snapshot(self.source, self.destination)
        self.assertEqual(0o600, self.destination.stat().st_mode & 0o777)

    def test_cli_reports_errors_without_success_payload(self):
        output, error = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            result = main(["verify", str(self.destination)])
        self.assertEqual(1, result)
        self.assertEqual("", output.getvalue())
        self.assertIn("Memory verify failed:", error.getvalue())

    def test_module_cli_snapshot_and_verify(self):
        self.archive.remember("CLI retained note")
        for arguments in (
            ["snapshot", str(self.source), str(self.destination)],
            ["verify", str(self.destination)],
        ):
            result = subprocess.run(
                [sys.executable, "-m", "astra.memory_backup", *arguments],
                cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(1, json.loads(result.stdout)["memory_count"])
            self.assertEqual("", result.stderr)


if __name__ == "__main__":
    unittest.main()
