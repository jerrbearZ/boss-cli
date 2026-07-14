"""Local HTTP dashboard for the Boss workflow."""

from __future__ import annotations

import json
import mimetypes
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..auth import Credential, load_credential, load_from_env
from ..commands._common import run_client_action
from ..workflow import init_db
from ..workflow.dashboard_service import DashboardService
from ..workflow.poller import sync_inbox
from ..workflow.sender import send_queued_actions

STATIC_DIR = Path(__file__).with_name("static")


class DashboardRuntime:
    """Mutable process state for one local dashboard server."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._sender_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self.sender_result: dict[str, Any] | None = None
        self.sender_error: str | None = None

    def sender_state(self) -> dict[str, Any]:
        with self._lock:
            running = self._sender_thread is not None and self._sender_thread.is_alive()
            return {
                "running": running,
                "stop_requested": self._stop_event.is_set(),
                "result": self.sender_result,
                "error": self.sender_error,
            }

    def start_sender(self, *, max_actions: int, engine: str, delay_seconds: float) -> dict[str, Any]:
        credential = _load_dashboard_credential()
        if credential is None:
            raise DashboardError("No saved Boss credential. Run `boss login` first.", status=HTTPStatus.UNAUTHORIZED)

        with self._lock:
            if self._sender_thread is not None and self._sender_thread.is_alive():
                raise DashboardError("Sender is already running", status=HTTPStatus.CONFLICT)
            self._stop_event = threading.Event()
            self.sender_result = None
            self.sender_error = None

            def _run() -> None:
                try:
                    with init_db(self.db_path) as store:
                        self.sender_result = send_queued_actions(
                            store,
                            credential,
                            max_actions=max_actions,
                            engine=engine,
                            stop_requested=self._stop_event.is_set,
                            delay_seconds=delay_seconds,
                        )
                except Exception as exc:  # noqa: BLE001 - surface worker errors to dashboard.
                    self.sender_error = str(exc)

            self._sender_thread = threading.Thread(target=_run, name="boss-dashboard-sender", daemon=True)
            self._sender_thread.start()
        return self.sender_state()

    def stop_sender(self) -> dict[str, Any]:
        self._stop_event.set()
        with init_db(self.db_path) as store:
            store.append_event(event_type="sender_stop_requested", summary="Dashboard stop requested")
        return self.sender_state()


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
        runtime.stop_sender()
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
                    self._send_json(self._with_store(lambda service: service.health() | {"sender": runtime.sender_state()}))
                    return
                if parsed.path == "/api/candidates":
                    limit = _int_query(parsed.query, "limit", 200)
                    self._send_json(self._with_store(lambda service: service.candidates(limit=limit)))
                    return
                if parsed.path == "/api/queue":
                    self._send_json(self._with_store(lambda service: service.queue(limit=200)))
                    return
                if parsed.path == "/api/templates":
                    self._send_json(self._with_store(lambda service: service.templates()))
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
                if parsed.path == "/api/sync":
                    self._send_json(self._sync(payload))
                    return
                if parsed.path == "/api/templates":
                    self._send_json(
                        self._with_store(
                            lambda service: service.create_template(
                                name=str(payload.get("name") or "dashboard_message"),
                                body=str(payload.get("body") or ""),
                                version=str(payload.get("version") or "") or None,
                                approved=bool(payload.get("approved", True)),
                            )
                        )
                    )
                    return
                if parsed.path == "/api/enqueue":
                    candidate_ids = [int(item) for item in payload.get("candidate_ids", [])]
                    template_id = int(payload.get("template_id") or 0)
                    self._send_json(self._with_store(lambda service: service.enqueue(candidate_ids=candidate_ids, template_id=template_id)))
                    return
                if parsed.path == "/api/sender/start":
                    self._send_json(
                        runtime.start_sender(
                            max_actions=int(payload.get("max_actions") or 5),
                            engine=str(payload.get("engine") or "camoufox"),
                            delay_seconds=float(payload.get("delay_seconds") or 1),
                        )
                    )
                    return
                if parsed.path == "/api/sender/stop":
                    self._send_json(runtime.stop_sender())
                    return
                if parsed.path == "/api/sender/pause":
                    reason = str(payload.get("reason") or "operator")
                    self._send_json(self._with_store(lambda service: service.pause(reason=reason)))
                    return
                if parsed.path == "/api/sender/resume":
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

        def _sync(self, payload: dict[str, Any]) -> dict[str, Any]:
            credential = _load_dashboard_credential()
            if credential is None:
                raise DashboardError("No saved Boss credential. Run `boss login` first.", status=HTTPStatus.UNAUTHORIZED)
            with init_db(runtime.db_path) as store:
                return run_client_action(
                    credential,
                    lambda client: sync_inbox(
                        store,
                        client,
                        client.credential if isinstance(client.credential, Credential) else credential,
                        enc_job_id=str(payload.get("enc_job_id") or ""),
                        label_id=int(payload.get("label_id") or 0),
                        limit=int(payload.get("limit") or 100),
                        max_pages=int(payload.get("max_pages") or 20),
                        history_mode=str(payload.get("history_mode") or "changed"),  # type: ignore[arg-type]
                        history_budget=int(payload.get("history_budget") or 20),
                        include_profile=bool(payload.get("include_profile", False)),
                        full_scan=bool(payload.get("full_scan", False)),
                    ),
                )

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


def _load_dashboard_credential() -> Credential | None:
    return load_credential() or load_from_env()


def _int_query(query: str, key: str, default: int) -> int:
    values = parse_qs(query).get(key)
    if not values:
        return default
    try:
        return int(values[0])
    except ValueError:
        return default
