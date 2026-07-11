"""Tests for workflow redaction and idempotency helpers."""

from __future__ import annotations

from boss_cli.workflow.redaction import (
    hash_name,
    message_fingerprint,
    outbound_idempotency_key,
    redact_name,
    redact_text,
    sha256_text,
    stable_json_hash,
)


def test_redact_text_removes_contact_values():
    text = "候选人手机号 13812345678，微信 wxCandidate_123，邮箱 person@example.com"

    redacted = redact_text(text)

    assert "13812345678" not in redacted
    assert "wxCandidate_123" not in redacted
    assert "person@example.com" not in redacted
    assert "[PHONE]" in redacted
    assert "[CONTACT]" in redacted
    assert "[EMAIL]" in redacted


def test_redact_text_truncates_after_redaction():
    redacted = redact_text("电话 13812345678 " + ("x" * 200), max_length=30)

    assert len(redacted) <= 33
    assert "13812345678" not in redacted
    assert redacted.endswith("...")


def test_redact_name_does_not_return_full_name():
    assert redact_name("张三") == "张*"
    assert redact_name("Alice Smith") == "A***"
    assert redact_name("A") == "*"
    assert hash_name(" Alice ") == hash_name("alice")


def test_hash_helpers_are_stable():
    assert sha256_text("same") == sha256_text("same")
    assert stable_json_hash({"b": 2, "a": 1}) == stable_json_hash({"a": 1, "b": 2})


def test_message_fingerprint_prefers_boss_message_id():
    with_id = message_fingerprint(direction="inbound", text="hello", boss_msg_id=123)
    same_id_other_text = message_fingerprint(direction="outbound", text="different", boss_msg_id=123)
    without_id = message_fingerprint(direction="inbound", text="hello", sent_at="2026-07-12T00:00:00Z")

    assert with_id == same_id_other_text
    assert with_id != without_id


def test_outbound_idempotency_key_is_stable_and_sensitive_to_template_version():
    key = outbound_idempotency_key(
        candidate_id=1,
        action_type="send_template",
        template_id=2,
        template_version="v1",
        trigger_message_fingerprint="abc",
    )
    same = outbound_idempotency_key(
        candidate_id=1,
        action_type="send_template",
        template_id=2,
        template_version="v1",
        trigger_message_fingerprint="abc",
    )
    changed = outbound_idempotency_key(
        candidate_id=1,
        action_type="send_template",
        template_id=2,
        template_version="v2",
        trigger_message_fingerprint="abc",
    )

    assert key == same
    assert key != changed
