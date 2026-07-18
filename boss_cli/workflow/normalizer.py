"""Normalize Boss API payloads into workflow records."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..auth import Credential
from .redaction import (
    hash_name,
    message_fingerprint,
    redact_name,
    redact_text,
    sha256_text,
    stable_json_hash,
)


def account_hash_for_credential(credential: Credential) -> str:
    """Build a fallback non-secret account hash from available cookies."""
    cookies = credential.cookies or {}
    parts = [
        cookies.get("wt2", ""),
        cookies.get("zp_at", ""),
        cookies.get("Hm_lvt_194df3105ad7148dcf2b98a91b5e727a", ""),
    ]
    return sha256_text("boss-cookie:" + ("|".join(parts) or credential.as_cookie_header()))


def account_hash_for_recruiter_id(recruiter_user_id: int | str) -> str:
    """Build a stable, non-secret account hash from Boss' own recruiter user id."""
    return sha256_text(f"boss-recruiter:{recruiter_user_id}")


def extract_recruiter_user_id_from_user_info(payload: Any) -> int | None:
    """Extract a stable user id from the current-account payload when available."""
    if not isinstance(payload, dict):
        return None
    candidates = [payload]
    for key in ("userInfo", "user", "bossInfo", "data"):
        value = payload.get(key)
        if isinstance(value, dict):
            candidates.append(value)
    for item in candidates:
        for key in ("userId", "uid", "bossId", "id"):
            value = _int_or_none(item.get(key))
            if value:
                return value
    return None


def extract_recruiter_user_id(last_messages: Any) -> int | None:
    """Infer the logged-in recruiter id from latest-message payloads.

    Boss latest-message rows carry both the candidate uid and the message
    ``fromId``/``toId`` values. The recruiter is the other side of that pair.
    This survives QR relogin because it is a Boss-side user id, not a cookie.
    """
    if not isinstance(last_messages, list):
        return None

    counts: dict[int, int] = {}
    for item in last_messages:
        if not isinstance(item, dict):
            continue
        candidate_uid = _int_or_none(item.get("uid") or item.get("friendId"))
        info = item.get("lastMsgInfo", {}) if isinstance(item.get("lastMsgInfo"), dict) else {}
        from_id = _int_or_none(info.get("fromId"))
        to_id = _int_or_none(info.get("toId"))
        if not candidate_uid or not from_id or not to_id:
            continue

        recruiter_id: int | None = None
        if from_id == candidate_uid and to_id != candidate_uid:
            recruiter_id = to_id
        elif to_id == candidate_uid and from_id != candidate_uid:
            recruiter_id = from_id
        if recruiter_id:
            counts[recruiter_id] = counts.get(recruiter_id, 0) + 1

    if not counts:
        return None
    return max(counts.items(), key=lambda item: item[1])[0]


def message_direction(message: dict[str, Any], *, candidate_uid: int | None, recruiter_user_id: int | None) -> str:
    """Return inbound/outbound when Boss ids make direction unambiguous."""
    if not isinstance(message, dict):
        return "unknown"
    info = message.get("lastMsgInfo", {}) if isinstance(message.get("lastMsgInfo"), dict) else {}
    from_id = _int_or_none(info.get("fromId"))
    to_id = _int_or_none(info.get("toId"))
    if recruiter_user_id and from_id == recruiter_user_id:
        return "outbound"
    if recruiter_user_id and to_id == recruiter_user_id:
        return "inbound"
    if candidate_uid and from_id == candidate_uid:
        return "inbound"
    if candidate_uid and to_id == candidate_uid:
        return "outbound"
    return "unknown"


def index_last_messages(last_messages: Any) -> dict[int, dict[str, Any]]:
    """Index Boss last-message payloads by uid/friend id."""
    indexed: dict[int, dict[str, Any]] = {}
    if not isinstance(last_messages, list):
        return indexed
    for item in last_messages:
        if not isinstance(item, dict):
            continue
        key = item.get("uid") or item.get("friendId")
        if key:
            indexed[int(key)] = item
    return indexed


