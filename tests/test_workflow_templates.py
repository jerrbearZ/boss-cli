"""Tests for the curated continuous-automation template catalog."""

from __future__ import annotations

import json

from click.testing import CliRunner

from boss_cli.cli import cli
from boss_cli.workflow import init_db
from boss_cli.workflow.templates import AUTOMOTIVE_WECHAT_TEMPLATES, CATALOG_VERSION


def test_install_templates_approves_catalog_and_retires_legacy(tmp_path):
    db_path = tmp_path / "workflow.db"
    with init_db(db_path) as store:
        legacy_id = store.upsert_template(
            name="legacy",
            version="v1",
            body="legacy text",
            approved=True,
            active=True,
        )

    result = CliRunner().invoke(
        cli,
        ["workflow", "install-templates", "--db", str(db_path), "--retire-existing", "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)["data"]
    assert payload["catalog_version"] == CATALOG_VERSION
    assert payload["retired_template_ids"] == [legacy_id]

    with init_db(db_path) as store:
        active = store.list_active_templates()
        assert len(active) == len(AUTOMOTIVE_WECHAT_TEMPLATES)
        assert {row["body"] for row in active} == {
            template.body for template in AUTOMOTIVE_WECHAT_TEMPLATES
        }
        assert {row["selection_guidance"] for row in active} == {
            template.selection_guidance for template in AUTOMOTIVE_WECHAT_TEMPLATES
        }
        assert store.get_template(legacy_id)["retired_at"] is not None


def test_install_templates_is_idempotent(tmp_path):
    db_path = tmp_path / "workflow.db"
    args = ["workflow", "install-templates", "--db", str(db_path), "--json"]

    first = CliRunner().invoke(cli, args)
    second = CliRunner().invoke(cli, args)

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert all(item["status"] == "existing" for item in json.loads(second.output)["data"]["templates"])
    with init_db(db_path) as store:
        assert len(store.list_active_templates()) == len(AUTOMOTIVE_WECHAT_TEMPLATES)
