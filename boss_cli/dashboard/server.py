"""Local HTTP dashboard for the Boss workflow."""

from __future__ import annotations

import json
import mimetypes
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..workflow import init_db
from ..workflow.dashboard_service import DashboardService

STATIC_DIR = Path(__file__).with_name("static")


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
                    self._send_json(
                        self._with_store(lambda service: service.approve_template(template_id))
                    )
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

        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def _with_store(self, fn: Any) -> Any:
            with init_db(runtime.db_path) as store:
                return fn(DashboardService(store))

        def _serve_static(self, name: str) -> None:
            path = (STATIC_DIR / name).resolve()
            if not str(path).startswith(str(STATIC_DIR.resolve())) or not path.exists() or not path.is_file():
                raise DashboardError("Not found", status=HTTPStatus.NOT_FOUND)
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            data = path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length == 0:
                return {}
            data = self.rfile.read(length)
            return json.loads(data.decode("utf-8"))

        def _send_json(self, data: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
            body = json.dumps({"ok": True, "data": data}, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
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
            self.end_headers()
            self.wfile.write(body)

    return DashboardHandler


def _int_query(query: str, key: str, default: int) -> int:
    values = parse_qs(query).get(key)
    if not values:
        return default
    try:
        return int(values[0])
    except ValueError:
        return default
