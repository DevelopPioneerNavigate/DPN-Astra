from datetime import timedelta
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from unittest.mock import Mock, patch

from astra.archive import Archive
from astra.core import AstraCore
from astra.project_changes import MAX_CHANGE_SET_BYTES, ProjectChanges
from astra.project_validation import ProjectSyntaxError
from astra.repository import RepositoryContext
from astra.server import make_handler


class ChangeSetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "a.py").write_bytes(b"value = 1\r\n")
        (self.root / "b.json").write_bytes(b'{"value":1}\n')
        self.repository = RepositoryContext(self.root)
        self.changes = self.repository.changes
        self.entries = [
            {"path": "a.py", "content": "value = 2\n"},
            {"path": "b.json", "content": '{"value":2}\n'},
        ]

    def preview(self):
        return self.repository.validation.propose_many(self.entries)

    def test_preview_stages_every_diff_without_writing(self):
        result = self.preview()
        self.assertFalse(result["atomic_apply"])
        self.assertFalse(result["tests_executed"])
        self.assertEqual(["Python", "JSON"], [r["validation"]["language"] for r in result["changes"]])
        self.assertEqual(["a.py", "b.json"], [r["proposal"]["path"] for r in result["changes"]])
        for entry in result["changes"]:
            self.assertEqual("pending", entry["proposal"]["state"])
            self.assertIn("+", entry["proposal"]["diff"])
            self.assertEqual(entry["proposal"], self.changes.describe(entry["proposal"]["id"]))
        self.assertEqual(b"value = 1\r\n", (self.root / "a.py").read_bytes())
        self.assertEqual(b'{"value":1}\n', (self.root / "b.json").read_bytes())

    def test_every_file_requires_separate_confirmation_and_rollback(self):
        result = self.preview()
        first, second = [entry["proposal"]["id"] for entry in result["changes"]]
        with self.assertRaises(PermissionError):
            self.changes.apply(first)
        self.changes.apply(first, confirmed=True)
        self.assertEqual("applied", self.changes.describe(first)["state"])
        self.assertEqual("pending", self.changes.describe(second)["state"])
        self.assertEqual(b'{"value":1}\n', (self.root / "b.json").read_bytes())
        self.changes.apply(second, confirmed=True)
        self.changes.rollback(first, confirmed=True)
        self.assertEqual(b"value = 1\r\n", (self.root / "a.py").read_bytes())
        self.assertEqual(b'{"value":2}\n', (self.root / "b.json").read_bytes())
        self.changes.rollback(second, confirmed=True)
        self.assertEqual(b'{"value":1}\n', (self.root / "b.json").read_bytes())

    def test_invalid_last_file_prevents_all_staging(self):
        self.entries[1]["content"] = '{"value":}'
        with self.assertRaises(ProjectSyntaxError):
            self.preview()
        self.assertEqual({}, self.changes._proposals)
        self.assertEqual([], self.changes.audit_log()["events"])

    def test_ineligible_duplicate_and_noop_targets_prevent_all_staging(self):
        for last in (
            {"path": "../outside.py", "content": "x=2"},
            {"path": "new.py", "content": "x=2"},
            {"path": "a.py", "content": "value=3"},
            {"path": "./a.py", "content": "value=3"},
            {"path": "b.json", "content": '{"value":1}\n'},
        ):
            with self.subTest(last=last):
                with self.assertRaises((ValueError, FileNotFoundError)):
                    self.repository.validation.propose_many([self.entries[0], last])
                self.assertEqual({}, self.changes._proposals)
                self.assertEqual([], self.changes.audit_log()["events"])

    def test_bounds_and_malformed_entries_precede_staging(self):
        for entries in (
            None, {}, [], self.entries * 3, [None],
            [{"path": "a.py"}], [{"path": "a.py", "content": [], "extra": 1}],
            [{"path": "a.py", "content": "\ud800"}],
            [{"path": "a.py", "content": "x" * (MAX_CHANGE_SET_BYTES + 1)}],
        ):
            with self.subTest(entries=str(entries)[:80]):
                with self.assertRaises(ValueError):
                    self.repository.validation.propose_many(entries)
                self.assertEqual({}, self.changes._proposals)

    def test_five_files_and_aggregate_exact_limit(self):
        entries = []
        for index in range(5):
            path = f"sample{index}.txt"
            (self.root / path).write_text("Original")
            entries.append({"path": path, "content": "x" * (MAX_CHANGE_SET_BYTES // 5)})
        entries[-1]["content"] += "x" * (MAX_CHANGE_SET_BYTES % 5)
        result = self.repository.validation.propose_many(entries)
        self.assertEqual(5, len(result["changes"]))
        self.assertTrue(all(entry["validation"] is None for entry in result["changes"]))
        entries[-1]["content"] += "x"
        with self.assertRaisesRegex(ValueError, "UTF-8 bytes"):
            self.repository.validation.propose_many(entries)
        self.assertEqual(5, len(self.changes._proposals))

    def test_capacity_rejection_does_not_partially_stage_set(self):
        with patch("astra.project_changes.MAX_PENDING_PROPOSALS", 1):
            with self.assertRaisesRegex(ValueError, "Too many"):
                self.preview()
        self.assertEqual({}, self.changes._proposals)
        self.assertEqual([], self.changes.audit_log()["events"])

    def test_source_change_after_preparation_is_rejected_before_registration(self):
        prepare = self.changes._prepare

        def prepare_and_modify(path, content, *, expected_sha256=None):
            proposal = prepare(path, content, expected_sha256=expected_sha256)
            if path == "b.json":
                (self.root / "a.py").write_text("value = 99\n")
            return proposal

        with patch.object(self.changes, "_prepare", side_effect=prepare_and_modify):
            with self.assertRaisesRegex(ValueError, "changed since review"):
                self.preview()
        self.assertEqual({}, self.changes._proposals)
        self.assertEqual([], self.changes.audit_log()["events"])

    def test_partial_apply_conflict_does_not_undo_or_hide_successful_file(self):
        result = self.preview()
        first, second = [entry["proposal"]["id"] for entry in result["changes"]]
        self.changes.apply(first, confirmed=True)
        (self.root / "b.json").write_text('{"value":99}')
        with self.assertRaisesRegex(ValueError, "changed since review"):
            self.changes.apply(second, confirmed=True)
        self.assertEqual("applied", self.changes.describe(first)["state"])
        self.assertEqual("pending", self.changes.describe(second)["state"])
        self.assertEqual(b"value = 2\r\n", (self.root / "a.py").read_bytes())
        self.changes.rollback(first, confirmed=True)
        self.assertEqual(b"value = 1\r\n", (self.root / "a.py").read_bytes())
        self.assertEqual('{"value":99}', (self.root / "b.json").read_text())

    def test_write_failure_keeps_individual_states_explicit(self):
        result = self.preview()
        first, second = [entry["proposal"]["id"] for entry in result["changes"]]
        self.changes.apply(first, confirmed=True)
        with patch.object(self.changes, "_atomic_replace", side_effect=OSError("Injected write failure")):
            with self.assertRaisesRegex(OSError, "Injected"):
                self.changes.apply(second, confirmed=True)
        self.assertEqual("applied", self.changes.describe(first)["state"])
        self.assertEqual("pending", self.changes.describe(second)["state"])

    def test_intervening_edit_blocks_rollback_without_affecting_other_file(self):
        result = self.preview()
        first, second = [entry["proposal"]["id"] for entry in result["changes"]]
        self.changes.apply(first, confirmed=True)
        self.changes.apply(second, confirmed=True)
        (self.root / "a.py").write_text("value = 99\n")
        with self.assertRaisesRegex(ValueError, "changed since review"):
            self.changes.rollback(first, confirmed=True)
        self.assertEqual("applied", self.changes.describe(first)["state"])
        self.changes.rollback(second, confirmed=True)
        self.assertEqual("value = 99\n", (self.root / "a.py").read_text())
        self.assertEqual(b'{"value":1}\n', (self.root / "b.json").read_bytes())

    def test_audit_error_after_write_can_be_reconciled_with_recorded_state(self):
        result = self.preview()
        first = result["changes"][0]["proposal"]["id"]
        with patch.object(self.changes, "_record_event", side_effect=OSError("Audit failed after replace")):
            with self.assertRaises(OSError):
                self.changes.apply(first, confirmed=True)
        self.assertEqual("applied", self.changes.describe(first)["state"])
        self.assertEqual(b"value = 2\r\n", (self.root / "a.py").read_bytes())
        self.changes.rollback(first, confirmed=True)
        self.assertEqual(b"value = 1\r\n", (self.root / "a.py").read_bytes())

    def test_audit_failure_does_not_leave_actionable_unreported_proposals(self):
        record = self.changes._record_event

        def fail_second(event, proposal_id, proposal):
            if proposal.path == "b.json":
                raise OSError("Audit unavailable")
            record(event, proposal_id, proposal)

        with patch.object(self.changes, "_record_event", side_effect=fail_second):
            with self.assertRaisesRegex(OSError, "Audit unavailable"):
                self.preview()
        self.assertEqual({}, self.changes._proposals)
        # An attempted stage can leave metadata, but never a hidden actionable proposal.
        self.assertEqual(1, len(self.changes.audit_log()["events"]))

    def test_expiry_and_restart_make_state_unavailable(self):
        result = self.preview()
        first = result["changes"][0]["proposal"]["id"]
        self.changes._proposals[first].created_at -= timedelta(minutes=16)
        with self.assertRaisesRegex(ValueError, "expired"):
            self.changes.describe(first)
        with self.assertRaisesRegex(ValueError, "not found"):
            ProjectChanges(self.root).describe(result["changes"][1]["proposal"]["id"])


class ChangeSetApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name) / "project"
        root.mkdir()
        (root / "a.py").write_text("value = 1\n")
        (root / "b.json").write_text('{"value":1}\n')
        self.repository = RepositoryContext(root)
        self.entries = [{"path": "a.py", "content": "value = 2\n"},
                        {"path": "b.json", "content": '{"value":2}\n'}]
        core = AstraCore(Mock(), Archive(Path(self.tmp.name) / "memory.sqlite"), self.repository)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(core))
        thread = Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, payload=None, headers=None):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            connection.request(method, path, None if payload is None else json.dumps(payload),
                               {"Content-Type": "application/json", **(headers or {})})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_http_preview_and_per_file_state_refresh(self):
        code, result = self.request("POST", "/api/project/change-set", {"files": self.entries})
        self.assertEqual(201, code)
        self.assertFalse(result["atomic_apply"])
        self.assertFalse(result["tests_executed"])
        first = result["changes"][0]["proposal"]
        path = "/api/project/proposals/" + first["id"]
        self.assertEqual((200, {"proposal": first}), self.request("GET", path))
        self.assertEqual(403, self.request("POST", path + "/apply", {})[0])
        self.assertEqual(200, self.request("POST", path + "/apply", {"confirm": True})[0])
        self.assertEqual("applied", self.request("GET", path)[1]["proposal"]["state"])
        other = result["changes"][1]["proposal"]["id"]
        self.assertEqual("pending", self.request("GET", "/api/project/proposals/" + other)[1]["proposal"]["state"])
        self.assertEqual(404, self.request("GET", path + "/extra")[0])
        self.assertEqual(404, self.request("GET", "/api/project/proposals/unknown")[0])

    def test_http_validation_and_origin_fail_before_staging(self):
        code, result = self.request("POST", "/api/project/change-set", {"files": self.entries},
                                    {"Origin": "https://untrusted.invalid"})
        self.assertEqual(403, code)
        self.entries[-1]["content"] = "{"
        code, result = self.request("POST", "/api/project/change-set", {"files": self.entries})
        self.assertEqual(422, code)
        self.assertFalse(result["validation"]["valid"])
        self.assertEqual(400, self.request("POST", "/api/project/change-set", {"files": []})[0])
        self.assertEqual({}, self.repository.changes._proposals)

    def test_state_storage_failure_is_an_explicit_error(self):
        with patch.object(self.repository.changes, "describe", side_effect=OSError("Audit unavailable")):
            code, result = self.request("GET", "/api/project/proposals/example")
        self.assertEqual(503, code)
        self.assertIn("unavailable", result["error"])


if __name__ == "__main__":
    unittest.main()
