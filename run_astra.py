"""Start ASTRA without installing third-party Python packages."""
from astra.archive import Archive
from astra.config import Config
from astra.core import AstraCore
from astra.provider import OllamaProvider
from astra.repository import RepositoryContext
from astra.server import serve


def main():
    config = Config.from_env()
    archive = Archive(config.data_dir / "astra_memory.sqlite3")
    repository = (
        RepositoryContext(
            config.project_dir,
            audit_path=config.data_dir / "astra_project_audit.sqlite3",
        )
        if config.project_dir
        else None
    )
    core = AstraCore(OllamaProvider(config.ollama_url, config.model), archive, repository)
    serve(core, port=config.port)


if __name__ == "__main__":
    main()
