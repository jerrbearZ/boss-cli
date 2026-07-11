"""Read-only Boss inbox sync for the workflow store."""

from __future__ import annotations

from typing import Any

from ..auth import Credential
from ..client import BossClient
from .db import WorkflowStore, utc_now
from .normalizer import (
    account_hash_for_credential,
    index_last_messages,
    normalize_candidate,
    normalize_message,
)
from .redaction import redact_text


def sync_inbox(
    store: WorkflowStore,
    client: BossClient,
    credential: Credential,
    *,
    enc_job_id: str = "",
    label_id: int = 0,
    limit: int = 100,
) -> dict[str, Any]:
    """Sync Boss recruiter inbox state into SQLite without sending."""
    run_id = store.create_run(run_type="sync", requested_by="dashboard")
    summary = {
        "seen": 0,
        "candidates_upserted": 0,
        "messages_upserted": 0,
        "errors": 0,
    }
    try:
        account_id = store.upsert_account(
            account_hash=account_hash_for_credential(credential),
            last_auth_ok_at=utc_now(),
        )
        friend_data = client.get_boss_friend_list(label_id=label_id, enc_job_id=enc_job_id, page=1)
        friend_rows = friend_data.get("result", []) if isinstance(friend_data, dict) else []
        friend_rows = friend_rows[: max(limit, 0)]
        friend_ids = [int(row["friendId"]) for row in friend_rows if row.get("friendId")]
        summary["seen"] = len(friend_ids)

        if not friend_ids:
            store.append_event(run_id=run_id, event_type="sync_completed", summary="No Boss candidates found")
            store.finish_run(run_id, status="completed", summary=summary)
            return {"run_id": run_id, **summary}

        details_payload = client.get_boss_friend_details(friend_ids)
        details = details_payload.get("friendList", []) if isinstance(details_payload, dict) else []
        last_messages: list[dict[str, Any]] = []
        for batch in _chunks(friend_ids, 50):
            payload = client.get_boss_last_messages(batch)
            if isinstance(payload, list):
                last_messages.extend(payload)
        last_by_uid = index_last_messages(last_messages)

        with store.transaction():
            for detail in details:
                uid = int(detail.get("uid") or detail.get("friendId") or 0)
                last_message = last_by_uid.get(uid) or last_by_uid.get(int(detail.get("friendId") or 0)) or {}
                job_db_id = None
                encrypt_job_id = str(detail.get("encryptJobId") or enc_job_id or "")
                if encrypt_job_id:
                    job_db_id = store.upsert_job(
                        account_id=account_id,
                        enc_job_id=encrypt_job_id,
                        job_name=str(detail.get("jobName") or ""),
                    )
                candidate_id = store.upsert_candidate(
                    **normalize_candidate(
                        detail,
                        account_id=account_id,
                        job_id=job_db_id,
                        last_message=last_message,
                    )
                )
                summary["candidates_upserted"] += 1

                text = normalize_message(last_message, direction="unknown") if last_message else None
                if text and text.get("text_redacted"):
                    store.upsert_message(candidate_id=candidate_id, **text)
                    summary["messages_upserted"] += 1

        store.append_event(
            run_id=run_id,
            event_type="sync_completed",
            summary=f"Synced {summary['candidates_upserted']} candidates",
            details=summary,
        )
        store.finish_run(run_id, status="completed", summary=summary)
        return {"run_id": run_id, **summary}
    except Exception as exc:
        summary["errors"] += 1
        store.append_event(
            run_id=run_id,
            event_type="sync_failed",
            severity="error",
            summary=f"Sync failed: {redact_text(str(exc))}",
            details=summary,
        )
        store.finish_run(run_id, status="failed", stop_reason=type(exc).__name__, summary=summary)
        raise


def _chunks(values: list[int], size: int) -> list[list[int]]:
    return [values[index:index + size] for index in range(0, len(values), size)]
