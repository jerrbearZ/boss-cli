"""Incremental, read-only Boss inbox synchronization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from ..auth import Credential
from .db import WorkflowStore, utc_now
from .normalizer import (
    account_hash_for_credential,
    account_hash_for_recruiter_id,
    extract_history_messages,
    extract_recruiter_user_id,
    extract_recruiter_user_id_from_user_info,
    index_last_messages,
    message_direction,
    normalize_candidate,
    normalize_history_message,
    normalize_job,
    normalize_message,
    normalize_profile_summary,
)
from .reader import BossReadGateway
from .redaction import redact_text, stable_json_hash

HistoryMode = Literal["none", "changed", "all"]


@dataclass(frozen=True)
class SyncOptions:
    """Bounds and enrichment policy for one read-only synchronization run."""

    enc_job_id: str = ""
    label_id: int = 0
    limit: int = 100
    max_pages: int = 20
    history_mode: HistoryMode = "changed"
    history_budget: int = 20
    history_count: int = 50
    max_history_messages: int = 200
    include_profile: bool = False
    full_scan: bool = False
    resume: bool = True

    def filters(self) -> dict[str, Any]:
        return {
            "enc_job_id": self.enc_job_id,
            "label_id": self.label_id,
            "limit": self.limit,
            "max_pages": self.max_pages,
            "history_mode": self.history_mode,
            "include_profile": self.include_profile,
            "full_scan": self.full_scan,
        }


def sync_inbox(
    store: WorkflowStore,
    client: BossReadGateway,
    credential: Credential,
    *,
    enc_job_id: str = "",
    label_id: int = 0,
    limit: int = 100,
    max_pages: int = 20,
    history_mode: HistoryMode = "changed",
    history_budget: int = 20,
    history_count: int = 50,
    max_history_messages: int = 200,
    include_profile: bool = False,
    full_scan: bool = False,
    resume: bool = True,
    requested_by: str = "dashboard",
) -> dict[str, Any]:
    """Synchronize Boss recruiter state into SQLite without invoking write APIs."""
    options = SyncOptions(
        enc_job_id=enc_job_id,
        label_id=label_id,
        limit=max(limit, 0),
        max_pages=max(max_pages, 1),
        history_mode=history_mode,
        history_budget=max(history_budget, 0),
        history_count=max(history_count, 1),
        max_history_messages=max(max_history_messages, 1),
        include_profile=include_profile,
        full_scan=full_scan,
        resume=resume,
    )
    if history_mode not in ("none", "changed", "all"):
        raise ValueError(f"Unsupported history mode: {history_mode}")

    run_id = store.create_run(run_type="sync", requested_by=requested_by, filters=options.filters())
    summary: dict[str, Any] = {
        "account_id": None,
        "pages": 0,
        "seen": 0,
        "candidates_upserted": 0,
        "candidates_new": 0,
        "candidates_changed": 0,
        "candidates_unchanged": 0,
        "messages_upserted": 0,
        "messages_inserted": 0,
        "history_conversations": 0,
        "history_deferred": 0,
        "profiles_upserted": 0,
        "jobs_upserted": 0,
        "errors": 0,
        "inactive_candidates": 0,
        "complete_scan": False,
        "resumed": False,
    }
    observed_at = utc_now()
    fallback_hash = account_hash_for_credential(credential)
    account_id: int | None = None
    recruiter_user_id: int | None = None
    jobs_payload: list[dict[str, Any]] = []
    jobs_loaded = False
    jobs_persisted = False
    history_remaining = options.history_budget
    filter_hash = stable_json_hash({"enc_job_id": enc_job_id, "label_id": label_id})
    page = 1
    seen_page_hashes: set[str] = set()
    stop_reason: str | None = None
    scan_complete = False

    try:
        user_info, _ = _optional_call(client, "get_user_info", default={})
        recruiter_user_id = extract_recruiter_user_id_from_user_info(user_info)
        if recruiter_user_id:
            account_id = store.resolve_account(
                stable_hash=account_hash_for_recruiter_id(recruiter_user_id),
                fallback_hash=fallback_hash,
                identity_source="user_info",
            )
            store.attach_run_account(run_id, account_id)
            summary["account_id"] = account_id
            page, summary["resumed"] = _resume_page(store, account_id, filter_hash, options.resume)

        jobs_value, jobs_loaded = _optional_call(client, "get_boss_chatted_jobs", default=[])
        if isinstance(jobs_value, list):
            jobs_payload = [item for item in jobs_value if isinstance(item, dict)]

        while summary["pages"] < options.max_pages:
            if options.limit and summary["seen"] >= options.limit:
                stop_reason = "limit_reached"
                break

            friend_data = client.get_boss_friend_list(
                label_id=options.label_id,
                enc_job_id=options.enc_job_id,
                page=page,
            )
            friend_rows = friend_data.get("result", []) if isinstance(friend_data, dict) else []
            friend_rows = [row for row in friend_rows if isinstance(row, dict) and row.get("friendId")]
            page_hash = stable_json_hash(friend_rows)
            if friend_rows and page_hash in seen_page_hashes:
                stop_reason = "duplicate_page"
                break
            seen_page_hashes.add(page_hash)

            if not friend_rows:
                scan_complete = True
                break

            remaining = options.limit - summary["seen"] if options.limit else len(friend_rows)
            if options.limit and len(friend_rows) > remaining:
                friend_rows = friend_rows[:remaining]
                stop_reason = "limit_reached"

            friend_ids = [int(row["friendId"]) for row in friend_rows]
            details = _read_details(client, friend_ids)
            last_messages = _read_latest_messages(client, friend_ids)
            if recruiter_user_id is None:
                recruiter_user_id = extract_recruiter_user_id(last_messages)

            if account_id is None:
                stable_hash = account_hash_for_recruiter_id(recruiter_user_id) if recruiter_user_id else None
                account_id = store.resolve_account(
                    stable_hash=stable_hash,
                    fallback_hash=fallback_hash,
                    identity_source="message_participants" if stable_hash else "credential",
                    observed_friend_ids=friend_ids,
                )
                store.attach_run_account(run_id, account_id)
                summary["account_id"] = account_id

            if not jobs_persisted:
                summary["jobs_upserted"] = _persist_jobs(store, jobs_payload, account_id, run_id)
                jobs_persisted = True

            last_by_uid = index_last_messages(last_messages)
            details_by_friend = {
                int(item.get("friendId") or item.get("uid") or 0): item
                for item in details
                if item.get("friendId") or item.get("uid")
            }
            page_entries: list[dict[str, Any]] = []
            for friend_row in friend_rows:
                friend_id = int(friend_row["friendId"])
                detail = details_by_friend.get(friend_id)
                if detail is None:
                    detail = next(
                        (item for item in details if int(item.get("friendId") or 0) == friend_id),
                        None,
                    )
                if detail is None:
                    _record_candidate_error(
                        store,
                        summary,
                        run_id=run_id,
                        account_id=account_id,
                        friend_id=friend_id,
                        page=page,
                        exc=ValueError("Candidate detail missing from Boss response"),
                    )
                    continue

                try:
                    source = int(detail.get("friendSource") or detail.get("source") or detail.get("geekSource") or 0)
                    uid = int(detail.get("uid") or friend_id)
                    last_message = last_by_uid.get(uid) or last_by_uid.get(friend_id) or {}
                    direction = message_direction(
                        last_message,
                        candidate_uid=uid,
                        recruiter_user_id=recruiter_user_id,
                    )
                    normalized_latest = (
                        normalize_message(last_message, direction=direction, source="latest")
                        if last_message
                        else None
                    )
                    previous = store.get_candidate_by_external_key(
                        account_id=account_id,
                        friend_id=friend_id,
                        friend_source=source,
                    )
                    latest_fingerprint = normalized_latest["fingerprint"] if normalized_latest else None
                    changed = previous is None or previous.get("last_message_fingerprint") != latest_fingerprint
                    history_rows: list[dict[str, Any]] = []
                    history_status = str(previous.get("history_sync_status") or "pending") if previous else "pending"
                    needs_history = previous is None or history_status in {"pending", "deferred", "failed"}
                    should_read_history = options.history_mode == "all" or (
                        options.history_mode == "changed" and (changed or needs_history)
                    )
                    if should_read_history and history_remaining > 0 and hasattr(client, "get_boss_chat_history"):
                        history_rows = _read_history(
                            store,
                            client,
                            friend_id=friend_id,
                            candidate_id=int(previous["id"]) if previous else None,
                            candidate_uid=uid,
                            recruiter_user_id=recruiter_user_id,
                            count=options.history_count,
                            maximum=options.max_history_messages,
                        )
                        history_remaining -= 1
                        summary["history_conversations"] += 1
                        history_status = "synced"
                    elif should_read_history:
                        history_status = "deferred"
                        summary["history_deferred"] += 1

                    profile_summary: dict[str, Any] | None = None
                    if options.include_profile and hasattr(client, "get_boss_chat_geek_info"):
                        encrypt_geek_id = str(detail.get("encryptGeekId") or detail.get("encryptUid") or "")
                        job_id_value = _int_or_none(detail.get("jobId"))
                        security_id = str(detail.get("securityId") or "")
                        if encrypt_geek_id and job_id_value:
                            profile = client.get_boss_chat_geek_info(
                                encrypt_geek_id=encrypt_geek_id,
                                security_id=security_id,
                                job_id=job_id_value,
                            )
                            profile_summary = normalize_profile_summary(profile)

                    page_entries.append(
                        {
                            "detail": detail,
                            "previous": previous,
                            "last_message": last_message,
                            "latest": normalized_latest,
                            "direction": direction,
                            "history": history_rows,
                            "history_status": history_status,
                            "profile": profile_summary,
                        }
                    )
                except Exception as exc:  # noqa: BLE001 - isolate malformed candidate payloads.
                    _record_candidate_error(
                        store,
                        summary,
                        run_id=run_id,
                        account_id=account_id,
                        friend_id=friend_id,
                        page=page,
                        exc=exc,
                    )

            with store.transaction():
                for entry in page_entries:
                    _persist_candidate_entry(
                        store,
                        entry,
                        account_id=account_id,
                        run_id=run_id,
                        observed_at=observed_at,
                        summary=summary,
                        fallback_enc_job_id=options.enc_job_id,
                    )
                store.set_sync_checkpoint(
                    account_id=account_id,
                    stream="inbox",
                    filter_hash=filter_hash,
                    cursor={"next_page": page + 1},
                    completed=False,
                )

            summary["pages"] += 1
            summary["seen"] += len(friend_rows)
            has_more = _has_more(friend_data)
            if has_more is False:
                scan_complete = True
                break
            if stop_reason == "limit_reached":
                break
            page += 1

        if account_id is None:
            account_id = store.resolve_account(
                stable_hash=None,
                fallback_hash=fallback_hash,
                identity_source="credential",
            )
            store.attach_run_account(run_id, account_id)
            summary["account_id"] = account_id
        if not jobs_persisted:
            summary["jobs_upserted"] = _persist_jobs(store, jobs_payload, account_id, run_id)
            jobs_persisted = True

        if summary["pages"] >= options.max_pages and not scan_complete and stop_reason is None:
            stop_reason = "max_pages_reached"

        if scan_complete or stop_reason == "duplicate_page":
            store.set_sync_checkpoint(
                account_id=account_id,
                stream="inbox",
                filter_hash=filter_hash,
                cursor={"next_page": 1},
                completed=True,
            )
        summary["complete_scan"] = scan_complete

        safe_full_scan = (
            options.full_scan
            and scan_complete
            and not options.enc_job_id
            and options.label_id == 0
            and not summary["resumed"]
        )
        if safe_full_scan:
            summary["inactive_candidates"] = store.mark_unseen_candidates_inactive(
                account_id=account_id,
                run_id=run_id,
            )
            if jobs_loaded:
                store.mark_unseen_jobs_inactive(account_id=account_id, run_id=run_id)

        store.set_setting("active_account_id", account_id)
        store.append_event(
            run_id=run_id,
            event_type="sync_completed" if scan_complete else "sync_partial",
            summary=f"Synchronized {summary['candidates_upserted']} candidates across {summary['pages']} pages",
            details=summary,
        )
        store.finish_run(
            run_id,
            status="completed" if scan_complete or stop_reason == "limit_reached" else "partial",
            stop_reason=stop_reason,
            summary=summary,
            request_count=_request_count(client),
        )
        return {"run_id": run_id, "stop_reason": stop_reason, **summary}
    except Exception as exc:
        summary["errors"] += 1
        store.append_event(
            run_id=run_id,
            event_type="sync_failed",
            severity="error",
            summary=f"Sync failed: {redact_text(str(exc))}",
            details=summary,
        )
        store.finish_run(
            run_id,
            status="failed",
            stop_reason=type(exc).__name__,
            summary=summary,
            request_count=_request_count(client),
        )
        raise


def _resume_page(
    store: WorkflowStore,
    account_id: int,
    filter_hash: str,
    resume: bool,
) -> tuple[int, bool]:
    if not resume:
        return 1, False
    checkpoint = store.get_sync_checkpoint(
        account_id=account_id,
        stream="inbox",
        filter_hash=filter_hash,
    )
    if not checkpoint or checkpoint["completed"]:
        return 1, False
    page = _int_or_none(checkpoint["cursor"].get("next_page")) or 1
    return max(page, 1), page > 1


def _read_details(client: BossReadGateway, friend_ids: list[int]) -> list[dict[str, Any]]:
    details: list[dict[str, Any]] = []
    for batch in _chunks(friend_ids, 50):
        payload = client.get_boss_friend_details(batch)
        rows = payload.get("friendList", []) if isinstance(payload, dict) else []
        details.extend(item for item in rows if isinstance(item, dict))
    return details


def _read_latest_messages(client: BossReadGateway, friend_ids: list[int]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for batch in _chunks(friend_ids, 50):
        payload = client.get_boss_last_messages(batch)
        if isinstance(payload, list):
            messages.extend(item for item in payload if isinstance(item, dict))
    return messages


def _read_history(
    store: WorkflowStore,
    client: BossReadGateway,
    *,
    friend_id: int,
    candidate_id: int | None,
    candidate_uid: int | None,
    recruiter_user_id: int | None,
    count: int,
    maximum: int,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    max_msg_id = 0
    seen_cursors: set[int] = set()
    while len(normalized) < maximum:
        payload = client.get_boss_chat_history(
            gid=friend_id,
            count=min(count, maximum - len(normalized)),
            max_msg_id=max_msg_id,
        )
        rows = extract_history_messages(payload)
        if not rows:
            break
        reached_stored = False
        numeric_ids: list[int] = []
        for row in rows:
            item = normalize_history_message(
                row,
                candidate_uid=candidate_uid,
                recruiter_user_id=recruiter_user_id,
            )
            if candidate_id is not None and store.has_message_fingerprint(candidate_id, item["fingerprint"]):
                reached_stored = True
            normalized.append(item)
            numeric_id = _int_or_none(item.get("boss_msg_id"))
            if numeric_id:
                numeric_ids.append(numeric_id)
            if len(normalized) >= maximum:
                break
        if reached_stored or len(rows) < count or not numeric_ids:
            break
        next_cursor = min(numeric_ids)
        if next_cursor in seen_cursors:
            break
        seen_cursors.add(next_cursor)
        max_msg_id = next_cursor
    return normalized


def _persist_jobs(
    store: WorkflowStore,
    jobs: list[dict[str, Any]],
    account_id: int,
    run_id: str,
) -> int:
    count = 0
    with store.transaction():
        for job in jobs:
            normalized = normalize_job(job, account_id=account_id, run_id=run_id)
            if normalized:
                store.upsert_job(**normalized)
                count += 1
    return count


def _persist_candidate_entry(
    store: WorkflowStore,
    entry: dict[str, Any],
    *,
    account_id: int,
    run_id: str,
    observed_at: str,
    summary: dict[str, Any],
    fallback_enc_job_id: str,
) -> None:
    detail = entry["detail"]
    previous = entry["previous"]
    latest = entry["latest"]
    enc_job_id = str(detail.get("encryptJobId") or fallback_enc_job_id or "")
    job_db_id = None
    if enc_job_id:
        job_db_id = store.upsert_job(
            account_id=account_id,
            enc_job_id=enc_job_id,
            job_name=str(detail.get("jobName") or ""),
            boss_job_id=_int_or_none(detail.get("jobId")),
            last_seen_run_id=run_id,
        )
    candidate_id = store.upsert_candidate(
        **normalize_candidate(
            detail,
            account_id=account_id,
            job_id=job_db_id,
            last_message=entry["last_message"],
            message_direction_value=entry["direction"],
            observed_at=observed_at,
            run_id=run_id,
        )
    )
    summary["candidates_upserted"] += 1
    if previous is None:
        summary["candidates_new"] += 1
    elif latest and previous.get("last_message_fingerprint") != latest["fingerprint"]:
        summary["candidates_changed"] += 1
    else:
        summary["candidates_unchanged"] += 1

    for message in ([latest] if latest else []) + list(entry["history"]):
        existed = store.has_message_fingerprint(candidate_id, message["fingerprint"])
        store.upsert_message(candidate_id=candidate_id, **message)
        summary["messages_upserted"] += 1
        if not existed:
            summary["messages_inserted"] += 1
    synced_through = max(
        (str(item.get("sent_at") or "") for item in entry["history"]),
        default=None,
    )
    store.mark_candidate_history_state(
        candidate_id,
        status=entry["history_status"],
        synced_through=synced_through,
    )
    store.refresh_candidate_activity(candidate_id)
    if entry["profile"]:
        store.upsert_candidate_snapshot(
            candidate_id=candidate_id,
            source="chat_profile",
            payload_hash=stable_json_hash(entry["profile"]),
            summary=entry["profile"],
            fetched_at=observed_at,
        )
        summary["profiles_upserted"] += 1


def _record_candidate_error(
    store: WorkflowStore,
    summary: dict[str, Any],
    *,
    run_id: str,
    account_id: int,
    friend_id: int,
    page: int,
    exc: Exception,
) -> None:
    summary["errors"] += 1
    store.record_sync_error(
        run_id=run_id,
        account_id=account_id,
        friend_id=friend_id,
        page=page,
        error_code=type(exc).__name__,
        error_message=str(exc),
    )


def _has_more(payload: Any) -> bool | None:
    if not isinstance(payload, dict):
        return None
    for key in ("hasMore", "hasNext", "has_next"):
        if key in payload:
            return bool(payload[key])
    page = _int_or_none(payload.get("page"))
    page_count = _int_or_none(payload.get("pageCount") or payload.get("totalPage"))
    if page is not None and page_count is not None:
        return page < page_count
    return None


def _optional_call(client: BossReadGateway, method_name: str, *, default: Any) -> tuple[Any, bool]:
    method = getattr(client, method_name, None)
    if method is None:
        return default, False
    try:
        return method(), True
    except Exception:  # noqa: BLE001 - optional enrichment cannot block core inbox reads.
        return default, False


def _request_count(client: BossReadGateway) -> int:
    stats = getattr(client, "request_stats", {})
    return int(stats.get("request_count", 0)) if isinstance(stats, dict) else 0


def _int_or_none(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _chunks(values: list[int], size: int) -> list[list[int]]:
    return [values[index:index + size] for index in range(0, len(values), size)]
