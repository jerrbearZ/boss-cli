"""Tests for dashboard workflow services."""

from __future__ import annotations

import http.client
import json
import threading
from http.server import ThreadingHTTPServer

from click.testing import CliRunner

from boss_cli.auth import Credential
from boss_cli.browser_reply import BrowserReplyError, BrowserReplyResult, BrowserWechatResult
from boss_cli.cli import cli
from boss_cli.dashboard.server import DashboardRuntime, make_handler
from boss_cli.workflow import init_db
from boss_cli.workflow.dashboard_service import DashboardService
from boss_cli.workflow.poller import sync_inbox
from boss_cli.workflow.sender import send_queued_actions


class FakeBossClient:
    def get_boss_friend_list(self, label_id=0, enc_job_id="", page=1):
        return {"result": [{"friendId": 101}]}

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
                    "name": "张三",
                    "jobName": "销售代理",
                }
            ]
        }

    def get_boss_last_messages(self, friend_ids):
        return [
            {
                "uid": 501,
                "lastMsgInfo": {
                    "showText": "我对岗位感兴趣",
                    "lastTime": "2026-07-12T10:00:00Z",
                    "msgId": "msg-1",
                },
            }
        ]


def test_sync_inbox_persists_candidates_and_messages(tmp_path):
    store = init_db(tmp_path / "workflow.db")
    try:
        result = sync_inbox(store, FakeBossClient(), Credential({"wt2": "account"}), limit=10)

        assert result["candidates_upserted"] == 1
        assert result["messages_upserted"] == 1
        assert store.row_count("accounts") == 1
        assert store.row_count("candidates") == 1
        assert store.row_count("messages") == 1
        assert store.row_count("workflow_runs") == 1
    finally:
        store.close()


def test_dashboard_command_help_registered():
    result = CliRunner().invoke(cli, ["dashboard", "--help"])

    assert result.exit_code == 0
    assert "--port" in result.output
    assert "--db" in result.output


def test_dashboard_service_template_and_enqueue(tmp_path):
    store = init_db(tmp_path / "workflow.db")
    try:
        sync_inbox(store, FakeBossClient(), Credential({"wt2": "account"}), limit=10)
        service = DashboardService(store)
        candidate_id = service.candidates()[0]["id"]
        template = service.create_template(name="reply", version="v1", body="您好，可以详细沟通", approved=True)

        result = service.enqueue(candidate_ids=[candidate_id], template_id=template["id"])
        queue = service.queue()

        assert result["selected"] == 1
        assert result["queued"] == 1
        assert queue[0]["status"] == "queued"
        assert queue[0]["template_name"] == "reply"
    finally:
        store.close()


def test_dashboard_template_versions_are_immutable(tmp_path):
    store = init_db(tmp_path / "workflow.db")
    try:
        service = DashboardService(store)
        service.create_template(name="reply", version="v1", body="First", approved=False)

        try:
            service.create_template(name="reply", version="v1", body="Edited", approved=False)
        except ValueError as exc:
            assert "immutable" in str(exc)
        else:
            raise AssertionError("expected duplicate version rejection")
    finally:
        store.close()


def test_dashboard_plans_message_then_wechat_idempotently(tmp_path):
    store = init_db(tmp_path / "workflow.db")
    try:
        sync_inbox(store, FakeBossClient(), Credential({"wt2": "account"}), limit=10)
        service = DashboardService(store)
        candidate_id = service.candidates()[0]["id"]
        template = service.create_template(name="reply", version="v1", body="您好，可以详细沟通", approved=True)

        first = service.enqueue(
            candidate_ids=[candidate_id],
            template_id=template["id"],
            send_message=True,
            request_wechat=True,
        )
        second = service.enqueue(
            candidate_ids=[candidate_id],
            template_id=template["id"],
            send_message=True,
            request_wechat=True,
        )
        actions = store.conn.execute("SELECT * FROM outbound_actions ORDER BY id").fetchall()

        assert first["queued"] == 2
        assert first["message_actions"] == 1
        assert first["wechat_actions"] == 1
        assert second["already_planned"] == 2
        assert len(actions) == 2
        assert actions[0]["action_type"] == "send_message"
        assert actions[0]["template_id"] == template["id"]
        assert actions[1]["action_type"] == "exchange_wechat"
        assert actions[1]["template_id"] is None
        assert actions[1]["depends_on_action_id"] == actions[0]["id"]
    finally:
        store.close()


