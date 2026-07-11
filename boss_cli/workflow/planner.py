"""Planning helpers for selected dashboard candidates."""

from __future__ import annotations

from typing import Any

from .db import WorkflowStore
from .redaction import outbound_idempotency_key, sha256_text


def enqueue_selected_candidates(
    store: WorkflowStore,
    *,
    candidate_ids: list[int],
    template_id: int,
    selection_source: str = "dashboard",
) -> dict[str, Any]:
    """Persist selected candidates and create outbound actions idempotently."""
    template = store.get_template(template_id)
    if not template:
        raise ValueError(f"Unknown template id: {template_id}")
    if not template.get("approved"):
        raise ValueError("Template must be approved before enqueue")

    run_id = store.create_run(run_type="enqueue", requested_by=selection_source)
    summary = {
        "selected": 0,
        "queued": 0,
        "skipped_duplicate": 0,
        "missing": 0,
    }
    body = str(template["body"])
    version = str(template["version"])

    with store.transaction():
        for candidate_id in candidate_ids:
            candidate = store.get_candidate(candidate_id)
            if not candidate:
                summary["missing"] += 1
                continue
            store.record_selection(
                run_id=run_id,
                candidate_id=candidate_id,
                selected=True,
                selection_source=selection_source,
            )
            summary["selected"] += 1
            trigger = str(candidate.get("last_message_fingerprint") or sha256_text(f"candidate:{candidate_id}"))
            key = outbound_idempotency_key(
                candidate_id=candidate_id,
                action_type="send_template",
                template_id=template_id,
                template_version=version,
                trigger_message_fingerprint=trigger,
            )
            status = "skipped_duplicate" if store.has_message_text(candidate_id, body) else "queued"
            store.enqueue_action(
                candidate_id=candidate_id,
                action_type="send_template",
                template_id=template_id,
                idempotency_key=key,
                status=status,
            )
            summary[status] += 1

    store.append_event(
        run_id=run_id,
        event_type="enqueue_completed",
        summary=f"Queued {summary['queued']} actions from {summary['selected']} selected candidates",
        details=summary,
    )
    store.finish_run(run_id, status="completed", summary=summary)
    return {"run_id": run_id, **summary}
