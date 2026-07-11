"""Queue sender for Boss dashboard/CLI workflows."""

from __future__ import annotations

import time
from typing import Any, Callable

from ..auth import Credential
from ..browser_reply import BrowserReplyResult, BrowserReplyTarget, send_boss_message_via_browser
from .db import WorkflowStore, utc_now
from .normalizer import message_fingerprint
from .redaction import redact_text, sha256_text

StopCheck = Callable[[], bool]
SendFunc = Callable[[Credential, BrowserReplyTarget, str], BrowserReplyResult]


def send_queued_actions(
    store: WorkflowStore,
    credential: Credential,
    *,
    max_actions: int = 5,
    engine: str = "camoufox",
    stop_requested: StopCheck | None = None,
    delay_seconds: float = 1.0,
    send_func: SendFunc | None = None,
) -> dict[str, Any]:
    """Send queued actions one at a time, with durable state transitions."""
    run_id = store.create_run(run_type="send", requested_by="dashboard")
    sender = send_func or _send_with_engine(engine)
    summary = {
        "claimed": 0,
        "verified": 0,
        "skipped_duplicate": 0,
        "failed_retryable": 0,
        "failed_terminal": 0,
        "sent_unverified": 0,
        "stopped": False,
    }
    stop_reason = None

    try:
        for _ in range(max(max_actions, 0)):
            if stop_requested and stop_requested():
                summary["stopped"] = True
                stop_reason = "stop_requested"
                break
            if store.is_paused():
                summary["stopped"] = True
                stop_reason = "paused"
                break

            action = store.claim_next_action(worker_id=run_id)
            if action is None:
                break
            summary["claimed"] += 1
            action_id = int(action["id"])
            context = store.get_action_context(action_id)
            if not context:
                store.mark_action_failed(
                    action_id,
                    error_code="missing_context",
                    error_message="Action context not found",
                    retryable=False,
                )
                summary["failed_terminal"] += 1
                stop_reason = "missing_context"
                break

            body = str(context["template_body"])
            candidate_id = int(context["candidate_id"])
            if store.has_message_text(candidate_id, body):
                store.mark_action_status(action_id, status="skipped_duplicate")
                store.append_event(
                    run_id=run_id,
                    event_type="send_skipped_duplicate",
                    action_id=action_id,
                    candidate_id=candidate_id,
                    summary="Exact template text already exists in stored messages",
                )
                summary["skipped_duplicate"] += 1
                continue

            target = _target_from_context(context)
            if not target.encrypt_uid:
                store.mark_action_failed(
                    action_id,
                    error_code="target_missing_context",
                    error_message="Candidate missing encrypt_uid for browser send",
                    retryable=False,
                )
                summary["failed_terminal"] += 1
                stop_reason = "target_missing_context"
                break

            try:
                store.mark_action_status(action_id, status="sending")
                result = sender(credential, target, body)
                if result.verified:
                    verified_at = utc_now()
                    store.mark_action_verified(action_id, verified_at=verified_at)
                    store.upsert_message(
                        candidate_id=candidate_id,
                        fingerprint=message_fingerprint(direction="outbound", text=body, sent_at=verified_at),
                        direction="outbound",
                        sent_at=verified_at,
                        text_hash=sha256_text(body),
                        text_redacted=body,
                    )
                    store.append_event(
                        run_id=run_id,
                        event_type="send_verified",
                        action_id=action_id,
                        candidate_id=candidate_id,
                        summary="Message sent and verified",
                        details=result.to_dict(),
                    )
                    summary["verified"] += 1
                else:
                    store.mark_action_status(action_id, status="sent_unverified", error_code="latest_message_mismatch")
                    store.append_event(
                        run_id=run_id,
                        event_type="send_unverified",
                        severity="error",
                        action_id=action_id,
                        candidate_id=candidate_id,
                        summary="Browser send returned without latest-message verification",
                        details=result.to_dict(),
                    )
                    summary["sent_unverified"] += 1
                    stop_reason = "latest_message_mismatch"
                    break
            except Exception as exc:
                store.mark_action_failed(
                    action_id,
                    error_code=type(exc).__name__,
                    error_message=redact_text(str(exc)),
                    retryable=False,
                )
                store.append_event(
                    run_id=run_id,
                    event_type="send_failed",
                    severity="error",
                    action_id=action_id,
                    candidate_id=candidate_id,
                    summary=f"Send failed: {redact_text(str(exc))}",
                )
                summary["failed_terminal"] += 1
                stop_reason = type(exc).__name__
                break

            if delay_seconds > 0:
                time.sleep(delay_seconds)

        status = "stopped" if summary["stopped"] else "completed"
        store.finish_run(run_id, status=status, stop_reason=stop_reason, summary=summary)
        return {"run_id": run_id, "stop_reason": stop_reason, **summary}
    except Exception as exc:
        store.finish_run(run_id, status="failed", stop_reason=type(exc).__name__, summary=summary)
        raise


def _send_with_engine(engine: str) -> SendFunc:
    def _send(credential: Credential, target: BrowserReplyTarget, body: str) -> BrowserReplyResult:
        return send_boss_message_via_browser(credential, target, body, engine=engine)  # type: ignore[arg-type]

    return _send


def _target_from_context(context: dict[str, Any]) -> BrowserReplyTarget:
    return BrowserReplyTarget(
        friend_id=int(context.get("uid") or context.get("friend_id") or 0),
        friend_source=int(context.get("friend_source") or 0),
        encrypt_uid=str(context.get("encrypt_uid") or context.get("encrypt_geek_id") or ""),
        name=str(context.get("name_redacted") or ""),
        job_name=str(context.get("job_name") or ""),
    )
