"""Loopback-only HTTP API and local control console."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .core import AstraCore
from .provider import ModelError

WEB_DIR = Path(__file__).parent / "web"
ASSETS = {"/": ("index.html", "text/html; charset=utf-8"), "/app.css": ("app.css", "text/css; charset=utf-8"), "/app.js": ("app.js", "application/javascript; charset=utf-8")}
MAX_BODY = 8192


def make_handler(core: AstraCore):
    class AstraHandler(BaseHTTPRequestHandler):
        def _headers(self, status, content_type, length):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
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
            if url.path == "/api/status":
                self._send(200, core.status())
            elif url.path == "/api/history":
                try:
                    session = parse_qs(url.query).get("session", ["default"])[0]
                    self._send(200, {"messages": core.history(session)})
                except ValueError as exc:
                    self._send(400, {"error": str(exc)})
            elif url.path == "/api/memories":
                self._send(200, {"memories": core.archive.list_memories()})
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
                    self._send(201, {"memory": core.archive.remember(body.get("note"))})
                elif path == "/api/history/clear":
                    core.clear_history(body.get("session", "default"))
                    self._send(200, {"cleared": True})
                else:
                    self._send(404, {"error": "Not found"})
            except ValueError as exc:
                self._send(400, {"error": str(exc)})
            except ModelError as exc:
                self._send(503, {"error": str(exc)})

        def do_DELETE(self):
            if not self._authorized_host(write=True):
                return
            path = urlsplit(self.path).path
            if not path.startswith("/api/memories/"):
                self._send(404, {"error": "Not found"})
                return
            try:
                value = int(path.rsplit("/", 1)[1])
                self._send(200, {"deleted": core.archive.forget(value)})
            except ValueError:
                self._send(400, {"error": "Invalid memory ID"})

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
