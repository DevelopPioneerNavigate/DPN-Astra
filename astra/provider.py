"""Ollama model transport; no simulated AI response on provider failure."""
import json
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .inference import ChatMessage, ModelError, ModelHealth


MAX_RESPONSE_BYTES = 4 * 1024 * 1024


def validate_ollama_url(url: str) -> str:
    parts = urlsplit(url)
    if (
        parts.scheme != "http"
        or parts.hostname not in ("127.0.0.1", "localhost", "::1")
        or parts.username
        or parts.password
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
    ):
        raise ValueError("ASTRA_OLLAMA_URL must be a loopback HTTP endpoint in v0.1")
    if parts.port is not None and not 0 < parts.port < 65536:
        raise ValueError("Invalid Ollama port")
    return url.rstrip("/")


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        fp.close()
        raise ModelError("Ollama redirects are not allowed; configure its loopback endpoint directly.")


class OllamaProvider:
    name = "Ollama"

    def __init__(self, base_url: str, model: str):
        self.base_url = validate_ollama_url(base_url)
        self.model = model
        self._opener = build_opener(ProxyHandler({}), _NoRedirects())

    def _request_json(self, request: Request, timeout: int) -> dict[str, object]:
        try:
            with self._opener.open(request, timeout=timeout) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            code = exc.code
            exc.close()
            raise ModelError(f"Ollama rejected the request (HTTP {code}). Check the service and configured model.") from exc
        except (URLError, TimeoutError, OSError, HTTPException) as exc:
            raise ModelError("Cannot reach the local Ollama server. Start Ollama and install the configured model.") from exc
        if len(body) > MAX_RESPONSE_BYTES:
            raise ModelError(f"Ollama response exceeds the {MAX_RESPONSE_BYTES}-byte limit.")
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeError, ValueError, RecursionError) as exc:
            raise ModelError("Ollama returned invalid JSON.") from exc
        if not isinstance(data, dict) or "error" in data:
            raise ModelError("Ollama returned an invalid response or reported a backend error.")
        return data

    def health(self) -> ModelHealth:
        try:
            data = self._request_json(Request(self.base_url + "/api/tags"), timeout=2)
            models = data.get("models")
            if not isinstance(models, list):
                raise ModelError("Ollama returned an invalid model list.")
            installed = []
            for entry in models:
                if not isinstance(entry, dict):
                    raise ModelError("Ollama returned an invalid model entry.")
                name = entry.get("name")
                if not isinstance(name, str) or not name.strip():
                    raise ModelError("Ollama returned an invalid model name.")
                installed.append(name)
            installed_match = self.model in installed or (
                ":" not in self.model and self.model + ":latest" in installed
            )
            health: ModelHealth = {
                "connected": True,
                "model_installed": installed_match,
                "installed_models": installed[:20],
            }
            if not installed_match:
                health["error"] = "The configured model is not installed in local Ollama."
            return health
        except ModelError as exc:
            return {
                "connected": False,
                "model_installed": False,
                "installed_models": [],
                "error": str(exc),
            }

    def chat(self, messages: list[ChatMessage], *, json_mode: bool = False) -> str:
        body: dict[str, object] = {"model": self.model, "messages": messages, "stream": False, "options": {"temperature": 0.65}}
        if json_mode:
            body["format"] = "json"
        payload = json.dumps(body).encode("utf-8")
        request = Request(self.base_url + "/api/chat", data=payload, headers={"Content-Type": "application/json"}, method="POST")
        data = self._request_json(request, timeout=120)
        message = data.get("message")
        if not isinstance(message, dict):
            raise ModelError("Ollama returned an invalid chat message.")
        answer = message.get("content")
        if not isinstance(answer, str):
            raise ModelError("Ollama returned invalid chat content.")
        if not answer.strip():
            raise ModelError("The selected model returned an empty response.")
        return answer.strip()
