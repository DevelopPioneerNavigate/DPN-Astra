"""Bounded, non-executing syntax checks for supported project text files."""
import ast
import json
from pathlib import Path
import tomllib

from .project_changes import ProjectChanges


MAX_VALIDATION_BYTES = 24 * 1024
VALIDATORS = {
    ".json": "JSON",
    ".py": "Python",
    ".toml": "TOML",
}


class ProjectValidation:
    def __init__(self, changes: ProjectChanges):
        self.changes = changes

    def validate(self, relative_path: str, content: str | None = None) -> dict:
        file = self.changes.read_file(relative_path)
        suffix = Path(file["path"]).suffix.lower()
        language = VALIDATORS.get(suffix)
        if language is None:
            raise ValueError("Static validation currently supports Python, JSON, and TOML files")
        if content is not None:
            if not isinstance(content, str):
                raise ValueError("Validation content must be text")
            if len(content.encode("utf-8")) > MAX_VALIDATION_BYTES:
                raise ValueError(f"Validation content exceeds {MAX_VALIDATION_BYTES} bytes")
            source = content
        else:
            source = file["content"]
        try:
            if suffix == ".py":
                ast.parse(source, filename=file["path"])
            elif suffix == ".json":
                json.loads(source, parse_constant=self._reject_nonstandard_json)
            else:
                tomllib.loads(source)
        except SyntaxError as exc:
            return {
                "path": file["path"],
                "language": language,
                "valid": False,
                "error": {
                    "message": exc.msg,
                    "line": exc.lineno,
                    "column": exc.offset,
                },
            }
        except json.JSONDecodeError as exc:
            return {
                "path": file["path"],
                "language": language,
                "valid": False,
                "error": {
                    "message": exc.msg,
                    "line": exc.lineno,
                    "column": exc.colno,
                },
            }
        except tomllib.TOMLDecodeError as exc:
            return {
                "path": file["path"],
                "language": language,
                "valid": False,
                "error": {
                    "message": str(exc),
                    "line": getattr(exc, "lineno", None),
                    "column": getattr(exc, "colno", None),
                },
            }
        except ValueError as exc:
            return {
                "path": file["path"],
                "language": language,
                "valid": False,
                "error": {"message": str(exc), "line": None, "column": None},
            }
        return {
            "path": file["path"],
            "language": language,
            "valid": True,
            "error": None,
        }

    @staticmethod
    def _reject_nonstandard_json(value):
        raise ValueError(f"Non-standard JSON constant {value!r}")
