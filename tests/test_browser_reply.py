from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from boss_cli.browser_reply import (
    BrowserReplyError,
    BrowserReplyResult,
    BrowserReplyTarget,
    _request_wechat_from_page,
    request_wechat_via_browser,
    resolve_browser_reply_target,
)
from boss_cli.cli import cli


runner = CliRunner()


class FakeClient:
    def __init__(self, detail: dict):
        self.detail = detail

    def get_boss_friend_details(self, friend_ids):
        return {"friendList": [self.detail]}


def test_resolve_browser_reply_target_from_friend_detail():
    target = resolve_browser_reply_target(
        FakeClient(
            {
                "uid": 123,
                "friendSource": 0,
                "encryptUid": "enc-uid",
                "name": "candidate",
                "jobName": "sales",
            }
        ),
        123,
    )

    assert target.friend_id == 123
    assert target.friend_source == 0
    assert target.encrypt_uid == "enc-uid"
    assert target.name == "candidate"
    assert target.job_name == "sales"


def test_resolve_browser_reply_target_requires_encrypt_uid():
    try:
        resolve_browser_reply_target(FakeClient({"uid": 123}), 123)
    except BrowserReplyError as exc:
        assert exc.code == "target_missing_context"
    else:
        raise AssertionError("expected BrowserReplyError")


def test_recruiter_reply_browser_help():
    result = runner.invoke(cli, ["recruiter", "reply-browser", "--help"])

    assert result.exit_code == 0
    assert "--engine" in result.output
    assert "--dry-run" in result.output
    assert "--headless" in result.output


def test_recruiter_reply_browser_dry_run_does_not_send():
    mock_cred = MagicMock()
    mock_cred.cookies = {"wt2": "x"}
    fake_client = FakeClient(
        {
            "uid": 123,
            "friendSource": 0,
            "encryptUid": "enc-uid",
            "name": "candidate",
            "jobName": "sales",
        }
    )

    with (
        patch("boss_cli.commands._common.get_credential", return_value=mock_cred),
        patch("boss_cli.commands.recruiter.run_client_action", side_effect=lambda cred, action: action(fake_client)),
        patch("boss_cli.commands.recruiter.send_boss_message_via_browser") as send,
    ):
        result = runner.invoke(cli, ["recruiter", "reply-browser", "123", "hello", "--dry-run", "--json"])

    assert result.exit_code == 0
    send.assert_not_called()
    data = json.loads(result.output)
    assert data["ok"] is True
    assert data["data"]["dry_run"] is True
    assert data["data"]["sent"] is False
    assert data["data"]["target"]["friend_id"] == 123


def test_recruiter_reply_browser_invokes_browser_adapter():
    mock_cred = MagicMock()
    mock_cred.cookies = {"wt2": "x"}
    fake_client = FakeClient(
        {
            "uid": 123,
            "friendSource": 0,
            "encryptUid": "enc-uid",
            "name": "candidate",
            "jobName": "sales",
        }
    )
    adapter_result = BrowserReplyResult(
        ok=True,
        friend_id=123,
        sent=True,
        verified=True,
        engine="camoufox",
        method="iBossRoot.chat.sendMessage",
        target={"friend_id": 123, "friend_source": 0, "encrypt_uid": "enc-uid", "name": "candidate", "job_name": "sales"},
        verification={"status": "matched", "matched": True, "last_text": "hello"},
    )

    with (
        patch("boss_cli.commands._common.get_credential", return_value=mock_cred),
        patch("boss_cli.commands.recruiter.run_client_action", side_effect=lambda cred, action: action(fake_client)),
        patch("boss_cli.commands.recruiter.send_boss_message_via_browser", return_value=adapter_result) as send,
    ):
        result = runner.invoke(cli, ["recruiter", "reply-browser", "123", "hello", "-y", "--json"])

    assert result.exit_code == 0
    send.assert_called_once()
    _, target, message = send.call_args.args
    assert target.friend_id == 123
    assert message == "hello"
    data = json.loads(result.output)
    assert data["data"]["verified"] is True


def test_request_wechat_browser_returns_verified_result():
    credential = MagicMock()
    target = BrowserReplyTarget(friend_id=123, friend_source=0, encrypt_uid="enc-uid")
    with patch(
        "boss_cli.browser_reply._request_wechat_once_with_engine",
        return_value=("dom.exchange-wechat", {"matched": True, "indicator": "等待对方同意"}),
    ) as request:
        result = request_wechat_via_browser(credential, target, engine="camoufox")

    request.assert_called_once()
    assert result.ok is True
    assert result.requested is True
    assert result.verified is True
    assert result.method == "dom.exchange-wechat"


