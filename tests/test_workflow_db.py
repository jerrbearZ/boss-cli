"""Tests for the production workflow SQLite store."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from click.testing import CliRunner

from boss_cli.cli import cli
from boss_cli.workflow import init_db
from boss_cli.workflow.redaction import hash_name, outbound_idempotency_key, redact_name, sha256_text


def test_init_db_creates_expected_tables(tmp_path):
    store = init_db(tmp_path / "workflow.db")
    try:
        tables = set(store.table_names())
        schema_version = store.current_schema_version()
    finally:
        store.close()

    assert schema_version == 4
    assert {
        "accounts",
        "jobs",
        "candidates",
        "messages",
        "candidate_snapshots",
        "rulesets",
        "decisions",
        "message_templates",
        "outbound_actions",
        "action_attempts",
        "events",
        "operator_selections",
        "rate_limit_buckets",
        "schema_migrations",
        "workflow_runs",
        "workflow_settings",
        "sync_checkpoints",
        "sync_run_errors",
    }.issubset(tables)


def test_account_candidate_and_message_upserts_are_idempotent(tmp_path):
    store = init_db(tmp_path / "workflow.db")
    try:
        account_id = store.upsert_account(account_hash=sha256_text("boss-account"))
        same_account_id = store.upsert_account(account_hash=sha256_text("boss-account"), last_auth_ok_at="2026-07-12T00:00:00Z")
        job_id = store.upsert_job(account_id=account_id, enc_job_id="job-1", job_name="Sales")

        candidate_id = store.upsert_candidate(
            account_id=account_id,
            friend_id=1001,
            friend_source=0,
            uid=5001,
            job_id=job_id,
            encrypt_job_id="job-1",
            job_name="Sales",
            name_redacted=redact_name("张三"),
            name_hash=hash_name("张三"),
            last_message_preview="联系我 13812345678",
            last_message_fingerprint="last-1",
        )
        same_candidate_id = store.upsert_candidate(
            account_id=account_id,
            friend_id=1001,
            friend_source=0,
            uid=5002,
            name_redacted=redact_name("张三"),
            name_hash=hash_name("张三"),
            last_message_fingerprint="last-2",
        )

        message_id = store.upsert_message(
            candidate_id=candidate_id,
            fingerprint="msg-fp-1",
            direction="inbound",
            boss_msg_id="boss-msg-1",
            text_hash=sha256_text("hello"),
            text_redacted="我的微信 wxCandidate_123",
        )
        same_message_id = store.upsert_message(
            candidate_id=candidate_id,
            fingerprint="msg-fp-1",
            direction="inbound",
            text_hash=sha256_text("hello again"),
            text_redacted="我的微信 wxCandidate_123",
        )

        candidate = store.get_candidate(candidate_id)

        assert account_id == same_account_id
        assert candidate_id == same_candidate_id
        assert message_id == same_message_id
        assert store.row_count("accounts") == 1
        assert store.row_count("candidates") == 1
        assert store.row_count("messages") == 1
        assert candidate is not None
        assert candidate["uid"] == 5002
        assert "13812345678" not in candidate["last_message_preview"]
    finally:
        store.close()


def test_candidate_sync_upsert_preserves_operator_owned_state(tmp_path):
    store = init_db(tmp_path / "workflow.db")
    try:
        account_id = store.upsert_account(account_hash="account")
        candidate_id = store.upsert_candidate(
            account_id=account_id,
            friend_id=1001,
            do_not_contact=True,
            current_stage="manual_review",
        )
        store.upsert_candidate(
            account_id=account_id,
            friend_id=1001,
            do_not_contact=False,
            current_stage="seen",
            last_seen_run_id="sync-run",
        )

        candidate = store.get_candidate(candidate_id)

        assert candidate is not None
        assert candidate["do_not_contact"] == 1
        assert candidate["current_stage"] == "manual_review"
        assert candidate["last_seen_run_id"] == "sync-run"
    finally:
        store.close()


def test_candidate_sync_does_not_regress_activity_or_erase_identifiers(tmp_path):
    store = init_db(tmp_path / "workflow.db")
    try:
        account_id = store.upsert_account(account_hash="account")
        candidate_id = store.upsert_candidate(
            account_id=account_id,
            friend_id=1001,
            encrypt_uid="stable-encrypt-id",
            job_name="Sales",
            last_seen_at="2026-07-14T10:00:00Z",
        )
        store.upsert_candidate(
            account_id=account_id,
            friend_id=1001,
            encrypt_uid="",
            job_name="",
            last_seen_at="2026-07-13T10:00:00Z",
        )

        candidate = store.get_candidate(candidate_id)

        assert candidate is not None
        assert candidate["encrypt_uid"] == "stable-encrypt-id"
        assert candidate["job_name"] == "Sales"
        assert candidate["last_seen_at"] == "2026-07-14T10:00:00Z"
    finally:
        store.close()


def test_stable_account_identity_promotes_cookie_account(tmp_path):
    store = init_db(tmp_path / "workflow.db")
    try:
        fallback_id = store.resolve_account(
            stable_hash=None,
            fallback_hash="cookie-hash",
            identity_source="credential",
        )
        stable_id = store.resolve_account(
            stable_hash="recruiter-hash",
            fallback_hash="cookie-hash",
            identity_source="message_participants",
        )

        accounts = store.list_accounts()

        assert stable_id == fallback_id
        assert len(accounts) == 1
        assert accounts[0]["account_hash"] == "recruiter-hash"
        assert accounts[0]["external_id_hash"] == "recruiter-hash"
    finally:
        store.close()


def test_sync_checkpoint_round_trip(tmp_path):
    store = init_db(tmp_path / "workflow.db")
    try:
        account_id = store.upsert_account(account_hash="account")
        store.set_sync_checkpoint(
            account_id=account_id,
            stream="inbox",
            filter_hash="all",
            cursor={"next_page": 3},
            completed=False,
        )

        checkpoint = store.get_sync_checkpoint(
            account_id=account_id,
            stream="inbox",
            filter_hash="all",
        )

        assert checkpoint is not None
        assert checkpoint["cursor"] == {"next_page": 3}
        assert checkpoint["completed"] is False
    finally:
        store.close()


def test_v2_database_migrates_in_place(tmp_path):
    db_path = tmp_path / "workflow.db"
    schema_path = Path(__file__).parents[1] / "boss_cli" / "workflow" / "schema.sql"
    conn = sqlite3.connect(db_path)
    conn.executescript(schema_path.read_text(encoding="utf-8"))
    conn.execute(
        """
        INSERT INTO accounts(
          platform, account_hash, status, created_at, updated_at
        ) VALUES ('boss', 'existing', 'active', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')
        """
    )
    conn.commit()
    conn.close()

    store = init_db(db_path)
    try:
        account = store.list_accounts()[0]

        assert store.current_schema_version() == 4
        assert account["account_hash"] == "existing"
        assert account["identity_source"] == "credential"
        assert "sync_checkpoints" in store.table_names()
    finally:
        store.close()


def test_v3_actions_migrate_to_typed_nullable_actions(tmp_path):
    db_path = tmp_path / "workflow.db"
    root = Path(__file__).parents[1] / "boss_cli" / "workflow"
    conn = sqlite3.connect(db_path)
    conn.executescript((root / "schema.sql").read_text(encoding="utf-8"))
    conn.executescript((root / "migrations" / "0003_incremental_reader.sql").read_text(encoding="utf-8"))
    conn.execute(
        """
        INSERT INTO accounts(platform, account_hash, status, identity_source, created_at, updated_at)
        VALUES ('boss', 'account', 'active', 'credential', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')
        """
    )
    conn.execute(
        """
        INSERT INTO candidates(account_id, friend_id, first_seen_at, last_seen_at, created_at, updated_at)
        VALUES (1, 1001, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z',
                '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')
        """
    )
    conn.execute(
        """
        INSERT INTO message_templates(name, version, body, body_hash, created_at, updated_at)
        VALUES ('reply', 'v1', 'hello', 'hash', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')
        """
    )
    conn.execute(
        """
        INSERT INTO outbound_actions(
          candidate_id, action_type, template_id, idempotency_key, created_at, updated_at
        ) VALUES (1, 'send_template', 1, 'legacy-key', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')
        """
    )
    conn.commit()
    conn.close()

    store = init_db(db_path)
    try:
        action = store.get_action(1)
        columns = {row["name"]: row for row in store.conn.execute("PRAGMA table_info(outbound_actions)")}

        assert store.current_schema_version() == 4
        assert action is not None
        assert action["action_type"] == "send_message"
        assert "depends_on_action_id" in columns
        assert columns["template_id"]["notnull"] == 0
        assert store.conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        store.close()


def test_store_transaction_rolls_back_batch(tmp_path):
    store = init_db(tmp_path / "workflow.db")
    try:
        try:
            with store.transaction():
                account_id = store.upsert_account(account_hash="account")
                store.upsert_candidate(account_id=account_id, friend_id=1001)
                raise RuntimeError("rollback")
        except RuntimeError:
            pass

        assert store.row_count("accounts") == 0
        assert store.row_count("candidates") == 0
    finally:
        store.close()


def test_decision_and_outbound_action_idempotency(tmp_path):
    store = init_db(tmp_path / "workflow.db")
    try:
        account_id = store.upsert_account(account_hash="account")
        candidate_id = store.upsert_candidate(account_id=account_id, friend_id=1001)
        ruleset_id = store.upsert_ruleset(name="default", version="v1", config={"required_keywords": []}, approved=True, active=True)
        template_id = store.upsert_template(name="first_wechat_exchange", version="v1", body="您好，可以交换微信详细沟通", approved=True)

        decision_id = store.record_decision(
            candidate_id=candidate_id,
            ruleset_id=ruleset_id,
            input_fingerprint="input-1",
            decision="send",
            reason_code="matched",
            evidence={"keywords": ["商务"]},
        )
        same_decision_id = store.record_decision(
            candidate_id=candidate_id,
            ruleset_id=ruleset_id,
            input_fingerprint="input-1",
            decision="send",
            reason_code="matched",
            evidence={"keywords": ["商务"]},
        )

        key = outbound_idempotency_key(
            candidate_id=candidate_id,
            action_type="send_template",
            template_id=template_id,
            template_version="v1",
            trigger_message_fingerprint="input-1",
        )
        action_id = store.enqueue_action(
            candidate_id=candidate_id,
            action_type="send_template",
            template_id=template_id,
            idempotency_key=key,
        )
        same_action_id = store.enqueue_action(
            candidate_id=candidate_id,
            action_type="send_template",
            template_id=template_id,
            idempotency_key=key,
        )

        summary = store.queue_summary()

        assert decision_id == same_decision_id
        assert action_id == same_action_id
        assert store.row_count("decisions") == 1
        assert store.row_count("outbound_actions") == 1
        assert summary["queued"] == 1
        assert summary["total"] == 1
    finally:
        store.close()


def test_action_claim_and_terminal_states_are_persisted(tmp_path):
    store = init_db(tmp_path / "workflow.db")
    try:
        account_id = store.upsert_account(account_hash="account")
        candidate_id = store.upsert_candidate(account_id=account_id, friend_id=1001)
        template_id = store.upsert_template(name="first", version="v1", body="hello")
        verified_action_id = store.enqueue_action(
            candidate_id=candidate_id,
            action_type="send_template",
            template_id=template_id,
            idempotency_key="verified-key",
        )

        claimed = store.claim_next_action(worker_id="worker-1", now="2026-07-12T00:00:00Z")
        assert claimed is not None
        assert claimed["id"] == verified_action_id
        assert claimed["status"] == "locked"
        assert claimed["attempts"] == 1

        store.mark_action_verified(verified_action_id, verified_at="2026-07-12T00:01:00Z")

        failed_action_id = store.enqueue_action(
            candidate_id=candidate_id,
            action_type="send_template",
            template_id=template_id,
            idempotency_key="failed-key",
        )
        claimed_failed = store.claim_next_action(worker_id="worker-1", now="2026-07-12T00:02:00Z")
        assert claimed_failed is not None
        assert claimed_failed["id"] == failed_action_id

        store.mark_action_failed(
            failed_action_id,
            error_code="browser_error",
            error_message="候选人电话 13812345678 页面不可见",
            retryable=False,
        )

        verified = store.get_action(verified_action_id)
        failed = store.get_action(failed_action_id)
        summary = store.queue_summary()

        assert verified is not None
        assert verified["status"] == "verified"
        assert failed is not None
        assert failed["status"] == "failed_terminal"
        assert "13812345678" not in failed["last_error_message_redacted"]
        assert summary["verified"] == 1
        assert summary["failed_terminal"] == 1
    finally:
        store.close()


def test_expired_sending_lease_requires_manual_review(tmp_path):
    store = init_db(tmp_path / "workflow.db")
    try:
        account_id = store.upsert_account(account_hash="account")
        candidate_id = store.upsert_candidate(account_id=account_id, friend_id=1001)
        template_id = store.upsert_template(name="first", version="v1", body="hello")
        action_id = store.enqueue_action(
            candidate_id=candidate_id,
            action_type="send_message",
            template_id=template_id,
            idempotency_key="lease-key",
        )
        claimed = store.claim_next_action(
            worker_id="worker-1",
            lease_seconds=60,
            now="2026-07-12T00:00:00Z",
        )
        assert claimed is not None
        store.mark_action_status(action_id, status="sending")

        sending = store.get_action(action_id)
        assert sending is not None
        assert sending["locked_until"] == "2026-07-12T00:01:00Z"

        next_action = store.claim_next_action(worker_id="worker-2", now="2026-07-12T00:02:00Z")
        uncertain = store.get_action(action_id)

        assert next_action is None
        assert uncertain is not None
        assert uncertain["status"] == "needs_review"
        assert uncertain["last_error_code"] == "expired_sending_lease"
    finally:
        store.close()


def test_append_event_redacts_summary(tmp_path):
    store = init_db(tmp_path / "workflow.db")
    try:
        event_id = store.append_event(event_type="test", summary="联系 13812345678", details={"ok": True})
        row = store.conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()

        assert row is not None
        assert "13812345678" not in row["summary"]
        assert json.loads(row["details_json"]) == {"ok": True}
    finally:
        store.close()


def test_workflow_init_db_command_outputs_json(tmp_path):
    db_path = tmp_path / "workflow.db"
    result = CliRunner().invoke(cli, ["workflow", "init-db", "--db", str(db_path), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["data"]["db"] == str(db_path)
    assert payload["data"]["schema_version"] == 4
    assert "outbound_actions" in payload["data"]["tables"]
    assert db_path.exists()
