import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from astra.sandbox import execution_readiness


class ExecutionReadinessTests(unittest.TestCase):
    def test_missing_runtimes_leave_execution_unavailable(self):
        with tempfile.TemporaryDirectory() as local_appdata:
            with patch.dict(os.environ, {"LOCALAPPDATA": local_appdata}):
                with patch("astra.sandbox.shutil.which", return_value=None):
                    readiness = execution_readiness()

        self.assertEqual("unavailable", readiness["status"])
        self.assertFalse(readiness["enabled"])
        self.assertFalse(readiness["isolation_verified"])
        self.assertTrue(all(
            state in {"not_found", "not_applicable"}
            for state in readiness["runtimes"].values()
        ))

    def test_detects_per_user_podman_install_when_path_is_stale(self):
        with tempfile.TemporaryDirectory() as local_appdata:
            podman = Path(local_appdata) / "Programs" / "Podman" / "podman.exe"
            podman.parent.mkdir(parents=True)
            podman.touch()
            with patch.dict(os.environ, {"LOCALAPPDATA": local_appdata}):
                with patch("astra.sandbox.shutil.which", return_value=None):
                    readiness = execution_readiness()

        self.assertEqual("present_unverified", readiness["runtimes"]["podman"])
        self.assertFalse(readiness["enabled"])
        self.assertFalse(readiness["isolation_verified"])

    def test_detected_runtime_and_permission_do_not_enable_execution(self):
        with patch("astra.sandbox.shutil.which", return_value="runtime.exe"):
            with patch("astra.sandbox.Path.is_file", return_value=True):
                readiness = execution_readiness(permission_configured=True)

        self.assertTrue(readiness["permission_configured"])
        self.assertFalse(readiness["enabled"])
        self.assertFalse(readiness["isolation_verified"])
        self.assertEqual("present_unverified", readiness["runtimes"]["docker"])
        self.assertEqual("present_unverified", readiness["runtimes"]["wsl"])
        self.assertIn("not executed", readiness["reason"])


if __name__ == "__main__":
    unittest.main()
