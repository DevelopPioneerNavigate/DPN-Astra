"""Start ASTRA without installing third-party Python packages."""
from astra.archive import Archive
from astra.config import Config
from astra.core import AstraCore
from astra.provider import OllamaProvider
from astra.server import serve


def main():
    config = Config.from_env()
    archive = Archive(config.data_dir / "astra_memory.sqlite3")
    core = AstraCore(OllamaProvider(config.ollama_url, config.model), archive)
    serve(core, port=config.port)


if __name__ == "__main__":
    main()
