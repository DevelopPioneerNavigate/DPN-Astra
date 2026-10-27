from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
import os
import stat
import tempfile
from threading import Thread
import unittest
from pathlib import Path
from unittest.mock import Mock

from astra.archive import Archive
from astra.command import CommandDenied, execute
from astra.config import Config
from astra.core import AstraCore
from astra.provider import ModelError
from astra.server import make_handler


class AstraTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.archive = Archive(Path(self.tmp.name) / "memory.sqlite")
        self.provider = Mock()
        self.provider.model = "test-model"
        self.provider.chat.return_value = "I can help you investigate that, Doctor."
        self.provider.health.return_value = {"connected": True, "model_installed": True, "installed_models": ["test-model"]}
        self.core = AstraCore(self.provider, self.archive)

    def test_explicit_memory_crud(self):
        self.assertEqual([], self.archive.list_memories())
        memory = self.archive.remember("My research priority is instrument calibration.")
        self.assertEqual(1, len(self.archive.list_memories()))
        self.assertTrue(self.archive.forget(memory["id"]))
        self.assertEqual([], self.archive.list_memories())

    def test_chat_history_is_in_memory_only(self):
        self.assertIn("Doctor", self.core.chat("How is the sensor project?"))
        self.assertEqual(2, len(self.core.history()))
        self.core.clear_history()
        self.assertEqual([], self.core.history())

    def test_user_saved_notes_in_prompt(self):
        self.archive.remember("Lab preference: document every test.")
        self.core.chat("Hello")
        messages = self.provider.chat.call_args[0][0]
        self.assertIn("document every test", messages[0]["content"])

    def test_invalid_payloads_are_rejected(self):
        for bad in ("", "x" * 4001, 5):
            with self.assertRaises(ValueError):
                self.core.chat(bad)
        with self.assertRaises(ValueError):
            self.core.history("../../files")
        with self.assertRaises(ValueError):
            self.archive.remember(" ")

    def test_failed_model_request_creates_no_false_history(self):
        self.provider.chat.side_effect = ModelError("unavailable")
        with self.assertRaises(ModelError):
            self.core.chat("Hello")
        self.assertEqual([], self.core.history())

    def test_command_is_disabled(self):
        with self.assertRaises(CommandDenied):
            execute("unlock-door", {"target": "SOC-201"})

    def test_status_truthfulness(self):
        status = self.core.status()
        self.assertEqual("disabled", status["modules"]["COMMAND"])
        self.assertFalse(status["project_context"]["enabled"])
        self.assertFalse(status["security"]["remote_actions"])

    def test_config_rejects_remote_model(self):
        from unittest.mock import patch
        with patch.dict("os.environ", {"ASTRA_OLLAMA_URL": "http://remote.example:11434"}):
            with self.assertRaises(ValueError):
                Config.from_env()

    def test_config_prepares_persistent_data_directory(self):
        data_dir = Path(self.tmp.name) / "nested" / "astra-data"
        config = Config(data_dir=data_dir)

        prepared = config.prepare_data_dir()

        self.assertEqual(data_dir.resolve(), prepared)
        self.assertTrue(prepared.is_dir())
        self.assertEqual(prepared, config.prepare_data_dir())
        if os.name == "posix":
            self.assertEqual(0o700, stat.S_IMODE(prepared.stat().st_mode))
            self.assertEqual(0o700, stat.S_IMODE(prepared.parent.stat().st_mode))

    def test_data_directory_path_must_not_be_a_file(self):
        file_path = Path(self.tmp.name) / "not-a-directory"
        file_path.write_text("existing data", encoding="utf-8")

        with self.assertRaises(ValueError):
            Config(data_dir=file_path).prepare_data_dir()

    def test_liveness_and_readiness_health_endpoints(self):
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0), make_handler(self.core)
        )
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = HTTPConnection(
            "127.0.0.1", server.server_address[1], timeout=2
        )
        self.addCleanup(connection.close)
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        host = f"127.0.0.1:{server.server_address[1]}"

        connection.request("GET", "/health/live", headers={"Host": host})
        live = connection.getresponse()
        self.assertEqual(200, live.status)
        self.assertEqual({"status": "alive"}, json.loads(live.read()))
        self.provider.health.assert_not_called()

        connection.request(
            "GET",
            "/health/live",
            headers={"Host": f"untrusted.example:{server.server_address[1]}"},
        )
        rejected = connection.getresponse()
        self.assertEqual(403, rejected.status)
        rejected.read()

        self.provider.health.return_value = {
            "connected": False,
            "model_installed": False,
            "installed_models": [],
        }
        connection.request("GET", "/health/ready", headers={"Host": host})
        unavailable = connection.getresponse()
        self.assertEqual(503, unavailable.status)
        self.assertEqual({"status": "not_ready"}, json.loads(unavailable.read()))

        self.provider.health.return_value = {
            "connected": True,
            "model_installed": True,
            "installed_models": ["test-model"],
        }
        connection.request("GET", "/health/ready", headers={"Host": host})
        ready = connection.getresponse()
        self.assertEqual(200, ready.status)
        self.assertEqual({"status": "ready"}, json.loads(ready.read()))


    def test_ollama_latest_tag_is_recognized(self):
        from astra.provider import OllamaProvider
        from unittest.mock import patch
        from io import BytesIO

        class FakeResponse:
            def __enter__(self):
                return BytesIO(b'{"models":[{"name":"llama3.2:latest"}]}')
            def __exit__(self, *args):
                return False

        provider = OllamaProvider("http://127.0.0.1:11434", "llama3.2")
        with patch("astra.provider.urlopen", return_value=FakeResponse()):
            result = provider.health()
        self.assertTrue(result["connected"])
        self.assertTrue(result["model_installed"])



if __name__ == "__main__":
    unittest.main()
