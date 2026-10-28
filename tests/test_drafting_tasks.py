from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
from threading import Event, Thread
import time
import unittest
from unittest.mock import Mock, patch

from astra.archive import Archive
from astra.core import AstraCore
from astra.drafting_tasks import DraftingTasks, MAX_TASKS
from astra.inference import ModelError
from astra.project_changes import ProjectDraftConflict
from astra.project_drafting import ProjectDrafting
from astra.repository import RepositoryContext
from astra.server import make_handler


class DraftingTaskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.target = self.root / "sample.py"
        self.target.write_bytes(b"value = 1\r\n")
        self.repository = RepositoryContext(self.root)
        self.provider = Mock()
        self.provider.name = "Controlled fixture"
        self.provider.model = "not-a-live-model"
        self.answer = json.dumps({"files": [{"path": "sample.py", "content": "value = 2\n"}]})
        self.provider.chat.return_value = self.answer
        self.drafting = ProjectDrafting(self.provider, self.repository)
        self.tasks = DraftingTasks(self.drafting)

    def start(self, **kwargs):
        return self.tasks.start(["sample.py"], "Change value", confirmed=True, **kwargs)

    def wait(self, task_id):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = self.tasks.status(task_id)
            if not result["worker_active"]:
                return result
            time.sleep(.005)
        self.fail("Draft worker did not exit")

    def blocked_provider(self):
        entered, release = Event(), Event()
        self.addCleanup(release.set)

        def inference(_messages):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("Test did not release provider")
            return self.answer

        self.provider.chat.side_effect = inference
        task = self.start()
        self.addCleanup(lambda: self.wait(task["id"]))
        self.addCleanup(release.set)
        self.assertTrue(entered.wait(2))
        return task, release

    def test_completed_task_stages_only_and_preserves_apply_confirmation(self):
        task = self.start(budget_seconds=30)
        result = self.wait(task["id"])
        self.assertEqual("completed", result["state"])
        self.assertFalse(result["worker_active"])
        self.assertFalse(result["durable"])
        self.assertFalse(result["tests_executed"])
        self.assertFalse(result["transport_interrupt_supported"])
        self.assertEqual(30, result["budget_seconds"])
        self.assertEqual(["sample.py"], result["paths"])
        proposal = result["result"]["changes"][0]["proposal"]
        self.assertEqual(b"value = 1\r\n", self.target.read_bytes())
        with self.assertRaises(PermissionError):
            self.repository.changes.apply(proposal["id"])
        self.repository.changes.apply(proposal["id"], confirmed=True)
        self.assertEqual(b"value = 2\r\n", self.target.read_bytes())
        self.assertEqual("completed", self.tasks.cancel(task["id"], confirmed=True)["state"])

    def test_cancel_during_inference_discards_late_output_and_drains(self):
        task, release = self.blocked_provider()
        result = self.tasks.cancel(task["id"], confirmed=True)
        self.assertEqual("cancel_requested", result["state"])
        self.assertTrue(result["worker_active"])
        self.assertIsNone(result["result"])
        with self.assertRaisesRegex(ProjectDraftConflict, "draining"):
            self.start()
        with self.assertRaisesRegex(ProjectDraftConflict, "already running"):
            self.drafting.draft("sample.py", "Change", confirmed=True)
        release.set()
        result = self.wait(task["id"])
        self.assertEqual("cancelled", result["state"])
        self.assertIsNone(result["result"])
        self.assertEqual([], self.repository.changes.audit_log()["events"])
        self.assertEqual(b"value = 1\r\n", self.target.read_bytes())
        self.provider.chat.side_effect = None
        self.assertEqual("completed", self.wait(self.start()["id"])["state"])

    def test_budget_exhaustion_rejects_late_output_and_keeps_worker_slot(self):
        task, release = self.blocked_provider()
        self.tasks._tasks[task["id"]].started -= 121
        result = self.tasks.status(task["id"])
        self.assertEqual("timed_out", result["state"])
        self.assertTrue(result["worker_active"])
        with self.assertRaises(ProjectDraftConflict):
            self.start()
        self.assertEqual("timed_out", self.tasks.cancel(task["id"], confirmed=True)["state"])
        release.set()
        result = self.wait(task["id"])
        self.assertEqual("timed_out", result["state"])
        self.assertFalse(result["worker_active"])
        self.assertEqual({}, self.repository.changes._proposals)
        self.assertEqual([], self.repository.changes.audit_log()["events"])

    def test_budget_checked_again_after_syntax_validation_before_staging(self):
        validate = self.repository.validation.validate

        def slow_validation(*args, **kwargs):
            result = validate(*args, **kwargs)
            for task in self.tasks._tasks.values():
                task.started -= 121
            return result

        with patch.object(self.repository.validation, "validate", side_effect=slow_validation):
            result = self.wait(self.start()["id"])
        self.assertEqual("timed_out", result["state"])
        self.assertEqual({}, self.repository.changes._proposals)
        self.assertEqual([], self.repository.changes.audit_log()["events"])

    def test_budget_boundary_is_elapsed_greater_than_or_equal_to_limit(self):
        task, release = self.blocked_provider()
        stored = self.tasks._tasks[task["id"]]
        with patch("astra.drafting_tasks.time.monotonic", return_value=stored.started + stored.budget_seconds - .001):
            self.assertEqual("inference", self.tasks.status(task["id"])["state"])
        with patch("astra.drafting_tasks.time.monotonic", return_value=stored.started + stored.budget_seconds):
            self.assertEqual("timed_out", self.tasks.status(task["id"])["state"])
        release.set()
        self.assertEqual("timed_out", self.wait(task["id"])["state"])
        self.assertEqual({}, self.repository.changes._proposals)

    def test_cancel_after_staging_acceptance_cannot_claim_to_undo_proposals(self):
        stage = self.tasks._stage
        accepted, release = Event(), Event()

        def stage_then_wait(*args):
            result = stage(*args)
            accepted.set()
            if not release.wait(5):
                raise RuntimeError("Test did not release staging")
            return result

        with patch.object(self.tasks, "_stage", side_effect=stage_then_wait):
            task = self.start()
            try:
                self.assertTrue(accepted.wait(2))
                result = self.tasks.cancel(task["id"], confirmed=True)
                self.assertEqual("completed", result["state"])
                self.assertTrue(result["worker_active"])
                self.assertIsNone(result["result"])
            finally:
                release.set()
                result = self.wait(task["id"])
        self.assertEqual("completed", result["state"])
        self.assertEqual(1, len(self.repository.changes._proposals))

    def test_cancel_before_worker_starts_never_calls_provider(self):
        with patch("astra.drafting_tasks.Thread") as thread:
            task = self.start()
            self.tasks.cancel(task["id"], confirmed=True)
            thread.assert_called_once()
            self.tasks._run(self.tasks._tasks[task["id"]], "Change value")
        self.assertEqual("cancelled", self.tasks.status(task["id"])["state"])
        self.provider.chat.assert_not_called()
        self.assertEqual({}, self.repository.changes._proposals)

    def test_failures_are_explicit_logged_and_slot_released(self):
        self.provider.chat.side_effect = ModelError("Controlled provider unavailable")
        with self.assertLogs("astra.drafting_tasks", level="ERROR") as logs:
            result = self.wait(self.start()["id"])
        self.assertEqual("failed", result["state"])
        self.assertIn("Controlled provider unavailable", result["error"])
        self.assertIn(result["id"], logs.output[0])
        self.assertIsNone(result["result"])
        self.provider.chat.side_effect = None
        self.assertEqual("completed", self.wait(self.start()["id"])["state"])

    def test_invalid_syntax_and_stale_baseline_are_failed_tasks(self):
        self.provider.chat.return_value = json.dumps({"files": [{"path": "sample.py", "content": "def ("}]})
        with self.assertLogs("astra.drafting_tasks", level="ERROR"):
            result = self.wait(self.start()["id"])
        self.assertEqual("failed", result["state"])
        self.assertIn("ProjectSyntaxError", result["error"])

        def changed(_messages):
            self.target.write_text("value = 99\n")
            return self.answer

        self.provider.chat.side_effect = changed
        with self.assertLogs("astra.drafting_tasks", level="ERROR"):
            result = self.wait(self.start()["id"])
        self.assertEqual("failed", result["state"])
        self.assertIn("ProjectDraftConflict", result["error"])
        self.assertEqual({}, self.repository.changes._proposals)

    def test_invalid_inputs_and_confirmation_do_not_start_worker(self):
        for confirm in (False, None, "true", 1):
            with self.subTest(confirm=confirm), self.assertRaises(PermissionError):
                self.tasks.start(["sample.py"], "Change", confirmed=confirm)
        for budget in (True, 9, 121, 30.0, "30", None):
            with self.subTest(budget=budget), self.assertRaises(ValueError):
                self.start(budget_seconds=budget)
        for paths in ([], None, "sample.py", ["sample.py"] * 6, [None], [""], ["x" * 241]):
            with self.subTest(paths=paths), self.assertRaises(ValueError):
                self.tasks.start(paths, "Change", confirmed=True)
        self.assertEqual({}, self.tasks._tasks)
        self.provider.chat.assert_not_called()

    def test_status_cancellation_unknown_ids_and_confirmation(self):
        for task_id in ("missing", None, 1):
            with self.subTest(task_id=task_id), self.assertRaises(LookupError):
                self.tasks.status(task_id)
        with self.assertRaises(LookupError):
            self.tasks.cancel("missing", confirmed=True)
        task = self.start()
        self.wait(task["id"])
        for confirm in (False, None, "true", 1):
            with self.subTest(confirm=confirm), self.assertRaises(PermissionError):
                self.tasks.cancel(task["id"], confirmed=confirm)
        self.assertEqual("completed", self.tasks.status(task["id"])["state"])

    def test_task_history_bounded_restart_loses_tasks_and_inputs_are_copied(self):
        self.provider.chat.return_value = "bad JSON"
        first = None
        with self.assertLogs("astra.drafting_tasks", level="ERROR"):
            for _ in range(MAX_TASKS + 1):
                task = self.start()
                if first is None:
                    first = task["id"]
                self.wait(task["id"])
        self.assertEqual(MAX_TASKS, len(self.tasks._tasks))
        with self.assertRaises(LookupError):
            self.tasks.status(first)
        with self.assertRaises(LookupError):
            DraftingTasks(self.drafting).status(task["id"])
        task["paths"].append("extra.py")
        self.assertEqual(["sample.py"], self.tasks.status(task["id"])["paths"])
        self.assertNotIn("instruction", task)
        listed = self.tasks.list_tasks()
        self.assertEqual(MAX_TASKS, len(listed["tasks"]))
        self.assertEqual(task["id"], listed["tasks"][0]["id"])
        self.assertNotIn("result", listed["tasks"][0])

    def test_worker_start_failure_is_not_an_accepted_task(self):
        with patch("astra.drafting_tasks.Thread.start", side_effect=RuntimeError("controlled unavailable")):
            with self.assertRaisesRegex(ModelError, "Could not start"):
                self.start()
        self.assertFalse(next(iter(self.tasks._tasks.values())).worker_active)
        self.provider.chat.assert_not_called()


