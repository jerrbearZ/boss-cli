"""Continuous inbox synchronization, constrained decisions, and verified sending."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Event
from typing import Any, Callable

from ..auth import Credential
from .db import WorkflowStore, utc_now
from .planner import enqueue_selected_candidates
from .poller import sync_inbox
from .reader import BossReadGateway
from .redaction import stable_json_hash
from .selector import PROMPT_VERSION, TemplateSelection, TemplateSelectionError, TemplateSelector
from .sender import MessagePreflightFunc, MessageSendFunc, WechatSendFunc, send_queued_actions


@dataclass(frozen=True)
class AutomationConfig:
    """Safety and throughput controls for one automation process."""

    live: bool = False
    request_wechat: bool = True
    poll_interval_seconds: float = 30.0
    error_backoff_seconds: float = 120.0
    candidate_limit: int = 20
    max_actions_per_cycle: int = 10
    action_delay_seconds: float = 1.0
    confidence_threshold: float = 0.75
    inbox_limit: int = 100
    max_pages: int = 3
    history_budget: int = 20
    history_count: int = 50
    daemon_name: str = "primary"

    @property
    def decision_prompt_version(self) -> str:
        mode = "live" if self.live else "dry"
        return f"{PROMPT_VERSION}-{mode}"

    @property
    def lease_seconds(self) -> int:
        expected_send_seconds = self.max_actions_per_cycle * max(self.action_delay_seconds, 0)
        return max(900, int(self.poll_interval_seconds * 3 + expected_send_seconds + 120))


CycleRunner = Callable[[], dict[str, Any]]


def template_catalog_hash(templates: list[dict[str, Any]]) -> str:
    """Hash the exact approved catalog that constrained a decision."""
    catalog = [
        {
            "id": int(template["id"]),
            "name": template.get("name"),
            "version": template.get("version"),
            "body_hash": template.get("body_hash"),
            "guidance": template.get("selection_guidance") or "",
        }
        for template in templates
    ]
    return stable_json_hash(catalog)


def run_automation_cycle(
    store: WorkflowStore,
    client: BossReadGateway,
    credential: Credential,
    selector: TemplateSelector,
    config: AutomationConfig,
    *,
    send_func: MessageSendFunc | None = None,
    wechat_send_func: WechatSendFunc | None = None,
    message_preflight_func: MessagePreflightFunc | None = None,
    stop_requested: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Run one durable sync, decision, enqueue, and optional send cycle."""
    run_id = store.create_run(
        run_type="automation_cycle",
        requested_by="daemon",
        filters={"live": config.live, "request_wechat": config.request_wechat},
    )
    summary: dict[str, Any] = {
        "account_id": None,
        "synced": 0,
        "eligible": 0,
        "selected": 0,
        "review": 0,
        "skipped": 0,
        "errors": 0,
        "queued": 0,
        "sent": 0,
        "wechat_verified": 0,
        "paused": False,
        "live": config.live,
    }
    try:
        sync = sync_inbox(
            store,
            client,
            credential,
            limit=config.inbox_limit,
            max_pages=config.max_pages,
            history_mode="changed",
            history_budget=config.history_budget,
            history_count=config.history_count,
            requested_by="daemon",
        )
        account_id = int(sync["account_id"])
        summary["account_id"] = account_id
        summary["synced"] = int(sync.get("messages_inserted") or 0)
        store.attach_run_account(run_id, account_id)

        if store.is_paused():
            summary["paused"] = True
            store.append_event(
                run_id=run_id,
                event_type="automation_paused",
                summary="Inbox synchronized; decisions and sending remain paused",
                details=summary,
            )
            store.finish_run(run_id, status="paused", stop_reason="operator_paused", summary=summary)
            return {"run_id": run_id, "status": "paused", **summary}

        templates = store.list_active_templates()
        cycle_status = "completed"
        if not templates:
            store.append_event(
                run_id=run_id,
                event_type="automation_needs_templates",
                severity="warning",
                summary="No active approved templates; new conversations were not decided",
            )
            cycle_status = "needs_review"
        else:
            catalog_hash = template_catalog_hash(templates)
            candidates = store.list_automation_candidates(
                account_id=account_id,
                catalog_hash=catalog_hash,
                prompt_version=config.decision_prompt_version,
                limit=config.candidate_limit,
            )
            summary["eligible"] = len(candidates)
            for candidate in candidates:
                if stop_requested and stop_requested():
                    break
                candidate_id = int(candidate["id"])
                trigger_fingerprint = str(candidate["trigger_fingerprint"])
                context = store.get_automation_context(candidate_id)
                if context is None:
                    summary["errors"] += 1
                    continue

                selection = _safe_select(selector, context, templates)
                outcome = selection.outcome
                selected_template_id = selection.template_id
                reason = selection.reason
                if outcome == "selected" and selection.confidence < config.confidence_threshold:
                    outcome = "review"
                    reason = f"Below confidence threshold: {selection.reason}"

                persisted_outcome = "dry_run" if outcome == "selected" and not config.live else outcome
                decision_key = stable_json_hash(
                    {
                        "candidate_id": candidate_id,
                        "trigger": trigger_fingerprint,
                        "catalog": catalog_hash,
                        "prompt": config.decision_prompt_version,
                    }
                )
                if outcome == "selected" and config.live and selected_template_id is not None:
                    planned = enqueue_selected_candidates(
                        store,
                        candidate_ids=[candidate_id],
                        template_id=selected_template_id,
                        send_message=True,
                        request_wechat=config.request_wechat,
                        account_id=account_id,
                        selection_source="daemon",
                    )
                    summary["queued"] += int(planned.get("queued") or 0)

                store.record_automation_decision(
                    run_id=run_id,
                    account_id=account_id,
                    candidate_id=candidate_id,
                    trigger_fingerprint=trigger_fingerprint,
                    catalog_hash=catalog_hash,
                    decision_key=decision_key,
                    template_id=selected_template_id,
                    outcome=persisted_outcome,
                    confidence=selection.confidence,
                    reason=reason,
                    provider=selection.provider,
                    model=selection.model,
                    prompt_version=config.decision_prompt_version,
                    response_hash=selection.response_hash,
                )
                summary[outcome if outcome in {"selected", "review", "skipped"} else "errors"] += 1

        send_summary: dict[str, Any] | None = None
        if config.live and not (stop_requested and stop_requested()):
            send_summary = send_queued_actions(
                store,
                credential,
                max_actions=config.max_actions_per_cycle,
                stop_requested=stop_requested,
                delay_seconds=config.action_delay_seconds,
                send_func=send_func,
                wechat_send_func=wechat_send_func,
                message_preflight_func=message_preflight_func,
                account_id=account_id,
                requested_by="daemon",
            )
            summary["sent"] = int(send_summary.get("messages_verified") or 0)
            summary["wechat_verified"] = int(send_summary.get("wechat_verified") or 0)
            summary["send"] = send_summary

        status = cycle_status
        if send_summary and send_summary.get("stopped"):
            status = "needs_review"
        store.append_event(
            run_id=run_id,
            event_type="automation_cycle_completed",
            summary=(
                f"Processed {summary['eligible']} conversations; sent {summary['sent']} messages "
                f"and verified {summary['wechat_verified']} WeChat requests"
            ),
            details=summary,
        )
        store.finish_run(run_id, status=status, summary=summary)
        return {"run_id": run_id, "status": status, **summary}
    except Exception as exc:
        summary["errors"] += 1
        store.append_event(
            run_id=run_id,
            event_type="automation_cycle_failed",
            severity="error",
            summary=f"Automation cycle failed: {type(exc).__name__}",
        )
        store.finish_run(run_id, status="failed", stop_reason=type(exc).__name__, summary=summary)
        raise


