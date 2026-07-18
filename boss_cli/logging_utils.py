"""Bounded UTF-8 operational logging with defensive secret redaction."""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .platform import ensure_private_directory, ensure_private_file
from .workflow.redaction import redact_text

_PATTERNS = (
    (re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)((?:DASHSCOPE_API_KEY|BOSS_COOKIES)\s*[:=]\s*)[^\s]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)((?:__zp_stoken__|zp_at|wt2|wbg)\s*=\s*)[^;\s]+"), r"\1[REDACTED]"),
    (re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "[PHONE]"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[EMAIL]"),
)


def redact_log_message(value: str) -> str:
    result = value
    for pattern, replacement in _PATTERNS:
        result = pattern.sub(replacement, result)
    return redact_text(result, max_length=0)


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_log_message(record.getMessage())
        record.args = ()
        return True


def configure_rotating_file_logging(path: Path, *, verbose: bool = False) -> None:
    """Attach one idempotent rotating file handler to the root logger."""
    resolved = path.expanduser().resolve()
    ensure_private_directory(resolved.parent)
    resolved.touch(exist_ok=True)
    ensure_private_file(resolved)
    root = logging.getLogger()
    for handler in root.handlers:
        if isinstance(handler, RotatingFileHandler) and Path(handler.baseFilename) == resolved:
            return
        handler.setLevel(logging.INFO if verbose else logging.WARNING)
    handler = RotatingFileHandler(
        resolved,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    handler.addFilter(RedactingFilter())
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
