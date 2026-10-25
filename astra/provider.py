"""Ollama model transport; no simulated AI response on provider failure."""
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ModelError(RuntimeError):
    pass


class OllamaProvider:
    def __init__(self, base_url: str, model: str):
        self.base_url = base_url
        self.model = model

    def health(self):
        try:
            with urlopen(Request(self.base_url + "/api/tags"), timeout=2) as response:
                data = json.load(response)
            installed = [m.get("name", "") for m in data.get("models", [])]
            installed_match = self.model in installed or (":" not in self.model and self.model + ":latest" in installed)
            return {"connected": True, "model_installed": installed_match, "installed_models": installed[:20]}
        except (HTTPError, URLError, TimeoutError, ValueError, OSError):
            return {"connected": False, "model_installed": False, "installed_models": []}

    def chat(self, messages):
        payload = json.dumps({"model": self.model, "messages": messages, "stream": False, "options": {"temperature": 0.65}}).encode("utf-8")
        request = Request(self.base_url + "/api/chat", data=payload, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=120) as response:
                data = json.load(response)
        except HTTPError as exc:
            raise ModelError("Ollama rejected the request. Check that the selected model is installed.") from exc
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            raise ModelError("Cannot reach the local Ollama server. Start Ollama and install the configured model.") from exc
        answer = data.get("message", {}).get("content", "")
        if not isinstance(answer, str) or not answer.strip():
            raise ModelError("The selected model returned an empty response.")
        return answer.strip()
