"""Bounded, non-executing syntax checks for supported project text files."""
import ast
import json
from pathlib import Path
import tomllib

from .project_changes import ProjectChanges


MAX_VALIDATION_BYTES = 24 * 1024
MAX_PROJECT_DIAGNOSTICS = 100
VALIDATORS = {
    ".json": "JSON",
    ".py": "Python",
    ".toml": "TOML",
}


class ProjectValidation:
    def __init__(self, changes: ProjectChanges, list_files):
        self.changes = changes
        self.list_files = list_files

    def validate(self, relative_path: str, content: str | None = None) -> dict:
        file = self.changes.read_file(relative_path)
        suffix = Path(file["path"]).suffix.lower()
        language = VALIDATORS.get(suffix)
        if language is None:
            raise ValueError("Static validation currently supports Python, JSON, and TOML files")
        if content is not None:
            if not isinstance(content, str):
                raise ValueError("Validation content must be text")
            try:
                content_bytes = content.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise ValueError("Validation content must contain valid Unicode") from exc
            if len(content_bytes) > MAX_VALIDATION_BYTES:
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
        except RecursionError:
            return {
                "path": file["path"],
                "language": language,
                "valid": False,
                "error": {
                    "message": "Nesting is too deep for safe static validation",
                    "line": None,
                    "column": None,
                },
            }
        return {
            "path": file["path"],
            "language": language,
            "valid": True,
            "error": None,
        }

    def validate_project(self) -> dict:
        inventory = self.list_files()
        results = []
        skipped = []
        for file in inventory["files"]:
            suffix = Path(file["path"]).suffix.lower()
            if suffix not in VALIDATORS:
                continue
            if file["bytes"] > MAX_VALIDATION_BYTES:
                skipped.append({
                    "path": file["path"],
                    "reason": f"File exceeds {MAX_VALIDATION_BYTES} byte validation limit",
                })
                continue
            try:
                results.append(self.validate(file["path"]))
            except (OSError, ValueError) as exc:
                skipped.append({"path": file["path"], "reason": str(exc)[:300]})

        valid_count = sum(result["valid"] for result in results)
        invalid_count = len(results) - valid_count
        diagnostics = [
            result for result in results if not result["valid"]
        ] + [
            {"path": file["path"], "language": None, "valid": False, "error": {
                "message": "Skipped: " + file["reason"],
                "line": None,
                "column": None,
            }}
            for file in skipped
        ]
        return {
            "results": diagnostics[:MAX_PROJECT_DIAGNOSTICS],
            "truncated": len(diagnostics) > MAX_PROJECT_DIAGNOSTICS,
            "warnings": inventory["warnings"],
            "summary": {
                "checked": len(results),
                "valid": valid_count,
                "invalid": invalid_count,
                "skipped": len(skipped),
            },
        }

    @staticmethod
    def _reject_nonstandard_json(value):
        raise ValueError(f"Non-standard JSON constant {value!r}")
