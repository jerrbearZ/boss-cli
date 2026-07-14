"""Tests for constrained continuous recruiting automation."""

from __future__ import annotations

import json

import pytest

from boss_cli.auth import Credential
from boss_cli.browser_reply import BrowserReplyResult, BrowserWechatResult
from boss_cli.workflow import init_db
from boss_cli.workflow.automation import AutomationConfig, run_automation_cycle, run_daemon
from boss_cli.workflow.selector import AlibabaQwenSelector, TemplateSelection, TemplateSelectionError


class FakeBossClient:
    def __init__(self) -> None:
        self.message_id = "msg-1"
        self.message = "I am interested in this role"

    @property
    def request_stats(self):
        return {"request_count": 1}

    def get_user_info(self):
        return {"userInfo": {"userId": 900}}

    def get_boss_chatted_jobs(self):
        return []

    def get_boss_friend_list(self, label_id=0, enc_job_id="", sort="", page=1):
        return {"result": [{"friendId": 101}], "hasMore": False}

    def get_boss_friend_details(self, friend_ids):
        return {
            "friendList": [
                {
                    "friendId": 101,
                    "uid": 501,
                    "friendSource": 0,
                    "encryptUid": "enc-501",
                    "encryptGeekId": "geek-501",
                    "encryptJobId": "job-1",
                    "name": "Candidate",
                    "jobName": "Sales",
                }
            ]
        }

    def get_boss_last_messages(self, friend_ids, src=0):
        return [
            {
                "uid": 501,
                "lastMsgInfo": {
                    "fromId": 501,
                    "toId": 900,
                    "showText": self.message,
                    "msgTime": 1783917600000,
                    "msgId": self.message_id,
                    "type": "text",
                },
            }
        ]

    def get_boss_chat_history(self, gid, count=20, max_msg_id=0):
        return {"messages": []}


class FixedSelector:
    provider = "test"
    model = "fixed"

    def __init__(self, outcome="selected", confidence=0.95):
        self.outcome = outcome
        self.confidence = confidence
        self.calls = 0

    def select(self, context, templates):
        self.calls += 1
        template_id = int(templates[0]["id"]) if self.outcome == "selected" else None
        return TemplateSelection(
            outcome=self.outcome,
            template_id=template_id,
            confidence=self.confidence,
            reason="fixed test decision",
            provider=self.provider,
            model=self.model,
            response_hash="response-hash",
        )


def approved_template(store, *, version="v1"):
    return store.upsert_template(
        name="interest_reply",
        version=version,
        body="Thanks for your interest. Let us discuss the role.",
        approved=True,
        active=True,
        approved_by="test",
        selection_guidance="Use for clear role interest.",
    )


def test_qwen_selector_uses_json_mode_and_validates_selection():
    captured = {}

    def fake_post(url, headers, payload, timeout):
        captured.update({"url": url, "headers": headers, "payload": payload, "timeout": timeout})
        value = {"outcome": "selected", "template_id": 7, "confidence": 0.91, "reason": "Direct interest"}
        return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value)}}]}

    selector = AlibabaQwenSelector(api_key="secret", model="qwen-plus", post_json=fake_post)
    result = selector.select(
        {"job_name": "Sales", "messages": [{"direction": "inbound", "content_kind": "text", "text_redacted": "hello"}]},
        [{"id": 7, "name": "reply", "body": "Approved text", "approved": 1, "active": 1, "retired_at": None}],
    )

    assert result.template_id == 7
    assert captured["url"].endswith("/chat/completions")
    assert captured["payload"]["model"] == "qwen-plus"
    assert captured["payload"]["response_format"] == {"type": "json_object"}
    assert captured["payload"]["enable_thinking"] is False
    assert "one of [7]" in captured["payload"]["messages"][0]["content"]


