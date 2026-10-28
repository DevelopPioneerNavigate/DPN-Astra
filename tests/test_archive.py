from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import tempfile
from threading import Thread
import unittest
from unittest.mock import Mock, patch

from astra.archive import (
    Archive, MEMORY_KINDS, SCHEMA_VERSION, MemoryConflict, MemoryNotFound, MemoryExportTooLarge,
)
from astra.core import AstraCore
from astra.server import make_handler


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "memory.sqlite"

    def create_legacy_database(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "CREATE TABLE memories (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "note TEXT NOT NULL, created_at TEXT NOT NULL)"
            )
            db.execute(
                "INSERT INTO memories VALUES (?, ?, ?)",
                (17, "Original note", "2024-01-02T03:04:05+00:00"),
            )

    def test_migration_preserves_legacy_data_and_does_not_invent_consent(self):
        self.create_legacy_database()
        archive = Archive(self.path)

        self.assertEqual(
            [{
                "id": 17,
                "note": "Original note",
                "created_at": "2024-01-02T03:04:05+00:00",
                "kind": "note",
                "provenance": "legacy_user_note",
                "source": "",
                "consented_at": None,
                "revision": 1,
                "updated_at": None,
            }],
            archive.list_memories(),
        )
        self.assertEqual(archive.list_memories(), Archive(self.path).list_memories())
        self.assertEqual(18, archive.remember("A new note")["id"])
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(SCHEMA_VERSION, db.execute("PRAGMA user_version").fetchone()[0])

    def test_failed_migration_rolls_back_columns_data_and_version(self):
        self.create_legacy_database()

        class FailingConnection(sqlite3.Connection):
            def execute(self, sql, parameters=()):
                if "ADD COLUMN source" in sql:
                    raise sqlite3.OperationalError("Injected migration failure")
                return super().execute(sql, parameters)

        def connect(_archive):
            db = sqlite3.connect(self.path, factory=FailingConnection)
            db.row_factory = sqlite3.Row
            return db

        with patch.object(Archive, "_connect", connect):
            with self.assertRaisesRegex(sqlite3.OperationalError, "Injected"):
                Archive(self.path)
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(0, db.execute("PRAGMA user_version").fetchone()[0])
            self.assertEqual(
                ["id", "note", "created_at"],
                [row[1] for row in db.execute("PRAGMA table_info(memories)")],
            )
            self.assertEqual("Original note", db.execute("SELECT note FROM memories").fetchone()[0])
        self.assertEqual(1, len(Archive(self.path).list_memories()))

    def test_newer_schema_is_rejected_without_changing_database(self):
        self.create_legacy_database()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("PRAGMA user_version = 999")
        original = self.path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "Unsupported memory schema version: 999"):
            Archive(self.path)
        self.assertEqual(original, self.path.read_bytes())

    def test_incomplete_versioned_schema_is_rejected(self):
        self.create_legacy_database()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            Archive(self.path)

    def test_concurrent_initialization_migrates_once(self):
        self.create_legacy_database()
        with ThreadPoolExecutor(max_workers=2) as pool:
            archives = list(pool.map(lambda _: Archive(self.path), range(2)))
        self.assertEqual(archives[0].list_memories(), archives[1].list_memories())

    def test_version_one_migration_preserves_metadata(self):
        self.create_legacy_database()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("ALTER TABLE memories ADD COLUMN kind TEXT NOT NULL DEFAULT 'hypothesis'")
            db.execute("ALTER TABLE memories ADD COLUMN provenance TEXT NOT NULL DEFAULT 'user_entry'")
            db.execute("ALTER TABLE memories ADD COLUMN source TEXT NOT NULL DEFAULT 'notebook'")
            db.execute("ALTER TABLE memories ADD COLUMN consented_at TEXT")
            db.execute("PRAGMA user_version = 1")
        archive = Archive(self.path)
        memory = archive.list_memories()[0]
        self.assertEqual("hypothesis", memory["kind"])
        self.assertEqual("notebook", memory["source"])
        self.assertEqual(1, memory["revision"])
        self.assertIsNone(memory["updated_at"])
        self.assertIsNone(memory["consented_at"])
        self.assertEqual(memory, Archive(self.path).list_memories()[0])

    def test_revision_migration_failure_rolls_back_entire_upgrade(self):
        self.create_legacy_database()

        class FailingConnection(sqlite3.Connection):
            def execute(self, sql, parameters=()):
                if "ADD COLUMN updated_at" in sql:
                    raise sqlite3.OperationalError("Injected revision migration failure")
                return super().execute(sql, parameters)

        def connect(_archive):
            db = sqlite3.connect(self.path, factory=FailingConnection)
            db.row_factory = sqlite3.Row
            return db

        with patch.object(Archive, "_connect", connect):
            with self.assertRaisesRegex(sqlite3.OperationalError, "revision migration"):
                Archive(self.path)
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(0, db.execute("PRAGMA user_version").fetchone()[0])
            self.assertEqual(3, len(db.execute("PRAGMA table_info(memories)").fetchall()))

    def test_edit_preserves_creation_consent_and_increments_revision(self):
        archive = Archive(self.path)
        original = archive.remember("Original", kind="hypothesis", source="Old source")
        edited = archive.edit(original["id"], " Revised ", revision=1, kind="decision", source=" New ")
        self.assertEqual(original["id"], edited["id"])
        self.assertEqual(original["created_at"], edited["created_at"])
        self.assertEqual(original["consented_at"], edited["consented_at"])
        self.assertEqual("Revised", edited["note"])
        self.assertEqual("New", edited["source"])
        self.assertEqual("decision", edited["kind"])
        self.assertEqual("user_edit", edited["provenance"])
        self.assertEqual(2, edited["revision"])
        self.assertIsNotNone(datetime.fromisoformat(edited["updated_at"]).tzinfo)
        self.assertEqual([edited], Archive(self.path).list_memories())

    def test_legacy_edit_records_actual_consent_without_changing_creation(self):
        self.create_legacy_database()
        archive = Archive(self.path)
        edited = archive.edit(17, "Explicitly revised", revision=1)
        self.assertEqual("2024-01-02T03:04:05+00:00", edited["created_at"])
        self.assertEqual(edited["updated_at"], edited["consented_at"])
        self.assertEqual("user_edit", edited["provenance"])

    def test_stale_edit_and_deleted_memory_are_distinct(self):
        archive = Archive(self.path)
        memory = archive.remember("Original")
        edited = archive.edit(memory["id"], "Newer", revision=1)
        with self.assertRaises(MemoryConflict):
            archive.edit(memory["id"], "Stale", revision=1)
        self.assertEqual([edited], archive.list_memories())
        archive.forget(memory["id"])
        with self.assertRaises(MemoryNotFound):
            archive.edit(memory["id"], "Resurrected", revision=2)
        self.assertEqual([], archive.list_memories())

    def test_concurrent_edit_has_exactly_one_winner(self):
        archive = Archive(self.path)
        memory = archive.remember("Original")

        def edit(note):
            try:
                return archive.edit(memory["id"], note, revision=1)["note"]
            except MemoryConflict:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(edit, ("First", "Second")))
        self.assertEqual(1, results.count("conflict"))
        self.assertEqual(2, archive.list_memories()[0]["revision"])

    def test_invalid_edits_leave_memory_unchanged(self):
        archive = Archive(self.path)
        memory = archive.remember("Original")
        for revision in (None, True, 0, -1, "1", 1.0, 9223372036854775807):
            with self.subTest(revision=revision), self.assertRaises(ValueError):
                archive.edit(memory["id"], "Bad revision", revision=revision)
        for fields in ({"note": ""}, {"note": "n", "kind": []}, {"note": "n", "source": None}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                archive.edit(memory["id"], revision=1, **fields)
        self.assertEqual([memory], archive.list_memories())

    def test_edit_commit_failure_rolls_back_content_and_revision(self):
        archive = Archive(self.path)
        original = archive.remember("Original")

        class FailingConnection(sqlite3.Connection):
            def commit(self):
                raise sqlite3.OperationalError("Injected commit failure")

        def connect(_archive):
            db = sqlite3.connect(self.path, factory=FailingConnection)
            db.row_factory = sqlite3.Row
            return db

        with patch.object(Archive, "_connect", connect):
            with self.assertRaisesRegex(sqlite3.OperationalError, "commit failure"):
                archive.edit(original["id"], "Must roll back", revision=1)
        self.assertEqual([original], archive.list_memories())

    def test_category_edit_changes_next_chat_context(self):
        archive = Archive(self.path)
        memory = archive.remember("User retained hypothesis", kind="hypothesis")
        archive.edit(memory["id"], "Canon-only experience", kind="fiction", revision=1)
        provider = Mock()
        provider.chat.return_value = "Fixture"
        AstraCore(provider, archive).chat("Hello")
        prompt = provider.chat.call_args.args[0][0]["content"]
        self.assertNotIn("Canon-only experience", prompt)
        self.assertNotIn("User retained hypothesis", prompt)
        self.assertEqual(1, len(archive.list_memories()))

    def test_cursor_pages_reach_every_memory_without_duplicates(self):
        archive = Archive(self.path)
        for index in range(85):
            archive.remember(str(index))
        page = archive.memory_page()
        self.assertEqual(40, len(page["memories"]))
        newest_id = archive.remember("Inserted between pages")["id"]
        ids = [m["id"] for m in page["memories"]]
        while page["next_before_id"] is not None:
            page = archive.memory_page(before_id=page["next_before_id"])
            ids.extend(m["id"] for m in page["memories"])
        self.assertEqual(list(range(85, 0, -1)), ids)
        self.assertNotIn(newest_id, ids)
        self.assertIsNone(archive.memory_page(before_id=1)["next_before_id"])
        self.assertEqual([], archive.memory_page(before_id=1)["memories"])

    def test_cursor_boundaries_and_last_full_page(self):
        archive = Archive(self.path)
        for value in (0, -1, True, "2", 9223372036854775808):
            with self.subTest(value=value), self.assertRaises(ValueError):
                archive.memory_page(before_id=value)
        for index in range(40):
            archive.remember(str(index))
        self.assertIsNone(archive.memory_page()["next_before_id"])

    def test_all_categories_round_trip_with_user_provenance(self):
        archive = Archive(self.path)
        for kind in MEMORY_KINDS:
            with self.subTest(kind=kind):
                memory = archive.remember("  User entry  ", kind=kind, source="  lab notebook  ")
                self.assertEqual(kind, memory["kind"])
                self.assertEqual("User entry", memory["note"])
                self.assertEqual("lab notebook", memory["source"])
                self.assertEqual("user_entry", memory["provenance"])
                self.assertEqual(memory["created_at"], memory["consented_at"])
                self.assertIsNotNone(datetime.fromisoformat(memory["consented_at"]).tzinfo)
        records = Archive(self.path).list_memories()
        self.assertEqual(set(MEMORY_KINDS), {m["kind"] for m in records})

    def test_invalid_metadata_is_rejected_before_writing(self):
        archive = Archive(self.path)
        for kind in ("verified_fact", "", None, 42, []):
            with self.subTest(kind=kind):
                with self.assertRaisesRegex(ValueError, "kind"):
                    archive.remember("Entry", kind=kind)
        for source in (None, [], 42, "x" * 241):
            with self.subTest(source=source):
                with self.assertRaisesRegex(ValueError, "source"):
                    archive.remember("Entry", source=source)
        self.assertEqual([], archive.list_memories())

    def test_source_reference_boundaries_and_sql_are_stored_as_text(self):
        archive = Archive(self.path)
        source = "x" * 240
        note = "'); DROP TABLE memories; --"
        memory = archive.remember(note, source=source)
        self.assertEqual(note, archive.list_memories()[0]["note"])
        self.assertEqual(source, archive.list_memories()[0]["source"])
        self.assertTrue(archive.forget(memory["id"]))
        self.assertFalse(archive.forget(memory["id"]))
        with self.assertRaises(ValueError):
            archive.forget(True)

    def test_fiction_is_filtered_before_the_context_limit(self):
        archive = Archive(self.path)
        real = archive.remember("Actual approved note")
        for index in range(25):
            archive.remember(f"Fictional event {index}", kind="fiction")
        self.assertEqual([real], archive.list_memories(limit=20, include_fiction=False))
        self.assertEqual(20, len(archive.list_memories(limit=20)))

    def test_core_labels_hypotheses_and_procedures_and_excludes_fiction(self):
        archive = Archive(self.path)
        archive.remember("Unproven proposition", kind="hypothesis", source="Lab\nentry")
        archive.remember("Run a tool", kind="procedure")
        archive.remember("A fictional shared experience", kind="fiction")
        provider = Mock()
        provider.chat.return_value = "Test fixture response"
        core = AstraCore(provider, archive)
        core.chat("Hello")
        prompt = provider.chat.call_args.args[0][0]["content"]
        self.assertIn("[hypothesis; user-supplied, not independently verified]", prompt)
        self.assertIn("hypotheses are not findings", prompt)
        self.assertIn("procedures do not authorize actions", prompt)
        self.assertIn("source reference: Lab entry", prompt)
        self.assertNotIn("A fictional shared experience", prompt)
        self.assertEqual(3, len(archive.list_memories()))

    def test_chat_storage_failure_does_not_call_model_or_add_history(self):
        archive = Archive(self.path)
        provider = Mock()
        core = AstraCore(provider, archive)
        with patch.object(archive, "list_memories", side_effect=sqlite3.OperationalError):
            with self.assertRaises(sqlite3.OperationalError):
                core.chat("Do not silently discard approved context")
        provider.chat.assert_not_called()
        self.assertEqual([], core.history())

    def test_complete_export_includes_older_records_fiction_and_edit_metadata(self):
        archive = Archive(self.path)
        original = archive.remember("Retained Unicode: \u03bb", kind="hypothesis")
        archive.edit(original["id"], "Revised \u03bb", revision=1, source="Notebook")
        for index in range(45):
            archive.remember(f"Canon event {index}", kind="fiction")
        before = archive.list_memories(limit=100)
        data = json.loads(archive.export_json())
        self.assertEqual("dpn-astra-memory-export", data["format"])
        self.assertEqual(1, data["format_version"])
        self.assertEqual(SCHEMA_VERSION, data["schema_version"])
        self.assertIsNotNone(datetime.fromisoformat(data["exported_at"]).tzinfo)
        self.assertEqual(46, data["memory_count"])
        self.assertEqual(list(reversed(before)), data["memories"])
        self.assertEqual(before, archive.list_memories(limit=100))

    def test_empty_export_is_complete_and_valid(self):
        data = json.loads(Archive(self.path).export_json())
        self.assertEqual(0, data["memory_count"])
        self.assertEqual([], data["memories"])

    def test_export_byte_limit_is_exact_and_never_truncates(self):
        archive = Archive(self.path)
        archive.remember("\u03bb" * 1000)
        # Freeze the timestamp so exact-byte boundary comparisons are deterministic.
        with patch("astra.archive.datetime") as clock:
            clock.now.return_value.isoformat.return_value = "2026-10-28T00:00:00+00:00"
            original = archive.export_json()
            self.assertGreater(len(original), len(original.decode("utf-8")))
            with patch("astra.archive.MAX_EXPORT_BYTES", len(original)):
                self.assertEqual(original, archive.export_json())
            with patch("astra.archive.MAX_EXPORT_BYTES", len(original) - 1):
                with self.assertRaises(MemoryExportTooLarge):
                    archive.export_json()
        self.assertEqual(1, len(archive.list_memories()))

    def test_export_entry_limit_is_explicit(self):
        archive = Archive(self.path)
        archive.remember("One")
        archive.remember("Two")
        with patch("astra.archive.MAX_EXPORT_MEMORIES", 2):
            self.assertEqual(2, json.loads(archive.export_json())["memory_count"])
        with patch("astra.archive.MAX_EXPORT_MEMORIES", 1):
            with self.assertRaisesRegex(MemoryExportTooLarge, "entry limit"):
                archive.export_json()

    def test_real_four_mib_export_limit_rejects_complete_oversized_archive(self):
        archive = Archive(self.path)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executemany(
                "INSERT INTO memories (note, created_at) VALUES (?, ?)",
                [("\u03bb" * 1000, "2026-10-28T00:00:00+00:00")] * 2000,
            )
        with self.assertRaisesRegex(MemoryExportTooLarge, "4 MiB"):
            archive.export_json()
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(2000, db.execute("SELECT COUNT(*) FROM memories").fetchone()[0])

    def test_real_ten_thousand_entry_export_boundary(self):
        archive = Archive(self.path)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executemany(
                "INSERT INTO memories (note, created_at) VALUES (?, ?)",
                [("x", "2026-10-28T00:00:00+00:00")] * 10000,
            )
        data = json.loads(archive.export_json())
        self.assertEqual(10000, data["memory_count"])
        self.assertEqual(10000, len(data["memories"]))
        archive.remember("One beyond the allowed entry count")
        with self.assertRaisesRegex(MemoryExportTooLarge, "entry limit"):
            archive.export_json()

    def test_export_count_and_records_share_one_read_snapshot(self):
        archive = Archive(self.path)
        first = archive.remember("Before snapshot")
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("PRAGMA journal_mode = WAL")

        class ConcurrentInsertConnection(sqlite3.Connection):
            def execute(connection, sql, parameters=()):
                if sql.startswith("SELECT id, note"):
                    archive.remember("Committed after export count")
                return super().execute(sql, parameters)

        other = Archive(self.path)

        def connect():
            db = sqlite3.connect(self.path, factory=ConcurrentInsertConnection)
            db.row_factory = sqlite3.Row
            return db

        with patch.object(other, "_connect", connect):
            data = json.loads(other.export_json())
        self.assertEqual(1, data["memory_count"])
        self.assertEqual([first], data["memories"])
        self.assertEqual(2, len(archive.list_memories()))


class ArchiveApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.archive = Archive(Path(self.tmp.name) / "memory.sqlite")
        self.core = AstraCore(Mock(), self.archive)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.core))
        thread = Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path="/api/memories", payload=None, headers=None):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        try:
            connection.request(
                method, path,
                body=json.dumps(payload) if payload is not None else None,
                headers={"Content-Type": "application/json", **(headers or {})},
            )
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_legacy_post_stays_compatible_and_new_metadata_round_trips(self):
        code, result = self.request("POST", payload={"note": "Old API caller"})
        self.assertEqual(201, code)
        self.assertEqual("note", result["memory"]["kind"])
        code, result = self.request("POST", payload={
            "note": "A proposal", "kind": "hypothesis", "source": "notebook",
            "provenance": "verified_sensor", "consented_at": "invented",
        })
        self.assertEqual(201, code)
        memory = result["memory"]
        self.assertEqual("user_entry", memory["provenance"])
        self.assertNotEqual("invented", memory["consented_at"])
        code, result = self.request("GET")
        self.assertEqual(200, code)
        self.assertEqual(memory, result["memories"][0])
        code, result = self.request("DELETE", f"/api/memories/{memory['id']}")
        self.assertEqual((200, {"deleted": True}), (code, result))

    def test_invalid_metadata_returns_400_without_storing(self):
        for payload in (
            {"note": "Entry", "kind": "verified_fact"},
            {"note": "Entry", "kind": []},
            {"note": "Entry", "source": None},
        ):
            with self.subTest(payload=payload):
                code, result = self.request("POST", payload=payload)
                self.assertEqual(400, code)
                self.assertIn("error", result)
        self.assertEqual([], self.archive.list_memories())

    def test_database_failures_are_explicit_http_errors(self):
        for method, operation, path in (
            ("GET", "memory_page", "/api/memories"),
            ("POST", "remember", "/api/memories"),
            ("DELETE", "forget", "/api/memories/1"),
        ):
            with self.subTest(method=method):
                with patch.object(self.archive, operation, side_effect=sqlite3.OperationalError):
                    code, result = self.request(method, path, {"note": "Entry"} if method == "POST" else None)
                    self.assertEqual(503, code)
                    self.assertIn("storage unavailable", result["error"])

    def test_edit_workflow_conflict_validation_and_deletion(self):
        _, result = self.request("POST", payload={"note": "Original"})
        memory = result["memory"]
        path = f"/api/memories/{memory['id']}/edit"
        payload = {"note": "Revised", "kind": "hypothesis", "source": "notebook", "revision": 1}
        code, result = self.request("POST", path, payload)
        self.assertEqual(200, code)
        self.assertEqual(2, result["memory"]["revision"])
        self.assertEqual("Revised", result["memory"]["note"])
        self.assertEqual(409, self.request("POST", path, payload)[0])
        self.assertEqual(400, self.request("POST", path, {"note": "Missing revision"})[0])
        self.assertEqual(400, self.request("POST", path, {**payload, "revision": True})[0])
        self.assertEqual(400, self.request("POST", "/api/memories/9223372036854775808/edit", payload)[0])
        self.assertEqual(404, self.request("POST", path + "/extra", payload)[0])
        self.request("DELETE", f"/api/memories/{memory['id']}")
        self.assertEqual(404, self.request("POST", path, payload)[0])

    def test_http_cursor_pagination_and_invalid_cursors(self):
        for index in range(41):
            self.archive.remember(str(index))
        code, page = self.request("GET")
        self.assertEqual(200, code)
        self.assertEqual(40, len(page["memories"]))
        code, last = self.request("GET", f"/api/memories?before_id={page['next_before_id']}")
        self.assertEqual(200, code)
        self.assertEqual(1, len(last["memories"]))
        self.assertIsNone(last["next_before_id"])
        for query in ("", "0", "-1", "abc", "1.5", "9223372036854775808", "2&before_id=3"):
            with self.subTest(query=query):
                self.assertEqual(400, self.request("GET", "/api/memories?before_id=" + query)[0])

    def test_edit_database_failure_returns_503(self):
        with patch.object(self.archive, "edit", side_effect=sqlite3.OperationalError):
            self.assertEqual(503, self.request(
                "POST", "/api/memories/1/edit", {"note": "Entry", "revision": 1},
            )[0])

    def test_edit_rejects_cross_origin_write(self):
        memory = self.archive.remember("Original")
        code, result = self.request(
            "POST", f"/api/memories/{memory['id']}/edit",
            {"note": "Cross-origin mutation", "revision": 1},
            headers={"Origin": "https://untrusted.invalid"},
        )
        self.assertEqual(403, code)
        self.assertIn("Cross-origin", result["error"])
        self.assertEqual([memory], self.archive.list_memories())

    def test_explicit_export_http_workflow_and_origin_restriction(self):
        for index in range(45):
            self.archive.remember(f"Entry {index}", kind="fiction")
        code, result = self.request("POST", "/api/memories/export", {})
        self.assertEqual(200, code)
        self.assertEqual(45, result["memory_count"])
        self.assertEqual(45, len(result["memories"]))
        self.assertEqual("fiction", result["memories"][0]["kind"])
        self.assertEqual(404, self.request("GET", "/api/memories/export")[0])
        code, result = self.request(
            "POST", "/api/memories/export", {}, headers={"Origin": "https://untrusted.invalid"},
        )
        self.assertEqual(403, code)
        self.assertIn("Cross-origin", result["error"])

    def test_export_http_errors_are_not_partial_successes(self):
        for error, expected in (
            (MemoryExportTooLarge("Limit exceeded"), 413),
            (sqlite3.OperationalError("Database failure"), 503),
        ):
            with self.subTest(error=error):
                with patch.object(self.archive, "export_json", side_effect=error):
                    code, result = self.request("POST", "/api/memories/export", {})
                self.assertEqual(expected, code)
                self.assertIn("error", result)
                self.assertNotIn("memories", result)

    def test_export_response_has_download_and_no_store_headers(self):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        try:
            connection.request(
                "POST", "/api/memories/export", body="{}",
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            body = response.read()
            self.assertEqual(200, response.status)
            self.assertEqual('attachment; filename="astra-memories.json"', response.getheader("Content-Disposition"))
            self.assertEqual("no-store", response.getheader("Cache-Control"))
            self.assertEqual("application/json; charset=utf-8", response.getheader("Content-Type"))
            self.assertEqual(len(body), int(response.getheader("Content-Length")))
            self.assertEqual(0, json.loads(body)["memory_count"])
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
