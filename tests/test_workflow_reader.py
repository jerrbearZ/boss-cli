"""Integration tests for incremental, read-only Boss synchronization."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from boss_cli.auth import Credential
from boss_cli.cli import cli
from boss_cli.workflow import init_db
from boss_cli.workflow.normalizer import (
    extract_message_time,
    extract_recruiter_user_id_from_user_info,
    message_direction,
    normalize_history_message,
)
from boss_cli.workflow.poller import sync_inbox


class FakeReadClient:
    def __init__(self) -> None:
        self.credential = Credential({"wt2": "fake"})
        self.pages: dict[int, dict[str, Any]] = {
            1: {"result": [{"friendId": 101}, {"friendId": 102}], "hasMore": True},
            2: {"result": [{"friendId": 103}], "hasMore": False},
        }
        self.user_info: dict[str, Any] = {"userInfo": {"userId": 900}}
        self.jobs = [{"jobId": 71, "encryptJobId": "job-1", "jobName": "Sales"}]
        self.details = {
            101: self._detail(101, 501, "张三"),
            102: self._detail(102, 502, "李四"),
            103: self._detail(103, 503, "王五"),
        }
        self.latest = {
            101: self._latest(501, 501, 900, "msg-101", "联系我 13812345678"),
            102: self._latest(502, 900, 502, "msg-102", "您好"),
            103: self._latest(503, 503, 900, "msg-103", "想了解岗位"),
        }
        self.histories = {
            101: [self._history(10101, True, "联系我 13812345678")],
            102: [self._history(10201, False, "您好")],
            103: [self._history(10301, True, "想了解岗位")],
        }
        self.page_calls: list[int] = []
        self.history_calls: list[int] = []
        self.request_count = 0
        self.fail_page: int | None = None
        self.fail_jobs = False

    @property
    def request_stats(self) -> dict[str, int]:
        return {"request_count": self.request_count}

    def get_user_info(self):
        self.request_count += 1
        return self.user_info

    def get_boss_chatted_jobs(self):
        self.request_count += 1
        if self.fail_jobs:
            raise RuntimeError("jobs unavailable")
        return self.jobs

    def get_boss_friend_list(self, label_id=0, enc_job_id="", sort="", page=1):
        self.request_count += 1
        self.page_calls.append(page)
        if self.fail_page == page:
            raise RuntimeError(f"page {page} unavailable")
        return self.pages.get(page, {"result": [], "hasMore": False})

    def get_boss_friend_details(self, friend_ids):
        self.request_count += 1
        return {"friendList": [self.details[item] for item in friend_ids if item in self.details]}

    def get_boss_last_messages(self, friend_ids, src=0):
        self.request_count += 1
        return [self.latest[item] for item in friend_ids if item in self.latest]

    def get_boss_chat_history(self, gid, count=20, max_msg_id=0):
        self.request_count += 1
        self.history_calls.append(gid)
        return {"messages": self.histories.get(gid, []) if max_msg_id == 0 else []}

    def get_boss_chat_geek_info(self, encrypt_geek_id, security_id, job_id):
        self.request_count += 1
        return {
            "geekDetailInfo": {
                "geekBaseInfo": {
                    "degreeCategory": "本科",
                    "workYearDesc": "3年",
                    "expectPosition": "销售",
                    "expectCity": "上海",
                },
                "geekWorkExpList": [{"company": "Example"}],
            }
        }

    @staticmethod
    def _detail(friend_id: int, uid: int, name: str) -> dict[str, Any]:
        return {
            "friendId": friend_id,
            "uid": uid,
            "friendSource": 0,
            "encryptUid": f"enc-{uid}",
            "encryptGeekId": f"geek-{uid}",
            "encryptJobId": "job-1",
            "jobId": 71,
            "securityId": "present",
            "name": name,
            "jobName": "Sales",
        }

    @staticmethod
    def _latest(uid: int, from_id: int, to_id: int, msg_id: str, text: str) -> dict[str, Any]:
        return {
            "uid": uid,
            "lastMsgInfo": {
                "fromId": from_id,
                "toId": to_id,
                "msgId": msg_id,
                "showText": text,
                "msgTime": 1783917600000,
                "type": "text",
            },
        }

    @staticmethod
    def _history(msg_id: int, received: bool, text: str) -> dict[str, Any]:
        return {
            "msgId": msg_id,
            "received": received,
            "body": {"text": text},
            "msgTime": 1783917600000,
            "type": "text",
        }


def test_identity_direction_and_timestamp_normalization():
    assert extract_recruiter_user_id_from_user_info({"userInfo": {"userId": "900"}}) == 900
    assert message_direction(
        {"lastMsgInfo": {"fromId": 501, "toId": 900}},
        candidate_uid=501,
        recruiter_user_id=900,
    ) == "inbound"
    assert extract_message_time({"msgTime": 1783917600000}) == "2026-07-13T04:40:00Z"

    history = normalize_history_message(
        {"received": False, "body": {"text": "hello"}, "msgId": 1},
        candidate_uid=501,
        recruiter_user_id=900,
    )
    assert history["direction"] == "outbound"
    assert history["source"] == "history"


def test_multi_page_sync_is_idempotent_and_history_is_incremental(tmp_path):
    client = FakeReadClient()
    store = init_db(tmp_path / "workflow.db")
    try:
        first = sync_inbox(store, client, Credential({"wt2": "cookie-a"}), limit=10, history_budget=10)
        first_history_calls = list(client.history_calls)
        second = sync_inbox(store, client, Credential({"wt2": "cookie-b"}), limit=10, history_budget=10)

        candidates = store.list_candidates(account_id=first["account_id"])
        inbound = next(item for item in candidates if item["friend_id"] == 101)
        messages = store.conn.execute("SELECT * FROM messages ORDER BY id").fetchall()

        assert first["complete_scan"] is True
        assert first["pages"] == 2
        assert first["candidates_new"] == 3
        assert second["candidates_unchanged"] == 3
        assert client.history_calls == first_history_calls
        assert store.row_count("accounts") == 1
        assert store.row_count("candidates") == 3
        assert len(messages) == 6
        assert inbound["last_inbound_at"] == "2026-07-13T04:40:00Z"
        assert "13812345678" not in inbound["last_message_preview"]
        assert {row["direction"] for row in messages} == {"inbound", "outbound"}
    finally:
        store.close()


def test_history_budget_marks_remaining_conversations_deferred(tmp_path):
    client = FakeReadClient()
    store = init_db(tmp_path / "workflow.db")
    try:
        result = sync_inbox(store, client, Credential({"wt2": "cookie"}), history_budget=1)
        statuses = {
            row["history_sync_status"]
            for row in store.conn.execute("SELECT history_sync_status FROM candidates").fetchall()
        }

        assert result["history_conversations"] == 1
        assert result["history_deferred"] == 2
        assert statuses == {"synced", "deferred"}
    finally:
        store.close()


def test_deferred_history_is_drained_on_later_runs(tmp_path):
    client = FakeReadClient()
    store = init_db(tmp_path / "workflow.db")
    try:
        first = sync_inbox(store, client, Credential({"wt2": "cookie"}), history_budget=1)
        second = sync_inbox(store, client, Credential({"wt2": "cookie"}), history_budget=1)
        statuses = [
            row["history_sync_status"]
            for row in store.conn.execute("SELECT history_sync_status FROM candidates ORDER BY id").fetchall()
        ]

        assert first["history_deferred"] == 2
        assert second["history_conversations"] == 1
        assert statuses.count("synced") == 2
        assert statuses.count("deferred") == 1
    finally:
        store.close()


def test_missing_candidate_detail_is_isolated_and_audited(tmp_path):
    client = FakeReadClient()
    del client.details[102]
    store = init_db(tmp_path / "workflow.db")
    try:
        result = sync_inbox(store, client, Credential({"wt2": "cookie"}), history_mode="none")
        error = store.conn.execute("SELECT * FROM sync_run_errors").fetchone()

        assert result["errors"] == 1
        assert result["candidates_upserted"] == 2
        assert error is not None
        assert error["friend_id"] == 102
        assert error["error_code"] == "ValueError"
    finally:
        store.close()


def test_interrupted_sync_resumes_from_committed_page(tmp_path):
    client = FakeReadClient()
    client.fail_page = 2
    store = init_db(tmp_path / "workflow.db")
    try:
        with pytest.raises(RuntimeError, match="page 2 unavailable"):
            sync_inbox(store, client, Credential({"wt2": "cookie"}), history_mode="none")

        client.fail_page = None
        client.page_calls.clear()
        result = sync_inbox(store, client, Credential({"wt2": "cookie"}), history_mode="none")

        assert result["resumed"] is True
        assert client.page_calls[0] == 2
        assert store.row_count("candidates") == 3
    finally:
        store.close()


def test_complete_full_scan_marks_missing_candidate_inactive(tmp_path):
    client = FakeReadClient()
    store = init_db(tmp_path / "workflow.db")
    try:
        sync_inbox(store, client, Credential({"wt2": "cookie"}), history_mode="none", full_scan=True)
        client.pages = {1: {"result": [{"friendId": 101}], "hasMore": False}}
        result = sync_inbox(store, client, Credential({"wt2": "cookie"}), history_mode="none", full_scan=True)

        inactive = store.conn.execute(
            "SELECT COUNT(*) AS count FROM candidates WHERE inactive_at IS NOT NULL"
        ).fetchone()

        assert result["inactive_candidates"] == 2
        assert inactive["count"] == 2
    finally:
        store.close()


def test_failed_optional_job_read_does_not_deactivate_jobs(tmp_path):
    client = FakeReadClient()
    store = init_db(tmp_path / "workflow.db")
    try:
        sync_inbox(store, client, Credential({"wt2": "cookie"}), history_mode="none", full_scan=True)
        client.fail_jobs = True
        sync_inbox(store, client, Credential({"wt2": "cookie"}), history_mode="none", full_scan=True)

        job = store.conn.execute("SELECT * FROM jobs WHERE enc_job_id='job-1'").fetchone()

        assert job is not None
        assert job["active"] == 1
        assert job["inactive_at"] is None
    finally:
        store.close()


def test_empty_inbox_still_synchronizes_jobs(tmp_path):
    client = FakeReadClient()
    client.pages = {1: {"result": [], "hasMore": False}}
    store = init_db(tmp_path / "workflow.db")
    try:
        result = sync_inbox(store, client, Credential({"wt2": "cookie"}), history_mode="none")

        assert result["complete_scan"] is True
        assert result["jobs_upserted"] == 1
        assert store.row_count("jobs") == 1
    finally:
        store.close()


def test_duplicate_page_checkpoint_restarts_at_first_page(tmp_path):
    client = FakeReadClient()
    repeated = {"result": [{"friendId": 101}]}
    client.pages = {1: repeated, 2: repeated}
    store = init_db(tmp_path / "workflow.db")
    try:
        first = sync_inbox(store, client, Credential({"wt2": "cookie"}), history_mode="none")
        client.page_calls.clear()
        second = sync_inbox(store, client, Credential({"wt2": "cookie"}), history_mode="none")

        assert first["stop_reason"] == "duplicate_page"
        assert second["resumed"] is False
        assert client.page_calls[0] == 1
    finally:
        store.close()


def test_profile_enrichment_is_explicit_and_redacted_snapshot_is_deduped(tmp_path):
    client = FakeReadClient()
    store = init_db(tmp_path / "workflow.db")
    try:
        sync_inbox(
            store,
            client,
            Credential({"wt2": "cookie"}),
            history_mode="none",
            include_profile=True,
        )
        first_count = store.row_count("candidate_snapshots")
        sync_inbox(
            store,
            client,
            Credential({"wt2": "cookie"}),
            history_mode="none",
            include_profile=True,
        )

        snapshot = store.conn.execute("SELECT * FROM candidate_snapshots LIMIT 1").fetchone()

        assert first_count == 3
        assert store.row_count("candidate_snapshots") == 3
        assert snapshot is not None
        assert "Example" not in snapshot["summary_json"]
    finally:
        store.close()


def test_message_participants_keep_account_stable_when_cookie_rotates(tmp_path):
    client = FakeReadClient()
    client.user_info = {}
    store = init_db(tmp_path / "workflow.db")
    try:
        first = sync_inbox(store, client, Credential({"wt2": "cookie-a"}), history_mode="none")
        second = sync_inbox(store, client, Credential({"wt2": "cookie-b"}), history_mode="none")

        assert first["account_id"] == second["account_id"]
        assert store.row_count("accounts") == 1
        assert store.list_accounts()[0]["identity_source"] == "message_participants"
    finally:
        store.close()


def test_workflow_sync_command_uses_incremental_reader(tmp_path):
    client = FakeReadClient()
    db_path = tmp_path / "workflow.db"
    credential = Credential({"wt2": "cookie"})
    runner = CliRunner()

    with (
        patch("boss_cli.commands.workflow._get_workflow_credential", return_value=credential),
        patch(
            "boss_cli.commands._common.run_client_action",
            side_effect=lambda supplied, action: action(client),
        ),
    ):
        result = runner.invoke(
            cli,
            [
                "workflow",
                "sync",
                "--db",
                str(db_path),
                "--history",
                "none",
                "--json",
            ],
        )

    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert payload["ok"] is True
    assert payload["data"]["complete_scan"] is True
    assert payload["data"]["candidates_upserted"] == 3
    assert db_path.exists()