def test_qwen_selector_rejects_id_outside_catalog():
    def fake_post(url, headers, payload, timeout):
        value = {"outcome": "selected", "template_id": 99, "confidence": 1, "reason": "invalid"}
        return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value)}}]}

    selector = AlibabaQwenSelector(api_key="secret", model="qwen-plus", post_json=fake_post)
    with pytest.raises(TemplateSelectionError, match="outside the approved catalog"):
        selector.select(
            {"messages": []},
            [{"id": 7, "body": "Approved", "approved": 1, "active": 1, "retired_at": None}],
        )


def test_qwen_selector_rejects_non_object_json():
    def fake_post(url, headers, payload, timeout):
        return {"choices": [{"finish_reason": "stop", "message": {"content": "[]"}}]}

    selector = AlibabaQwenSelector(api_key="secret", post_json=fake_post)
    with pytest.raises(TemplateSelectionError, match="non-object"):
        selector.select(
            {"messages": []},
            [{"id": 7, "body": "Approved", "approved": 1, "active": 1, "retired_at": None}],
        )


def test_retryable_selector_failure_is_not_persisted(tmp_path):
    class RetryableSelector(FixedSelector):
        def select(self, context, templates):
            raise TemplateSelectionError("temporary outage", retryable=True)

    with init_db(tmp_path / "workflow.db") as store:
        approved_template(store)
        with pytest.raises(TemplateSelectionError, match="temporary outage"):
            run_automation_cycle(
                store,
                FakeBossClient(),
                Credential({"wt2": "test"}),
                RetryableSelector(),
                AutomationConfig(),
            )

        assert store.row_count("automation_decisions") == 0


def test_dry_cycle_records_selection_without_queueing_and_is_idempotent(tmp_path):
    client = FakeBossClient()
    selector = FixedSelector()
    with init_db(tmp_path / "workflow.db") as store:
        approved_template(store)
        first = run_automation_cycle(store, client, Credential({"wt2": "test"}), selector, AutomationConfig())
        second = run_automation_cycle(store, client, Credential({"wt2": "test"}), selector, AutomationConfig())

        assert first["selected"] == 1
        assert second["eligible"] == 0
        assert selector.calls == 1
        assert store.queue_summary()["total"] == 0
        assert store.automation_summary(account_id=first["account_id"])["dry_run"] == 1


def test_low_confidence_selection_requires_review(tmp_path):
    with init_db(tmp_path / "workflow.db") as store:
        approved_template(store)
        result = run_automation_cycle(
            store,
            FakeBossClient(),
            Credential({"wt2": "test"}),
            FixedSelector(confidence=0.4),
            AutomationConfig(live=True, confidence_threshold=0.75, max_actions_per_cycle=0),
        )

        assert result["review"] == 1
        assert result["queued"] == 0
        assert store.queue_summary()["total"] == 0


def test_live_cycle_sends_message_then_wechat(tmp_path):
    calls = []

    def send_message(credential, target, body):
        calls.append(("message", target.friend_id, body))
        return BrowserReplyResult(
            ok=True,
            friend_id=target.friend_id,
            sent=True,
            verified=True,
            engine="fake",
            method="fake.message",
            target=target.safe_dict(),
            verification={"matched": True},
        )

    def send_wechat(credential, target):
        calls.append(("wechat", target.friend_id, None))
        return BrowserWechatResult(
            ok=True,
            friend_id=target.friend_id,
            requested=True,
            verified=True,
            engine="fake",
            method="fake.wechat",
            target=target.safe_dict(),
            verification={"matched": True},
        )

    with init_db(tmp_path / "workflow.db") as store:
        approved_template(store)
        result = run_automation_cycle(
            store,
            FakeBossClient(),
            Credential({"wt2": "test"}),
            FixedSelector(),
            AutomationConfig(live=True, max_actions_per_cycle=2, action_delay_seconds=0),
            send_func=send_message,
            wechat_send_func=send_wechat,
            message_preflight_func=lambda credential, target, body: False,
        )

        assert [call[0] for call in calls] == ["message", "wechat"]
        assert result["sent"] == 1
        assert result["wechat_verified"] == 1
        assert store.queue_summary()["verified"] == 2


