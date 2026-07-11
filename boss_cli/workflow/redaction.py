"""Hashing, redaction, and idempotency helpers for workflow state."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

_CN_PHONE_RE = re.compile(r"(?<!\d)(?:\+?86[-\s]?)?1[3-9]\d{9}(?!\d)")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_LONG_NUMBER_RE = re.compile(r"(?<!\d)\d{8,}(?!\d)")
_CONTACT_LABEL_RE = re.compile(
    r"(?i)\b(wechat|weixin|wx|vx)\b\s*[:：]?\s*([A-Za-z][A-Za-z0-9_-]{4,29})"
    r"|(?:微信|微信号|微信ID|微\s*信|v信)\s*[:：]?\s*([A-Za-z][A-Za-z0-9_-]{4,29})"
)
_URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)


def sha256_text(value: Any) -> str:
    """Return a stable SHA-256 hex digest for text-like input."""
    if value is None:
        value = ""
    if isinstance(value, bytes):
        payload = value
    else:
        payload = str(value).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def stable_json_dumps(value: Any) -> str:
    """Serialize JSON-like data deterministically for hashing/storage."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def stable_json_hash(value: Any) -> str:
    """Hash JSON-like data after deterministic serialization."""
    return sha256_text(stable_json_dumps(value))


def redact_name(name: str | None) -> str:
    """Return a display-safe name that does not expose the full raw name."""
    value = (name or "").strip()
    if not value:
        return ""
    if len(value) == 1:
        return "*"
    if re.search(r"[\u4e00-\u9fff]", value):
        return f"{value[0]}*"
    return f"{value[0]}***"


def hash_name(name: str | None) -> str:
    """Hash a normalized candidate name."""
    return sha256_text((name or "").strip().casefold())


def redact_text(text: str | None, *, max_length: int = 160) -> str:
    """Redact contact-like values and truncate message text for safe storage."""
    value = str(text or "")
    value = _URL_RE.sub("[URL]", value)
    value = _EMAIL_RE.sub("[EMAIL]", value)
    value = _CN_PHONE_RE.sub("[PHONE]", value)
    value = _LONG_NUMBER_RE.sub("[NUMBER]", value)

    def _replace_contact(match: re.Match[str]) -> str:
        label = match.group(1) or "微信"
        return f"{label}:[CONTACT]"

    value = _CONTACT_LABEL_RE.sub(_replace_contact, value)
    value = re.sub(r"\s+", " ", value).strip()
    if max_length > 0 and len(value) > max_length:
        return f"{value[:max_length].rstrip()}..."
    return value


def message_fingerprint(
    *,
    direction: str,
    text: str | None = None,
    boss_msg_id: str | int | None = None,
    sent_at: str | None = None,
    msg_type: str | None = None,
) -> str:
    """Create a stable message fingerprint, preferring Boss message ids."""
    if boss_msg_id:
        return sha256_text(f"boss_msg_id:{boss_msg_id}")
    return stable_json_hash(
        {
            "direction": direction,
            "msg_type": msg_type or "",
            "sent_at": sent_at or "",
            "text_hash": sha256_text(text or ""),
        }
    )


def outbound_idempotency_key(
    *,
    candidate_id: int,
    action_type: str,
    template_id: int,
    template_version: str,
    trigger_message_fingerprint: str,
) -> str:
    """Create the production idempotency key for an outbound action."""
    return sha256_text(
        "|".join(
            [
                str(candidate_id),
                action_type,
                str(template_id),
                template_version,
                trigger_message_fingerprint,
            ]
        )
    )