class DraftingTaskApiTests(unittest.TestCase):
    setUp = DraftingTaskTests.setUp

    def start_server(self, repository):
        archive = Archive(self.root / "memory.sqlite")
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(AstraCore(self.provider, archive, repository)))
        thread = Thread(target=server.serve_forever)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        return server

    def request(self, server, method, path, payload=None, headers=None):
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        try:
            connection.request(method, path, json.dumps(payload) if payload is not None else None,
                               {"Content-Type": "application/json", **(headers or {})})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_http_start_status_cancel_and_guards(self):
        entered, release = Event(), Event()
        exited = Event()
        self.addCleanup(release.set)

        def inference(_messages):
            entered.set()
            try:
                if not release.wait(5):
                    raise RuntimeError("Test did not release provider")
                return self.answer
            finally:
                exited.set()

        self.provider.chat.side_effect = inference
        server = self.start_server(self.repository)
        endpoint = "/api/project/draft-tasks"
        payload = {"paths": ["sample.py"], "instruction": "Change", "confirm": True, "budget_seconds": 30}
        self.assertEqual(403, self.request(server, "POST", endpoint, {**payload, "confirm": 1})[0])
        self.assertEqual(403, self.request(server, "POST", endpoint, payload, {"Origin": "https://untrusted.invalid"})[0])
        self.assertEqual(400, self.request(server, "POST", endpoint, {**payload, "budget_seconds": 0})[0])
        self.provider.chat.assert_not_called()
        code, result = self.request(server, "POST", endpoint, payload)
        self.assertEqual(202, code)
        path = endpoint + "/" + result["task"]["id"]
        code, listed = self.request(server, "GET", endpoint)
        self.assertEqual(200, code)
        self.assertEqual(result["task"]["id"], listed["tasks"][0]["id"])
        self.assertNotIn("result", listed["tasks"][0])
        try:
            self.assertTrue(entered.wait(2))
            code, result = self.request(server, "GET", path)
            self.assertEqual(200, code)
            self.assertEqual("inference", result["task"]["state"])
            self.assertEqual(409, self.request(server, "POST", endpoint, payload)[0])
            self.assertEqual(403, self.request(server, "POST", path + "/cancel", {"confirm": False})[0])
            code, result = self.request(server, "POST", path + "/cancel", {"confirm": True})
            self.assertEqual(200, code)
            self.assertEqual("cancel_requested", result["task"]["state"])
        finally:
            release.set()
            self.assertTrue(exited.wait(2))
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            code, result = self.request(server, "GET", path)
            if not result["task"]["worker_active"]:
                break
            time.sleep(.01)
        self.assertEqual("cancelled", result["task"]["state"])
        self.assertFalse(result["task"]["worker_active"])
        self.assertEqual([], self.repository.changes.audit_log()["events"])
        self.assertEqual(404, self.request(server, "GET", endpoint + "/missing")[0])
        self.assertEqual(404, self.request(server, "POST", endpoint + "/missing/cancel", {"confirm": True})[0])
        self.assertEqual(404, self.request(server, "GET", path + "/extra")[0])
        self.assertEqual(404, self.request(server, "POST", path + "/apply", {})[0])
        self.assertEqual(403, self.request(server, "GET", path, headers={"Host": "untrusted.invalid"})[0])
        disabled = self.start_server(None)
        self.assertEqual(404, self.request(disabled, "POST", endpoint, payload)[0])
        self.assertEqual(404, self.request(disabled, "GET", path)[0])


if __name__ == "__main__":
    unittest.main()