def run_daemon(
    store: WorkflowStore,
    cycle_runner: CycleRunner,
    config: AutomationConfig,
    *,
    stop_event: Event | None = None,
    once: bool = False,
    owner_id: str | None = None,
) -> dict[str, Any]:
    """Own the daemon lease and run cycles until stopped."""
    stop = stop_event or Event()
    owner = owner_id or uuid.uuid4().hex
    if not store.claim_daemon(
        daemon_name=config.daemon_name,
        owner_id=owner,
        live_mode=config.live,
        lease_seconds=config.lease_seconds,
    ):
        raise RuntimeError(f"automation daemon '{config.daemon_name}' is already running")

    cycles = 0
    failures = 0
    last_result: dict[str, Any] | None = None
    try:
        while not stop.is_set():
            started_at = utc_now()
            try:
                last_result = cycle_runner()
                cycles += 1
                wait_seconds = config.poll_interval_seconds
                cycle_status = str(last_result.get("status") or "completed")
                error_code = None
                error_message = None
            except Exception as exc:  # noqa: BLE001 - daemon must persist failure and continue.
                failures += 1
                wait_seconds = config.error_backoff_seconds
                cycle_status = "failed"
                error_code = type(exc).__name__
                error_message = str(exc)
                last_result = {"status": "failed", "error_code": error_code}

            finished_at = utc_now()
            next_poll_at = None if once else _after_seconds(wait_seconds)
            store.heartbeat_daemon(
                daemon_name=config.daemon_name,
                owner_id=owner,
                lease_seconds=config.lease_seconds,
                account_id=_int_or_none((last_result or {}).get("account_id")),
                last_run_id=(last_result or {}).get("run_id"),
                cycle_status=cycle_status,
                cycle_summary=last_result,
                cycle_started_at=started_at,
                cycle_finished_at=finished_at,
                next_poll_at=next_poll_at,
                error_code=error_code,
                error_message=error_message,
            )
            if once:
                break
            stop.wait(max(wait_seconds, 0))
    finally:
        store.release_daemon(daemon_name=config.daemon_name, owner_id=owner)

    return {"cycles": cycles, "failures": failures, "last_result": last_result}


def _safe_select(
    selector: TemplateSelector,
    context: dict[str, Any],
    templates: list[dict[str, Any]],
) -> TemplateSelection:
    try:
        return selector.select(context, templates)
    except TemplateSelectionError as exc:
        if exc.retryable:
            raise
        return TemplateSelection(
            outcome="review",
            template_id=None,
            confidence=0,
            reason=str(exc),
            provider=selector.provider,
            model=selector.model,
        )


def _after_seconds(seconds: float) -> str:
    value = datetime.now(timezone.utc) + timedelta(seconds=max(seconds, 0))
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
