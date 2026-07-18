"""Durable daemon control, health, backup, and recovery tests."""

from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import stat
from unittest.mock import patch

from click.testing import CliRunner

from boss_cli.auth import Credential
from boss_cli.cli import cli
from boss_cli.exceptions import SessionExpiredError
from boss_cli.workflow import init_db
from boss_cli.workflow.automation import AutomationConfig, run_daemon
from boss_cli.workflow.health import HealthExitCode, evaluate_health
from boss_cli.workflow.maintenance import create_backup, restore_backup


def test_durable_stop_is_observed_between_cycles(tmp_path):
    with init_db(tmp_path / "workflow.db") as store:

        def cycle():
            store.request_daemon_stop(reason="test")
            return {"status": "completed", "run_id": "run"}

        result = run_daemon(store, cycle, AutomationConfig(poll_interval_seconds=30))
        state = store.get_daemon_state()

        assert result["cycles"] == 1
        assert state["status"] == "stopped"
        assert state["owner_id"] is None


def test_authentication_failure_pauses_and_requires_operator(tmp_path):
    with init_db(tmp_path / "workflow.db") as store:

        def cycle():
            raise SessionExpiredError()

        result = run_daemon(store, cycle, AutomationConfig(), once=True)

        assert result["failures"] == 1
        assert store.is_paused()
        assert store.get_operator_required()["code"] == "not_authenticated"
        assert store.get_daemon_state()["status"] == "authentication_failure"


def test_stale_sending_action_moves_to_review_on_lease_takeover(tmp_path):
    with init_db(tmp_path / "workflow.db") as store:
        account_id = store.upsert_account(account_hash="account")
        candidate_id = store.upsert_candidate(account_id=account_id, friend_id=100)
        template_id = store.upsert_template(name="reply", version="v1", body="hello")
        action_id = store.enqueue_action(
            candidate_id=candidate_id,
            action_type="send_message",
            template_id=template_id,
            idempotency_key="action",
        )
        store.mark_action_status(action_id, status="sending")

        assert store.claim_daemon(daemon_name="primary", owner_id="new", live_mode=True, lease_seconds=900)
        action = store.get_action(action_id)
        store.release_daemon(daemon_name="primary", owner_id="new")

        assert action["status"] == "needs_review"
        assert action["last_error_code"] == "uncertain_after_process_exit"


def test_health_exit_code_precedence(tmp_path):
    with init_db(tmp_path / "workflow.db") as store:
        code, _ = evaluate_health(store)
        assert code == HealthExitCode.STALE_OR_STOPPED

        store.set_setting("paused", True)
        code, _ = evaluate_health(store)
        assert code == HealthExitCode.PAUSED

        store.set_operator_required("not_authenticated")
        code, _ = evaluate_health(store)
        assert code == HealthExitCode.AUTHENTICATION_FAILURE


def test_fresh_running_heartbeat_is_healthy(tmp_path):
    now = datetime.now(timezone.utc)
    with init_db(tmp_path / "workflow.db") as store:
        store.claim_daemon(daemon_name="primary", owner_id="owner", live_mode=False, lease_seconds=900)
        code, data = evaluate_health(store, now=now)
        assert code == HealthExitCode.HEALTHY
        assert data["heartbeat_age_seconds"] is not None
        store.release_daemon(daemon_name="primary", owner_id="owner")


def test_online_backup_and_guarded_restore_preserve_replaced_database(tmp_path):
    db_path = tmp_path / "workflow.db"
    backup_path = tmp_path / "backup.db"
    with init_db(db_path) as store:
        store.set_setting("marker", "before")
    backup = create_backup(db_path, destination=backup_path, kind="manual")
    with init_db(db_path) as store:
        store.set_setting("marker", "after")

    restored = restore_backup(backup_path, db_path)
    with init_db(db_path) as store:
        marker = store.get_setting("marker")

    assert backup["integrity"] == "ok"
    assert restored["integrity"] == "ok"
    assert marker == "before"
    assert restored["preserved_database"] is not None


def test_resume_clears_auth_operator_state_only_after_live_validation(tmp_path):
    db_path = tmp_path / "workflow.db"
    with init_db(db_path) as store:
        store.set_setting("paused", True)
        store.set_operator_required("not_authenticated")

    health = {"authenticated": True}
    with (
        patch("boss_cli.commands.workflow.load_from_env", return_value=None),
        patch("boss_cli.auth.load_credential", return_value=Credential({"wt2": "test"})),
        patch("boss_cli.auth.verify_credential_details", return_value=health),
    ):
        result = CliRunner().invoke(cli, ["workflow", "resume", "--db", str(db_path)])

    assert result.exit_code == 0
    with init_db(db_path) as store:
        assert store.get_operator_required() is None
        assert not store.is_paused()


def test_existing_database_is_backed_up_before_migration_on_every_platform(tmp_path):
    db_path = tmp_path / "workflow.db"
    schema_path = Path(__file__).parents[1] / "boss_cli" / "workflow" / "schema.sql"
    connection = sqlite3.connect(db_path)
    connection.executescript(schema_path.read_text(encoding="utf-8"))
    connection.close()

    with init_db(db_path) as store:
        assert store.current_schema_version() == 6

    backups = list((tmp_path / "backups").glob("workflow-pre-update-schema2-*.db"))
    assert len(backups) == 1
    if os.name != "nt":
        assert stat.S_IMODE(backups[0].stat().st_mode) == 0o600


def test_database_and_state_directory_are_private_on_posix(tmp_path):
    if os.name == "nt":
        return
    db_path = tmp_path / "private" / "workflow.db"
    with init_db(db_path):
        pass

    assert stat.S_IMODE(db_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(db_path.stat().st_mode) == 0o600


def test_force_release_moves_inflight_action_to_review_and_clears_owner(tmp_path):
    with init_db(tmp_path / "workflow.db") as store:
        account_id = store.upsert_account(account_hash="account")
        candidate_id = store.upsert_candidate(account_id=account_id, friend_id=101)
        template_id = store.upsert_template(name="reply", version="v1", body="hello")
        action_id = store.enqueue_action(
            candidate_id=candidate_id,
            action_type="send_message",
            template_id=template_id,
            idempotency_key="force-stop",
        )
        store.mark_action_status(action_id, status="sending")
        store.claim_daemon(daemon_name="primary", owner_id="owner", live_mode=True, lease_seconds=900)
        # Claim recovery already reviewed the first simulated action, so put it
        # back in sending to model termination after ownership was acquired.
        store.mark_action_status(action_id, status="sending")

        assert store.force_release_daemon() == 1
        assert store.get_action(action_id)["status"] == "needs_review"
        assert store.get_action(action_id)["last_error_code"] == "uncertain_after_forced_stop"
        assert store.get_daemon_state()["owner_id"] is None