def test_dashboard_plans_wechat_only_without_template(tmp_path):
    store = init_db(tmp_path / "workflow.db")
    try:
        sync_inbox(store, FakeBossClient(), Credential({"wt2": "account"}), limit=10)
        service = DashboardService(store)
        candidate_id = service.candidates()[0]["id"]

        result = service.enqueue(
            candidate_ids=[candidate_id],
            template_id=None,
            send_message=False,
            request_wechat=True,
        )
        action = service.queue()[0]

        assert result["queued"] == 1
        assert action["action_type"] == "exchange_wechat"
        assert action["template_id"] is None
        assert action["template_name"] is None
    finally:
        store.close()


def test_dashboard_candidates_are_scoped_to_active_account(tmp_path):
    store = init_db(tmp_path / "workflow.db")
    try:
        first_account = store.upsert_account(account_hash="first")
        second_account = store.upsert_account(account_hash="second")
        store.upsert_candidate(account_id=first_account, friend_id=101, name_redacted="A*")
        store.upsert_candidate(account_id=second_account, friend_id=202, name_redacted="B*")
        store.set_setting("active_account_id", second_account)

        service = DashboardService(store)
        candidates = service.candidates()
        health = service.health()

        assert [item["friend_id"] for item in candidates] == [202]
        assert health["active_account_id"] == second_account
        assert health["candidate_count"] == 1
    finally:
        store.close()


def test_dashboard_enqueue_requires_active_account(tmp_path):
    store = init_db(tmp_path / "workflow.db")
    try:
        service = DashboardService(store)
        try:
            service.enqueue(candidate_ids=[1], template_id=None, send_message=False, request_wechat=True)
        except ValueError as exc:
            assert "Sync the inbox" in str(exc)
        else:
            raise AssertionError("expected active-account validation")
    finally:
        store.close()


def test_sender_marks_fake_send_verified(tmp_path):
    store = init_db(tmp_path / "workflow.db")
    try:
        sync_inbox(store, FakeBossClient(), Credential({"wt2": "account"}), limit=10)
        service = DashboardService(store)
        candidate_id = service.candidates()[0]["id"]
        template = service.create_template(name="reply", version="v1", body="您好，可以详细沟通", approved=True)
        service.enqueue(candidate_ids=[candidate_id], template_id=template["id"])

        def fake_send(credential, target, body):
            return BrowserReplyResult(
                ok=True,
                friend_id=target.friend_id,
                sent=True,
                verified=True,
                engine="fake",
                method="fake",
                target=target.safe_dict(),
                verification={"matched": True},
            )

        result = send_queued_actions(
            store,
            Credential({"wt2": "account"}),
            max_actions=1,
            send_func=fake_send,
            message_preflight_func=lambda credential, target, body: False,
            delay_seconds=0,
        )

        assert result["verified"] == 1
        assert store.queue_summary()["verified"] == 1
        assert store.row_count("messages") == 2
    finally:
        store.close()


def test_sender_runs_message_then_wechat_in_dependency_order(tmp_path):
    store = init_db(tmp_path / "workflow.db")
    calls = []
    try:
        sync_inbox(store, FakeBossClient(), Credential({"wt2": "account"}), limit=10)
        service = DashboardService(store)
        candidate_id = service.candidates()[0]["id"]
        template = service.create_template(name="reply", version="v1", body="您好，可以详细沟通", approved=True)
        service.enqueue(
            candidate_ids=[candidate_id],
            template_id=template["id"],
            request_wechat=True,
        )

        def fake_message(credential, target, body):
            calls.append(("message", target.friend_id))
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

        def fake_wechat(credential, target):
            calls.append(("wechat", target.friend_id))
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

        result = send_queued_actions(
            store,
            Credential({"wt2": "account"}),
            max_actions=2,
            send_func=fake_message,
            wechat_send_func=fake_wechat,
            message_preflight_func=lambda credential, target, body: False,
            delay_seconds=0,
        )

        assert calls == [("message", 501), ("wechat", 501)]
        assert result["verified"] == 2
        assert result["messages_verified"] == 1
        assert result["wechat_verified"] == 1
        assert store.queue_summary()["verified"] == 2
        assert store.conn.execute(
            "SELECT COUNT(*) FROM messages WHERE content_kind='contact_request'"
        ).fetchone()[0] == 1
    finally:
        store.close()


