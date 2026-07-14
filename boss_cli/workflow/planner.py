"""Planning helpers for selected dashboard candidates."""

from __future__ import annotations

from typing import Any

from .db import WorkflowStore
from .models import EXCHANGE_WECHAT, SEND_MESSAGE
from .redaction import outbound_idempotency_key, sha256_text


def enqueue_selected_candidates(
    store: WorkflowStore,
    *,
    candidate_ids: list[int],
    template_id: int | None = None,
    send_message: bool = True,
    request_wechat: bool = False,
    account_id: int | None = None,
    selection_source: str = "dashboard",
) -> dict[str, Any]:
    """Persist selected candidates and create typed outbound actions idempotently."""
    if not send_message and not request_wechat:
        raise ValueError("Select at least one action")

    template: dict[str, Any] | None = None
    if send_message:
        if template_id is None:
            raise ValueError("An approved template is required for message actions")
        template = store.get_template(template_id)
        if not template:
            raise ValueError(f"Unknown template id: {template_id}")
        if not template.get("approved"):
            raise ValueError("Template must be approved before enqueue")

    run_id = store.create_run(
        run_type="enqueue",
        requested_by=selection_source,
        account_id=account_id,
        filters={"send_message": send_message, "request_wechat": request_wechat},
    )
    summary = {
        "selected": 0,
        "queued": 0,
        "skipped_duplicate": 0,
        "already_planned": 0,
        "do_not_contact": 0,
        "missing": 0,
        "message_actions": 0,
        "wechat_actions": 0,
    }
    body = str(template["body"]) if template else ""
    version = str(template["version"]) if template else ""

    with store.transaction():
        for candidate_id in candidate_ids:
            candidate = store.get_candidate(candidate_id)
            if not candidate or (account_id is not None and int(candidate["account_id"]) != account_id):
                summary["missing"] += 1
                continue
            if candidate.get("do_not_contact"):
                summary["do_not_contact"] += 1
                continue
            store.record_selection(
                run_id=run_id,
                candidate_id=candidate_id,
                selected=True,
                selection_source=selection_source,
            )
            summary["selected"] += 1
            trigger = str(candidate.get("last_message_fingerprint") or sha256_text(f"candidate:{candidate_id}"))

            message_action_id: int | None = None
            if send_message:
                key = outbound_idempotency_key(
                    candidate_id=candidate_id,
                    action_type=SEND_MESSAGE,
                    template_id=template_id,
                    template_version=version,
                    trigger_message_fingerprint=trigger,
                )
                existing = store.get_action_by_idempotency_key(key)
                if existing:
                    message_action_id = int(existing["id"])
                    summary["already_planned"] += 1
                else:
                    status = "skipped_duplicate" if store.has_message_text(candidate_id, body) else "queued"
                    message_action_id = store.enqueue_action(
                        candidate_id=candidate_id,
                        action_type=SEND_MESSAGE,
                        template_id=template_id,
                        idempotency_key=key,
                        status=status,
                        priority=100,
                    )
                    summary[status] += 1
                    summary["message_actions"] += 1

            if request_wechat:
                key = outbound_idempotency_key(
                    candidate_id=candidate_id,
                    action_type=EXCHANGE_WECHAT,
                    trigger_message_fingerprint=trigger,
                )
                if store.get_action_by_idempotency_key(key):
                    summary["already_planned"] += 1
                elif store.has_verified_action(candidate_id, EXCHANGE_WECHAT):
                    summary["skipped_duplicate"] += 1
                else:
                    store.enqueue_action(
                        candidate_id=candidate_id,
                        action_type=EXCHANGE_WECHAT,
                        idempotency_key=key,
                        depends_on_action_id=message_action_id,
                        priority=110,
                    )
                    summary["queued"] += 1
                    summary["wechat_actions"] += 1

    store.append_event(
        run_id=run_id,
        event_type="enqueue_completed",
        summary=f"Queued {summary['queued']} actions from {summary['selected']} selected candidates",
        details=summary,
    )
    store.finish_run(run_id, status="completed", summary=summary)
    return {"run_id": run_id, **summary}
