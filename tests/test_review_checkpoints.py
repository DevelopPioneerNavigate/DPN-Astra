from contextlib import closing
from datetime import datetime, timedelta, timezone
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
from threading import Thread
import unittest
from unittest.mock import Mock, patch

from astra.archive import Archive
from astra.core import AstraCore
from astra.permissions import RepositoryPermissions
from astra.project_audit import ProjectAuditLog, SCHEMA_VERSION
from astra.project_changes import MAX_CHANGE_BYTES, ProjectChanges
from astra.repository import RepositoryContext
from astra.server import make_handler


class ReviewCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "project"
        self.root.mkdir()
        self.target = self.root / "sample.py"
        self.original = b"value = 1\r\n"
        self.target.write_bytes(self.original)
        self.audit_path = Path(self.tmp.name) / "audit.sqlite3"
        self.changes = ProjectChanges(self.root, audit_path=self.audit_path)

    def propose(self):
        return self.changes.propose("sample.py", "value = 2\n")

    def test_reload_recovers_real_review_without_disk_writes(self):
        proposal = self.propose()
        report = self.changes.review_checkpoints()
        self.assertTrue(report["durable"])
        self.assertFalse(report["tests_executed"])
        self.assertFalse(report["atomic_apply"])
        self.assertEqual(1, report["total"])
        self.assertEqual(proposal, report["changes"][0]["proposal"])
        self.assertTrue(report["changes"][0]["recovered"])
        self.assertIsNone(report["changes"][0]["validation"])
        self.assertTrue(report["checkpoints"][0]["recoverable"])
        self.assertEqual(self.original, self.target.read_bytes())
        with self.assertRaises(PermissionError):
            self.changes.apply(proposal["id"])
        self.changes.apply(proposal["id"], confirmed=True)
        self.assertEqual("applied", self.changes.review_checkpoints()["changes"][0]["proposal"]["state"])
        self.changes.rollback(proposal["id"], confirmed=True)
        report = self.changes.review_checkpoints()
        self.assertEqual("rolled_back", report["checkpoints"][0]["state"])
        self.assertEqual(1, report["total"])
        self.assertEqual(self.original, self.target.read_bytes())

    def test_restart_retains_only_metadata_not_authority_or_source(self):
        proposal = self.propose()
        self.changes.apply(proposal["id"], confirmed=True)
        restarted = ProjectChanges(self.root, audit_path=self.audit_path)
        report = restarted.review_checkpoints()
        self.assertEqual([], report["changes"])
        record = report["checkpoints"][0]
        self.assertEqual("applied", record["state"])
        self.assertFalse(record["recoverable"])
        self.assertEqual(proposal["id"], record["proposal_id"])
        for field in ("content", "diff", "original", "replacement", "instruction"):
            self.assertNotIn(field, record)
        with self.assertRaisesRegex(ValueError, "not found"):
            restarted.rollback(proposal["id"], confirmed=True)
        with self.assertRaisesRegex(ValueError, "not found"):
            restarted.apply(proposal["id"], confirmed=True)
        self.assertEqual(b"value = 2\r\n", self.target.read_bytes())

    def test_expiry_retains_record_but_not_actionable_review(self):
        proposal = self.propose()
        self.changes._proposals[proposal["id"]].created_at = datetime.now(timezone.utc) - timedelta(hours=1)
        report = self.changes.review_checkpoints()
        self.assertEqual([], report["changes"])
        self.assertEqual("expired", report["checkpoints"][0]["event"])
        self.assertFalse(report["checkpoints"][0]["recoverable"])
        self.assertEqual(self.original, self.target.read_bytes())

    def test_project_identity_isolates_shared_audit_storage(self):
        self.propose()
        other_root = Path(self.tmp.name) / "other"
        other_root.mkdir()
        (other_root / "sample.py").write_text("value = 3\n")
        other = ProjectChanges(other_root, audit_path=self.audit_path)
        self.assertEqual(0, other.review_checkpoints()["total"])
        other.propose("sample.py", "value = 4\n")
        self.assertEqual(1, other.review_checkpoints()["total"])
        self.assertEqual(1, self.changes.review_checkpoints()["total"])
        self.assertNotEqual(
            self.changes.review_checkpoints()["checkpoints"][0]["original_sha256"],
            other.review_checkpoints()["checkpoints"][0]["original_sha256"],
        )

    def test_no_audit_storage_recovers_only_current_process(self):
        changes = ProjectChanges(self.root)
        proposal = changes.propose("sample.py", "value = 2\n")
        report = changes.review_checkpoints()
        self.assertFalse(report["durable"])
        self.assertEqual(0, report["total"])
        self.assertEqual(proposal, report["changes"][0]["proposal"])
        self.assertEqual([], ProjectChanges(self.root).review_checkpoints()["changes"])

    def test_recovery_does_not_assert_disk_matches_or_bypass_hash_guard(self):
        proposal = self.propose()
        self.target.write_bytes(b"value = 99\n")
        report = self.changes.review_checkpoints()
        self.assertEqual("pending", report["changes"][0]["proposal"]["state"])
        with self.assertRaisesRegex(ValueError, "changed since review"):
            self.changes.apply(proposal["id"], confirmed=True)
        self.assertEqual(b"value = 99\n", self.target.read_bytes())

    def test_checkpoint_storage_failure_rolls_back_audit_append(self):
        with closing(sqlite3.connect(self.audit_path)) as db, db:
            db.execute(
                """CREATE TRIGGER reject_checkpoint BEFORE INSERT ON project_review_checkpoints
                   BEGIN SELECT RAISE(ABORT, 'controlled checkpoint failure'); END"""
            )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "controlled checkpoint"):
            self.propose()
        self.assertEqual([], self.changes.audit_log()["events"])
        self.assertEqual([], self.changes.review_checkpoints()["changes"])
        self.assertEqual(0, self.changes.review_checkpoints()["total"])
        self.assertEqual(self.original, self.target.read_bytes())

    def test_attempted_stage_metadata_never_reconstitutes_failed_set(self):
        (self.root / "other.json").write_text('{"value":1}')
        log = self.changes._audit_log
        append = log.append
        calls = 0

        def append_then_fail(event, proposal_id, proposal):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise sqlite3.OperationalError("controlled append failure")
            return append(event, proposal_id, proposal)

        with patch.object(log, "append", side_effect=append_then_fail):
            with self.assertRaises(sqlite3.OperationalError):
                self.changes.propose_many([
                    {"path": "sample.py", "content": "value = 2\n"},
                    {"path": "other.json", "content": '{"value":2}'},
                ])
        report = self.changes.review_checkpoints()
        self.assertEqual([], report["changes"])
        self.assertEqual(1, report["total"])
        self.assertFalse(report["checkpoints"][0]["recoverable"])

    def test_checkpoint_page_is_bounded_and_reports_total(self):
        log = self.changes._audit_log
        proposal = self.propose()
        source = self.changes._proposals[proposal["id"]]
        for index in range(105):
            log.append("staged", f"fixture-{index}", source)
        report = self.changes.review_checkpoints()
        self.assertEqual(106, report["total"])
        self.assertEqual(100, len(report["checkpoints"]))
        self.assertTrue(report["truncated"])
        self.assertEqual("fixture-104", report["checkpoints"][0]["proposal_id"])
        self.assertEqual(1, len(report["changes"]))
        for invalid in (0, 101, True, None, "1"):
            with self.subTest(limit=invalid), self.assertRaises(ValueError):
                log.checkpoints(invalid)

    def test_v1_migration_preserves_chain_and_does_not_guess_legacy_project(self):
        self.propose()
        before = self.changes.audit_log()["events"]
        with closing(sqlite3.connect(self.audit_path)) as db, db:
            db.execute("DROP TABLE project_review_checkpoints")
            db.execute("PRAGMA user_version=1")
        restarted = ProjectChanges(self.root, audit_path=self.audit_path)
        self.assertEqual(before, restarted.audit_log()["events"])
        self.assertTrue(restarted.audit_log()["integrity"]["valid"])
        self.assertEqual(0, restarted.review_checkpoints()["total"])
        with closing(sqlite3.connect(self.audit_path)) as db, db:
            self.assertEqual(SCHEMA_VERSION, db.execute("PRAGMA user_version").fetchone()[0])

    def test_failed_migration_rolls_back_version_and_tables(self):
        with closing(sqlite3.connect(self.audit_path)) as db, db:
            db.execute("DROP TABLE project_review_checkpoints")
            db.execute("CREATE TABLE project_review_checkpoints (invalid TEXT)")
            db.execute("PRAGMA user_version=1")
        with self.assertRaises(sqlite3.OperationalError):
            ProjectAuditLog(self.audit_path)
        with closing(sqlite3.connect(self.audit_path)) as db, db:
            self.assertEqual(1, db.execute("PRAGMA user_version").fetchone()[0])
            self.assertEqual(["invalid"], [row[1] for row in db.execute("PRAGMA table_info(project_review_checkpoints)")])

    def test_inspection_compares_exact_bytes_without_changing_lifecycle(self):
        proposal = self.propose()
        events = self.changes.audit_log()["events"]
        first = self.changes.inspect_checkpoint(proposal["id"])
        self.assertEqual("matches_original", first["comparison"])
        self.assertEqual(hashlib.sha256(self.original).hexdigest(), first["current_sha256"])
        self.assertEqual("pending", first["recorded_state"])
        self.assertEqual("staged", first["recorded_event"])
        self.assertFalse(first["tests_executed"])
        self.assertFalse(first["mutation_authorized"])
        self.assertIsNotNone(datetime.fromisoformat(first["checked_at"]).tzinfo)
        self.assertEqual(events, self.changes.audit_log()["events"])
        self.changes.apply(proposal["id"], confirmed=True)
        self.assertEqual("matches_proposed", self.changes.inspect_checkpoint(proposal["id"])["comparison"])
        self.changes.rollback(proposal["id"], confirmed=True)
        self.assertEqual("matches_original", self.changes.inspect_checkpoint(proposal["id"])["comparison"])
        self.target.write_bytes(b"value = 1\n")
        result = self.changes.inspect_checkpoint(proposal["id"])
        self.assertEqual("diverged", result["comparison"])
        self.assertEqual("rolled_back", result["recorded_state"])
        self.assertEqual(b"value = 1\n", self.target.read_bytes())
        self.assertEqual({
            "proposal_id", "path", "recorded_event", "recorded_state",
            "original_sha256", "proposed_sha256", "current_sha256", "comparison",
            "checked_at", "tests_executed", "mutation_authorized",
        }, set(result))

    def test_inspection_after_restart_does_not_restore_proposals_or_source(self):
        proposal = self.propose()
        self.changes.apply(proposal["id"], confirmed=True)
        restarted = ProjectChanges(self.root, audit_path=self.audit_path)
        result = restarted.inspect_checkpoint(proposal["id"])
        self.assertEqual("matches_proposed", result["comparison"])
        self.assertEqual({}, restarted._proposals)
        self.assertNotIn("content", result)
        with self.assertRaisesRegex(ValueError, "not found"):
            restarted.rollback(proposal["id"], confirmed=True)
        self.assertEqual(b"value = 2\r\n", self.target.read_bytes())

    def test_inspection_is_scoped_and_not_limited_to_latest_page(self):
        proposal = self.propose()
        source = self.changes._proposals[proposal["id"]]
        for index in range(101):
            self.changes._audit_log.append("staged", f"fixture-{index}", source)
        self.assertNotIn(proposal["id"], [
            row["proposal_id"] for row in self.changes.review_checkpoints()["checkpoints"]
        ])
        self.assertEqual("matches_original", self.changes.inspect_checkpoint(proposal["id"])["comparison"])
        other_root = Path(self.tmp.name) / "other-project"
        other_root.mkdir()
        other = ProjectChanges(other_root, audit_path=self.audit_path)
        with self.assertRaisesRegex(LookupError, "not found"):
            other.inspect_checkpoint(proposal["id"])

    def test_inspection_uses_live_proposal_without_persistent_storage(self):
        changes = ProjectChanges(self.root)
        proposal = changes.propose("sample.py", "value = 2\n")
        result = changes.inspect_checkpoint(proposal["id"])
        self.assertEqual("matches_original", result["comparison"])
        self.assertEqual("pending", result["recorded_state"])
        self.assertIsNone(result["recorded_event"])
        changes._proposals[proposal["id"]].created_at = datetime.now(timezone.utc) - timedelta(hours=1)
        with self.assertRaises(LookupError):
            changes.inspect_checkpoint(proposal["id"])

    def test_inspection_rejects_unknown_or_invalid_id(self):
        for value in (None, 42, "", "x" * 129):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.changes.inspect_checkpoint(value)
        for value in ("unknown", "' OR 1=1 --"):
            with self.subTest(value=value), self.assertRaises(LookupError):
                self.changes.inspect_checkpoint(value)

    def test_inspection_missing_large_or_binary_file_is_explicit_error(self):
        proposal = self.propose()
        self.target.unlink()
        with self.assertRaises(FileNotFoundError):
            self.changes.inspect_checkpoint(proposal["id"])
        self.target.write_bytes(b"x" * (MAX_CHANGE_BYTES + 1))
        with self.assertRaisesRegex(ValueError, "larger than"):
            self.changes.inspect_checkpoint(proposal["id"])
        self.target.write_bytes(b"\xff")
        with self.assertRaisesRegex(ValueError, "UTF-8"):
            self.changes.inspect_checkpoint(proposal["id"])
        self.assertEqual(b"\xff", self.target.read_bytes())

    def test_inspection_rechecks_read_permission(self):
        proposal = self.propose()
        self.changes.permissions = RepositoryPermissions(read=False)
        with self.assertRaisesRegex(PermissionError, "read access is not authorized"):
            self.changes.inspect_checkpoint(proposal["id"])

    def test_tampered_checkpoint_paths_and_hashes_never_bypass_filters(self):
        proposal = self.propose()
        for path in ("../outside.py", ".env", "secret.py", "/absolute.py"):
            with self.subTest(path=path):
                with closing(sqlite3.connect(self.audit_path)) as db, db:
                    db.execute("UPDATE project_review_checkpoints SET path=?", (path,))
                with self.assertRaises(ValueError):
                    self.changes.inspect_checkpoint(proposal["id"])
        with closing(sqlite3.connect(self.audit_path)) as db, db:
            db.execute("UPDATE project_review_checkpoints SET path='sample.py', original_sha256='invalid'")
        with self.assertRaisesRegex(ValueError, "hashes are invalid"):
            self.changes.inspect_checkpoint(proposal["id"])
        self.assertEqual(self.original, self.target.read_bytes())
        self.assertTrue(self.changes.audit_log()["integrity"]["valid"])

    def test_comparison_does_not_claim_recorded_operation_succeeded(self):
        proposal = self.propose()
        self.target.write_bytes(b"value = 2\r\n")
        before = self.changes.audit_log()["events"]
        result = self.changes.inspect_checkpoint(proposal["id"])
        self.assertEqual("pending", result["recorded_state"])
        self.assertEqual("matches_proposed", result["comparison"])
        self.assertEqual(before, self.changes.audit_log()["events"])
        self.assertEqual("pending", self.changes.describe(proposal["id"])["state"])
        with self.assertRaisesRegex(ValueError, "changed since review"):
            self.changes.apply(proposal["id"], confirmed=True)


class ReviewCheckpointApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "project"
        self.root.mkdir()
        (self.root / "sample.py").write_text("value = 1\n")
        self.archive = Archive(Path(self.tmp.name) / "memory.sqlite")
        self.repository = RepositoryContext(self.root, audit_path=Path(self.tmp.name) / "audit.sqlite")
        self.provider = Mock()

    def start_server(self, repository):
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0), make_handler(AstraCore(self.provider, self.archive, repository)),
        )
        thread = Thread(target=server.serve_forever)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        return server

    def get(self, server, *, host=None, path="/api/project/checkpoints"):
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        try:
            connection.request("GET", path, headers={"Host": host} if host else {})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_http_live_recovery_and_restart_metadata_only(self):
        proposal = self.repository.changes.propose("sample.py", "value = 2\n")
        server = self.start_server(self.repository)
        code, result = self.get(server)
        self.assertEqual(200, code)
        self.assertEqual(proposal, result["changes"][0]["proposal"])
        self.assertFalse(result["tests_executed"])
        restarted = RepositoryContext(self.root, audit_path=Path(self.tmp.name) / "audit.sqlite")
        code, result = self.get(self.start_server(restarted))
        self.assertEqual(200, code)
        self.assertEqual([], result["changes"])
        self.assertEqual(1, result["total"])
        self.provider.chat.assert_not_called()

    def test_http_missing_context_host_guard_and_storage_error(self):
        server = self.start_server(self.repository)
        self.assertEqual(403, self.get(server, host="untrusted.invalid")[0])
        with patch.object(self.repository.changes, "review_checkpoints", side_effect=sqlite3.OperationalError("fixture")):
            code, result = self.get(server)
        self.assertEqual(503, code)
        self.assertIn("unavailable", result["error"])
        self.assertEqual(404, self.get(self.start_server(None))[0])

    def test_http_inspection_returns_readonly_comparison_and_exact_errors(self):
        proposal = self.repository.changes.propose("sample.py", "value = 2\n")
        server = self.start_server(self.repository)
        path = "/api/project/checkpoints/" + proposal["id"] + "/inspect"
        code, result = self.get(server, path=path)
        self.assertEqual(200, code)
        self.assertEqual("matches_original", result["inspection"]["comparison"])
        self.assertFalse(result["inspection"]["mutation_authorized"])
        self.assertEqual(403, self.get(server, path=path, host="untrusted.invalid")[0])
        self.assertEqual(404, self.get(self.start_server(None), path=path)[0])
        self.assertEqual(404, self.get(server, path="/api/project/checkpoints/unknown/inspect")[0])
        self.assertEqual(404, self.get(server, path=path + "/extra")[0])
        self.assertEqual(404, self.get(server, path=path.replace("/inspect", "/apply"))[0])
        for exception, expected in (
            (sqlite3.OperationalError("fixture storage"), 503),
            (PermissionError("read denied"), 403),
            (ValueError("not eligible"), 400),
            (FileNotFoundError("missing"), 404),
            (OSError("fixture disk"), 500),
        ):
            with self.subTest(exception=exception), patch.object(
                self.repository.changes, "inspect_checkpoint", side_effect=exception,
            ):
                code, result = self.get(server, path=path)
                self.assertEqual(expected, code)
                self.assertIn("error", result)
                self.assertNotIn("inspection", result)
        self.provider.chat.assert_not_called()
        self.assertEqual("value = 1\n", (self.root / "sample.py").read_text())


if __name__ == "__main__":
    unittest.main()
