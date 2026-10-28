"""Loopback-only HTTP API and local control console."""
import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .core import AstraCore
from .archive import MemoryConflict, MemoryNotFound, MemoryExportTooLarge
from .inference import ModelError
from .project_validation import ProjectSyntaxError
from .project_changes import ProjectDraftConflict
from .project_drafting import ProjectDrafting

WEB_DIR = Path(__file__).parent / "web"
ASSETS = {"/": ("index.html", "text/html; charset=utf-8"), "/app.css": ("app.css", "text/css; charset=utf-8"), "/project.css": ("project.css", "text/css; charset=utf-8"), "/app.js": ("app.js", "application/javascript; charset=utf-8")}
MAX_BODY = 65536


def make_handler(core: AstraCore):
    drafting = ProjectDrafting(core.provider, core.repository) if core.repository is not None else None

    class AstraHandler(BaseHTTPRequestHandler):
        def _headers(self, status, content_type, length, *, download_name=None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            if download_name is not None:
                self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()

        def _send(self, status, payload):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._headers(status, "application/json; charset=utf-8", len(body))
            self.wfile.write(body)

        def _authorized_host(self, write=False):
            port = self.server.server_address[1]
            allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
            if self.headers.get("Host", "").lower() not in allowed:
                self._send(403, {"error": "Untrusted Host header"})
                return False
            origin = self.headers.get("Origin")
            if write and origin and origin.lower() not in {"http://" + a for a in allowed}:
                self._send(403, {"error": "Cross-origin writes not allowed"})
                return False
            return True

        def _read_json(self):
            raw_length = self.headers.get("Content-Length", "")
            if not raw_length.isdecimal():
                raise ValueError("Content-Length required")
            length = int(raw_length)
            if length <= 0 or length > MAX_BODY:
                raise ValueError("Request body outside permitted size")
            try:
                obj = json.loads(self.rfile.read(length))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("Invalid JSON") from exc
            if not isinstance(obj, dict):
                raise ValueError("Expected a JSON object")
            return obj

        def _memory_scope(self, scope):
            if scope == "global":
                return ""
            if scope == "project" and core.project_key:
                return core.project_key
            raise ValueError("Memory scope must be global or an enabled current project")

        def do_GET(self):
            if not self._authorized_host():
                return
            url = urlsplit(self.path)
            if url.path in ASSETS:
                filename, mime = ASSETS[url.path]
                blob = (WEB_DIR / filename).read_bytes()
                self._headers(200, mime, len(blob))
                self.wfile.write(blob)
                return
            if url.path == "/health/live":
                self._send(200, {"status": "alive"})
            elif url.path == "/health/ready":
                health = core.provider.health()
                ready = health["connected"] and health["model_installed"]
                self._send(
                    200 if ready else 503,
                    {"status": "ready" if ready else "not_ready"},
                )
            elif url.path == "/api/status":
                self._send(200, core.status())
            elif url.path == "/api/history":
                try:
                    session = parse_qs(url.query).get("session", ["default"])[0]
                    self._send(200, {"messages": core.history(session)})
                except ValueError as exc:
                    self._send(400, {"error": str(exc)})
            elif url.path == "/api/memories":
                try:
                    query = parse_qs(url.query, keep_blank_values=True)
                    cursor = query.get("before_id")
                    if cursor is not None and (len(cursor) != 1 or not cursor[0].isascii() or not cursor[0].isdecimal()):
                        raise ValueError("Invalid memory cursor")
                    before_id = int(cursor[0]) if cursor is not None else None
                    scopes = query.get("scope", ["global"])
                    if len(scopes) != 1:
                        raise ValueError("Expected one memory scope")
                    self._send(200, core.archive.memory_page(
                        before_id=before_id, project_key=self._memory_scope(scopes[0]),
                    ))
                except ValueError as exc:
                    self._send(400, {"error": str(exc)})
                except sqlite3.Error:
                    self._send(503, {"error": "Memory storage unavailable"})
            elif url.path == "/api/project/files":
                if core.repository is None:
                    self._send(404, {"error": "Read-only project context is not enabled"})
                    return
                self._send(200, core.repository.files())
            elif url.path == "/api/project/search":
                if core.repository is None:
                    self._send(404, {"error": "Read-only project context is not enabled"})
                    return
                query = parse_qs(url.query).get("q", [""])[0]
                try:
                    self._send(200, core.repository.search(query))
                except ValueError as exc:
                    self._send(400, {"error": str(exc)})
            elif url.path == "/api/project/dependencies":
                if core.repository is None:
                    self._send(404, {"error": "Read-only project context is not enabled"})
                    return
                try:
                    self._send(200, core.repository.dependencies())
                except OSError as exc:
                    self._send(500, {"error": f"Could not inspect project dependencies: {exc.strerror or str(exc)}"})
            elif url.path == "/api/project/file":
                if core.repository is None:
                    self._send(404, {"error": "Read-only project context is not enabled"})
                    return
                relative_path = parse_qs(url.query).get("path", [""])[0]
                try:
                    self._send(200, core.repository.changes.read_file(relative_path))
                except (FileNotFoundError, NotADirectoryError):
                    self._send(404, {"error": "Project file was not found"})
                except ValueError as exc:
                    self._send(400, {"error": str(exc)})
            elif url.path == "/api/project/changes":
                if core.repository is None:
                    self._send(404, {"error": "Read-only project context is not enabled"})
                    return
                try:
                    self._send(200, core.repository.changes.audit_log())
                except OSError as exc:
                    self._send(500, {"error": f"Project audit log unavailable: {exc.strerror or str(exc)}"})
            elif url.path == "/api/project/checkpoints":
                if core.repository is None:
                    self._send(404, {"error": "Project context is not enabled"})
                    return
                try:
                    self._send(200, core.repository.changes.review_checkpoints())
                except (OSError, sqlite3.Error):
                    self._send(503, {"error": "Project review checkpoints unavailable"})
            elif url.path.startswith("/api/project/proposals/"):
                if core.repository is None:
                    self._send(404, {"error": "Project context is not enabled"})
                    return
                parts = url.path.strip("/").split("/")
                if len(parts) != 4:
                    self._send(404, {"error": "Not found"})
                    return
                try:
                    self._send(200, {"proposal": core.repository.changes.describe(parts[3])})
                except ValueError as exc:
                    self._send(404, {"error": str(exc)})
                except (OSError, sqlite3.Error):
                    self._send(503, {"error": "Project proposal state unavailable"})
            else:
                self._send(404, {"error": "Not found"})

        def do_POST(self):
            if not self._authorized_host(write=True):
                return
            try:
                body = self._read_json()
                path = urlsplit(self.path).path
                if path == "/api/chat":
                    reply = core.chat(body.get("message"), body.get("session", "default"))
                    self._send(200, {"reply": reply})
                elif path == "/api/memories":
                    self._send(201, {"memory": core.archive.remember(
                        body.get("note"),
                        kind=body.get("kind", "note"),
                        source=body.get("source", ""),
                        project_key=self._memory_scope(body.get("scope", "global")),
                    )})
                elif path == "/api/memories/export":
                    exported = core.archive.export_json()
                    self._headers(
                        200, "application/json; charset=utf-8", len(exported),
                        download_name="astra-memories.json",
                    )
                    self.wfile.write(exported)
                elif path.startswith("/api/memories/"):
                    parts = path.strip("/").split("/")
                    if len(parts) != 4 or parts[3] != "edit":
                        self._send(404, {"error": "Not found"})
                        return
                    self._send(200, {"memory": core.archive.edit(
                        int(parts[2]), body.get("note"), revision=body.get("revision"),
                        kind=body.get("kind", "note"), source=body.get("source", ""),
                        project_key=self._memory_scope(body.get("scope", "global")),
                    )})
                elif path == "/api/history/clear":
                    core.clear_history(body.get("session", "default"))
                    self._send(200, {"cleared": True})
                elif path == "/api/project/draft":
                    if drafting is None:
                        self._send(404, {"error": "Project context is not enabled"})
                        return
                    result = drafting.draft(
                        body.get("path"), body.get("instruction"), confirmed=body.get("confirm") is True,
                    )
                    self._send(201, result)
                elif path == "/api/project/draft-set":
                    if drafting is None:
                        self._send(404, {"error": "Project context is not enabled"})
                        return
                    self._send(201, drafting.draft_many(
                        body.get("paths"), body.get("instruction"), confirmed=body.get("confirm") is True,
                    ))
                elif path == "/api/project/change-set":
                    if core.repository is None:
                        self._send(404, {"error": "Project context is not enabled"})
                        return
                    self._send(201, core.repository.validation.propose_many(body.get("files")))
                elif path == "/api/project/proposals":
                    if core.repository is None:
                        self._send(404, {"error": "Read-only project context is not enabled"})
                        return
                    result = core.repository.validation.propose(body.get("path"), body.get("content"))
                    self._send(201, result)
                elif path == "/api/project/validate":
                    if core.repository is None:
                        self._send(404, {"error": "Read-only project context is not enabled"})
                        return
                    result = core.repository.validation.validate(
                        body.get("path"),
                        content=body.get("content"),
                    )
                    self._send(200, {"validation": result})
                elif path == "/api/project/validate-all":
                    if core.repository is None:
                        self._send(404, {"error": "Read-only project context is not enabled"})
                        return
                    self._send(200, {"validation": core.repository.validation.validate_project()})
                elif path.startswith("/api/project/proposals/"):
                    if core.repository is None:
                        self._send(404, {"error": "Read-only project context is not enabled"})
                        return
                    parts = path.strip("/").split("/")
                    if len(parts) != 5 or parts[2] != "proposals" or parts[4] not in {"apply", "rollback"}:
                        self._send(404, {"error": "Not found"})
                        return
                    proposal_id = parts[3]
                    if parts[4] == "apply":
                        proposal = core.repository.changes.apply(
                            proposal_id,
                            confirmed=body.get("confirm") is True,
                        )
                    else:
                        proposal = core.repository.changes.rollback(
                            proposal_id,
                            confirmed=body.get("confirm") is True,
                        )
                    self._send(200, {"proposal": proposal})
                else:
                    self._send(404, {"error": "Not found"})
            except ProjectSyntaxError as exc:
                self._send(422, {"error": str(exc), "validation": exc.result})
            except ProjectDraftConflict as exc:
                self._send(409, {"error": str(exc)})
            except MemoryConflict as exc:
                self._send(409, {"error": str(exc)})
            except MemoryNotFound as exc:
                self._send(404, {"error": str(exc)})
            except MemoryExportTooLarge as exc:
                self._send(413, {"error": str(exc)})
            except ValueError as exc:
                self._send(400, {"error": str(exc)})
            except PermissionError as exc:
                self._send(403, {"error": str(exc)})
            except (FileNotFoundError, NotADirectoryError):
                self._send(404, {"error": "Project file was not found"})
            except ModelError as exc:
                self._send(503, {"error": str(exc)})
            except sqlite3.Error:
                self._send(503, {"error": "Local database storage unavailable"})
            except OSError as exc:
                self._send(500, {"error": f"Project file operation failed: {exc.strerror or str(exc)}"})

        def do_DELETE(self):
            if not self._authorized_host(write=True):
                return
            path = urlsplit(self.path).path
            if not path.startswith("/api/memories/"):
                self._send(404, {"error": "Not found"})
                return
            try:
                value = int(path.rsplit("/", 1)[1])
                scopes = parse_qs(urlsplit(self.path).query, keep_blank_values=True).get("scope", ["global"])
                if len(scopes) != 1:
                    raise ValueError("Expected one memory scope")
                self._send(200, {"deleted": core.archive.forget(
                    value, project_key=self._memory_scope(scopes[0]),
                )})
            except ValueError:
                self._send(400, {"error": "Invalid memory ID"})
            except MemoryNotFound as exc:
                self._send(404, {"error": str(exc)})
            except sqlite3.Error:
                self._send(503, {"error": "Memory storage unavailable"})

    return AstraHandler


def serve(core, port=8766):
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(core))
    print(f"DPN ASTRA v0.1.0 | http://127.0.0.1:{server.server_address[1]}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nASTRA console shutting down")
    finally:
        server.server_close()
