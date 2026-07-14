"""Typed queue worker for verified Boss message and WeChat actions."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from ..auth import Credential
from ..browser_reply import (
    BrowserReplyError,
    BrowserReplyResult,
    BrowserReplyTarget,
    BrowserWechatResult,
    request_wechat_via_browser,
    send_boss_message_via_browser,
)
from ..client import BossClient
from .db import WorkflowStore, utc_now
from .models import EXCHANGE_WECHAT, SEND_MESSAGE
from .normalizer import message_fingerprint
from .redaction import redact_text, sha256_text

StopCheck = Callable[[], bool]
MessageSendFunc = Callable[[Credential, BrowserReplyTarget, str], BrowserReplyResult]
WechatSendFunc = Callable[[Credential, BrowserReplyTarget], BrowserWechatResult]
MessagePreflightFunc = Callable[[Credential, BrowserReplyTarget, str], bool]

_RETRYABLE_CODES = {
    "browser_engine_failed",
    "browser_page_load_failed",
    "browser_chat_not_ready",
    "browser_ws_not_connected",
    "browser_target_not_visible",
}


def send_queued_actions(
    store: WorkflowStore,
    credential: Credential,
    *,
    max_actions: int = 5,
    engine: str = "camoufox",
    stop_requested: StopCheck | None = None,
    delay_seconds: float = 1.0,
    send_func: MessageSendFunc | None = None,
    wechat_send_func: WechatSendFunc | None = None,
    message_preflight_func: MessagePreflightFunc | None = None,
    account_id: int | None = None,
    requested_by: str = "dashboard",
) -> dict[str, Any]:
    """Run eligible actions one at a time and stop on an uncertain outcome."""
    run_id = store.create_run(run_type="send", requested_by=requested_by, account_id=account_id)
    message_sender = send_func or _message_sender(engine)
    wechat_sender = wechat_send_func or _wechat_sender(engine)
    preflight = message_preflight_func or _latest_message_matches
    summary = {
        "claimed": 0,
        "verified": 0,
        "messages_verified": 0,
        "wechat_verified": 0,
        "skipped_duplicate": 0,
        "failed_retryable": 0,
        "failed_terminal": 0,
        "sent_unverified": 0,
        "cancelled_dependents": 0,
        "stopped": False,
    }
    stop_reason: str | None = None

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

            action = store.claim_next_action(worker_id=run_id, account_id=account_id)
            if action is None:
                break
            summary["claimed"] += 1
            action_id = int(action["id"])
            context = store.get_action_context(action_id)
            if not context:
                _fail_action(
                    store,
                    action_id,
                    summary,
                    code="missing_context",
                    message="Action context not found",
                    retryable=False,
                )
                stop_reason = "missing_context"
                break

            action_type = str(context.get("action_type") or "")
            candidate_id = int(context["candidate_id"])
            target = _target_from_context(context)
            try:
                if action_type in {SEND_MESSAGE, "send_template"}:
                    outcome = _execute_message(
                        store,
                        credential,
                        run_id=run_id,
                        action_id=action_id,
                        candidate_id=candidate_id,
                        context=context,
                        target=target,
                        sender=message_sender,
                        preflight=preflight,
                    )
                    summary[outcome] += 1
                    if outcome == "verified":
                        summary["messages_verified"] += 1
                    elif outcome == "sent_unverified":
                        summary["stopped"] = True
                        stop_reason = "latest_message_mismatch"
                        summary["cancelled_dependents"] += store.cancel_dependents(
                            action_id,
                            reason="Message delivery could not be verified",
                        )
                        break
                elif action_type == EXCHANGE_WECHAT:
                    outcome = _execute_wechat(
                        store,
                        credential,
                        run_id=run_id,
                        action_id=action_id,
                        candidate_id=candidate_id,
                        target=target,
                        sender=wechat_sender,
                    )
                    summary[outcome] += 1
                    if outcome == "verified":
                        summary["wechat_verified"] += 1
                    elif outcome == "sent_unverified":
                        summary["stopped"] = True
                        stop_reason = "wechat_exchange_not_verified"
                        break
                else:
                    raise BrowserReplyError(f"Unsupported action type: {action_type}", code="unsupported_action_type")
            except Exception as exc:  # noqa: BLE001 - persist every worker failure.
                code = getattr(exc, "code", type(exc).__name__)
                retryable = str(code) in _RETRYABLE_CODES
                _fail_action(
                    store,
                    action_id,
                    summary,
                    code=str(code),
                    message=str(exc),
                    retryable=retryable,
                )
                if not retryable:
                    summary["cancelled_dependents"] += store.cancel_dependents(
                        action_id,
                        reason=f"Prerequisite failed: {code}",
                    )
                summary["stopped"] = True
                stop_reason = str(code)
                break

            if delay_seconds > 0:
                time.sleep(delay_seconds)

        status = "stopped" if summary["stopped"] else "completed"
        store.finish_run(run_id, status=status, stop_reason=stop_reason, summary=summary)
        return {"run_id": run_id, "stop_reason": stop_reason, **summary}
    except Exception as exc:
        store.finish_run(run_id, status="failed", stop_reason=type(exc).__name__, summary=summary)
        raise


def _execute_message(
    store: WorkflowStore,
    credential: Credential,
    *,
    run_id: str,
    action_id: int,
    candidate_id: int,
    context: dict[str, Any],
    target: BrowserReplyTarget,
    sender: MessageSendFunc,
    preflight: MessagePreflightFunc,
) -> str:
    body = str(context.get("template_body") or "")
    if not body:
        raise BrowserReplyError("Message action has no template body", code="missing_template")
    if not target.encrypt_uid:
        raise BrowserReplyError("Candidate missing encrypt_uid for browser send", code="target_missing_context")

    if store.has_message_text(candidate_id, body) or preflight(credential, target, body):
        store.mark_action_status(action_id, status="skipped_duplicate")
        store.append_event(
            run_id=run_id,
            event_type="send_skipped_duplicate",
            action_id=action_id,
            candidate_id=candidate_id,
            summary="Exact message already exists; send skipped",
        )
        return "skipped_duplicate"

    store.mark_action_status(action_id, status="sending")
    result = sender(credential, target, body)
    if not result.verified:
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
        return "sent_unverified"

    verified_at = utc_now()
    store.mark_action_verified(action_id, verified_at=verified_at)
    store.upsert_message(
        candidate_id=candidate_id,
        fingerprint=message_fingerprint(direction="outbound", text=body, sent_at=verified_at),
        direction="outbound",
        sent_at=verified_at,
        text_hash=sha256_text(body),
        text_redacted=redact_text(body),
        source="automation",
        content_kind="text",
    )
    store.append_event(
        run_id=run_id,
        event_type="send_verified",
        action_id=action_id,
        candidate_id=candidate_id,
        summary="Message sent and verified",
        details=result.to_dict(),
    )
    return "verified"


def _execute_wechat(
    store: WorkflowStore,
    credential: Credential,
    *,
    run_id: str,
    action_id: int,
    candidate_id: int,
    target: BrowserReplyTarget,
    sender: WechatSendFunc,
) -> str:
    if store.has_verified_action(candidate_id, EXCHANGE_WECHAT):
        store.mark_action_status(action_id, status="skipped_duplicate")
        return "skipped_duplicate"

    store.mark_action_status(action_id, status="sending")
    result = sender(credential, target)
    if not result.verified:
        store.mark_action_status(action_id, status="sent_unverified", error_code="wechat_exchange_not_verified")
        store.append_event(
            run_id=run_id,
            event_type="wechat_exchange_unverified",
            severity="error",
            action_id=action_id,
            candidate_id=candidate_id,
            summary="WeChat exchange was clicked but not verified",
            details=result.to_dict(),
        )
        return "sent_unverified"

    verified_at = utc_now()
    store.mark_action_verified(action_id, verified_at=verified_at)
    store.upsert_message(
        candidate_id=candidate_id,
        fingerprint=sha256_text(f"wechat_exchange:{action_id}"),
        direction="outbound",
        sent_at=verified_at,
        text_hash=sha256_text("wechat_exchange_requested"),
        text_redacted="WeChat exchange requested",
        source="automation",
        content_kind="contact_request",
    )
    store.append_event(
        run_id=run_id,
        event_type="wechat_exchange_verified",
        action_id=action_id,
        candidate_id=candidate_id,
        summary="WeChat exchange requested and verified",
        details=result.to_dict(),
    )
    return "verified"


def _fail_action(
    store: WorkflowStore,
    action_id: int,
    summary: dict[str, Any],
    *,
    code: str,
    message: str,
    retryable: bool,
) -> None:
    store.mark_action_failed(
        action_id,
        error_code=code,
        error_message=message,
        retryable=retryable,
        next_retry_at=_retry_at() if retryable else None,
    )
    status = "failed_retryable" if retryable else "failed_terminal"
    summary[status] += 1
    store.append_event(
        event_type="send_failed",
        severity="error",
        action_id=action_id,
        summary=f"Outbound action failed: {redact_text(code)}",
    )


def _message_sender(engine: str) -> MessageSendFunc:
    def _send(credential: Credential, target: BrowserReplyTarget, body: str) -> BrowserReplyResult:
        return send_boss_message_via_browser(credential, target, body, engine=engine)  # type: ignore[arg-type]

    return _send


def _wechat_sender(engine: str) -> WechatSendFunc:
    def _send(credential: Credential, target: BrowserReplyTarget) -> BrowserWechatResult:
        return request_wechat_via_browser(credential, target, engine=engine)  # type: ignore[arg-type]

    return _send


def _latest_message_matches(credential: Credential, target: BrowserReplyTarget, body: str) -> bool:
    """Perform a live preflight read to recover safely from a crash after sending."""
    with BossClient(credential, request_delay=0.2) as client:
        rows = client.get_boss_last_messages([target.friend_id])
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        uid = int(row.get("uid") or row.get("friendId") or 0)
        if uid not in {0, target.friend_id}:
            continue
        info = row.get("lastMsgInfo") if isinstance(row.get("lastMsgInfo"), dict) else {}
        text = str(info.get("showText") or info.get("text") or row.get("showText") or row.get("text") or "")
        return text == body or body in text
    return False


def _target_from_context(context: dict[str, Any]) -> BrowserReplyTarget:
    return BrowserReplyTarget(
        friend_id=int(context.get("uid") or context.get("friend_id") or 0),
        friend_source=int(context.get("friend_source") or 0),
        encrypt_uid=str(context.get("encrypt_uid") or context.get("encrypt_geek_id") or ""),
        name="",
        job_name=str(context.get("job_name") or ""),
    )


def _retry_at() -> str:
    value = datetime.now(timezone.utc) + timedelta(minutes=5)
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")