def test_request_wechat_dom_requires_visible_success_change():
    class FakeControl:
        def __init__(self, conversation):
            self.conversation = conversation
            self.last = self

        def count(self):
            return 1

        def is_visible(self, timeout=0):
            return True

        def click(self, timeout=0):
            self.conversation.clicked = True

    class EmptyControl:
        last = None

        def __init__(self):
            self.last = self

        def count(self):
            return 0

        def is_visible(self, timeout=0):
            return False

    class FakeConversation:
        def __init__(self):
            self.clicked = False

        def inner_text(self, timeout=0):
            return "等待对方同意" if self.clicked else "换微信"

        def get_by_text(self, label, exact=True):
            return FakeControl(self) if label == "换微信" else EmptyControl()

    class FakePage:
        def __init__(self):
            self.conversation = FakeConversation()

        def get_by_text(self, label, exact=True):
            return EmptyControl()

        def wait_for_timeout(self, timeout):
            return None

    page = FakePage()
    target = BrowserReplyTarget(friend_id=123, friend_source=0, encrypt_uid="enc-uid")
    with (
        patch("boss_cli.browser_reply._prepare_chat_page"),
        patch(
            "boss_cli.browser_reply._select_target_conversation",
            return_value=page.conversation,
        ),
    ):
        method, verification = _request_wechat_from_page(page, target, timeout_ms=1000)

    assert method == "dom.exchange-wechat"
    assert verification["matched"] is True
    assert verification["indicator"] == "等待对方同意"


def test_request_wechat_dom_accepts_request_sent_wording():
    class FakeControl:
        def __init__(self, conversation):
            self.conversation = conversation
            self.last = self

        def count(self):
            return 1

        def is_visible(self, timeout=0):
            return True

        def click(self, timeout=0):
            self.conversation.clicked = True

    class EmptyControl:
        def __init__(self):
            self.last = self

        def count(self):
            return 0

        def is_visible(self, timeout=0):
            return False

    class FakeConversation:
        def __init__(self):
            self.clicked = False

        def inner_text(self, timeout=0):
            return "请求交换微信已发送" if self.clicked else "换微信"

        def get_by_text(self, label, exact=True):
            return FakeControl(self) if label == "换微信" else EmptyControl()

    class FakePage:
        def __init__(self):
            self.conversation = FakeConversation()

        def get_by_text(self, label, exact=True):
            return EmptyControl()

        def wait_for_timeout(self, timeout):
            return None

    page = FakePage()
    target = BrowserReplyTarget(friend_id=123, friend_source=0, encrypt_uid="enc-uid")
    with (
        patch("boss_cli.browser_reply._prepare_chat_page"),
        patch(
            "boss_cli.browser_reply._select_target_conversation",
            return_value=page.conversation,
        ),
    ):
        method, verification = _request_wechat_from_page(page, target, timeout_ms=1000)

    assert method == "dom.exchange-wechat"
    assert verification == {"status": "matched", "matched": True, "indicator": "请求交换微信已发送"}


def test_request_wechat_accepts_incoming_exchange_before_confirming():
    calls = []

    class FakeControl:
        def __init__(self, label, click):
            self.label = label
            self._click = click
            self.last = self

        def count(self):
            return 1

        def is_visible(self, timeout=0):
            return True

        def click(self, timeout=0):
            calls.append(self.label)
            self._click()

    class EmptyControl:
        def __init__(self):
            self.last = self

        def count(self):
            return 0

        def is_visible(self, timeout=0):
            return False

    class FakeConversation:
        completed = False

        def inner_text(self, timeout=0):
            return "双方已交换微信" if self.completed else "换微信"

        def get_by_text(self, label, exact=True):
            if label == "换微信":
                return FakeControl(label, lambda: None)
            return EmptyControl()

    class FakePage:
        def __init__(self):
            self.conversation = FakeConversation()

        def get_by_text(self, label, exact=True):
            if label == "同意":
                return FakeControl(label, lambda: None)
            if label == "确定":
                return FakeControl(label, lambda: setattr(self.conversation, "completed", True))
            return EmptyControl()

        def wait_for_timeout(self, timeout):
            return None

    page = FakePage()
    target = BrowserReplyTarget(friend_id=123, friend_source=0, encrypt_uid="enc-uid")
    with (
        patch("boss_cli.browser_reply._prepare_chat_page"),
        patch(
            "boss_cli.browser_reply._select_target_conversation",
            return_value=page.conversation,
        ),
    ):
        method, verification = _request_wechat_from_page(page, target, timeout_ms=1000)

    assert calls == ["换微信", "同意", "确定"]
    assert method == "dom.exchange-wechat"
    assert verification == {"status": "matched", "matched": True, "indicator": "双方已交换微信"}
