"""Local HTTP dashboard for the Boss workflow."""

from __future__ import annotations

import json
import logging
import mimetypes
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse, urlsplit

from ..workflow import init_db
from ..workflow.dashboard_service import DashboardService

STATIC_DIR = Path(__file__).with_name("static")
logger = logging.getLogger(__name__)
MAX_REQUEST_BODY_BYTES = 64 * 1024
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class DashboardRuntime:
    """Configuration shared by local dashboard request handlers."""

    def __init__(self, db_path: Path):
        self.db_path = db_path


class DashboardError(Exception):
    """HTTP-safe dashboard error."""

    def __init__(self, message: str, *, status: HTTPStatus = HTTPStatus.BAD_REQUEST):
        super().__init__(message)
        self.status = status


def run_dashboard(*, db_path: Path, host: str, port: int, open_browser: bool = True) -> None:
    """Run the local dashboard server until interrupted."""
    init_db(db_path).close()
    runtime = DashboardRuntime(db_path)
    handler_cls = make_handler(runtime)
    server = ThreadingHTTPServer((host, port), handler_cls)
    url = f"http://{host}:{server.server_port}"
    logger.info("dashboard status=started bind=%s port=%d", host, server.server_port)
    if open_browser:
        webbrowser.open(url)
    print(f"Boss workflow dashboard running at {url}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def make_handler(runtime: DashboardRuntime) -> type[BaseHTTPRequestHandler]:
    """Create a request handler bound to runtime state."""

    class DashboardHandler(BaseHTTPRequestHandler):
        server_version = "BossDashboard/1.0"

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API.
            try:
                self._validate_local_request(write=False)
                parsed = urlparse(self.path)
                if parsed.path == "/":
                    self._serve_static("index.html")
                    return
                if parsed.path.startswith("/static/"):
                    self._serve_static(parsed.path.removeprefix("/static/"))
                    return
                if parsed.path == "/api/health":
                    self._send_json(self._with_store(lambda service: service.health()))
                    return
                if parsed.path == "/api/queue":
                    self._send_json(self._with_store(lambda service: service.queue(limit=200)))
                    return
                if parsed.path == "/api/templates":
                    self._send_json(self._with_store(lambda service: service.templates()))
                    return
                if parsed.path == "/api/decisions":
                    limit = _int_query(parsed.query, "limit", 200)
                    self._send_json(self._with_store(lambda service: service.decisions(limit=limit)))
                    return
                if parsed.path == "/api/events":
                    self._send_json(self._with_store(lambda service: service.events(limit=200)))
                    return
                if parsed.path == "/api/runs":
                    self._send_json(self._with_store(lambda service: service.runs(limit=100)))
                    return
                raise DashboardError("Not found", status=HTTPStatus.NOT_FOUND)
            except DashboardError as exc:
                self._send_error(exc)
            except Exception as exc:  # noqa: BLE001 - return API error envelope.
                self._send_error(DashboardError(str(exc), status=HTTPStatus.INTERNAL_SERVER_ERROR))

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API.
            try:
                self._validate_local_request(write=True)
                parsed = urlparse(self.path)
                payload = self._read_json()
                if parsed.path == "/api/templates":
                    self._send_json(
                        self._with_store(
                            lambda service: service.create_template(
                                name=str(payload.get("name") or "dashboard_message"),
                                body=str(payload.get("body") or ""),
                                version=str(payload.get("version") or "") or None,
                                approved=False,
                                selection_guidance=str(payload.get("selection_guidance") or ""),
                            )
                        )
                    )
                    return
                if parsed.path == "/api/templates/approve":
                    template_id = int(payload.get("template_id") or 0)
                    if template_id <= 0:
                        raise DashboardError("A template id is required")
                    self._send_json(self._with_store(lambda service: service.approve_template(template_id)))
                    return
                if parsed.path == "/api/templates/retire":
                    template_id = int(payload.get("template_id") or 0)
                    if template_id <= 0:
                        raise DashboardError("A template id is required")
                    self._send_json(self._with_store(lambda service: service.retire_template(template_id)))
                    return
                if parsed.path == "/api/automation/pause":
                    reason = str(payload.get("reason") or "operator")
                    self._send_json(self._with_store(lambda service: service.pause(reason=reason)))
                    return
                if parsed.path == "/api/automation/resume":
                    self._send_json(self._with_store(lambda service: service.resume()))
                    return
                raise DashboardError("Not found", status=HTTPStatus.NOT_FOUND)
            except DashboardError as exc:
                self._send_error(exc)
            except Exception as exc:  # noqa: BLE001 - return API error envelope.
                self._send_error(DashboardError(str(exc), status=HTTPStatus.INTERNAL_SERVER_ERROR))

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib override name.
            return

        def _with_store(self, fn: Any) -> Any:
            with init_db(runtime.db_path) as store:
                return fn(DashboardService(store))

        def _serve_static(self, name: str) -> None:
            static_root = STATIC_DIR.resolve()
            path = (static_root / name).resolve()
            try:
                path.relative_to(static_root)
            except ValueError as exc:
                raise DashboardError("Not found", status=HTTPStatus.NOT_FOUND) from exc
            if not path.exists() or not path.is_file():
                raise DashboardError("Not found", status=HTTPStatus.NOT_FOUND)
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            data = path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self._send_security_headers()
            self.end_headers()
            self.wfile.write(data)

        def _read_json(self) -> dict[str, Any]:
            try:
                length = int(self.headers.get("Content-Length", "0") or 0)
            except ValueError as exc:
                raise DashboardError("Invalid Content-Length") from exc
            if length < 0 or length > MAX_REQUEST_BODY_BYTES:
                raise DashboardError("Request body is too large", status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            if length == 0:
                return {}
            try:
                data = self.rfile.read(length)
                value = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise DashboardError("Request body must be valid JSON") from exc
            if not isinstance(value, dict):
                raise DashboardError("Request body must contain a JSON object")
            return value

        def _validate_local_request(self, *, write: bool) -> None:
            host = self.headers.get("Host", "")
            try:
                hostname = urlsplit(f"//{host}").hostname
            except ValueError as exc:
                raise DashboardError("Invalid Host header", status=HTTPStatus.FORBIDDEN) from exc
            if hostname is None or hostname.casefold() not in LOOPBACK_HOSTS:
                raise DashboardError("Loopback Host header required", status=HTTPStatus.FORBIDDEN)
            if not write:
                return
            content_type = self.headers.get_content_type()
            if content_type != "application/json":
                raise DashboardError("Content-Type must be application/json", status=HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            origin = self.headers.get("Origin")
            if origin:
                try:
                    parsed_origin = urlsplit(origin)
                    origin_host = parsed_origin.hostname
                except ValueError as exc:
                    raise DashboardError("Invalid Origin header", status=HTTPStatus.FORBIDDEN) from exc
                if (
                    parsed_origin.scheme != "http"
                    or origin_host is None
                    or origin_host.casefold() not in LOOPBACK_HOSTS
                    or parsed_origin.netloc.casefold() != host.casefold()
                ):
                    raise DashboardError("Cross-origin dashboard writes are forbidden", status=HTTPStatus.FORBIDDEN)

        def _send_json(self, data: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
            body = json.dumps({"ok": True, "data": data}, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self._send_security_headers()
            self.end_headers()
            self.wfile.write(body)

        def _send_error(self, exc: DashboardError) -> None:
            body = json.dumps(
                {"ok": False, "error": {"message": str(exc), "status": int(exc.status)}},
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_response(exc.status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self._send_security_headers()
            self.end_headers()
            self.wfile.write(body)

        def _send_security_headers(self) -> None:
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")

    return DashboardHandler


def _int_query(query: str, key: str, default: int) -> int:
    values = parse_qs(query).get(key)
    if not values:
        return default
    try:
        return int(values[0])
    except ValueError:
        return default