def normalize_job(job: dict[str, Any], *, account_id: int, run_id: str) -> dict[str, Any] | None:
    """Normalize a recruiter job row, returning None when no stable job id exists."""
    enc_job_id = str(job.get("encryptJobId") or job.get("encJobId") or "")
    if not enc_job_id:
        return None
    metadata = {
        "salary": str(job.get("salaryDesc") or ""),
        "address": str(job.get("address") or job.get("cityName") or ""),
        "status": str(job.get("status") or job.get("jobStatus") or ""),
    }
    return {
        "account_id": account_id,
        "enc_job_id": enc_job_id,
        "job_name": str(job.get("jobName") or job.get("name") or ""),
        "active": True,
        "boss_job_id": _int_or_none(job.get("jobId") or job.get("id")),
        "last_seen_run_id": run_id,
        "metadata": metadata,
    }


def normalize_candidate(
    detail: dict[str, Any],
    *,
    account_id: int,
    job_id: int | None = None,
    last_message: dict[str, Any] | None = None,
    message_direction_value: str = "unknown",
    observed_at: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Normalize a Boss friend detail row for candidate upsert."""
    friend_id = int(detail.get("friendId") or detail.get("uid") or 0)
    uid = int(detail.get("uid") or friend_id or 0) or None
    name = str(detail.get("name") or detail.get("geekName") or "")
    preview = extract_message_text(last_message or {})
    fingerprint = normalize_message(last_message or {}, direction=message_direction_value).get("fingerprint") if last_message else None
    last_time = extract_message_time(last_message or {}) or detail.get("lastTime")

    return {
        "account_id": account_id,
        "friend_id": friend_id,
        "friend_source": int(detail.get("friendSource") or detail.get("source") or detail.get("geekSource") or 0),
        "uid": uid,
        "encrypt_uid": str(detail.get("encryptUid") or detail.get("encryptFriendId") or ""),
        "encrypt_geek_id": str(detail.get("encryptGeekId") or detail.get("encryptUid") or ""),
        "security_id_present": bool(detail.get("securityId")),
        "job_id": job_id,
        "encrypt_job_id": str(detail.get("encryptJobId") or ""),
        "job_name": str(detail.get("jobName") or ""),
        "name_redacted": redact_name(name),
        "name_hash": hash_name(name),
        "last_seen_at": last_time or None,
        "last_activity_at": last_time or None,
        "last_observed_at": observed_at,
        "last_seen_run_id": run_id,
        "last_message_preview": redact_text(preview) if preview else None,
        "last_message_fingerprint": fingerprint,
    }


def normalize_message(
    message: dict[str, Any],
    *,
    direction: str = "unknown",
    source: str = "latest",
) -> dict[str, Any]:
    """Normalize a Boss message-like payload for message upsert."""
    text = extract_message_text(message)
    sent_at = extract_message_time(message)
    info = message.get("lastMsgInfo", {}) if isinstance(message.get("lastMsgInfo"), dict) else {}
    boss_msg_id = message.get("msgId") or message.get("messageId") or message.get("mid") or info.get("msgId") or info.get("messageId")
    msg_type = str(message.get("type") or info.get("type") or info.get("msgType") or "")
    content_kind = message_content_kind(message, text=text, msg_type=msg_type)
    fingerprint = message_fingerprint(
        direction=direction,
        text=text,
        boss_msg_id=boss_msg_id,
        sent_at=sent_at,
        msg_type=msg_type,
    )
    return {
        "fingerprint": fingerprint,
        "direction": direction,
        "boss_msg_id": boss_msg_id,
        "msg_type": msg_type,
        "sent_at": sent_at,
        "text_hash": sha256_text(text),
        "text_redacted": redact_text(text),
        "source": source,
        "content_kind": content_kind,
        "payload_hash": stable_json_hash(message),
    }


def normalize_history_message(
    message: dict[str, Any],
    *,
    candidate_uid: int | None,
    recruiter_user_id: int | None,
) -> dict[str, Any]:
    """Normalize one history message and prefer the history ``received`` flag."""
    if "received" in message:
        direction = "inbound" if bool(message.get("received")) else "outbound"
    else:
        direction = message_direction(
            message,
            candidate_uid=candidate_uid,
            recruiter_user_id=recruiter_user_id,
        )
    return normalize_message(message, direction=direction, source="history")


def extract_history_messages(payload: Any) -> list[dict[str, Any]]:
    """Extract history rows from known Boss response shapes."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("messages", "messageList", "result", "list"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def normalize_profile_summary(payload: Any) -> dict[str, Any]:
    """Return a small redacted profile summary suitable for local snapshots."""
    if not isinstance(payload, dict):
        return {}
    root = payload.get("geekDetailInfo", payload)
    if not isinstance(root, dict):
        return {}
    base = root.get("geekBaseInfo", root)
    if not isinstance(base, dict):
        base = root
    work = root.get("geekWorkExpList", base.get("workExpList", []))
    education = root.get("geekEduExpList", base.get("eduExpList", []))
    return {
        "degree": str(base.get("degreeCategory") or base.get("degree") or ""),
        "work_year": str(base.get("workYearDesc") or base.get("workYear") or base.get("year") or ""),
        "expect_position": redact_text(str(base.get("expectPosition") or base.get("positionName") or "")),
        "expect_city": str(base.get("expectCity") or base.get("city") or ""),
        "work_experience_count": len(work) if isinstance(work, list) else 0,
        "education_count": len(education) if isinstance(education, list) else 0,
    }


def extract_message_text(message: dict[str, Any]) -> str:
    """Extract visible text from Boss last-message or history payloads."""
    if not isinstance(message, dict):
        return ""
    info = message.get("lastMsgInfo", {}) if isinstance(message.get("lastMsgInfo"), dict) else {}
    raw_body = message.get("body", {})
    body = raw_body if isinstance(raw_body, dict) else {}
    return str(
        info.get("showText")
        or info.get("text")
        or message.get("showText")
        or message.get("text")
        or body.get("text")
        or body.get("content")
        or (raw_body if isinstance(raw_body, str) else "")
        or ""
    )


def message_content_kind(message: dict[str, Any], *, text: str, msg_type: str) -> str:
    """Map a Boss message into a small stable content category."""
    body = message.get("body")
    lowered = text.casefold()
    if isinstance(body, dict) and body.get("resume"):
        return "resume"
    if any(token in lowered for token in ("交换微信", "换微信", "交换电话", "换电话")):
        return "contact_request"
    if msg_type in {"1", "text", "TEXT"} or text:
        return "text"
    if msg_type in {"2", "image", "IMAGE"}:
        return "image"
    if msg_type in {"system", "SYSTEM"}:
        return "system"
    return "unknown"


def extract_message_time(message: dict[str, Any]) -> str:
    """Extract a best-effort message time string."""
    if not isinstance(message, dict):
        return ""
    info = message.get("lastMsgInfo", {}) if isinstance(message.get("lastMsgInfo"), dict) else {}
    timestamp = info.get("msgTime") or message.get("lastTS") or message.get("msgTime") or info.get("timestamp") or message.get("timestamp")
    parsed = _timestamp_to_utc_iso(timestamp)
    if parsed:
        return parsed
    return str(info.get("lastTime") or info.get("time") or message.get("lastTime") or message.get("time") or message.get("dateTime") or "")


def _int_or_none(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _timestamp_to_utc_iso(value: Any) -> str:
    numeric = _int_or_none(value)
    if numeric is None:
        return ""
    seconds = numeric / 1000 if numeric > 10_000_000_000 else numeric
    try:
        return datetime.fromtimestamp(seconds, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    except (OverflowError, OSError, ValueError):
        return ""
