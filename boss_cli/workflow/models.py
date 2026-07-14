"""Typed workflow records and status constants."""

from __future__ import annotations

from typing import Literal, TypedDict

DecisionValue = Literal["send", "skip_duplicate", "needs_review", "rejected", "error"]
ActionType = Literal["send_message", "exchange_wechat"]
ActionStatus = Literal[
    "queued",
    "locked",
    "sending",
    "sent_unverified",
    "verified",
    "failed_retryable",
    "failed_terminal",
    "skipped_duplicate",
    "cancelled",
    "needs_review",
]
Severity = Literal["debug", "info", "warning", "error"]


class QueueSummary(TypedDict):
    """Counts of outbound actions by status."""

    queued: int
    locked: int
    sending: int
    sent_unverified: int
    verified: int
    failed_retryable: int
    failed_terminal: int
    skipped_duplicate: int
    cancelled: int
    needs_review: int
    total: int


ALL_ACTION_STATUSES: tuple[ActionStatus, ...] = (
    "queued",
    "locked",
    "sending",
    "sent_unverified",
    "verified",
    "failed_retryable",
    "failed_terminal",
    "skipped_duplicate",
    "cancelled",
    "needs_review",
)

SEND_MESSAGE = "send_message"
EXCHANGE_WECHAT = "exchange_wechat"
ALL_ACTION_TYPES: tuple[ActionType, ...] = (SEND_MESSAGE, EXCHANGE_WECHAT)
