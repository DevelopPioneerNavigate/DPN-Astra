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
from astra.project_changes import MAX_CHANGE_BYTES, MAX_CHANGE_SET_BYTES, ProjectDraftConflict
from astra.project_drafting import (
    EVIDENCE_TRUNCATION_MARKER, MAX_DRAFT_EVIDENCE_CHARS, ProjectDrafting,
)
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
        self.assertEqual({"json_mode": True}, self.provider.chat.call_args.kwargs)
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

    def test_single_draft_evidence_budget_boundaries_and_disclosure(self):
        samples = (
            "", "x" * MAX_DRAFT_EVIDENCE_CHARS,
            "x" * (MAX_DRAFT_EVIDENCE_CHARS + 1),
            "[sample.py]\n" + "1: value = 1\n" * 1000,
            "[sample.py]\n" + "1: # \u03bb\n" * 1000,
        )
        for evidence in samples:
            with self.subTest(length=len(evidence)), patch.object(
                self.repository, "context_for", return_value=evidence,
            ):
                response = self.draft()
                request = json.loads(self.provider.chat.call_args.args[0][-1]["content"])
                supplied = request["repository_evidence"]
                truncated = len(evidence) > MAX_DRAFT_EVIDENCE_CHARS
                self.assertLessEqual(len(supplied), MAX_DRAFT_EVIDENCE_CHARS)
                self.assertEqual(truncated, response["repository_evidence_truncated"])
                self.assertEqual(len(supplied), response["repository_evidence_characters"])
                if truncated:
                    self.assertTrue(supplied.endswith(EVIDENCE_TRUNCATION_MARKER))
                    prefix = supplied[:-len(EVIDENCE_TRUNCATION_MARKER)]
                    if prefix:
                        self.assertTrue(evidence.startswith(prefix + "\n"))
                else:
                    self.assertEqual(evidence, supplied)
                self.assertEqual(self.original.decode(), request["original_content"])
                self.assertEqual("instruction", list(request)[-1])
                self.assertEqual(self.original, self.target.read_bytes())

    def test_evidence_budget_never_truncates_target_or_instruction(self):
        source = "value = 1\n" + "# x\n" * 6000
        self.target.write_bytes(source.encode("utf-8"))
        instruction = ("Preserve behavior. " * 220)[:4000]
        with patch.object(self.repository, "context_for", return_value="1: evidence\n" * 1000):
            self.drafting.draft("sample.py", instruction, confirmed=True)
        request = json.loads(self.provider.chat.call_args.args[0][-1]["content"])
        self.assertEqual(source, request["original_content"])
        self.assertEqual(instruction, request["instruction"])
        self.assertEqual(source, self.target.read_text(encoding="utf-8"))

    def test_single_draft_accepts_full_instruction_limit(self):
        instruction = "Preserve behavior. " * 220
        instruction = instruction[:3993] + " value."
        self.assertEqual(4000, len(instruction))
        response = self.drafting.draft("sample.py", instruction, confirmed=True)
        request = json.loads(self.provider.chat.call_args.args[0][1]["content"])
        self.assertEqual(instruction, request["instruction"])
        self.assertIn("[sample.py]", request["repository_evidence"])
        self.assertEqual("pending", response["proposal"]["state"])
        self.assertEqual(self.original, self.target.read_bytes())

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
        def inference(_messages, *, json_mode=False):
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

        def inference(_messages, *, json_mode=False):
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

    def prepare_set(self):
        self.second = self.root / "other.json"
        self.second.write_bytes(b'{"value":1}\n')
        self.entries = [
            {"path": "sample.py", "content": "value = 2\n"},
            {"path": "other.json", "content": '{"value":2}\n'},
        ]
        self.provider.chat.return_value = json.dumps({"files": self.entries})

    def draft_set(self, paths=None):
        return self.drafting.draft_many(
            ["sample.py", "other.json"] if paths is None else paths,
            "Change both values", confirmed=True,
        )

    def test_multi_draft_stages_all_then_independent_apply_and_rollback(self):
        self.prepare_set()
        response = self.draft_set()
        self.assertEqual(2, len(response["changes"]))
        self.assertFalse(response["atomic_apply"])
        self.assertFalse(response["tests_executed"])
        self.assertEqual("fixture-model", response["model"])
        self.assertEqual(self.original, self.target.read_bytes())
        self.assertEqual(b'{"value":1}\n', self.second.read_bytes())
        first, second = [entry["proposal"]["id"] for entry in response["changes"]]
        self.repository.changes.apply(first, confirmed=True)
        self.assertEqual(b"value = 2\r\n", self.target.read_bytes())
        self.assertEqual("pending", self.repository.changes.describe(second)["state"])
        self.repository.changes.apply(second, confirmed=True)
        self.repository.changes.rollback(first, confirmed=True)
        self.assertEqual(self.original, self.target.read_bytes())
        self.assertEqual(b'{"value":2}\n', self.second.read_bytes())

    def test_multi_prompt_discloses_only_selected_sources_and_bounded_evidence(self):
        self.prepare_set()
        self.draft_set()
        self.assertEqual({"json_mode": True}, self.provider.chat.call_args.kwargs)
        messages = self.provider.chat.call_args.args[0]
        request = json.loads(messages[1]["content"])
        self.assertEqual("Change both values", request["instruction"])
        self.assertEqual([
            {"path": "sample.py", "original_content": self.original.decode()},
            {"path": "other.json", "original_content": '{"value":1}\n'},
        ], request["targets"])
        self.assertEqual({"instruction", "targets", "repository_evidence"}, set(request))
        self.assertIn("untrusted", messages[0]["content"])
        self.assertIn("no tools", messages[0]["content"])

    def test_multi_draft_accepts_full_instruction_limit(self):
        self.prepare_set()
        instruction = ("Preserve behavior. " * 220)[:3993] + " value."
        response = self.drafting.draft_many(
            ["sample.py", "other.json"], instruction, confirmed=True,
        )
        request = json.loads(self.provider.chat.call_args.args[0][1]["content"])
        self.assertEqual(instruction, request["instruction"])
        self.assertIn("[sample.py]", request["repository_evidence"])
        self.assertEqual(2, len(response["changes"]))
        self.assertEqual(self.original, self.target.read_bytes())
        self.assertEqual(b'{"value":1}\n', self.second.read_bytes())

    def test_multi_draft_uses_same_evidence_budget_and_preserves_targets(self):
        self.prepare_set()
        evidence = "[other.json]\n" + '1: {"value":1}\n' * 1000
        with patch.object(self.repository, "context_for", return_value=evidence):
            response = self.draft_set()
        request = json.loads(self.provider.chat.call_args.args[0][-1]["content"])
        supplied = request["repository_evidence"]
        self.assertLessEqual(len(supplied), MAX_DRAFT_EVIDENCE_CHARS)
        self.assertTrue(supplied.endswith(EVIDENCE_TRUNCATION_MARKER))
        self.assertTrue(response["repository_evidence_truncated"])
        self.assertEqual(len(supplied), response["repository_evidence_characters"])
        self.assertEqual(self.original.decode(), request["targets"][0]["original_content"])
        self.assertEqual('{"value":1}\n', request["targets"][1]["original_content"])
        self.assertEqual("Change both values", request["instruction"])
        self.assertEqual("instruction", list(request)[-1])
        self.assertEqual(self.original, self.target.read_bytes())
        self.assertEqual(b'{"value":1}\n', self.second.read_bytes())

    def test_multi_request_rejections_precede_model_call(self):
        self.prepare_set()
        for confirmed in (False, None, "true", 1):
            with self.subTest(confirmed=confirmed), self.assertRaises(PermissionError):
                self.drafting.draft_many(["sample.py"], "Change", confirmed=confirmed)
        for instruction in (None, "", "  ", "x" * 4001):
            with self.subTest(instruction=instruction), self.assertRaises(ValueError):
                self.drafting.draft_many(["sample.py"], instruction, confirmed=True)
        for paths in (None, [], "sample.py", ["sample.py"] * 6, [None],
                      ["sample.py", "sample.py"], ["sample.py", "./sample.py"],
                      ["../outside.py"], ["new.py"], [".env"]):
            with self.subTest(paths=paths), self.assertRaises((ValueError, FileNotFoundError)):
                self.drafting.draft_many(paths, "Change", confirmed=True)
        self.provider.chat.assert_not_called()
        self.assertEqual([], self.repository.changes.audit_log()["events"])

    def test_multi_source_aggregate_limit_precedes_disclosure(self):
        self.prepare_set()
        for name in ("one.txt", "two.txt", "three.txt"):
            (self.root / name).write_bytes(b"x" * (MAX_CHANGE_SET_BYTES // 3 + 1))
        with self.assertRaisesRegex(ValueError, "source exceeds"):
            self.draft_set(["one.txt", "two.txt", "three.txt"])
        self.provider.chat.assert_not_called()

    def test_multi_strict_shape_and_target_allowlist_reject_before_staging(self):
        self.prepare_set()
        responses = [
            "", "[]", "null", '{"files":[]}', '{"files":{},"extra":1}',
            '{"files":[],"files":[]}', '{"files":[{"path":"sample.py","path":"other.json","content":"x"}]}',
            json.dumps({"files": [self.entries[0]]}),
            json.dumps({"files": [self.entries[0], self.entries[0]]}),
            json.dumps({"files": [*self.entries, {"path": "extra.py", "content": "x=2"}]}),
            json.dumps({"files": [self.entries[0], {"path": "../other.json", "content": "{}"}]}),
            json.dumps({"files": [self.entries[0], {"path": "other.json", "content": {}, "extra": 1}]}),
            json.dumps({"files": [self.entries[0], {"path": "other.json", "content": "\ud800"}]}),
            "x" * (MAX_CHANGE_SET_BYTES * 8 + 1), None,
        ]
        for answer in responses:
            with self.subTest(answer=str(answer)[:60]):
                self.provider.chat.return_value = answer
                with self.assertRaises(ModelError):
                    self.draft_set()
                self.assertEqual([], self.repository.changes.audit_log()["events"])
                self.assertEqual(self.original, self.target.read_bytes())
                self.assertEqual(b'{"value":1}\n', self.second.read_bytes())

    def test_multi_content_utf8_and_aggregate_limits(self):
        self.prepare_set()
        for contents in (
            ["\u03bb" * (MAX_CHANGE_BYTES // 2 + 1), "{}"],
            ["x" * MAX_CHANGE_BYTES, "y" * MAX_CHANGE_BYTES, "z"],
        ):
            paths = ["sample.py", "other.json", "third.txt"][:len(contents)]
            self.provider.chat.return_value = json.dumps({"files": [
                {"path": path, "content": content} for path, content in zip(paths, contents)
            ]})
            with self.assertRaisesRegex(ModelError, "UTF-8 bytes"):
                self.draft_set()
        self.assertEqual([], self.repository.changes.audit_log()["events"])

    def test_multi_accepts_exact_aggregate_boundary_and_reordered_targets(self):
        self.prepare_set()
        paths = ["one.txt", "two.txt"]
        for path in paths:
            (self.root / path).write_bytes(b"a" * MAX_CHANGE_BYTES)
        self.provider.chat.return_value = json.dumps({"files": [
            {"path": path, "content": "b" * MAX_CHANGE_BYTES} for path in reversed(paths)
        ]})
        response = self.draft_set(paths)
        self.assertEqual(list(reversed(paths)), [entry["proposal"]["path"] for entry in response["changes"]])
        for path in paths:
            self.assertEqual(b"a" * MAX_CHANGE_BYTES, (self.root / path).read_bytes())

    def test_multi_syntax_or_noop_failure_leaves_no_subset(self):
        self.prepare_set()
        for content, exception in (('{"value":}', ProjectSyntaxError), ('{"value":1}\n', ValueError)):
            self.entries[1]["content"] = content
            self.provider.chat.return_value = json.dumps({"files": self.entries})
            with self.assertRaises(exception):
                self.draft_set()
            self.assertEqual([], self.repository.changes.audit_log()["events"])
            self.assertEqual({}, self.repository.changes._proposals)
        self.assertEqual(self.original, self.target.read_bytes())

    def test_multi_every_source_hash_checked_after_inference(self):
        self.prepare_set()
        for target in (self.target, self.second):
            def inference(_messages, *, json_mode=False):
                target.write_bytes(b"changed externally\n")
                return json.dumps({"files": self.entries})

            self.provider.chat.side_effect = inference
            with self.subTest(target=target), self.assertRaises(ProjectDraftConflict):
                self.draft_set()
            self.assertEqual([], self.repository.changes.audit_log()["events"])
            self.target.write_bytes(self.original)
            self.second.write_bytes(b'{"value":1}\n')

    def test_multi_and_single_generation_share_lock_and_release_after_failure(self):
        self.prepare_set()
        entered, release = Event(), Event()
        errors = []

        def inference(_messages, *, json_mode=False):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("Test did not release inference")
            raise ModelError("Controlled provider failure")

        def worker():
            try:
                self.draft_set()
            except ModelError as exc:
                errors.append(str(exc))

        self.provider.chat.side_effect = inference
        thread = Thread(target=worker)
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            for operation in (self.draft, self.draft_set):
                with self.assertRaisesRegex(ProjectDraftConflict, "already running"):
                    operation()
        finally:
            release.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(["Controlled provider failure"], errors)
        self.provider.chat.side_effect = None
        self.assertEqual(2, len(self.draft_set()["changes"]))

    def test_multi_baseline_map_requires_exact_paths(self):
        self.prepare_set()
        for baselines in ({}, {"sample.py": "wrong"}, {"sample.py": "wrong", "other.json": "wrong", "extra.py": "wrong"}):
            with self.subTest(baselines=baselines), self.assertRaisesRegex(ValueError, "exactly"):
                self.repository.validation.propose_many(self.entries, expected_sha256=baselines)
        self.assertEqual([], self.repository.changes.audit_log()["events"])


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

    def test_project_chat_accepts_full_message_limit(self):
        message = ("Explain behavior. " * 240)[:3993] + " value."
        self.provider.chat.return_value = "The sample sets value to one."
        code, result = self.post({"message": message}, "/api/chat")
        self.assertEqual(200, code)
        self.assertEqual("The sample sets value to one.", result["reply"])
        messages = self.provider.chat.call_args.args[0]
        self.assertEqual(message, messages[-1]["content"])
        self.assertIn("[sample.py]", messages[-2]["content"])

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

    def test_http_multi_draft_requires_confirmation_and_separate_apply(self):
        second = self.target.parent / "other.json"
        second.write_text('{"value":1}\n')
        self.provider.chat.return_value = json.dumps({"files": [
            {"path": "sample.py", "content": "value = 2\n"},
            {"path": "other.json", "content": '{"value":2}\n'},
        ]})
        payload = {"paths": ["sample.py", "other.json"], "instruction": "Change both", "confirm": True}
        endpoint = "/api/project/draft-set"
        self.assertEqual(403, self.post({**payload, "confirm": 1}, endpoint)[0])
        self.assertEqual(403, self.post(payload, endpoint, {"Origin": "https://untrusted.invalid"})[0])
        self.provider.chat.assert_not_called()
        code, result = self.post(payload, endpoint)
        self.assertEqual(201, code)
        self.assertEqual(2, len(result["changes"]))
        self.assertFalse(result["tests_executed"])
        self.assertFalse(result["atomic_apply"])
        self.assertEqual("value = 1\n", self.target.read_text())
        path = "/api/project/proposals/" + result["changes"][0]["proposal"]["id"] + "/apply"
        self.assertEqual(403, self.post({}, path)[0])
        self.assertEqual(200, self.post({"confirm": True}, path)[0])
        self.assertEqual("value = 2\n", self.target.read_text())
        self.assertEqual('{"value":1}\n', second.read_text())
        self.assertEqual([], self.core.history())

    def test_http_multi_errors_have_explicit_status(self):
        payload = {"paths": ["sample.py"], "instruction": "Change", "confirm": True}
        endpoint = "/api/project/draft-set"
        self.assertEqual(400, self.post({**payload, "paths": []}, endpoint)[0])
        self.assertEqual(503, self.post(payload, endpoint)[0])
        self.provider.chat.return_value = json.dumps({"files": [{"path": "sample.py", "content": "def ("}]})
        self.assertEqual(422, self.post(payload, endpoint)[0])
        with patch("astra.server.ProjectDrafting.draft_many", side_effect=ProjectDraftConflict("File changed")):
            self.assertEqual(409, self.post(payload, endpoint)[0])
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
                for endpoint in ("/api/project/draft", "/api/project/draft-set"):
                    connection.request("POST", endpoint,
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
