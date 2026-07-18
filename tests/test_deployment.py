"""Deployment configuration and log redaction tests."""

import json
import os
import stat

import pytest
from click.testing import CliRunner

from boss_cli.cli import cli
from boss_cli.deployment import DeploymentConfig, DeploymentConfigError, load_deployment_config, set_live_mode, write_deployment_config
from boss_cli.logging_utils import redact_log_message


def test_install_config_is_dry_and_matches_cross_platform_blueprint(tmp_path):
    path = write_deployment_config(DeploymentConfig(live=False), tmp_path / "deployment.json")
    value = json.loads(path.read_text(encoding="utf-8"))

    assert value == {
        "schema_version": 1,
        "live": False,
        "poll_interval_seconds": 30,
        "error_backoff_seconds": 120,
        "candidate_limit": 20,
        "max_actions_per_cycle": 10,
        "action_delay_seconds": 60,
        "request_wechat": True,
        "model": "qwen-plus",
        "dashboard_host": "127.0.0.1",
        "dashboard_port": 8765,
    }
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_deployment_rejects_remote_dashboard_and_unknown_keys(tmp_path):
    path = tmp_path / "deployment.json"
    value = DeploymentConfig().to_dict()
    value["dashboard_host"] = "0.0.0.0"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(DeploymentConfigError, match="127.0.0.1"):
        load_deployment_config(path)

    value = DeploymentConfig().to_dict()
    value["secret"] = "must-not-be-accepted"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(DeploymentConfigError, match="Unknown"):
        load_deployment_config(path)


def test_live_mode_requires_separate_explicit_update(tmp_path):
    path = write_deployment_config(DeploymentConfig(), tmp_path / "deployment.json")
    updated = set_live_mode(path, live=True)
    assert updated.live is True
    assert load_deployment_config(path).live is True


def test_deployment_cli_init_validate_and_enable_live(tmp_path):
    path = tmp_path / "deployment.json"
    runner = CliRunner()
    initialized = runner.invoke(cli, ["deployment", "init", "--config", str(path), "--json"])
    enabled = runner.invoke(cli, ["deployment", "enable-live", "--config", str(path), "--yes"])
    validated = runner.invoke(cli, ["deployment", "validate", "--config", str(path), "--json"])

    assert initialized.exit_code == 0
    assert enabled.exit_code == 0
    assert validated.exit_code == 0
    assert json.loads(validated.output)["data"]["live"] is True


def test_log_redaction_removes_keys_cookies_and_contact_values():
    message = (
        "Authorization: Bearer secret-token DASHSCOPE_API_KEY=secret-key "
        "BOSS_COOKIES=wt2=secret; wt2=cookie 13812345678 candidate@example.com"
    )
    redacted = redact_log_message(message)
    assert "secret-token" not in redacted
    assert "secret-key" not in redacted
    assert "13812345678" not in redacted
    assert "candidate@example.com" not in redacted
