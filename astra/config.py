"""Local-first configuration with conservative network defaults."""
from dataclasses import dataclass
import os
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Config:
    model: str = "llama3.2"
    ollama_url: str = "http://127.0.0.1:11434"
    data_dir: Path = Path.home() / ".dpn-astra"
    port: int = 8766

    @classmethod
    def from_env(cls) -> "Config":
        model = os.getenv("ASTRA_MODEL", "llama3.2").strip()
        url = os.getenv("ASTRA_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
        parts = urlsplit(url)
        if parts.scheme != "http" or parts.hostname not in ("127.0.0.1", "localhost", "::1") or parts.username or parts.password or parts.path not in ("", "/") or parts.query or parts.fragment:
            raise ValueError("ASTRA_OLLAMA_URL must be a loopback HTTP endpoint in v0.1")
        if not model or len(model) > 100:
            raise ValueError("Invalid ASTRA_MODEL")
        port = int(os.getenv("ASTRA_PORT", "8766"))
        if not 0 < port < 65536:
            raise ValueError("ASTRA_PORT must be between 1 and 65535")
        return cls(model=model, ollama_url=url, data_dir=Path(os.getenv("ASTRA_DATA_DIR", str(Path.home() / ".dpn-astra"))).expanduser(), port=port)
