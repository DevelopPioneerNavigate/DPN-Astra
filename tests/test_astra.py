import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from astra.archive import Archive
from astra.command import CommandDenied, execute
from astra.config import Config
from astra.core import AstraCore
from astra.provider import ModelError


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
        self.assertFalse(status["security"]["remote_actions"])

    def test_config_rejects_remote_model(self):
        from unittest.mock import patch
        with patch.dict("os.environ", {"ASTRA_OLLAMA_URL": "http://remote.example:11434"}):
            with self.assertRaises(ValueError):
                Config.from_env()


if __name__ == "__main__":
    unittest.main()
