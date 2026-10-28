from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch

from astra.archive import Archive
from astra.core import AstraCore
from astra.inference import ChatMessage, InferenceProvider, ModelHealth
from astra.provider import ModelError, OllamaProvider
from astra.server import make_handler


class OllamaTransportTests(unittest.TestCase):
    def setUp(self):
        self.payload = {"models": [{"name": "local-model:latest"}]}
        self.status = 200
        self.headers = {}
        self.requests = []
        test = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.respond()

            def do_POST(self):
                self.respond()

            def respond(self):
                length = int(self.headers.get("Content-Length", "0"))
                test.requests.append(
                    (self.command, self.path, self.rfile.read(length))
                )
                body = (
                    test.payload
                    if isinstance(test.payload, bytes)
                    else json.dumps(test.payload).encode("utf-8")
                )
                self.send_response(test.status)
                for name, value in test.headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(self.server.shutdown)
        self.provider = OllamaProvider(
            f"http://127.0.0.1:{self.server.server_port}", "local-model"
        )

    def test_discovers_local_models_and_latest_alias(self):
        health = self.provider.health()
        self.assertTrue(health["connected"])
        self.assertTrue(health["model_installed"])
        self.assertEqual(["local-model:latest"], health["installed_models"])
        self.assertEqual([("GET", "/api/tags", b"")], self.requests)

    def test_health_rejects_malformed_response_shapes(self):
        for payload in (
            [],
            None,
            {},
            {"models": None},
            {"models": [None]},
            {"models": [{"name": 42}]},
            {"models": [{"name": ""}]},
            b"not-json",
            b"\xff",
        ):
            with self.subTest(payload=payload):
                self.payload = payload
                health = self.provider.health()
                self.assertFalse(health["connected"])
                self.assertFalse(health["model_installed"])
                self.assertEqual([], health["installed_models"])
                self.assertIn("invalid", health["error"].lower())

    def test_chat_posts_messages_and_returns_trimmed_answer(self):
        self.payload = {"message": {"content": "  A local answer. \n"}}
        messages = [{"role": "user", "content": "Explain this code."}]
        self.assertEqual("A local answer.", self.provider.chat(messages))
        method, path, body = self.requests[0]
        self.assertEqual(("POST", "/api/chat"), (method, path))
        self.assertEqual(
            {
                "model": "local-model",
                "messages": messages,
                "stream": False,
                "options": {"temperature": 0.65},
            },
            json.loads(body),
        )

    def test_chat_rejects_invalid_shapes_without_attribute_errors(self):
        for payload in (
            [],
            None,
            {},
            {"message": None},
            {"message": {"content": 42}},
            {"message": {"content": " \n"}},
            {"error": "backend failure"},
            b"not-json",
            b"\xff",
        ):
            with self.subTest(payload=payload):
                self.payload = payload
                with self.assertRaises(ModelError):
                    self.provider.chat([{"role": "user", "content": "Hello"}])

    def test_missing_model_is_distinct_from_unreachable_provider(self):
        self.payload = {"models": [{"name": "another-model"}]}
        health = self.provider.health()
        self.assertTrue(health["connected"])
        self.assertFalse(health["model_installed"])
        self.assertIn("not installed", health["error"])

    def test_http_failure_reports_error_and_chat_fails(self):
        self.status = 500
        health = self.provider.health()
        self.assertFalse(health["connected"])
        self.assertIn("HTTP 500", health["error"])
        with self.assertRaisesRegex(ModelError, "HTTP 500"):
            self.provider.chat([{"role": "user", "content": "Hello"}])

    def test_redirect_is_rejected_without_following_location(self):
        self.status = 302
        self.headers["Location"] = self.provider.base_url + "/redirect-target"
        health = self.provider.health()
        self.assertIn("redirects are not allowed", health["error"])
        self.assertEqual([("GET", "/api/tags", b"")], self.requests)
        with self.assertRaisesRegex(ModelError, "redirects are not allowed"):
            self.provider.chat([{"role": "user", "content": "Hello"}])
        self.assertEqual(2, len(self.requests))

    def test_responses_are_bounded_at_exact_byte_limit(self):
        self.payload = {"message": {"content": "A bounded answer"}}
        size = len(json.dumps(self.payload).encode("utf-8"))
        with patch("astra.provider.MAX_RESPONSE_BYTES", size):
            self.assertEqual(
                "A bounded answer",
                self.provider.chat([{"role": "user", "content": "Hello"}]),
            )
        with patch("astra.provider.MAX_RESPONSE_BYTES", size - 1):
            with self.assertRaisesRegex(ModelError, "byte limit"):
                self.provider.chat([{"role": "user", "content": "Hello"}])
            self.assertIn("byte limit", self.provider.health()["error"])

    def test_health_checks_all_models_before_capping_returned_inventory(self):
        self.payload = {
            "models": [
                {"name": f"other-{index}"} for index in range(20)
            ] + [{"name": "local-model:latest"}]
        }
        health = self.provider.health()
        self.assertTrue(health["model_installed"])
        self.assertEqual(20, len(health["installed_models"]))
        self.assertNotIn("local-model:latest", health["installed_models"])

    def test_timeouts_are_reported_with_no_success_fallback(self):
        with patch.object(self.provider._opener, "open", side_effect=TimeoutError):
            health = self.provider.health()
            self.assertFalse(health["connected"])
            self.assertIn("Cannot reach", health["error"])
            with self.assertRaisesRegex(ModelError, "Cannot reach"):
                self.provider.chat([{"role": "user", "content": "Hello"}])

    def test_proxy_environment_cannot_redirect_loopback_requests(self):
        with patch.dict(
            "os.environ",
            {
                "http_proxy": "http://127.0.0.1:1",
                "HTTP_PROXY": "http://127.0.0.1:1",
                "no_proxy": "",
                "NO_PROXY": "",
            },
        ):
            provider = OllamaProvider(self.provider.base_url, self.provider.model)
            self.assertTrue(provider.health()["connected"])

    def test_constructor_rejects_nonlocal_or_invalid_endpoints(self):
        for endpoint in (
            "https://127.0.0.1:11434",
            "http://remote.example:11434",
            "http://127.0.0.1:0",
            "http://127.0.0.1:70000",
            "http://127.0.0.1:not-a-port",
            "http://user:password@localhost:11434",
            "http://localhost:11434/api",
            "http://localhost:11434?query=value",
            "http://localhost:11434#fragment",
        ):
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(ValueError):
                    OllamaProvider(endpoint, "local-model")

    def test_readiness_and_chat_surface_malformed_backend_without_history(self):
        self.payload = []
        with tempfile.TemporaryDirectory() as directory:
            core = AstraCore(
                self.provider, Archive(Path(directory) / "memory.sqlite")
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(core))
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            connection = HTTPConnection("127.0.0.1", server.server_port, timeout=2)
            try:
                connection.request("GET", "/health/ready")
                response = connection.getresponse()
                self.assertEqual(503, response.status)
                self.assertEqual({"status": "not_ready"}, json.loads(response.read()))
                connection.request("GET", "/api/status")
                response = connection.getresponse()
                self.assertEqual(200, response.status)
                status = json.loads(response.read())
                self.assertEqual("Ollama", status["provider"])
                self.assertIn("invalid", status["model_status"]["error"].lower())
                connection.request(
                    "POST",
                    "/api/chat",
                    body=json.dumps({"message": "Hello"}),
                    headers={"Content-Type": "application/json"},
                )
                response = connection.getresponse()
                self.assertEqual(503, response.status)
                self.assertIn("invalid", json.loads(response.read())["error"].lower())
                self.assertEqual([], core.history())
            finally:
                connection.close()
                server.shutdown()
                thread.join()
                server.server_close()


class InferenceContractTests(unittest.TestCase):
    def test_core_accepts_a_structural_provider_without_ollama_identity(self):
        class TestLocalProvider:
            name = "Test local adapter"
            model = "test-model"

            def health(self) -> ModelHealth:
                return {
                    "connected": True,
                    "model_installed": True,
                    "installed_models": ["test-model"],
                }

            def chat(self, messages: list[ChatMessage]) -> str:
                return "Fixture answer: " + messages[-1]["content"]

        provider: InferenceProvider = TestLocalProvider()
        with tempfile.TemporaryDirectory() as directory:
            core = AstraCore(provider, Archive(Path(directory) / "memory.sqlite"))
            self.assertEqual("Fixture answer: Hello", core.chat("Hello"))
            self.assertEqual("Test local adapter", core.status()["provider"])
            self.assertEqual("test-model", core.status()["model"])
            self.assertEqual(
                [
                    {"role": "user", "content": "Hello"},
                    {"role": "assistant", "content": "Fixture answer: Hello"},
                ],
                core.history(),
            )


if __name__ == "__main__":
    unittest.main()
