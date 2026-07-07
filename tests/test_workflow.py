"""Tests for Boss-only workflow dry-run helpers."""

from __future__ import annotations

import json

from click.testing import CliRunner

from boss_cli.cli import cli
from boss_cli.commands.workflow import _classify_candidate, _run_boss_dry_run

runner = CliRunner()


def test_workflow_help_registered():
    result = runner.invoke(cli, ["workflow", "--help"])
    assert result.exit_code == 0
    assert "dry-run" in result.output


def test_workflow_dry_run_help():
    result = runner.invoke(cli, ["workflow", "dry-run", "--help"])
    assert result.exit_code == 0
    assert "--rules-file" in result.output
    assert "--allow-browser-auth" in result.output
    assert "--fetch-resume" in result.output


def test_classify_candidate_matched():
    rules = {
        "required_keywords": ["商务"],
        "preferred_keywords": ["效果商务"],
        "reject_keywords": [],
        "min_years_experience": 2,
    }
    decision = _classify_candidate("候选人有 3 年效果商务经验，熟悉广告投放。", rules)
    assert decision["status"] == "matched"
    assert "效果商务" in decision["matched_preferred_keywords"]


def test_classify_candidate_rejected_by_keyword():
    rules = {
        "required_keywords": [],
        "preferred_keywords": ["商务"],
        "reject_keywords": ["不考虑"],
        "min_years_experience": None,
    }
    decision = _classify_candidate("候选人明确表示不考虑商务岗位。", rules)
    assert decision["status"] == "rejected"
    assert "不考虑" in decision["matched_reject_keywords"]


def test_run_dry_run_does_not_send_or_fetch_resume_by_default():
    class FakeClient:
        def __init__(self):
            self.resume_calls = 0
            self.send_calls = 0

        def get_boss_friend_list(self, label_id=0, enc_job_id="", page=1):
            return {"result": [{"friendId": 101}]}

        def get_boss_friend_details(self, friend_ids):
            return {
                "friendList": [
                    {
                        "friendId": 101,
                        "uid": 501,
                        "name": "测试候选人",
                        "jobName": "效果商务",
                        "encryptUid": "enc-geek",
                        "encryptJobId": "enc-job",
                    }
                ]
            }

        def get_boss_last_messages(self, friend_ids):
            return [{"uid": 501, "lastMsgInfo": {"showText": "我有 4 年效果商务经验"}}]

        def get_boss_chat_history(self, gid, count=20):
            return {"messages": [{"body": {"text": "做过效果商务和广告投放"}}]}

        def get_boss_view_geek(self, **kwargs):
            self.resume_calls += 1
            return {}

        def boss_send_message(self, *args, **kwargs):
            self.send_calls += 1
            return {}

    client = FakeClient()
    result = _run_boss_dry_run(
        client,
        rules={
            "required_keywords": [],
            "preferred_keywords": ["效果商务"],
            "reject_keywords": [],
            "min_years_experience": None,
            "reply_template": "请添加微信。",
        },
        enc_job_id="",
        label_id=0,
        page=1,
        limit=5,
        include_chat=True,
        chat_count=20,
        fetch_resume=False,
    )

    assert result["sent_messages"] == 0
    assert result["summary"]["matched"] == 1
    assert result["candidates"][0]["would_send_message"] is False
    assert client.resume_calls == 0
    assert client.send_calls == 0


def test_rules_file_shape_example_is_valid():
    with open("docs/recruiting-workflow/examples/boss-dry-run-rules.json", encoding="utf-8") as fh:
        data = json.load(fh)
    assert isinstance(data["preferred_keywords"], list)
    assert data["reply_template"]
