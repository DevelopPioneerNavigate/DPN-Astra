from contextlib import closing
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from threading import Event, Thread
import time
import unittest
from unittest.mock import Mock, patch

from astra.archive import Archive
from astra.core import AstraCore
from astra.drafting_tasks import DraftingTasks
from astra.inference import ModelError
from astra.project_audit import ProjectAuditLog, SCHEMA_VERSION
from astra.project_drafting import ProjectDrafting
from astra.repository import RepositoryContext
from astra.server import make_handler


class TaskCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "project"
        self.root.mkdir()
        self.target = self.root / "sample.py"
        self.target.write_bytes(b"value = 1\r\n")
        self.audit_path = Path(self.tmp.name) / "audit.sqlite"
        self.repository = RepositoryContext(self.root, audit_path=self.audit_path)
        self.provider = Mock()
        self.provider.name = "Fixture"
        self.provider.model = "not-a-live-model"
        self.provider.chat.return_value = json.dumps({
            "files": [{"path": "sample.py", "content": "value = 2\n"}],
        })
        self.tasks = DraftingTasks(ProjectDrafting(self.provider, self.repository))
        store = self.repository.changes.audit_store
        assert store is not None
        self.store = store

    def start(self):
        return self.tasks.start(["sample.py"], "change value", confirmed=True)

    def wait(self, task_id):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = self.tasks.status(task_id)
            if not result["worker_active"]:
                return result
            time.sleep(.005)
        self.fail("Worker did not exit")

    def restarted(self):
        return DraftingTasks(ProjectDrafting(
            self.provider, RepositoryContext(self.root, audit_path=self.audit_path),
        ))

    def blocked(self):
        entered, release = Event(), Event()
        answer = self.provider.chat.return_value

        def inference(_messages, *, json_mode=False):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("Test did not release provider")
            return answer

        self.provider.chat.side_effect = inference
        task = self.start()
        self.addCleanup(lambda: self.wait(task["id"]))
        self.addCleanup(release.set)
        self.assertTrue(entered.wait(2))
        return task, release

    def test_completed_metadata_survives_restart_without_restoring_task(self):
        result = self.wait(self.start()["id"])
        self.assertTrue(result["checkpoint_enabled"])
        self.assertIsNone(result["checkpoint_error"])
        self.assertFalse(result["durable"])
        report = self.tasks.history()
        self.assertTrue(report["durable"])
        record = report["checkpoints"][0]
        self.assertEqual("current_process", record["continuity"])
        self.assertEqual("completed", record["state"])
        self.assertEqual("review_staged", record["outcome_code"])
        self.assertEqual([entry["proposal"]["id"] for entry in result["result"]["changes"]], record["proposal_ids"])
        restarted = self.restarted()
        record = restarted.history()["checkpoints"][0]
        self.assertEqual("historical_finished", record["continuity"])
        self.assertFalse(record["task_available"])
        self.assertTrue(record["metadata_only"])
        self.assertFalse(record["recorded_worker_active"])
        self.assertNotIn("result", record)
        self.assertNotIn("owner_id", record)
        self.assertEqual([], restarted.list_tasks()["tasks"])
        with self.assertRaises(LookupError):
            restarted.status(result["id"])
        with self.assertRaises(LookupError):
            restarted.cancel(result["id"], confirmed=True)
        self.assertEqual({}, restarted.drafting.repository.changes._proposals)
        self.assertEqual(b"value = 1\r\n", self.target.read_bytes())

    def test_unobserved_worker_is_not_declared_stopped_or_resumed(self):
        task, release = self.blocked()
        restarted = self.restarted()
        record = restarted.history()["checkpoints"][0]
        self.assertEqual("inference", record["state"])
        self.assertEqual("historical_unverified", record["continuity"])
        self.assertTrue(record["recorded_worker_active"])
        self.assertNotIn("worker_active", record)
        self.assertEqual([], restarted.list_tasks()["tasks"])
        self.provider.chat.assert_called_once()
        self.tasks.cancel(task["id"], confirmed=True)
        record = restarted.history()["checkpoints"][0]
        self.assertEqual("cancel_requested", record["state"])
        self.assertEqual("historical_unverified", record["continuity"])
        release.set()
        self.wait(task["id"])
        record = restarted.history()["checkpoints"][0]
        self.assertEqual("cancelled", record["state"])
        self.assertEqual("historical_finished", record["continuity"])

    def test_queued_checkpoint_reopens_as_unknown_without_spawning(self):
        with patch("astra.drafting_tasks.Thread") as thread:
            task = self.start()
        self.provider.chat.assert_not_called()
        with patch("astra.drafting_tasks.Thread") as restart_thread:
            restarted = self.restarted()
            record = restarted.history()["checkpoints"][0]
        thread.assert_called_once()
        restart_thread.assert_not_called()
        self.assertEqual("queued", record["state"])
        self.assertEqual("historical_unverified", record["continuity"])
        self.assertEqual(task["id"], record["task_id"])

    def test_timeout_and_failure_outcomes_are_persisted_without_exception_text(self):
        task, release = self.blocked()
        self.tasks._tasks[task["id"]].started -= 121
        self.assertEqual("timed_out", self.tasks.status(task["id"])["state"])
        self.assertTrue(self.tasks.history()["checkpoints"][0]["recorded_worker_active"])
        release.set()
        self.wait(task["id"])
        record = self.restarted().history()["checkpoints"][0]
        self.assertEqual("budget_exhausted", record["outcome_code"])
        self.assertFalse(record["recorded_worker_active"])
        marker = "exception text must not be persisted"
        self.provider.chat.side_effect = ModelError(marker)
        with self.assertLogs("astra.drafting_tasks", level="ERROR"):
            result = self.wait(self.start()["id"])
        self.assertIn(marker, result["error"])
        record = self.restarted().history()["checkpoints"][0]
        self.assertEqual("failed", record["state"])
        self.assertEqual("draft_failed", record["outcome_code"])
        self.assertNotIn(marker, json.dumps(record))

    def test_sqlite_contains_only_allowlisted_metadata(self):
        instruction = "INSTRUCTION_SENTINEL_DO_NOT_STORE"
        self.target.write_text('value = "SOURCE_SENTINEL_DO_NOT_STORE"\n')
        self.provider.chat.return_value = json.dumps({
            "files": [{"path": "sample.py", "content": 'value = "OUTPUT_SENTINEL_DO_NOT_STORE"\n'}],
        })
        task = self.tasks.start(["sample.py"], instruction, confirmed=True)
        self.assertEqual("completed", self.wait(task["id"])["state"])
        with closing(sqlite3.connect(self.audit_path)) as db:
            dump = "\n".join(db.iterdump())
            columns = [row[1] for row in db.execute("PRAGMA table_info(drafting_task_checkpoints)")]
        for marker in (instruction, "SOURCE_SENTINEL_DO_NOT_STORE", "OUTPUT_SENTINEL_DO_NOT_STORE"):
            self.assertNotIn(marker, dump)
        self.assertEqual([
            "sequence", "project_key", "task_id", "owner_id", "paths_json", "created_at",
            "updated_at", "budget_seconds", "state", "worker_active", "outcome_code", "proposal_ids_json",
        ], columns)
        self.assertTrue(self.repository.changes.audit_log()["integrity"]["valid"])

    def test_project_isolation_and_memory_only_compatibility(self):
        self.wait(self.start()["id"])
        other_root = Path(self.tmp.name) / "other"
        other_root.mkdir()
        other = DraftingTasks(ProjectDrafting(
            self.provider, RepositoryContext(other_root, audit_path=self.audit_path),
        ))
        self.assertEqual(0, other.history()["total"])
        memory_only = DraftingTasks(ProjectDrafting(self.provider, RepositoryContext(self.root)))
        self.assertEqual({
            "checkpoints": [], "total": 0, "truncated": False, "durable": False,
        }, memory_only.history())

    def test_initial_checkpoint_failure_prevents_provider_and_worker(self):
        with patch.object(self.store, "save_task_checkpoint", side_effect=sqlite3.OperationalError("fixture")):
            with self.assertLogs("astra.drafting_tasks", level="ERROR"), patch("astra.drafting_tasks.Thread") as thread:
                with self.assertRaises(sqlite3.OperationalError):
                    self.start()
                thread.assert_not_called()
        self.provider.chat.assert_not_called()
        self.assertEqual([], self.tasks.list_tasks()["tasks"])
        self.assertEqual(0, self.tasks.history()["total"])

    def test_phase_checkpoint_failure_prevents_disclosure(self):
        save = self.store.save_task_checkpoint

        def fail_preparing(**kwargs):
            if kwargs["state"] == "preparing":
                raise sqlite3.OperationalError("fixture phase")
            save(**kwargs)

        with patch.object(self.store, "save_task_checkpoint", side_effect=fail_preparing):
            with self.assertLogs("astra.drafting_tasks", level="ERROR"):
                result = self.wait(self.start()["id"])
        self.assertEqual("failed", result["state"])
        self.provider.chat.assert_not_called()
        self.assertEqual("failed", self.tasks.history()["checkpoints"][0]["state"])

    def test_slow_checkpoint_write_cannot_send_source_after_budget_expires(self):
        save = self.store.save_task_checkpoint

        def delayed_inference_checkpoint(**kwargs):
            save(**kwargs)
            if kwargs["state"] == "inference":
                self.tasks._tasks[kwargs["task_id"]].started -= 121

        with patch.object(self.store, "save_task_checkpoint", side_effect=delayed_inference_checkpoint):
            result = self.wait(self.start()["id"])
        self.assertEqual("timed_out", result["state"])
        self.provider.chat.assert_not_called()
        self.assertEqual({}, self.repository.changes._proposals)
        self.assertEqual("timed_out", self.restarted().history()["checkpoints"][0]["state"])

    def test_abrupt_process_exit_leaves_unverified_metadata_without_resumption(self):
        script = """
import os
import sys
from pathlib import Path
from threading import Event
from astra.repository import RepositoryContext
from astra.project_drafting import ProjectDrafting
from astra.drafting_tasks import DraftingTasks

entered = Event()
class Provider:
    name = "Process-exit test fixture"
    model = "no-live-model"
    def chat(self, messages, *, json_mode=False):
        entered.set()
        Event().wait(60)

tasks = DraftingTasks(ProjectDrafting(
    Provider(), RepositoryContext(Path(sys.argv[1]), audit_path=Path(sys.argv[2])),
))
task = tasks.start(["sample.py"], "controlled interrupted draft", confirmed=True)
if not entered.wait(5):
    os._exit(2)
print(task["id"], flush=True)
os._exit(0)
"""
        child = subprocess.run(
            [sys.executable, "-c", script, str(self.root), str(self.audit_path)],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(0, child.returncode, child.stderr)
        task_id = child.stdout.strip()
        self.assertTrue(task_id)
        restarted = self.restarted()
        record = restarted.history()["checkpoints"][0]
        self.assertEqual(task_id, record["task_id"])
        self.assertEqual("inference", record["state"])
        self.assertEqual("historical_unverified", record["continuity"])
        self.assertEqual([], restarted.list_tasks()["tasks"])
        self.assertEqual({}, restarted.drafting.repository.changes._proposals)
        self.provider.chat.assert_not_called()
        self.assertEqual(b"value = 1\r\n", self.target.read_bytes())

    def test_staging_checkpoint_failure_prevents_actionable_proposals(self):
        save = self.store.save_task_checkpoint

        def fail_staging(**kwargs):
            if kwargs["state"] == "staging":
                raise sqlite3.OperationalError("fixture staging")
            save(**kwargs)

        with patch.object(self.store, "save_task_checkpoint", side_effect=fail_staging):
            with self.assertLogs("astra.drafting_tasks", level="ERROR"):
                result = self.wait(self.start()["id"])
        self.assertEqual("failed", result["state"])
        self.assertEqual({}, self.repository.changes._proposals)
        self.assertEqual([], self.repository.changes.audit_log()["events"])
        self.assertEqual(b"value = 1\r\n", self.target.read_bytes())

    def test_terminal_checkpoint_failure_preserves_completed_live_result(self):
        save = self.store.save_task_checkpoint

        def fail_completed(**kwargs):
            if kwargs["state"] == "completed":
                raise sqlite3.OperationalError("fixture terminal")
            save(**kwargs)

        with patch.object(self.store, "save_task_checkpoint", side_effect=fail_completed):
            with self.assertLogs("astra.drafting_tasks", level="ERROR"):
                result = self.wait(self.start()["id"])
        self.assertEqual("completed", result["state"])
        self.assertIn("storage unavailable", result["checkpoint_error"])
        self.assertEqual(1, len(result["result"]["changes"]))
        self.assertEqual(1, len(self.repository.changes._proposals))
        record = self.restarted().history()["checkpoints"][0]
        self.assertEqual("staging", record["state"])
        self.assertEqual("historical_unverified", record["continuity"])
        self.assertEqual([], record["proposal_ids"])
        self.assertEqual(b"value = 1\r\n", self.target.read_bytes())

    def test_cancel_still_blocks_staging_when_checkpoint_storage_fails(self):
        task, release = self.blocked()
        with patch.object(self.store, "save_task_checkpoint", side_effect=sqlite3.OperationalError("fixture")):
            with self.assertLogs("astra.drafting_tasks", level="ERROR"):
                cancelled = self.tasks.cancel(task["id"], confirmed=True)
                self.assertEqual("cancel_requested", cancelled["state"])
                self.assertIn("storage unavailable", cancelled["checkpoint_error"])
                release.set()
                result = self.wait(task["id"])
        self.assertEqual("cancelled", result["state"])
        self.assertIn("storage unavailable", result["checkpoint_error"])
        self.assertEqual({}, self.repository.changes._proposals)
        self.assertEqual("historical_unverified", self.restarted().history()["checkpoints"][0]["continuity"])

    def test_history_bound_order_count_and_inactive_task_eviction(self):
        result = self.wait(self.start()["id"])
        self.tasks._tasks.clear()
        self.assertEqual("historical_finished", self.tasks.history()["checkpoints"][0]["continuity"])
        for index in range(101):
            self.store.save_task_checkpoint(
                task_id=f"fixture-{index}", owner_id="previous-process", paths=["sample.py"],
                created_at="2026-10-30T00:00:00+00:00", budget_seconds=120,
                state="failed", worker_active=False, outcome_code="draft_failed", proposal_ids=[],
            )
        report = self.tasks.history()
        self.assertEqual(102, report["total"])
        self.assertEqual(100, len(report["checkpoints"]))
        self.assertTrue(report["truncated"])
        self.assertEqual("fixture-100", report["checkpoints"][0]["task_id"])
        for raw in ("0", "101", "true", "null", '"1"'):
            limit = json.loads(raw)
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                self.store.task_checkpoints(limit)
        self.assertNotEqual(result["id"], report["checkpoints"][0]["task_id"])

    def test_migration_preserves_v2_events_and_reviews(self):
        proposal = self.repository.changes.propose("sample.py", "value = 2\n")
        events = self.repository.changes.audit_log()["events"]
        reviews = self.repository.changes.review_checkpoints()["checkpoints"]
        with closing(sqlite3.connect(self.audit_path)) as db, db:
            db.execute("DROP TABLE drafting_task_checkpoints")
            db.execute("PRAGMA user_version=2")
        restarted = self.restarted()
        self.assertEqual(events, restarted.drafting.repository.changes.audit_log()["events"])
        self.assertTrue(restarted.drafting.repository.changes.audit_log()["integrity"]["valid"])
        stored_review = restarted.drafting.repository.changes.review_checkpoints()["checkpoints"][0]
        self.assertEqual(proposal["id"], stored_review["proposal_id"])
        self.assertEqual(reviews[0]["sequence"], stored_review["sequence"])
        self.assertEqual(0, restarted.history()["total"])
        with closing(sqlite3.connect(self.audit_path)) as db:
            self.assertEqual(SCHEMA_VERSION, db.execute("PRAGMA user_version").fetchone()[0])

    def test_migration_failure_leaves_version_and_existing_history(self):
        self.repository.changes.propose("sample.py", "value = 2\n")
        with closing(sqlite3.connect(self.audit_path)) as db, db:
            db.execute("DROP TABLE drafting_task_checkpoints")
            db.execute("CREATE TABLE drafting_task_checkpoints (invalid TEXT)")
            db.execute("PRAGMA user_version=2")
        with self.assertRaises(sqlite3.OperationalError):
            ProjectAuditLog(self.audit_path)
        with closing(sqlite3.connect(self.audit_path)) as db:
            self.assertEqual(2, db.execute("PRAGMA user_version").fetchone()[0])
            self.assertEqual(1, db.execute("SELECT COUNT(*) FROM project_change_events").fetchone()[0])
            self.assertEqual(["invalid"], [row[1] for row in db.execute("PRAGMA table_info(drafting_task_checkpoints)")])

    def test_worker_start_failure_persists_failed_outcome(self):
        with patch("astra.drafting_tasks.Thread.start", side_effect=RuntimeError("fixture")):
            with self.assertRaises(ModelError):
                self.start()
        record = self.restarted().history()["checkpoints"][0]
        self.assertEqual("failed", record["state"])
        self.assertFalse(record["recorded_worker_active"])
        self.assertEqual("worker_start_failed", record["outcome_code"])

    def server(self, repository):
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0), make_handler(AstraCore(
                self.provider, Archive(Path(self.tmp.name) / "memory.sqlite"), repository,
            )),
        )
        thread = Thread(target=server.serve_forever)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        return server

    def get(self, server, *, host=None):
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        try:
            connection.request("GET", "/api/project/draft-task-history", headers={"Host": host} if host else {})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_http_restart_history_and_guards(self):
        self.wait(self.start()["id"])
        server = self.server(RepositoryContext(self.root, audit_path=self.audit_path))
        code, result = self.get(server)
        self.assertEqual(200, code)
        self.assertTrue(result["durable"])
        self.assertEqual("historical_finished", result["checkpoints"][0]["continuity"])
        self.assertEqual(403, self.get(server, host="untrusted.invalid")[0])
        self.assertEqual(404, self.get(self.server(None))[0])
        self.assertEqual(1, self.provider.chat.call_count)
        for exception in (sqlite3.OperationalError("fixture"), ValueError("invalid metadata")):
            with patch("astra.project_audit.ProjectAuditLog.task_checkpoints", side_effect=exception):
                code, result = self.get(server)
            self.assertEqual(503, code)
            self.assertIn("unavailable or invalid", result["error"])
            self.assertNotIn("checkpoints", result)

    def test_malformed_stored_metadata_is_not_success_shaped(self):
        self.wait(self.start()["id"])
        server = self.server(self.repository)
        for paths in ('{"not":"a list"}', 'not json', '[""]', '[1]'):
            with closing(sqlite3.connect(self.audit_path)) as db, db:
                db.execute("UPDATE drafting_task_checkpoints SET paths_json=?", (paths,))
            code, result = self.get(server)
            self.assertEqual(503, code)
            self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
