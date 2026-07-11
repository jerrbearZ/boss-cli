"""Dashboard-facing workflow operations."""

from __future__ import annotations

import uuid
from typing import Any

from .db import WorkflowStore, utc_now
from .planner import enqueue_selected_candidates


class DashboardService:
    """Thin service layer shared by the dashboard API and tests."""

    def __init__(self, store: WorkflowStore):
        self.store = store

    def health(self) -> dict[str, Any]:
        return {
            "db": str(self.store.path),
            "schema_version": self.store.current_schema_version(),
            "paused": self.store.is_paused(),
            "queue": self.store.queue_summary(),
            "candidate_count": self.store.row_count("candidates"),
            "event_count": self.store.row_count("events"),
        }

    def candidates(self, *, limit: int = 200) -> list[dict[str, Any]]:
        return self.store.list_candidates(limit=limit)

    def queue(self, *, limit: int = 200) -> list[dict[str, Any]]:
        return self.store.list_queue(limit=limit)

    def events(self, *, limit: int = 200) -> list[dict[str, Any]]:
        return self.store.list_events(limit=limit)

    def runs(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return self.store.list_runs(limit=limit)

    def templates(self) -> list[dict[str, Any]]:
        return self.store.list_templates()

    def create_template(
        self,
        *,
        name: str,
        body: str,
        version: str | None = None,
        approved: bool = True,
    ) -> dict[str, Any]:
        if not body.strip():
            raise ValueError("Template body cannot be empty")
        template_version = version or f"{utc_now().replace(':', '').replace('-', '')}-{uuid.uuid4().hex[:8]}"
        template_id = self.store.upsert_template(
            name=name or "dashboard_message",
            version=template_version,
            body=body,
            approved=approved,
            active=approved,
            approved_by="dashboard",
            approved_at=utc_now() if approved else None,
        )
        self.store.append_event(
            event_type="template_approved" if approved else "template_saved",
            summary=f"Template {name}:{template_version} saved",
            details={"template_id": template_id, "approved": approved},
        )
        template = self.store.get_template(template_id)
        return template or {"id": template_id}

    def enqueue(self, *, candidate_ids: list[int], template_id: int) -> dict[str, Any]:
        return enqueue_selected_candidates(
            self.store,
            candidate_ids=candidate_ids,
            template_id=template_id,
            selection_source="dashboard",
        )

    def pause(self, *, reason: str = "operator") -> dict[str, Any]:
        self.store.set_setting("paused", {"paused": True, "reason": reason, "updated_at": utc_now()})
        self.store.append_event(event_type="sender_paused", summary=f"Sender paused: {reason}")
        return {"paused": True, "reason": reason}

    def resume(self) -> dict[str, Any]:
        self.store.set_setting("paused", {"paused": False, "reason": "", "updated_at": utc_now()})
        self.store.append_event(event_type="sender_resumed", summary="Sender resumed")
        return {"paused": False}
