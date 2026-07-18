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
        account_id = self._active_account_id()
        return {
            "db": str(self.store.path),
            "schema_version": self.store.current_schema_version(),
            "paused": self.store.is_paused(),
            "queue": self.store.queue_summary(account_id=account_id),
            "active_account_id": account_id,
            "candidate_count": self.store.candidate_count(account_id=account_id),
            "event_count": self.store.row_count("events"),
            "automation": self.store.automation_summary(account_id=account_id),
            "delivery": self.store.delivery_summary(account_id=account_id),
            "daemon": self.store.get_daemon_state(),
            "operator_required": self.store.get_operator_required(),
        }

    def candidates(self, *, limit: int = 200) -> list[dict[str, Any]]:
        return self.store.list_candidates(limit=limit, account_id=self._active_account_id())

    def queue(self, *, limit: int = 200) -> list[dict[str, Any]]:
        return self.store.list_queue(limit=limit, account_id=self._active_account_id())

    def events(self, *, limit: int = 200) -> list[dict[str, Any]]:
        return self.store.list_events(limit=limit)

    def runs(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return self.store.list_runs(limit=limit)

    def templates(self) -> list[dict[str, Any]]:
        return self.store.list_templates()

    def decisions(self, *, limit: int = 200) -> list[dict[str, Any]]:
        return self.store.list_automation_decisions(account_id=self._active_account_id(), limit=limit)

    def create_template(
        self,
        *,
        name: str,
        body: str,
        version: str | None = None,
        approved: bool = False,
        selection_guidance: str = "",
    ) -> dict[str, Any]:
        if not body.strip():
            raise ValueError("Template body cannot be empty")
        template_version = version or f"{utc_now().replace(':', '').replace('-', '')}-{uuid.uuid4().hex[:8]}"
        template_name = name or "dashboard_message"
        if self.store.get_template_version(name=template_name, version=template_version):
            raise ValueError("Template versions are immutable; save this edit as a new version")
        template_id = self.store.upsert_template(
            name=template_name,
            version=template_version,
            body=body,
            approved=approved,
            active=approved,
            approved_by="dashboard",
            approved_at=utc_now() if approved else None,
            selection_guidance=selection_guidance,
        )
        self.store.append_event(
            event_type="template_approved" if approved else "template_saved",
            summary=f"Template {name}:{template_version} saved",
            details={"template_id": template_id, "approved": approved},
        )
        template = self.store.get_template(template_id)
        return template or {"id": template_id}

    def approve_template(self, template_id: int) -> dict[str, Any]:
        template = self.store.get_template(template_id)
        if not template:
            raise ValueError(f"Unknown template id: {template_id}")
        self.store.set_template_approval(template_id, approved=True, approved_by="dashboard")
        self.store.append_event(
            event_type="template_approved",
            summary=f"Template {template['name']}:{template['version']} approved",
            details={"template_id": template_id},
        )
        return self.store.get_template(template_id) or {"id": template_id}

    def retire_template(self, template_id: int) -> dict[str, Any]:
        template = self.store.get_template(template_id)
        if not template:
            raise ValueError(f"Unknown template id: {template_id}")
        self.store.retire_template(template_id)
        self.store.append_event(
            event_type="template_retired",
            summary=f"Template {template['name']}:{template['version']} retired",
            details={"template_id": template_id},
        )
        return self.store.get_template(template_id) or {"id": template_id}

    def enqueue(
        self,
        *,
        candidate_ids: list[int],
        template_id: int | None,
        send_message: bool = True,
        request_wechat: bool = False,
    ) -> dict[str, Any]:
        account_id = self._active_account_id()
        if account_id is None:
            raise ValueError("Sync the inbox before queueing outbound actions")
        return enqueue_selected_candidates(
            self.store,
            candidate_ids=candidate_ids,
            template_id=template_id,
            send_message=send_message,
            request_wechat=request_wechat,
            account_id=account_id,
            selection_source="dashboard",
        )

    def pause(self, *, reason: str = "operator") -> dict[str, Any]:
        self.store.set_setting("paused", {"paused": True, "reason": reason, "updated_at": utc_now()})
        self.store.append_event(event_type="automation_paused", summary=f"Automation paused: {reason}")
        return {"paused": True, "reason": reason}

    def resume(self) -> dict[str, Any]:
        self.store.set_setting("paused", {"paused": False, "reason": "", "updated_at": utc_now()})
        self.store.append_event(event_type="automation_resumed", summary="Automation resumed")
        return {"paused": False}

    def _active_account_id(self) -> int | None:
        value = self.store.get_setting("active_account_id")
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None
