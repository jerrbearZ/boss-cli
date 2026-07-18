"""Stable daemon health states and process exit codes."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import IntEnum
from typing import Any

from .db import WorkflowStore


class HealthExitCode(IntEnum):
    HEALTHY = 0
    STALE_OR_STOPPED = 2
    PAUSED = 3
    AUTHENTICATION_FAILURE = 4
    REVIEW_REQUIRED = 5
    DATABASE_FAILURE = 6


def evaluate_health(
    store: WorkflowStore,
    *,
    daemon_name: str = "primary",
    max_age_seconds: float = 120,
    now: datetime | None = None,
) -> tuple[HealthExitCode, dict[str, Any]]:
    """Evaluate durable state without exposing candidate or credential data."""
    state = store.get_daemon_state(daemon_name)
    queue = store.queue_summary()
    operator_required = store.get_operator_required()
    checked_at = now or datetime.now(timezone.utc)
    heartbeat_age: float | None = None
    if state and state.get("heartbeat_at"):
        try:
            heartbeat = datetime.fromisoformat(str(state["heartbeat_at"]).replace("Z", "+00:00"))
            heartbeat_age = max(0.0, (checked_at - heartbeat).total_seconds())
        except ValueError:
            heartbeat_age = None

    if operator_required and operator_required.get("code") == "not_authenticated":
        code = HealthExitCode.AUTHENTICATION_FAILURE
        status = "authentication_failure"
    elif store.is_paused():
        code = HealthExitCode.PAUSED
        status = "paused"
    elif queue["needs_review"] or queue["sent_unverified"] or queue["sending"] or queue["failed_terminal"]:
        code = HealthExitCode.REVIEW_REQUIRED
        status = "review_required"
    elif state and state.get("status") == "running" and heartbeat_age is not None and heartbeat_age <= max_age_seconds:
        code = HealthExitCode.HEALTHY
        status = "healthy"
    else:
        code = HealthExitCode.STALE_OR_STOPPED
        status = "stale_or_stopped"

    return code, {
        "status": status,
        "exit_code": int(code),
        "daemon_name": daemon_name,
        "daemon_status": state.get("status") if state else "not_started",
        "heartbeat_at": state.get("heartbeat_at") if state else None,
        "heartbeat_age_seconds": round(heartbeat_age, 3) if heartbeat_age is not None else None,
        "max_age_seconds": max_age_seconds,
        "paused": store.is_paused(),
        "operator_required": operator_required,
        "review_required_count": (queue["needs_review"] + queue["sent_unverified"] + queue["sending"] + queue["failed_terminal"]),
        "schema_version": store.current_schema_version(),
        "db": str(store.path),
    }
