"""Tests for dashboard workflow services."""

from __future__ import annotations

import http.client
import json
import threading
from http.server import ThreadingHTTPServer

from click.testing import CliRunner

from boss_cli.auth import Credential
from boss_cli.browser_reply import BrowserReplyResult
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
            delay_seconds=0,
        )

        assert result["verified"] == 1
        assert store.queue_summary()["verified"] == 1
        assert store.row_count("messages") == 2
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
        assert payload["data"]["schema_version"] == 3
        assert payload["data"]["queue"]["total"] == 0
    finally:
        server.shutdown()
        server.server_close()
