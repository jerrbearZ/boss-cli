"""Normalize Boss API payloads into workflow records."""

from __future__ import annotations

from typing import Any

from ..auth import Credential
from .redaction import hash_name, message_fingerprint, redact_name, redact_text, sha256_text


def account_hash_for_credential(credential: Credential) -> str:
    """Build a stable, non-secret account hash from available cookies."""
    cookies = credential.cookies or {}
    parts = [
        cookies.get("wt2", ""),
        cookies.get("zp_at", ""),
        cookies.get("Hm_lvt_194df3105ad7148dcf2b98a91b5e727a", ""),
    ]
    return sha256_text("|".join(parts) or credential.as_cookie_header())


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


def normalize_candidate(
    detail: dict[str, Any],
    *,
    account_id: int,
    job_id: int | None = None,
    last_message: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize a Boss friend detail row for candidate upsert."""
    friend_id = int(detail.get("friendId") or detail.get("uid") or 0)
    uid = int(detail.get("uid") or friend_id or 0) or None
    name = str(detail.get("name") or detail.get("geekName") or "")
    preview = extract_message_text(last_message or {})
    fingerprint = normalize_message(last_message or {}, direction="unknown").get("fingerprint") if last_message else None
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
        "last_message_preview": redact_text(preview) if preview else None,
        "last_message_fingerprint": fingerprint,
    }


def normalize_message(message: dict[str, Any], *, direction: str = "unknown") -> dict[str, Any]:
    """Normalize a Boss message-like payload for message upsert."""
    text = extract_message_text(message)
    sent_at = extract_message_time(message)
    info = message.get("lastMsgInfo", {}) if isinstance(message.get("lastMsgInfo"), dict) else {}
    boss_msg_id = (
        message.get("msgId")
        or message.get("messageId")
        or info.get("msgId")
        or info.get("messageId")
    )
    msg_type = str(message.get("type") or info.get("type") or info.get("msgType") or "")
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
    }


def extract_message_text(message: dict[str, Any]) -> str:
    """Extract visible text from Boss last-message or history payloads."""
    if not isinstance(message, dict):
        return ""
    info = message.get("lastMsgInfo", {}) if isinstance(message.get("lastMsgInfo"), dict) else {}
    body = message.get("body", {}) if isinstance(message.get("body"), dict) else {}
    return str(
        info.get("showText")
        or info.get("text")
        or message.get("showText")
        or message.get("text")
        or body.get("text")
        or body.get("content")
        or ""
    )


def extract_message_time(message: dict[str, Any]) -> str:
    """Extract a best-effort message time string."""
    if not isinstance(message, dict):
        return ""
    info = message.get("lastMsgInfo", {}) if isinstance(message.get("lastMsgInfo"), dict) else {}
    return str(
        info.get("lastTime")
        or info.get("time")
        or message.get("lastTime")
        or message.get("time")
        or message.get("dateTime")
        or ""
    )
