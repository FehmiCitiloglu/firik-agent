"""Loopback-only HTTP server for the Firik Agent model library UI."""

from __future__ import annotations

import json
import mimetypes
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from socketserver import TCPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .model_catalog import DownloadManager, ModelCatalog, ModelCatalogError

_MAX_BODY_BYTES = 16_384
_STATIC_ROOT = files("firik_agent").joinpath("web")


class ModelLibraryApplication:
    """Application services shared by HTTP handler instances."""

    def __init__(
        self,
        catalog: ModelCatalog | None = None,
        downloads: DownloadManager | None = None,
    ) -> None:
        self.catalog = catalog or ModelCatalog()
        self.downloads = downloads or DownloadManager()


class ModelLibraryServer(ThreadingHTTPServer):
    """Threaded server that is intentionally restricted to loopback."""

    daemon_threads = True

    def __init__(self, address: tuple[str, int], app: ModelLibraryApplication) -> None:
        if address[0] not in {"127.0.0.1", "localhost"}:
            raise ValueError("Model UI may only bind to loopback")
        self.app = app
        super().__init__(address, ModelLibraryHandler)

    def server_bind(self) -> None:
        """Bind without HTTPServer's unnecessary reverse-DNS lookup."""
        TCPServer.server_bind(self)
        self.server_name = "localhost"
        self.server_port = int(self.server_address[1])


class ModelLibraryHandler(BaseHTTPRequestHandler):
    server: ModelLibraryServer

    def do_GET(self) -> None:
        if not self._valid_host():
            self._json_error(HTTPStatus.BAD_REQUEST, "Invalid Host header")
            return
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/health":
                self._json(HTTPStatus.OK, {"ok": True, "service": "firik-agent-models"})
            elif parsed.path == "/api/system":
                self._json(HTTPStatus.OK, self.server.app.catalog.storage())
            elif parsed.path == "/api/models/search":
                query = parse_qs(parsed.query)
                term = query.get("q", [""])[0]
                limit = self._bounded_int(query.get("limit", ["12"])[0], 1, 25)
                self._json(
                    HTTPStatus.OK, {"models": self.server.app.catalog.search(term, limit=limit)}
                )
            elif parsed.path == "/api/models/local":
                self._json(HTTPStatus.OK, {"models": self.server.app.catalog.local_models()})
            elif parsed.path.startswith("/api/models/"):
                model_id = unquote(parsed.path.removeprefix("/api/models/"))
                self._json(HTTPStatus.OK, self.server.app.catalog.detail(model_id))
            elif parsed.path == "/api/downloads":
                self._json(HTTPStatus.OK, {"downloads": self.server.app.downloads.list()})
            elif parsed.path.startswith("/api/downloads/"):
                job_id = parsed.path.removeprefix("/api/downloads/")
                job = self.server.app.downloads.get(job_id)
                if job is None:
                    self._json_error(HTTPStatus.NOT_FOUND, "Download not found")
                else:
                    self._json(HTTPStatus.OK, job)
            elif parsed.path.startswith("/api/"):
                self._json_error(HTTPStatus.NOT_FOUND, "API route not found")
            else:
                self._static(parsed.path)
        except ModelCatalogError as exc:
            self._json_error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:
            self._json_error(HTTPStatus.BAD_GATEWAY, f"Model service error: {str(exc)[:300]}")

    def do_POST(self) -> None:
        if not self._valid_host():
            self._json_error(HTTPStatus.BAD_REQUEST, "Invalid Host header")
            return
        if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
            self._json_error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Expected application/json")
            return
        try:
            body = self._read_json()
            path = urlparse(self.path).path
            if path == "/api/downloads":
                model_id = body.get("model_id")
                if not isinstance(model_id, str):
                    raise ModelCatalogError("model_id is required")
                self._json(HTTPStatus.ACCEPTED, self.server.app.downloads.start(model_id))
                return
            if path.startswith("/api/downloads/"):
                parts = path.removeprefix("/api/downloads/").split("/")
                if len(parts) != 2:
                    raise ModelCatalogError("Invalid download action")
                job_id, action = parts
                operations = {
                    "pause": self.server.app.downloads.pause,
                    "resume": self.server.app.downloads.resume,
                    "cancel": self.server.app.downloads.cancel,
                }
                operation = operations.get(action)
                if operation is None:
                    raise ModelCatalogError("Invalid download action")
                self._json(HTTPStatus.OK, operation(job_id))
                return
            self._json_error(HTTPStatus.NOT_FOUND, "API route not found")
        except ModelCatalogError as exc:
            self._json_error(HTTPStatus.BAD_REQUEST, str(exc))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json_error(HTTPStatus.BAD_REQUEST, "Invalid JSON body")

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.METHOD_NOT_ALLOWED)
        self.end_headers()

    def log_message(self, message: str, *args: Any) -> None:
        return None

    def _static(self, request_path: str) -> None:
        relative = request_path.lstrip("/") or "index.html"
        if ".." in Path(relative).parts:
            self._json_error(HTTPStatus.BAD_REQUEST, "Invalid asset path")
            return
        resource = _STATIC_ROOT.joinpath(relative)
        if not resource.is_file():
            resource = _STATIC_ROOT.joinpath("index.html")
        if not resource.is_file():
            self._json_error(HTTPStatus.SERVICE_UNAVAILABLE, "Web UI assets are not built")
            return
        payload = resource.read_bytes()
        media_type = mimetypes.guess_type(str(resource))[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self._security_headers()
        self.send_header("Content-Type", media_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _read_json(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ModelCatalogError("Content-Length is required")
        length = self._bounded_int(raw_length, 0, _MAX_BODY_BYTES)
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ModelCatalogError("JSON body must be an object")
        return payload

    def _json(self, status: HTTPStatus, payload: Any) -> None:
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self._security_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _json_error(self, status: HTTPStatus, message: str) -> None:
        self._json(status, {"error": message[:500]})

    def _security_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'"
        )
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")

    def _valid_host(self) -> bool:
        host = self.headers.get("Host", "").split(":", 1)[0].lower()
        return host in {"127.0.0.1", "localhost"}

    @staticmethod
    def _bounded_int(value: str, minimum: int, maximum: int) -> int:
        try:
            number = int(value)
        except ValueError as exc:
            raise ModelCatalogError("Expected an integer") from exc
        if not minimum <= number <= maximum:
            raise ModelCatalogError(f"Integer must be between {minimum} and {maximum}")
        return number


def serve_model_library(*, port: int = 7860, open_browser: bool = True) -> None:
    """Serve the model library until interrupted."""
    if not 1024 <= port <= 65535:
        raise ValueError("Port must be between 1024 and 65535")
    server = ModelLibraryServer(("127.0.0.1", port), ModelLibraryApplication())
    url = f"http://127.0.0.1:{port}"
    print(f"Firik Agent model library: {url}")
    if open_browser:
        threading.Timer(0.2, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping model library")
    finally:
        server.server_close()