def test_sender_cancels_wechat_when_message_fails_terminally(tmp_path):
    store = init_db(tmp_path / "workflow.db")
    try:
        sync_inbox(store, FakeBossClient(), Credential({"wt2": "account"}), limit=10)
        service = DashboardService(store)
        candidate_id = service.candidates()[0]["id"]
        template = service.create_template(name="reply", version="v1", body="您好，可以详细沟通", approved=True)
        service.enqueue(
            candidate_ids=[candidate_id],
            template_id=template["id"],
            request_wechat=True,
        )

        def fail_message(credential, target, body):
            raise BrowserReplyError("wrong target", code="browser_target_not_verified")

        result = send_queued_actions(
            store,
            Credential({"wt2": "account"}),
            max_actions=2,
            send_func=fail_message,
            message_preflight_func=lambda credential, target, body: False,
            delay_seconds=0,
        )
        statuses = [row["status"] for row in store.conn.execute("SELECT status FROM outbound_actions ORDER BY id")]

        assert result["failed_terminal"] == 1
        assert result["cancelled_dependents"] == 1
        assert statuses == ["failed_terminal", "cancelled"]
    finally:
        store.close()


def test_sender_keeps_dependency_queued_when_browser_backend_is_missing(tmp_path):
    store = init_db(tmp_path / "workflow.db")
    try:
        sync_inbox(store, FakeBossClient(), Credential({"wt2": "account"}), limit=10)
        service = DashboardService(store)
        candidate_id = service.candidates()[0]["id"]
        template = service.create_template(name="reply", version="v1", body="您好，可以详细沟通", approved=True)
        service.enqueue(candidate_ids=[candidate_id], template_id=template["id"], request_wechat=True)

        def fail_message(credential, target, body):
            raise BrowserReplyError("browser extra missing", code="browser_backend_missing")

        result = send_queued_actions(
            store,
            Credential({"wt2": "account"}),
            max_actions=2,
            send_func=fail_message,
            message_preflight_func=lambda credential, target, body: False,
            delay_seconds=0,
        )
        statuses = [row["status"] for row in store.conn.execute("SELECT status FROM outbound_actions ORDER BY id")]

        assert result["failed_retryable"] == 1
        assert result["cancelled_dependents"] == 0
        assert statuses == ["failed_retryable", "queued"]
    finally:
        store.close()


def test_sender_live_preflight_skips_duplicate_without_clicking(tmp_path):
    store = init_db(tmp_path / "workflow.db")
    try:
        sync_inbox(store, FakeBossClient(), Credential({"wt2": "account"}), limit=10)
        service = DashboardService(store)
        candidate_id = service.candidates()[0]["id"]
        template = service.create_template(name="reply", version="v1", body="already sent", approved=True)
        service.enqueue(candidate_ids=[candidate_id], template_id=template["id"])

        def should_not_send(credential, target, body):
            raise AssertionError("browser sender should not run")

        result = send_queued_actions(
            store,
            Credential({"wt2": "account"}),
            max_actions=1,
            send_func=should_not_send,
            message_preflight_func=lambda credential, target, body: True,
            delay_seconds=0,
        )

        assert result["skipped_duplicate"] == 1
        assert store.queue_summary()["skipped_duplicate"] == 1
    finally:
        store.close()


def test_dashboard_http_health_endpoint(tmp_path):
    db_path = tmp_path / "workflow.db"
    init_db(db_path).close()
    runtime = DashboardRuntime(db_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(runtime))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port)
        conn.request("GET", "/api/health")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))

        assert response.status == 200
        assert payload["ok"] is True
        assert payload["data"]["schema_version"] == 5
        assert payload["data"]["queue"]["total"] == 0
    finally:
        server.shutdown()
        server.server_close()


def test_dashboard_http_has_no_manual_enqueue_endpoint(tmp_path):
    db_path = tmp_path / "workflow.db"
    init_db(db_path).close()
    runtime = DashboardRuntime(db_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(runtime))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        body = json.dumps({"candidate_ids": [1], "send_message": False, "request_wechat": True})
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port)
        conn.request("POST", "/api/enqueue", body=body, headers={"Content-Type": "application/json"})
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))

        assert response.status == 404
        assert payload["ok"] is False
        assert payload["error"]["message"] == "Not found"
    finally:
        server.shutdown()
        server.server_close()