def test_existing_queue_drains_after_last_template_is_retired(tmp_path):
    calls = []

    def send_message(credential, target, body):
        calls.append("message")
        return BrowserReplyResult(
            ok=True,
            friend_id=target.friend_id,
            sent=True,
            verified=True,
            engine="fake",
            method="fake.message",
            target=target.safe_dict(),
            verification={"matched": True},
        )

    def send_wechat(credential, target):
        calls.append("wechat")
        return BrowserWechatResult(
            ok=True,
            friend_id=target.friend_id,
            requested=True,
            verified=True,
            engine="fake",
            method="fake.wechat",
            target=target.safe_dict(),
            verification={"matched": True},
        )

    with init_db(tmp_path / "workflow.db") as store:
        template_id = approved_template(store)
        run_automation_cycle(
            store,
            FakeBossClient(),
            Credential({"wt2": "test"}),
            FixedSelector(),
            AutomationConfig(live=True, max_actions_per_cycle=0),
        )
        store.retire_template(template_id)

        result = run_automation_cycle(
            store,
            FakeBossClient(),
            Credential({"wt2": "test"}),
            FixedSelector(),
            AutomationConfig(live=True, max_actions_per_cycle=2, action_delay_seconds=0),
            send_func=send_message,
            wechat_send_func=send_wechat,
            message_preflight_func=lambda credential, target, body: False,
        )

        assert result["status"] == "needs_review"
        assert calls == ["message", "wechat"]
        assert store.queue_summary()["verified"] == 2


def test_pause_allows_sync_but_blocks_decision_and_send(tmp_path):
    selector = FixedSelector()
    with init_db(tmp_path / "workflow.db") as store:
        approved_template(store)
        store.set_setting("paused", True)
        result = run_automation_cycle(
            store,
            FakeBossClient(),
            Credential({"wt2": "test"}),
            selector,
            AutomationConfig(live=True),
        )

        assert result["status"] == "paused"
        assert store.row_count("messages") == 1
        assert selector.calls == 0
        assert store.queue_summary()["total"] == 0


def test_template_catalog_change_reconsiders_same_inbound_message(tmp_path):
    selector = FixedSelector(outcome="review")
    with init_db(tmp_path / "workflow.db") as store:
        approved_template(store, version="v1")
        first = run_automation_cycle(store, FakeBossClient(), Credential({"wt2": "test"}), selector, AutomationConfig())
        approved_template(store, version="v2")
        second = run_automation_cycle(store, FakeBossClient(), Credential({"wt2": "test"}), selector, AutomationConfig())

        assert first["review"] == 1
        assert second["review"] == 1
        assert selector.calls == 2


def test_approving_new_template_version_retires_previous_version(tmp_path):
    with init_db(tmp_path / "workflow.db") as store:
        first_id = approved_template(store, version="v1")
        second_id = store.upsert_template(
            name="interest_reply",
            version="v2",
            body="Updated approved reply.",
            approved=False,
            active=False,
        )
        store.set_template_approval(second_id, approved=True, approved_by="test")

        first = store.get_template(first_id)
        second = store.get_template(second_id)
        assert first["active"] == 0
        assert first["retired_at"] is not None
        assert second["active"] == 1
        assert second["retired_at"] is None


def test_daemon_lease_excludes_second_owner_and_releases(tmp_path):
    db_path = tmp_path / "workflow.db"
    first = init_db(db_path)
    second = init_db(db_path)
    try:
        assert first.claim_daemon(daemon_name="primary", owner_id="one", live_mode=False, lease_seconds=900)
        assert not second.claim_daemon(daemon_name="primary", owner_id="two", live_mode=False, lease_seconds=900)
        first.release_daemon(daemon_name="primary", owner_id="one")
        result = run_daemon(second, lambda: {"status": "completed", "run_id": "run"}, AutomationConfig(), once=True, owner_id="two")

        assert result["cycles"] == 1
        assert second.get_daemon_state()["status"] == "stopped"
    finally:
        first.close()
        second.close()
