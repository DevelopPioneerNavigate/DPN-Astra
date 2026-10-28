from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
from threading import Event, Thread
import unittest
from unittest.mock import Mock, patch

from astra.archive import Archive
from astra.core import AstraCore
from astra.inference import ModelError
from astra.project_changes import MAX_CHANGE_BYTES, ProjectDraftConflict
from astra.project_drafting import ProjectDrafting
from astra.project_validation import ProjectSyntaxError
from astra.repository import RepositoryContext
from astra.server import make_handler


class ProjectDraftingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.target = self.root / "sample.py"
        self.original = b"value = 1\r\n"
        self.target.write_bytes(self.original)
        self.repository = RepositoryContext(self.root)
        self.provider = Mock()
        self.provider.name = "Local fixture"
        self.provider.model = "fixture-model"
        self.provider.chat.return_value = json.dumps({"content": "value = 2\n"})
        self.drafting = ProjectDrafting(self.provider, self.repository)

    def draft(self, **kwargs):
        return self.drafting.draft("sample.py", "Set value to two", confirmed=True, **kwargs)

    def test_draft_stages_real_diff_then_separate_apply_and_rollback(self):
        response = self.draft()
        self.assertEqual(self.original, self.target.read_bytes())
        self.assertEqual("pending", response["proposal"]["state"])
        self.assertIn("+value = 2", response["proposal"]["diff"])
        self.assertTrue(response["validation"]["valid"])
        self.assertFalse(response["tests_executed"])
        self.assertEqual("Local fixture", response["provider"])
        proposal_id = response["proposal"]["id"]
        with self.assertRaises(PermissionError):
            self.repository.changes.apply(proposal_id)
        self.repository.changes.apply(proposal_id, confirmed=True)
        self.assertEqual(b"value = 2\r\n", self.target.read_bytes())
        self.repository.changes.rollback(proposal_id, confirmed=True)
        self.assertEqual(self.original, self.target.read_bytes())
        self.assertEqual(
            ["staged", "applied", "rolled_back"],
            [e["event"] for e in self.repository.changes.audit_log()["events"]],
        )

    def test_prompt_contains_exact_target_and_bounded_repository_evidence(self):
        self.draft()
        messages = self.provider.chat.call_args.args[0]
        self.assertEqual(["system", "user"], [m["role"] for m in messages])
        request = json.loads(messages[1]["content"])
        self.assertEqual(self.original.decode(), request["original_content"])
        self.assertEqual("sample.py", request["target_path"])
        self.assertEqual("Set value to two", request["instruction"])
        self.assertIn("repository_evidence", request)
        self.assertIn("untrusted", messages[0]["content"])
        self.assertIn("no tools", messages[0]["content"])

    def test_approval_and_input_checks_precede_model_call(self):
        for confirmed in (False, None, "true", 1):
            with self.subTest(confirmed=confirmed), self.assertRaises(PermissionError):
                self.drafting.draft("sample.py", "Change it", confirmed=confirmed)
        for instruction in ("", " " * 4, "x" * 4001, None, []):
            with self.subTest(instruction=instruction), self.assertRaises(ValueError):
                self.drafting.draft("sample.py", instruction, confirmed=True)
        for path in ("../sample.py", ".env", "secret.py", "new.py"):
            with self.subTest(path=path), self.assertRaises((ValueError, FileNotFoundError)):
                self.drafting.draft(path, "Change it", confirmed=True)
        self.provider.chat.assert_not_called()
        self.assertEqual([], self.repository.changes.audit_log()["events"])

    def test_malformed_model_responses_never_stage_or_write(self):
        for answer in (
            "", "```json\n{\"content\":\"value=2\"}\n```", "[]", "null",
            '{"content":42}', '{"content":"x","path":"other.py"}',
            '{"content":"x","content":"y"}', '{"content":"\\ud800"}',
            '{"content":', "x" * (MAX_CHANGE_BYTES * 8 + 1), None,
        ):
            with self.subTest(answer=str(answer)[:50]):
                self.provider.chat.return_value = answer
                with self.assertRaises(ModelError):
                    self.draft()
                self.assertEqual(self.original, self.target.read_bytes())
                self.assertEqual([], self.repository.changes.audit_log()["events"])

    def test_content_byte_limit_counts_utf8_and_releases_generation_lock(self):
        self.provider.chat.return_value = json.dumps({"content": "\u03bb" * (MAX_CHANGE_BYTES // 2 + 1)})
        with self.assertRaisesRegex(ModelError, "UTF-8 bytes"):
            self.draft()
        self.provider.chat.return_value = json.dumps({"content": "value = 2\n"})
        self.assertEqual("pending", self.draft()["proposal"]["state"])

    def test_invalid_syntax_noop_and_provider_failures_are_explicit(self):
        self.provider.chat.return_value = json.dumps({"content": "def broken("})
        with self.assertRaises(ProjectSyntaxError):
            self.draft()
        self.provider.chat.return_value = json.dumps({"content": "value = 1\n"})
        with self.assertRaisesRegex(ValueError, "identical"):
            self.draft()
        self.provider.chat.side_effect = ModelError("Local model unavailable")
        with self.assertRaisesRegex(ModelError, "unavailable"):
            self.draft()
        self.assertEqual(self.original, self.target.read_bytes())
        self.assertEqual([], self.repository.changes.audit_log()["events"])

    def test_source_modified_during_inference_cannot_be_staged(self):
        def inference(_messages):
            self.target.write_text("value = 99\n", encoding="utf-8")
            return json.dumps({"content": "value = 2\n"})

        self.provider.chat.side_effect = inference
        with self.assertRaises(ProjectDraftConflict):
            self.draft()
        self.assertEqual("value = 99\n", self.target.read_text())
        self.assertEqual([], self.repository.changes.audit_log()["events"])

    def test_source_modified_after_draft_cannot_be_applied(self):
        response = self.draft()
        self.target.write_text("value = 99\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "changed since review"):
            self.repository.changes.apply(response["proposal"]["id"], confirmed=True)
        self.assertEqual("value = 99\n", self.target.read_text())

    def test_only_one_generation_runs_at_a_time(self):
        entered, release = Event(), Event()
        results, errors = [], []

        def inference(_messages):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("Test did not release inference")
            return json.dumps({"content": "value = 2\n"})

        def worker():
            try:
                results.append(self.draft())
            except Exception as exc:
                errors.append(exc)

        self.provider.chat.side_effect = inference
        thread = Thread(target=worker)
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            with self.assertRaisesRegex(ProjectDraftConflict, "already running"):
                self.draft()
        finally:
            release.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual([], errors)
        self.assertEqual(1, len(results))
        self.assertEqual(1, self.provider.chat.call_count)

    def test_unsupported_syntax_is_honestly_reported(self):
        (self.root / "sample.js").write_text("let value = 1;\n")
        self.provider.chat.return_value = json.dumps({"content": "let value = 2;\n"})
        result = self.drafting.draft("sample.js", "Change value", confirmed=True)
        self.assertIsNone(result["validation"])
        self.assertFalse(result["tests_executed"])

    def test_exact_content_limit_is_allowed_and_target_path_cannot_be_chosen_by_model(self):
        target = self.root / "sample.txt"
        target.write_text("Original\n")
        content = "x" * MAX_CHANGE_BYTES
        self.provider.chat.return_value = json.dumps({"content": content})
        result = self.drafting.draft("sample.txt", "Replace the content", confirmed=True)
        self.assertEqual(content, result["content"])
        self.assertEqual("sample.txt", result["proposal"]["path"])
        self.assertEqual("Original\n", target.read_text())
        self.assertIsNone(result["validation"])

    def test_json_and_toml_drafts_use_existing_static_validators(self):
        for path, original, valid, invalid in (
            ("sample.json", '{"value":1}', '{"value":2}', '{"value":}'),
            ("sample.toml", "value = 1", "value = 2", "value = ["),
        ):
            with self.subTest(path=path):
                (self.root / path).write_text(original)
                self.provider.chat.return_value = json.dumps({"content": valid})
                self.assertTrue(self.drafting.draft(path, "Change value", confirmed=True)["validation"]["valid"])
                self.provider.chat.return_value = json.dumps({"content": invalid})
                with self.assertRaises(ProjectSyntaxError):
                    self.drafting.draft(path, "Change value", confirmed=True)
                self.assertEqual(original, (self.root / path).read_text())


class ProjectDraftApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name) / "project"
        root.mkdir()
        self.target = root / "sample.py"
        self.target.write_text("value = 1\n")
        self.repository = RepositoryContext(root)
        self.provider = Mock()
        self.provider.name = "Fixture"
        self.provider.model = "fixture-model"
        self.provider.chat.return_value = json.dumps({"content": "value = 2\n"})
        archive = Archive(Path(self.tmp.name) / "memory.sqlite")
        self.core = AstraCore(self.provider, archive, self.repository)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.core))
        thread = Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(self.server.shutdown)

    def post(self, payload, path="/api/project/draft", headers=None):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            connection.request("POST", path, json.dumps(payload),
                               {"Content-Type": "application/json", **(headers or {})})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_http_draft_and_separate_confirmed_apply(self):
        code, result = self.post({"path": "sample.py", "instruction": "Change value", "confirm": True})
        self.assertEqual(201, code)
        self.assertFalse(result["tests_executed"])
        self.assertEqual("value = 1\n", self.target.read_text())
        path = "/api/project/proposals/" + result["proposal"]["id"] + "/apply"
        self.assertEqual(403, self.post({}, path)[0])
        self.assertEqual(200, self.post({"confirm": True}, path)[0])
        self.assertEqual("value = 2\n", self.target.read_text())
        self.assertEqual([], self.core.history())

    def test_http_draft_errors_and_origin_guard(self):
        payload = {"path": "sample.py", "instruction": "Change value", "confirm": True}
        self.assertEqual(403, self.post({**payload, "confirm": False})[0])
        self.assertEqual(403, self.post(payload, headers={"Origin": "https://untrusted.invalid"})[0])
        self.provider.chat.assert_not_called()
        self.provider.chat.return_value = "Not JSON"
        self.assertEqual(503, self.post(payload)[0])
        self.provider.chat.return_value = json.dumps({"content": "def ("})
        code, result = self.post(payload)
        self.assertEqual(422, code)
        self.assertFalse(result["validation"]["valid"])
        with patch("astra.server.ProjectDrafting.draft", side_effect=ProjectDraftConflict("File changed")):
            self.assertEqual(409, self.post(payload)[0])
        self.assertEqual("value = 1\n", self.target.read_text())
        self.assertEqual([], self.repository.changes.audit_log()["events"])

    def test_drafting_without_repository_is_explicitly_unavailable(self):
        # A separate handler factory is necessary: repository capability is fixed at startup.
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0), make_handler(AstraCore(self.provider, self.core.archive)),
        )
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            try:
                connection.request("POST", "/api/project/draft",
                                   json.dumps({"path": "sample.py", "instruction": "Change", "confirm": True}),
                                   {"Content-Type": "application/json"})
                response = connection.getresponse()
                self.assertEqual(404, response.status)
                self.assertIn("not enabled", json.loads(response.read())["error"])
            finally:
                connection.close()
        finally:
            server.shutdown()
            thread.join()
            server.server_close()
        self.provider.chat.assert_not_called()


if __name__ == "__main__":
    unittest.main()
