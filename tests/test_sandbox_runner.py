import json
from contextlib import closing
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from threading import Thread
from unittest.mock import Mock, patch

from astra.archive import Archive
from astra.core import AstraCore
from astra.permissions import RepositoryPermissions
from astra.project_audit import ProjectAuditLog
from astra.repository import RepositoryContext
from astra.sandbox import (
    MACHINE_NAME,
    PREFLIGHT,
    PROFILE,
    SandboxNotRunning,
    TEST_IMAGE,
    SandboxUnavailable,
    ProjectSandboxRunner,
)
from astra.server import make_handler


class SandboxRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "project"
        self.root.mkdir()
        self.audit_path = Path(self.tmp.name) / "audit.sqlite3"

    def repository(self, execute=True):
        return RepositoryContext(
            self.root,
            permissions=RepositoryPermissions(read=True, execute=execute),
            audit_path=self.audit_path,
        )

    @staticmethod
    def audit_store(repository):
        audit = repository.changes.audit_store
        if audit is None:
            raise AssertionError("Test repository must have persistent audit storage")
        return audit

    def test_command_is_fixed_offline_and_read_only(self):
        command = ProjectSandboxRunner._container_command(
            "podman.exe", r"C:\projects\astra", "astra-test-fixed",
        )

        self.assertEqual(TEST_IMAGE, command[command.index(TEST_IMAGE)])
        self.assertEqual(["python", "-c", PREFLIGHT], command[-3:])
        self.assertEqual("none", command[command.index("--network") + 1])
        self.assertEqual("ro", command[command.index("--volume") + 1].rsplit(":", 1)[1])
        self.assertIn("--read-only", command)
        self.assertIn("--pull=never", command)
        self.assertIn("--cap-drop=ALL", command)
        self.assertIn("--security-opt=no-new-privileges", command)
        self.assertEqual("1000:1000", command[command.index("--user") + 1])
        self.assertIn("nproc=128:128", command)
        self.assertIn("cpu=300:300", command)
        self.assertIn("/tmp:rw,nosuid,nodev,noexec,size=1g", command)
        self.assertNotIn("sh", command)
        self.assertIn('os.path.exists("/mnt/c")', PREFLIGHT)
        for invariant in (
            "network access unexpectedly succeeded",
            "effective capabilities are not empty",
            "no-new-privileges is not active",
            'must_be_read_only("/workspace", "project mount")',
        ):
            self.assertIn(invariant, PREFLIGHT)

    def test_readiness_verifies_wsl_machine_rootless_image_and_permissions(self):
        machine = [{
            "Name": MACHINE_NAME,
            "VMType": "wsl",
            "Running": True,
            "CPUs": 2,
            "Memory": str(4 * 1024**3),
        }]
        inspected = [{
            "Name": MACHINE_NAME,
            "State": "running",
            "Rootful": False,
            "Resources": {"CPUs": 2, "Memory": 4096},
        }]
        responses = [
            Mock(returncode=0, stdout=json.dumps(machine)),
            Mock(returncode=0, stdout=json.dumps(inspected)),
            Mock(returncode=0, stdout=json.dumps({
                "host": {"security": {"rootless": True}},
            })),
            Mock(returncode=0, stdout=""),
        ]
        runner = ProjectSandboxRunner(self.repository())
        with patch.object(runner, "_podman_path", return_value="podman"):
            with patch.object(runner, "_capture", side_effect=responses) as capture:
                readiness = runner.readiness()

        self.assertEqual("ready", readiness["status"])
        self.assertTrue(readiness["enabled"])
        self.assertFalse(readiness["isolation_verified"])
        self.assertFalse(readiness["resource_limits"]["container_memory_limit"])
        self.assertIn("verified per-container memory limit", readiness["reason"])
        self.assertEqual(4, capture.call_count)
        self.assertEqual(
            ["machine", "machine", "--connection", "--connection"],
            [call.args[0][1] for call in capture.call_args_list],
        )

    def test_readiness_does_not_enable_execution_without_opt_in(self):
        repository = self.repository(execute=False)
        runner = ProjectSandboxRunner(repository)
        with patch.object(runner, "_podman_path", return_value="podman"):
            with patch.object(runner, "_capture", side_effect=[
                Mock(returncode=0, stdout=json.dumps([{
                    "Name": MACHINE_NAME, "VMType": "wsl", "Running": True,
                    "CPUs": 2, "Memory": str(4 * 1024**3),
                }])),
                Mock(returncode=0, stdout=json.dumps([{
                    "Name": MACHINE_NAME, "State": "running", "Rootful": False,
                    "Resources": {"CPUs": 2, "Memory": 4096},
                }])),
                Mock(returncode=0, stdout=json.dumps({
                    "host": {"security": {"rootless": True}},
                })),
                Mock(returncode=0, stdout=""),
            ]):
                readiness = runner.readiness()

        self.assertEqual("permission_required", readiness["status"])
        self.assertFalse(readiness["permission_configured"])
        self.assertFalse(readiness["enabled"])

    def test_run_requires_execute_permission_confirmation_and_fixed_profile(self):
        runner = ProjectSandboxRunner(self.repository(execute=False))
        with self.assertRaises(PermissionError):
            runner.run_tests(confirmed=True, profile=PROFILE)

        runner = ProjectSandboxRunner(self.repository())
        with patch.object(runner, "readiness") as readiness:
            with self.assertRaises(PermissionError):
                runner.run_tests(confirmed=False, profile=PROFILE)
            with self.assertRaises(ValueError):
                runner.run_tests(confirmed=True, profile="arbitrary")
        readiness.assert_not_called()

    def test_cancel_requires_confirmation_and_kills_only_active_process(self):
        runner = ProjectSandboxRunner(self.repository())
        process = Mock()
        process.poll.return_value = None
        runner._active_process = process
        runner._active_run_id = "run-123"

        with self.assertRaises(PermissionError):
            runner.cancel_tests(confirmed=False)
        process.kill.assert_not_called()

        result = runner.cancel_tests(confirmed=True)

        self.assertEqual({"requested": True, "run_id": "run-123"}, result)
        process.kill.assert_called_once_with()
        self.assertTrue(runner._finish_active(process))
        with self.assertRaises(SandboxNotRunning):
            runner.cancel_tests(confirmed=True)

    def test_project_audit_migrates_schema_three_and_verifies_long_chains(self):
        repository = self.repository()
        audit = self.audit_store(repository)
        with closing(audit._connect()) as db, db:
            db.execute("DROP TABLE sandbox_run_events")
            db.execute("PRAGMA user_version=3")
        migrated = ProjectAuditLog(self.audit_path, project_key=audit.project_key)
        for index in range(105):
            migrated.append_sandbox_run(
                run_id=f"{index:032x}",
                profile=PROFILE,
                image_digest=TEST_IMAGE.rsplit("@", 1)[1],
                state="completed",
                exit_code=0,
                duration_ms=index,
                output_truncated=False,
            )

        history = migrated.sandbox_runs()

        self.assertEqual(105, history["total"])
        self.assertEqual(100, len(history["events"]))
        self.assertTrue(history["truncated"])
        self.assertEqual({"valid": True, "checked": 105}, history["integrity"])

        with closing(audit._connect()) as db, db:
            db.execute(
                "UPDATE sandbox_run_events SET state='failed' WHERE run_id=?",
                ("00000000000000000000000000000001",),
            )
        corrupted = migrated.sandbox_runs()
        self.assertFalse(corrupted["integrity"]["valid"])
        self.assertEqual(2, corrupted["integrity"]["first_invalid_sequence"])

    def test_unavailable_backend_attempt_is_audited(self):
        repository = self.repository()
        runner = ProjectSandboxRunner(repository)
        with patch.object(runner, "readiness", return_value={
            "enabled": False,
            "reason": "Pinned sandbox is unavailable",
        }):
            with self.assertRaisesRegex(SandboxUnavailable, "unavailable"):
                runner.run_tests(confirmed=True, profile=PROFILE)

        history = self.audit_store(repository).sandbox_runs()
        self.assertEqual("runner_error", history["events"][0]["state"])
        self.assertIsNone(history["events"][0]["exit_code"])
        self.assertTrue(history["integrity"]["valid"])

    def test_output_capture_is_drained_but_capped(self):
        captured = []
        truncated = ProjectSandboxRunner._drain_output(
            BytesIO(b"x" * (64 * 1024 + 8)),
            captured,
        )
        self.assertTrue(truncated)
        self.assertEqual(64 * 1024, sum(map(len, captured)))

    def test_audit_history_is_project_scoped(self):
        first = self.repository()
        first_audit = self.audit_store(first)
        other_root = Path(self.tmp.name) / "other-project"
        other_root.mkdir()
        second = RepositoryContext(
            other_root,
            permissions=RepositoryPermissions(read=True, execute=True),
            audit_path=self.audit_path,
        )
        first_audit.append_sandbox_run(
            run_id="a" * 32,
            profile=PROFILE,
            image_digest=TEST_IMAGE.rsplit("@", 1)[1],
            state="completed",
            exit_code=0,
            duration_ms=1,
            output_truncated=False,
        )
        self.assertEqual(1, first_audit.sandbox_runs()["total"])
        self.assertEqual(0, self.audit_store(second).sandbox_runs()["total"])

    def test_http_endpoint_requires_execute_opt_in_and_rejects_extra_fields(self):
        provider = Mock()
        provider.name = "test"
        provider.model = "test"
        provider.health.return_value = {"connected": True, "model_installed": True}
        core = AstraCore(provider, Archive(Path(self.tmp.name) / "memory.sqlite"), self.repository(execute=False))
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(core))
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        self.addCleanup(connection.close)
        headers = {
            "Host": f"127.0.0.1:{server.server_port}",
            "Content-Type": "application/json",
        }

        connection.request("GET", "/api/project/sandbox-runs", headers={"Host": headers["Host"]})
        response = connection.getresponse()
        history = json.loads(response.read())
        self.assertEqual(200, response.status)
        self.assertEqual(0, history["total"])
        with patch.object(core.sandbox, "readiness", return_value={
            "status": "permission_required",
            "enabled": False,
        }):
            connection.request("GET", "/api/project/sandbox", headers={"Host": headers["Host"]})
            response = connection.getresponse()
            readiness = json.loads(response.read())
        self.assertEqual(200, response.status)
        self.assertEqual("permission_required", readiness["status"])

        connection.request(
            "POST",
            "/api/project/test-run",
            body=json.dumps({"confirm": True, "profile": PROFILE}),
            headers=headers,
        )
        response = connection.getresponse()
        denied = json.loads(response.read())
        self.assertEqual(403, response.status)
        self.assertIn("not authorized", denied["error"])

        connection.request(
            "POST",
            "/api/project/test-run",
            body=json.dumps({"confirm": True, "profile": PROFILE, "command": "whoami"}),
            headers=headers,
        )
        response = connection.getresponse()
        rejected = json.loads(response.read())
        self.assertEqual(400, response.status)
        self.assertIn("fixed profile", rejected["error"])

        connection.request(
            "POST",
            "/api/project/test-cancel",
            body=json.dumps({"confirm": True}),
            headers=headers,
        )
        response = connection.getresponse()
        denied = json.loads(response.read())
        self.assertEqual(403, response.status)
        self.assertIn("not authorized", denied["error"])


if __name__ == "__main__":
    unittest.main()
