"""SQLite state store for the production Boss workflow."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .models import ALL_ACTION_STATUSES, QueueSummary
from .redaction import redact_text, sha256_text, stable_json_dumps

SCHEMA_VERSION = 5
SCHEMA_PATH = Path(__file__).with_name("schema.sql")
MIGRATIONS_PATH = Path(__file__).with_name("migrations")


def utc_now() -> str:
    """Return an ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def default_db_path() -> Path:
    """Return the default local workflow database path."""
    env_path = os.environ.get("BOSS_WORKFLOW_DB")
    if env_path:
        return Path(env_path).expanduser()
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")).expanduser()
    return data_home / "boss-cli" / "workflow.db"


def init_db(path: Path | str | None = None) -> "WorkflowStore":
    """Initialize or open the workflow database."""
    db_path = _resolve_db_path(path)
    if db_path != Path(":memory:"):
        db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    _configure_connection(conn)
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    _apply_migrations(conn)
    conn.commit()
    return WorkflowStore(db_path, conn)


def _resolve_db_path(path: Path | str | None) -> Path:
    if path is None:
        return default_db_path()
    return Path(path).expanduser()


def _configure_connection(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        # WAL is unavailable for in-memory databases; foreign keys still matter.
        pass


def _apply_migrations(conn: sqlite3.Connection) -> None:
    """Apply ordered SQL migrations newer than the database schema version."""
    row = conn.execute("SELECT MAX(version) AS version FROM schema_migrations").fetchone()
    current = int(row["version"] or 0)
    if not MIGRATIONS_PATH.exists():
        return
    for path in sorted(MIGRATIONS_PATH.glob("[0-9][0-9][0-9][0-9]_*.sql")):
        version = int(path.name.split("_", 1)[0])
        if version > current:
            conn.executescript(path.read_text(encoding="utf-8"))
            current = version


class WorkflowStore:
    """Small SQLite wrapper for durable workflow state."""

    def __init__(self, path: Path, conn: sqlite3.Connection):
        self.path = path
        self.conn = conn
        self._transaction_depth = 0

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "WorkflowStore":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            if self._transaction_depth == 0:
                self.conn.execute("BEGIN")
            self._transaction_depth += 1
            yield self.conn
        except Exception:
            self._transaction_depth = 0
            self.conn.rollback()
            raise
        else:
            self._transaction_depth -= 1
            if self._transaction_depth == 0:
                self.conn.commit()

    def _commit(self) -> None:
        if self._transaction_depth == 0:
            self.conn.commit()

    def table_names(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        return [str(row["name"]) for row in rows]

    def current_schema_version(self) -> int:
        row = self.conn.execute("SELECT MAX(version) AS version FROM schema_migrations").fetchone()
        return int(row["version"] or 0)

    def row_count(self, table: str) -> int:
        if table not in self.table_names():
            raise ValueError(f"unknown table: {table}")
        row = self.conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
        return int(row["count"])

    def upsert_account(
        self,
        *,
        account_hash: str,
        platform: str = "boss",
        status: str = "active",
        paused_until: str | None = None,
        last_auth_ok_at: str | None = None,
        last_error_code: str | None = None,
        external_id_hash: str | None = None,
        identity_source: str = "credential",
    ) -> int:
        now = utc_now()
        self.conn.execute(
            """
            INSERT INTO accounts (
              platform, account_hash, status, paused_until, last_auth_ok_at,
              last_error_code, external_id_hash, identity_source, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_hash) DO UPDATE SET
              platform=excluded.platform,
              status=excluded.status,
              paused_until=excluded.paused_until,
              last_auth_ok_at=COALESCE(excluded.last_auth_ok_at, accounts.last_auth_ok_at),
              last_error_code=excluded.last_error_code,
              external_id_hash=COALESCE(excluded.external_id_hash, accounts.external_id_hash),
              identity_source=CASE
                WHEN excluded.external_id_hash IS NOT NULL THEN excluded.identity_source
                ELSE accounts.identity_source
              END,
              updated_at=excluded.updated_at
            """,
            (
                platform,
                account_hash,
                status,
                paused_until,
                last_auth_ok_at,
                last_error_code,
                external_id_hash,
                identity_source,
                now,
                now,
            ),
        )
        self._commit()
        return self._id_for("accounts", "account_hash", account_hash)

    def resolve_account(
        self,
        *,
        stable_hash: str | None,
        fallback_hash: str,
        identity_source: str,
        observed_friend_ids: list[int] | None = None,
    ) -> int:
        """Resolve or promote a local account without binding identity to rotating cookies."""
        if stable_hash:
            stable = self.conn.execute(
                "SELECT id FROM accounts WHERE account_hash=? OR external_id_hash=? ORDER BY id LIMIT 1",
                (stable_hash, stable_hash),
            ).fetchone()
            if stable:
                account_id = int(stable["id"])
                self.conn.execute(
                    "UPDATE accounts SET external_id_hash=?, identity_source=?, last_auth_ok_at=?, updated_at=? WHERE id=?",
                    (stable_hash, identity_source, utc_now(), utc_now(), account_id),
                )
                self._commit()
                return account_id

            fallback = self.conn.execute("SELECT id FROM accounts WHERE account_hash=?", (fallback_hash,)).fetchone()
            candidate_account_id = int(fallback["id"]) if fallback else self._account_for_observed_friends(observed_friend_ids or [])
            if candidate_account_id is not None:
                self.conn.execute(
                    """
                    UPDATE accounts
                    SET account_hash=?, external_id_hash=?, identity_source=?, last_auth_ok_at=?, updated_at=?
                    WHERE id=?
                    """,
                    (stable_hash, stable_hash, identity_source, utc_now(), utc_now(), candidate_account_id),
                )
                self._commit()
                return candidate_account_id

            return self.upsert_account(
                account_hash=stable_hash,
                external_id_hash=stable_hash,
                identity_source=identity_source,
                last_auth_ok_at=utc_now(),
            )

        return self.upsert_account(
            account_hash=fallback_hash,
            identity_source="credential",
            last_auth_ok_at=utc_now(),
        )

    def _account_for_observed_friends(self, friend_ids: list[int]) -> int | None:
        if not friend_ids:
            return None
        placeholders = ",".join("?" for _ in friend_ids)
        rows = self.conn.execute(
            f"SELECT DISTINCT account_id FROM candidates WHERE friend_id IN ({placeholders})",
            friend_ids,
        ).fetchall()
        return int(rows[0]["account_id"]) if len(rows) == 1 else None

    def list_accounts(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM accounts ORDER BY updated_at DESC, id DESC").fetchall()
        return [_row_to_dict(row) or {} for row in rows]

    def upsert_job(
        self,
        *,
        account_id: int,
        enc_job_id: str,
        job_name: str | None = None,
        active: bool = True,
        last_seen_at: str | None = None,
        boss_job_id: int | None = None,
        last_seen_run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        now = utc_now()
        seen_at = last_seen_at or now
        self.conn.execute(
            """
            INSERT INTO jobs (
              account_id, enc_job_id, job_name, active, last_seen_at, boss_job_id,
              last_seen_run_id, inactive_at, metadata_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)
            ON CONFLICT(account_id, enc_job_id) DO UPDATE SET
              job_name=COALESCE(excluded.job_name, jobs.job_name),
              active=excluded.active,
              last_seen_at=excluded.last_seen_at,
              boss_job_id=COALESCE(excluded.boss_job_id, jobs.boss_job_id),
              last_seen_run_id=COALESCE(excluded.last_seen_run_id, jobs.last_seen_run_id),
              inactive_at=NULL,
              metadata_json=COALESCE(excluded.metadata_json, jobs.metadata_json),
              updated_at=excluded.updated_at
            """,
            (
                account_id,
                enc_job_id,
                job_name,
                int(active),
                seen_at,
                boss_job_id,
                last_seen_run_id,
                stable_json_dumps(metadata) if metadata else None,
                now,
                now,
            ),
        )
        self._commit()
        row = self.conn.execute(
            "SELECT id FROM jobs WHERE account_id=? AND enc_job_id=?",
            (account_id, enc_job_id),
        ).fetchone()
        return int(row["id"])

    def upsert_candidate(
        self,
        *,
        account_id: int,
        friend_id: int,
        friend_source: int = 0,
        uid: int | None = None,
        encrypt_uid: str | None = None,
        encrypt_geek_id: str | None = None,
        security_id_present: bool = False,
        job_id: int | None = None,
        encrypt_job_id: str | None = None,
        job_name: str | None = None,
        name_redacted: str | None = None,
        name_hash: str | None = None,
        do_not_contact: bool = False,
        current_stage: str = "seen",
        first_seen_at: str | None = None,
        last_seen_at: str | None = None,
        last_inbound_at: str | None = None,
        last_outbound_at: str | None = None,
        last_message_preview: str | None = None,
        last_message_fingerprint: str | None = None,
        last_observed_at: str | None = None,
        last_activity_at: str | None = None,
        last_seen_run_id: str | None = None,
        history_synced_through: str | None = None,
        history_sync_status: str = "pending",
    ) -> int:
        now = utc_now()
        first_seen = first_seen_at or now
        last_seen = last_seen_at or now
        observed_at = last_observed_at or now
        self.conn.execute(
            """
            INSERT INTO candidates (
              account_id, friend_id, uid, friend_source, encrypt_uid, encrypt_geek_id,
              security_id_present, job_id, encrypt_job_id, job_name, name_redacted,
              name_hash, do_not_contact, current_stage, first_seen_at, last_seen_at,
              last_inbound_at, last_outbound_at, last_message_preview,
              last_message_fingerprint, last_observed_at, last_activity_at,
              last_seen_run_id, history_synced_through, history_sync_status,
              inactive_at, created_at, updated_at
            )
            VALUES (
              ?, ?, ?, ?, ?,
              ?, ?, ?, ?, ?,
              ?, ?, ?, ?, ?,
              ?, ?, ?, ?, ?,
              ?, ?, ?, ?, ?,
              NULL, ?, ?
            )
            ON CONFLICT(account_id, friend_id, friend_source) DO UPDATE SET
              uid=COALESCE(excluded.uid, candidates.uid),
              encrypt_uid=COALESCE(NULLIF(excluded.encrypt_uid, ''), candidates.encrypt_uid),
              encrypt_geek_id=COALESCE(NULLIF(excluded.encrypt_geek_id, ''), candidates.encrypt_geek_id),
              security_id_present=excluded.security_id_present,
              job_id=COALESCE(excluded.job_id, candidates.job_id),
              encrypt_job_id=COALESCE(NULLIF(excluded.encrypt_job_id, ''), candidates.encrypt_job_id),
              job_name=COALESCE(NULLIF(excluded.job_name, ''), candidates.job_name),
              name_redacted=COALESCE(NULLIF(excluded.name_redacted, ''), candidates.name_redacted),
              name_hash=COALESCE(NULLIF(excluded.name_hash, ''), candidates.name_hash),
              last_seen_at=CASE
                WHEN candidates.last_seen_at > excluded.last_seen_at THEN candidates.last_seen_at
                ELSE excluded.last_seen_at
              END,
              last_inbound_at=COALESCE(excluded.last_inbound_at, candidates.last_inbound_at),
              last_outbound_at=COALESCE(excluded.last_outbound_at, candidates.last_outbound_at),
              last_message_preview=COALESCE(excluded.last_message_preview, candidates.last_message_preview),
              last_message_fingerprint=COALESCE(excluded.last_message_fingerprint, candidates.last_message_fingerprint),
              last_observed_at=excluded.last_observed_at,
              last_activity_at=COALESCE(excluded.last_activity_at, candidates.last_activity_at),
              last_seen_run_id=COALESCE(excluded.last_seen_run_id, candidates.last_seen_run_id),
              history_synced_through=COALESCE(excluded.history_synced_through, candidates.history_synced_through),
              history_sync_status=CASE
                WHEN excluded.history_sync_status='pending' THEN candidates.history_sync_status
                ELSE excluded.history_sync_status
              END,
              inactive_at=NULL,
              updated_at=excluded.updated_at
            """,
            (
                account_id,
                friend_id,
                uid,
                friend_source,
                encrypt_uid,
                encrypt_geek_id,
                int(security_id_present),
                job_id,
                encrypt_job_id,
                job_name,
                name_redacted,
                name_hash,
                int(do_not_contact),
                current_stage,
                first_seen,
                last_seen,
                last_inbound_at,
                last_outbound_at,
                redact_text(last_message_preview) if last_message_preview else None,
                last_message_fingerprint,
                observed_at,
                last_activity_at,
                last_seen_run_id,
                history_synced_through,
                history_sync_status,
                now,
                now,
            ),
        )
        self._commit()
        row = self.conn.execute(
            "SELECT id FROM candidates WHERE account_id=? AND friend_id=? AND friend_source=?",
            (account_id, friend_id, friend_source),
        ).fetchone()
        return int(row["id"])

    def upsert_message(
        self,
        *,
        candidate_id: int,
        fingerprint: str,
        direction: str,
        boss_msg_id: str | int | None = None,
        msg_type: str | None = None,
        sent_at: str | None = None,
        text_hash: str | None = None,
        text_redacted: str | None = None,
        source: str = "latest",
        content_kind: str = "text",
        payload_hash: str | None = None,
    ) -> int:
        now = utc_now()
        self.conn.execute(
            """
            INSERT INTO messages (
              candidate_id, boss_msg_id, direction, msg_type, sent_at, text_hash,
              text_redacted, fingerprint, source, content_kind, payload_hash,
              created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(candidate_id, fingerprint) DO UPDATE SET
              boss_msg_id=COALESCE(excluded.boss_msg_id, messages.boss_msg_id),
              direction=excluded.direction,
              msg_type=COALESCE(excluded.msg_type, messages.msg_type),
              sent_at=COALESCE(excluded.sent_at, messages.sent_at),
              text_hash=COALESCE(excluded.text_hash, messages.text_hash),
              text_redacted=COALESCE(excluded.text_redacted, messages.text_redacted),
              source=CASE WHEN excluded.source='history' THEN 'history' ELSE messages.source END,
              content_kind=excluded.content_kind,
              payload_hash=COALESCE(excluded.payload_hash, messages.payload_hash),
              updated_at=excluded.updated_at
            """,
            (
                candidate_id,
                str(boss_msg_id) if boss_msg_id is not None else None,
                direction,
                msg_type,
                sent_at,
                text_hash,
                redact_text(text_redacted) if text_redacted else None,
                fingerprint,
                source,
                content_kind,
                payload_hash,
                now,
                now,
            ),
        )
        self._commit()
        row = self.conn.execute(
            "SELECT id FROM messages WHERE candidate_id=? AND fingerprint=?",
            (candidate_id, fingerprint),
        ).fetchone()
        return int(row["id"])

    def has_message_fingerprint(self, candidate_id: int, fingerprint: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM messages WHERE candidate_id=? AND fingerprint=? LIMIT 1",
            (candidate_id, fingerprint),
        ).fetchone()
        return row is not None

    def upsert_candidate_snapshot(
        self,
        *,
        candidate_id: int,
        source: str,
        payload_hash: str,
        summary: dict[str, Any] | None,
        fetched_at: str | None = None,
    ) -> int:
        now = utc_now()
        self.conn.execute(
            """
            INSERT INTO candidate_snapshots(
              candidate_id, source, fetched_at, payload_hash, summary_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(candidate_id, source, payload_hash) DO NOTHING
            """,
            (
                candidate_id,
                source,
                fetched_at or now,
                payload_hash,
                stable_json_dumps(summary or {}),
                now,
            ),
        )
        self._commit()
        row = self.conn.execute(
            "SELECT id FROM candidate_snapshots WHERE candidate_id=? AND source=? AND payload_hash=?",
            (candidate_id, source, payload_hash),
        ).fetchone()
        return int(row["id"])

    def get_candidate_by_external_key(
        self,
        *,
        account_id: int,
        friend_id: int,
        friend_source: int = 0,
    ) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM candidates WHERE account_id=? AND friend_id=? AND friend_source=?",
            (account_id, friend_id, friend_source),
        ).fetchone()
        return _row_to_dict(row)

    def get_candidate_by_friend_id(self, *, account_id: int, friend_id: int) -> dict[str, Any] | None:
        """Return one account-scoped candidate for a BOSS conversation friend id."""
        row = self.conn.execute(
            "SELECT * FROM candidates WHERE account_id=? AND friend_id=? ORDER BY id DESC LIMIT 1",
            (account_id, friend_id),
        ).fetchone()
        return _row_to_dict(row)

    def mark_candidate_history_state(
        self,
        candidate_id: int,
        *,
        status: str,
        synced_through: str | None = None,
    ) -> None:
        self.conn.execute(
            """
            UPDATE candidates
            SET history_sync_status=?,
                history_synced_through=COALESCE(?, history_synced_through),
                updated_at=?
            WHERE id=?
            """,
            (status, synced_through, utc_now(), candidate_id),
        )
        self._commit()

    def refresh_candidate_activity(self, candidate_id: int) -> None:
        row = self.conn.execute(
            """
            SELECT
              MAX(CASE WHEN direction='inbound' THEN sent_at END) AS last_inbound,
              MAX(CASE WHEN direction='outbound' THEN sent_at END) AS last_outbound,
              MAX(sent_at) AS last_activity
            FROM messages
            WHERE candidate_id=?
            """,
            (candidate_id,),
        ).fetchone()
        self.conn.execute(
            """
            UPDATE candidates
            SET last_inbound_at=COALESCE(?, last_inbound_at),
                last_outbound_at=COALESCE(?, last_outbound_at),
                last_activity_at=COALESCE(?, last_activity_at),
                updated_at=?
            WHERE id=?
            """,
            (row["last_inbound"], row["last_outbound"], row["last_activity"], utc_now(), candidate_id),
        )
        self._commit()

    def mark_unseen_candidates_inactive(self, *, account_id: int, run_id: str) -> int:
        now = utc_now()
        cursor = self.conn.execute(
            """
            UPDATE candidates
            SET inactive_at=COALESCE(inactive_at, ?), updated_at=?
            WHERE account_id=? AND (last_seen_run_id IS NULL OR last_seen_run_id<>?)
            """,
            (now, now, account_id, run_id),
        )
        self._commit()
        return int(cursor.rowcount)

    def mark_unseen_jobs_inactive(self, *, account_id: int, run_id: str) -> int:
        now = utc_now()
        cursor = self.conn.execute(
            """
            UPDATE jobs
            SET active=0, inactive_at=COALESCE(inactive_at, ?), updated_at=?
            WHERE account_id=? AND (last_seen_run_id IS NULL OR last_seen_run_id<>?)
            """,
            (now, now, account_id, run_id),
        )
        self._commit()
        return int(cursor.rowcount)

    def upsert_ruleset(
        self,
        *,
        name: str,
        version: str,
        config: dict[str, Any] | str,
        approved: bool = False,
        active: bool = False,
        approved_at: str | None = None,
    ) -> int:
        now = utc_now()
        config_json = config if isinstance(config, str) else stable_json_dumps(config)
        config_hash = sha256_text(config_json)
        self.conn.execute(
            """
            INSERT INTO rulesets (
              name, version, config_json, config_hash, approved, active,
              approved_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name, version) DO UPDATE SET
              config_json=excluded.config_json,
              config_hash=excluded.config_hash,
              approved=excluded.approved,
              active=excluded.active,
              approved_at=excluded.approved_at,
              updated_at=excluded.updated_at
            """,
            (name, version, config_json, config_hash, int(approved), int(active), approved_at, now, now),
        )
        self._commit()
        row = self.conn.execute("SELECT id FROM rulesets WHERE name=? AND version=?", (name, version)).fetchone()
        return int(row["id"])

    def upsert_template(
        self,
        *,
        name: str,
        version: str,
        body: str,
        approved: bool = False,
        active: bool = False,
        approved_by: str | None = None,
        approved_at: str | None = None,
        selection_guidance: str = "",
    ) -> int:
        now = utc_now()
        self.conn.execute(
            """
            INSERT INTO message_templates (
              name, version, body, body_hash, approved, active, approved_by,
              approved_at, selection_guidance, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name, version) DO UPDATE SET
              body=excluded.body,
              body_hash=excluded.body_hash,
              approved=excluded.approved,
              active=excluded.active,
              approved_by=excluded.approved_by,
              approved_at=excluded.approved_at,
              selection_guidance=excluded.selection_guidance,
              updated_at=excluded.updated_at
            """,
            (
                name,
                version,
                body,
                sha256_text(body),
                int(approved),
                int(active),
                approved_by,
                approved_at,
                redact_text(selection_guidance, max_length=500),
                now,
                now,
            ),
        )
        self._commit()
        row = self.conn.execute("SELECT id FROM message_templates WHERE name=? AND version=?", (name, version)).fetchone()
        return int(row["id"])

    def record_decision(
        self,
        *,
        candidate_id: int,
        ruleset_id: int,
        input_fingerprint: str,
        decision: str,
        reason_code: str,
        evidence: dict[str, Any] | str | None = None,
    ) -> int:
        evidence_json = _json_or_none(evidence)
        now = utc_now()
        self.conn.execute(
            """
            INSERT INTO decisions (
              candidate_id, ruleset_id, input_fingerprint, decision, reason_code,
              evidence_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(candidate_id, ruleset_id, input_fingerprint) DO UPDATE SET
              decision=excluded.decision,
              reason_code=excluded.reason_code,
              evidence_json=excluded.evidence_json
            """,
            (candidate_id, ruleset_id, input_fingerprint, decision, reason_code, evidence_json, now),
        )
        self._commit()
        row = self.conn.execute(
            "SELECT id FROM decisions WHERE candidate_id=? AND ruleset_id=? AND input_fingerprint=?",
            (candidate_id, ruleset_id, input_fingerprint),
        ).fetchone()
        return int(row["id"])

    def enqueue_action(
        self,
        *,
        candidate_id: int,
        action_type: str,
        idempotency_key: str,
        template_id: int | None = None,
        depends_on_action_id: int | None = None,
        status: str = "queued",
        priority: int = 100,
        available_at: str | None = None,
        max_attempts: int = 3,
    ) -> int:
        now = utc_now()
        self.conn.execute(
            """
            INSERT INTO outbound_actions (
              candidate_id, action_type, template_id, depends_on_action_id,
              idempotency_key, status,
              priority, available_at, max_attempts, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(idempotency_key) DO NOTHING
            """,
            (
                candidate_id,
                action_type,
                template_id,
                depends_on_action_id,
                idempotency_key,
                status,
                priority,
                available_at,
                max_attempts,
                now,
                now,
            ),
        )
        self._commit()
        row = self.conn.execute(
            "SELECT id FROM outbound_actions WHERE idempotency_key=?",
            (idempotency_key,),
        ).fetchone()
        return int(row["id"])

    def claim_next_action(
        self,
        *,
        worker_id: str,
        lease_seconds: int = 300,
        now: str | None = None,
        account_id: int | None = None,
        candidate_id: int | None = None,
    ) -> dict[str, Any] | None:
        if self._transaction_depth > 0:
            raise RuntimeError("claim_next_action manages its own lease transaction")
        claim_time = now or utc_now()
        locked_until = _add_seconds(claim_time, lease_seconds)
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self.conn.execute(
                """
                UPDATE outbound_actions
                SET status='queued', locked_by=NULL, locked_at=NULL, locked_until=NULL,
                    updated_at=?
                WHERE status='locked' AND locked_until IS NOT NULL AND locked_until < ?
                """,
                (claim_time, claim_time),
            )
            self.conn.execute(
                """
                UPDATE outbound_actions
                SET status='needs_review', locked_by=NULL, locked_at=NULL,
                    locked_until=NULL, last_error_code='expired_sending_lease',
                    last_error_message_redacted='Sender stopped while action outcome was uncertain',
                    updated_at=?
                WHERE status='sending' AND locked_until IS NOT NULL AND locked_until < ?
                """,
                (claim_time, claim_time),
            )
            account_clause = "AND c.account_id=?" if account_id is not None else ""
            candidate_clause = "AND a.candidate_id=?" if candidate_id is not None else ""
            params: list[Any] = [claim_time, claim_time]
            if account_id is not None:
                params.append(account_id)
            if candidate_id is not None:
                params.append(candidate_id)
            row = self.conn.execute(
                f"""
                SELECT a.*
                FROM outbound_actions a
                JOIN candidates c ON c.id = a.candidate_id
                LEFT JOIN outbound_actions dependency ON dependency.id = a.depends_on_action_id
                WHERE a.status IN ('queued', 'failed_retryable')
                  AND a.attempts < a.max_attempts
                  AND (a.available_at IS NULL OR a.available_at <= ?)
                  AND (a.next_retry_at IS NULL OR a.next_retry_at <= ?)
                  AND (a.depends_on_action_id IS NULL OR dependency.status IN ('verified', 'skipped_duplicate'))
                  {account_clause}
                  {candidate_clause}
                ORDER BY a.priority ASC, a.created_at ASC, a.id ASC
                LIMIT 1
                """,
                params,
            ).fetchone()
            if row is None:
                self.conn.commit()
                return None
            self.conn.execute(
                """
                UPDATE outbound_actions
                SET status='locked',
                    attempts=attempts + 1,
                    locked_by=?,
                    locked_at=?,
                    locked_until=?,
                    last_attempt_at=?,
                    updated_at=?
                WHERE id=?
                """,
                (worker_id, claim_time, locked_until, claim_time, claim_time, row["id"]),
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return self.get_action(int(row["id"]))

    def mark_action_verified(
        self,
        action_id: int,
        *,
        sent_at: str | None = None,
        verified_at: str | None = None,
    ) -> None:
        now = utc_now()
        verified = verified_at or now
        sent = sent_at or verified
        self.conn.execute(
            """
            UPDATE outbound_actions
            SET status='verified',
                sent_at=?,
                verified_at=?,
                locked_by=NULL,
                locked_at=NULL,
                locked_until=NULL,
                updated_at=?
            WHERE id=?
            """,
            (sent, verified, now, action_id),
        )
        self._commit()

    def mark_action_failed(
        self,
        action_id: int,
        *,
        error_code: str,
        error_message: str,
        retryable: bool,
        next_retry_at: str | None = None,
    ) -> None:
        now = utc_now()
        status = "failed_retryable" if retryable else "failed_terminal"
        self.conn.execute(
            """
            UPDATE outbound_actions
            SET status=?,
                next_retry_at=?,
                locked_by=NULL,
                locked_at=NULL,
                locked_until=NULL,
                last_error_code=?,
                last_error_message_redacted=?,
                updated_at=?
            WHERE id=?
            """,
            (status, next_retry_at, error_code, redact_text(error_message), now, action_id),
        )
        self._commit()

    def queue_summary(self, *, account_id: int | None = None) -> QueueSummary:
        summary: QueueSummary = {status: 0 for status in ALL_ACTION_STATUSES}  # type: ignore[misc]
        if account_id is None:
            rows = self.conn.execute("SELECT status, COUNT(*) AS count FROM outbound_actions GROUP BY status").fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT a.status, COUNT(*) AS count
                FROM outbound_actions a
                JOIN candidates c ON c.id = a.candidate_id
                WHERE c.account_id=?
                GROUP BY a.status
                """,
                (account_id,),
            ).fetchall()
        total = 0
        for row in rows:
            status = str(row["status"])
            count = int(row["count"])
            if status in summary:
                summary[status] = count  # type: ignore[literal-required]
            total += count
        summary["total"] = total
        return summary

    def has_message_text(self, candidate_id: int, body: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM messages WHERE candidate_id=? AND text_hash=? LIMIT 1",
            (candidate_id, sha256_text(body)),
        ).fetchone()
        return row is not None

    def has_verified_action(self, candidate_id: int, action_type: str) -> bool:
        row = self.conn.execute(
            """
            SELECT 1
            FROM outbound_actions
            WHERE candidate_id=? AND action_type=? AND status IN ('verified', 'skipped_duplicate')
            LIMIT 1
            """,
            (candidate_id, action_type),
        ).fetchone()
        return row is not None

    def cancel_dependents(self, action_id: int, *, reason: str) -> int:
        now = utc_now()
        cursor = self.conn.execute(
            """
            UPDATE outbound_actions
            SET status='cancelled', last_error_code='dependency_failed',
                last_error_message_redacted=?, updated_at=?
            WHERE depends_on_action_id=? AND status IN ('queued', 'failed_retryable')
            """,
            (redact_text(reason), now, action_id),
        )
        self._commit()
        return int(cursor.rowcount)

    def mark_action_status(
        self,
        action_id: int,
        *,
        status: str,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        now = utc_now()
        if status == "sending":
            self.conn.execute(
                """
                UPDATE outbound_actions
                SET status=?, last_error_code=?, last_error_message_redacted=?, updated_at=?
                WHERE id=?
                """,
                (
                    status,
                    error_code,
                    redact_text(error_message) if error_message else None,
                    now,
                    action_id,
                ),
            )
            self._commit()
            return
        self.conn.execute(
            """
            UPDATE outbound_actions
            SET status=?,
                locked_by=NULL,
                locked_at=NULL,
                locked_until=NULL,
                last_error_code=?,
                last_error_message_redacted=?,
                updated_at=?
            WHERE id=?
            """,
            (
                status,
                error_code,
                redact_text(error_message) if error_message else None,
                now,
                action_id,
            ),
        )
        self._commit()

    def get_action_context(self, action_id: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT
              a.*,
              c.friend_id,
              c.uid,
              c.friend_source,
              c.encrypt_uid,
              c.encrypt_geek_id,
              c.name_redacted,
              c.job_name,
              t.name AS template_name,
              t.version AS template_version,
              t.body AS template_body,
              t.body_hash AS template_body_hash
            FROM outbound_actions a
            JOIN candidates c ON c.id = a.candidate_id
            LEFT JOIN message_templates t ON t.id = a.template_id
            WHERE a.id=?
            """,
            (action_id,),
        ).fetchone()
        return _row_to_dict(row)

    def list_candidates(
        self,
        *,
        limit: int = 200,
        status: str | None = None,
        account_id: int | None = None,
    ) -> list[dict[str, Any]]:
        params: list[Any] = []
        conditions: list[str] = []
        if status:
            conditions.append("c.current_stage=?")
            params.append(status)
        if account_id is not None:
            conditions.append("c.account_id=?")
            params.append(account_id)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)
        rows = self.conn.execute(
            f"""
            SELECT
              c.*,
              (
                SELECT status
                FROM outbound_actions a
                WHERE a.candidate_id = c.id
                ORDER BY a.created_at DESC
                LIMIT 1
              ) AS latest_action_status,
              (
                SELECT decision
                FROM decisions d
                WHERE d.candidate_id = c.id
                ORDER BY d.created_at DESC
                LIMIT 1
              ) AS latest_decision
            FROM candidates c
            {where}
            ORDER BY c.last_seen_at DESC, c.id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [_row_to_dict(row) or {} for row in rows]

    def candidate_count(self, *, account_id: int | None = None) -> int:
        if account_id is None:
            row = self.conn.execute("SELECT COUNT(*) AS count FROM candidates").fetchone()
        else:
            row = self.conn.execute(
                "SELECT COUNT(*) AS count FROM candidates WHERE account_id=?",
                (account_id,),
            ).fetchone()
        return int(row["count"])

    def list_queue(self, *, limit: int = 200, account_id: int | None = None) -> list[dict[str, Any]]:
        where = "WHERE c.account_id=?" if account_id is not None else ""
        params: tuple[Any, ...] = (account_id, limit) if account_id is not None else (limit,)
        rows = self.conn.execute(
            f"""
            SELECT
              a.*,
              c.name_redacted,
              c.job_name,
              c.last_message_preview,
              t.name AS template_name,
              t.version AS template_version,
              t.body AS template_body
            FROM outbound_actions a
            JOIN candidates c ON c.id = a.candidate_id
            LEFT JOIN message_templates t ON t.id = a.template_id
            {where}
            ORDER BY a.created_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [_row_to_dict(row) or {} for row in rows]

    def list_events(self, *, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM events ORDER BY created_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_row_to_dict(row) or {} for row in rows]

    def list_templates(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM message_templates ORDER BY created_at DESC, id DESC"
        ).fetchall()
        return [_row_to_dict(row) or {} for row in rows]

    def list_active_templates(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT *
            FROM message_templates
            WHERE approved=1 AND active=1 AND retired_at IS NULL
            ORDER BY name, version, id
            """
        ).fetchall()
        return [_row_to_dict(row) or {} for row in rows]

    def get_template(self, template_id: int) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM message_templates WHERE id=?", (template_id,)).fetchone()
        return _row_to_dict(row)

    def get_template_version(self, *, name: str, version: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM message_templates WHERE name=? AND version=?",
            (name, version),
        ).fetchone()
        return _row_to_dict(row)

    def set_template_approval(self, template_id: int, *, approved: bool, approved_by: str) -> None:
        now = utc_now()
        with self.transaction():
            if approved:
                self.conn.execute(
                    """
                    UPDATE message_templates
                    SET active=0, retired_at=COALESCE(retired_at, ?), updated_at=?
                    WHERE name=(SELECT name FROM message_templates WHERE id=?)
                      AND id<>? AND active=1
                    """,
                    (now, now, template_id, template_id),
                )
            self.conn.execute(
                """
                UPDATE message_templates
                SET approved=?, active=?, approved_by=?, approved_at=?,
                    retired_at=CASE WHEN ? THEN NULL ELSE retired_at END,
                    updated_at=?
                WHERE id=?
                """,
                (int(approved), int(approved), approved_by, now if approved else None, int(approved), now, template_id),
            )

    def retire_template(self, template_id: int) -> None:
        now = utc_now()
        self.conn.execute(
            """
            UPDATE message_templates
            SET active=0, retired_at=?, updated_at=?
            WHERE id=?
            """,
            (now, now, template_id),
        )
        self._commit()

    def list_automation_candidates(
        self,
        *,
        account_id: int,
        catalog_hash: str,
        prompt_version: str,
        limit: int = 20,
        friend_id: int | None = None,
    ) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT
              c.*,
              m.id AS trigger_message_id,
              m.fingerprint AS trigger_fingerprint,
              m.text_redacted AS trigger_text,
              m.sent_at AS trigger_sent_at
            FROM candidates c
            JOIN messages m ON m.id = (
              SELECT latest.id
              FROM messages latest
              WHERE latest.candidate_id=c.id
              ORDER BY COALESCE(latest.sent_at, latest.created_at) DESC, latest.id DESC
              LIMIT 1
            )
            WHERE c.account_id=?
              AND (? IS NULL OR c.friend_id=?)
              AND c.do_not_contact=0
              AND c.inactive_at IS NULL
              AND m.direction='inbound'
              AND m.content_kind IN ('text', 'contact_request')
              AND COALESCE(m.text_redacted, '') <> ''
              AND NOT EXISTS (
                SELECT 1
                FROM automation_decisions d
                WHERE d.candidate_id=c.id
                  AND d.trigger_fingerprint=m.fingerprint
                  AND d.catalog_hash=?
                  AND d.prompt_version=?
              )
            ORDER BY COALESCE(m.sent_at, m.created_at) ASC, c.id ASC
            LIMIT ?
            """,
            (account_id, friend_id, friend_id, catalog_hash, prompt_version, limit),
        ).fetchall()
        return [_row_to_dict(row) or {} for row in rows]

    def get_automation_context(self, candidate_id: int, *, message_limit: int = 8) -> dict[str, Any] | None:
        candidate = self.get_candidate(candidate_id)
        if candidate is None:
            return None
        rows = self.conn.execute(
            """
            SELECT direction, content_kind, sent_at, text_redacted, fingerprint
            FROM messages
            WHERE candidate_id=?
            ORDER BY COALESCE(sent_at, created_at) DESC, id DESC
            LIMIT ?
            """,
            (candidate_id, message_limit),
        ).fetchall()
        candidate["messages"] = [dict(row) for row in reversed(rows)]
        return candidate

    def record_automation_decision(
        self,
        *,
        account_id: int,
        candidate_id: int,
        trigger_fingerprint: str,
        catalog_hash: str,
        decision_key: str,
        outcome: str,
        confidence: float,
        reason: str,
        provider: str,
        model: str,
        prompt_version: str,
        template_id: int | None = None,
        response_hash: str | None = None,
        run_id: str | None = None,
    ) -> int:
        now = utc_now()
        self.conn.execute(
            """
            INSERT INTO automation_decisions(
              run_id, account_id, candidate_id, trigger_fingerprint, catalog_hash,
              decision_key, template_id, outcome, confidence, reason_redacted,
              provider, model, prompt_version, response_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(candidate_id, decision_key) DO UPDATE SET
              run_id=excluded.run_id,
              template_id=excluded.template_id,
              outcome=excluded.outcome,
              confidence=excluded.confidence,
              reason_redacted=excluded.reason_redacted,
              provider=excluded.provider,
              model=excluded.model,
              response_hash=excluded.response_hash
            """,
            (
                run_id,
                account_id,
                candidate_id,
                trigger_fingerprint,
                catalog_hash,
                decision_key,
                template_id,
                outcome,
                max(0.0, min(float(confidence), 1.0)),
                redact_text(reason, max_length=500),
                provider,
                model,
                prompt_version,
                response_hash,
                now,
            ),
        )
        self._commit()
        row = self.conn.execute(
            "SELECT id FROM automation_decisions WHERE candidate_id=? AND decision_key=?",
            (candidate_id, decision_key),
        ).fetchone()
        return int(row["id"])

    def list_automation_decisions(self, *, account_id: int | None, limit: int = 200) -> list[dict[str, Any]]:
        where = "WHERE d.account_id=?" if account_id is not None else ""
        params: tuple[Any, ...] = (account_id, limit) if account_id is not None else (limit,)
        rows = self.conn.execute(
            f"""
            SELECT
              d.*,
              c.name_redacted,
              c.job_name,
              c.last_message_preview,
              t.name AS template_name,
              t.version AS template_version
            FROM automation_decisions d
            JOIN candidates c ON c.id=d.candidate_id
            LEFT JOIN message_templates t ON t.id=d.template_id
            {where}
            ORDER BY d.created_at DESC, d.id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [_row_to_dict(row) or {} for row in rows]

    def automation_summary(self, *, account_id: int | None) -> dict[str, int]:
        summary = {"selected": 0, "review": 0, "skipped": 0, "error": 0, "dry_run": 0, "total": 0}
        if account_id is None:
            rows = self.conn.execute(
                "SELECT outcome, COUNT(*) AS count FROM automation_decisions GROUP BY outcome"
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT outcome, COUNT(*) AS count FROM automation_decisions WHERE account_id=? GROUP BY outcome",
                (account_id,),
            ).fetchall()
        for row in rows:
            outcome = str(row["outcome"])
            count = int(row["count"])
            if outcome in summary:
                summary[outcome] = count
            summary["total"] += count
        return summary

    def delivery_summary(self, *, account_id: int | None) -> dict[str, int]:
        summary = {"messages_verified": 0, "wechat_verified": 0, "failed": 0}
        where = "WHERE c.account_id=?" if account_id is not None else ""
        params: tuple[Any, ...] = (account_id,) if account_id is not None else ()
        rows = self.conn.execute(
            f"""
            SELECT a.action_type, a.status, COUNT(*) AS count
            FROM outbound_actions a
            JOIN candidates c ON c.id=a.candidate_id
            {where}
            GROUP BY a.action_type, a.status
            """,
            params,
        ).fetchall()
        for row in rows:
            action_type = str(row["action_type"])
            status = str(row["status"])
            count = int(row["count"])
            if status == "verified" and action_type in {"send_message", "send_template"}:
                summary["messages_verified"] += count
            elif status == "verified" and action_type == "exchange_wechat":
                summary["wechat_verified"] += count
            elif status in {"failed_retryable", "failed_terminal", "sent_unverified"}:
                summary["failed"] += count
        return summary

    def claim_daemon(
        self,
        *,
        daemon_name: str,
        owner_id: str,
        live_mode: bool,
        lease_seconds: int,
        now: str | None = None,
    ) -> bool:
        claim_time = now or utc_now()
        lease_expires = _add_seconds(claim_time, lease_seconds)
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute(
                "SELECT * FROM automation_daemon_state WHERE daemon_name=?",
                (daemon_name,),
            ).fetchone()
            if (
                row is not None
                and row["owner_id"] not in (None, owner_id)
                and row["status"] == "running"
                and str(row["lease_expires_at"] or "") > claim_time
            ):
                self.conn.commit()
                return False
            self.conn.execute(
                """
                INSERT INTO automation_daemon_state(
                  daemon_name, owner_id, status, live_mode, started_at,
                  heartbeat_at, lease_expires_at, updated_at
                ) VALUES (?, ?, 'running', ?, ?, ?, ?, ?)
                ON CONFLICT(daemon_name) DO UPDATE SET
                  owner_id=excluded.owner_id,
                  status='running',
                  live_mode=excluded.live_mode,
                  started_at=excluded.started_at,
                  heartbeat_at=excluded.heartbeat_at,
                  lease_expires_at=excluded.lease_expires_at,
                  last_error_code=NULL,
                  last_error_redacted=NULL,
                  updated_at=excluded.updated_at
                """,
                (daemon_name, owner_id, int(live_mode), claim_time, claim_time, lease_expires, claim_time),
            )
            self.conn.commit()
            return True
        except Exception:
            self.conn.rollback()
            raise

    def heartbeat_daemon(
        self,
        *,
        daemon_name: str,
        owner_id: str,
        lease_seconds: int,
        account_id: int | None = None,
        last_run_id: str | None = None,
        cycle_status: str | None = None,
        cycle_summary: dict[str, Any] | None = None,
        cycle_started_at: str | None = None,
        cycle_finished_at: str | None = None,
        next_poll_at: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        now = utc_now()
        cursor = self.conn.execute(
            """
            UPDATE automation_daemon_state
            SET account_id=COALESCE(?, account_id), heartbeat_at=?, lease_expires_at=?,
                last_run_id=COALESCE(?, last_run_id),
                last_cycle_status=COALESCE(?, last_cycle_status),
                cycle_summary_json=COALESCE(?, cycle_summary_json),
                last_cycle_started_at=COALESCE(?, last_cycle_started_at),
                last_cycle_finished_at=COALESCE(?, last_cycle_finished_at),
                next_poll_at=?, last_error_code=?, last_error_redacted=?, updated_at=?
            WHERE daemon_name=? AND owner_id=?
            """,
            (
                account_id,
                now,
                _add_seconds(now, lease_seconds),
                last_run_id,
                cycle_status,
                stable_json_dumps(cycle_summary) if cycle_summary is not None else None,
                cycle_started_at,
                cycle_finished_at,
                next_poll_at,
                error_code,
                redact_text(error_message, max_length=500) if error_message else None,
                now,
                daemon_name,
                owner_id,
            ),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("automation daemon lease is no longer owned by this process")
        self._commit()

    def release_daemon(self, *, daemon_name: str, owner_id: str, status: str = "stopped") -> None:
        now = utc_now()
        self.conn.execute(
            """
            UPDATE automation_daemon_state
            SET owner_id=NULL, status=?, heartbeat_at=?, lease_expires_at=NULL,
                next_poll_at=NULL, updated_at=?
            WHERE daemon_name=? AND owner_id=?
            """,
            (status, now, now, daemon_name, owner_id),
        )
        self._commit()

    def get_daemon_state(self, daemon_name: str = "primary") -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM automation_daemon_state WHERE daemon_name=?",
            (daemon_name,),
        ).fetchone()
        value = _row_to_dict(row)
        if value and value.get("cycle_summary_json"):
            try:
                value["cycle_summary"] = json.loads(value["cycle_summary_json"])
            except json.JSONDecodeError:
                value["cycle_summary"] = {}
        return value

    def set_setting(self, key: str, value: dict[str, Any] | str | bool | int | None) -> None:
        now = utc_now()
        value_json = stable_json_dumps(value)
        self.conn.execute(
            """
            INSERT INTO workflow_settings(key, value_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
              value_json=excluded.value_json,
              updated_at=excluded.updated_at
            """,
            (key, value_json, now),
        )
        self._commit()

    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self.conn.execute("SELECT value_json FROM workflow_settings WHERE key=?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value_json"])
        except json.JSONDecodeError:
            return default

    def is_paused(self) -> bool:
        value = self.get_setting("paused", False)
        if isinstance(value, dict):
            return bool(value.get("paused"))
        return bool(value)

    def create_run(
        self,
        *,
        run_type: str,
        requested_by: str | None = None,
        account_id: int | None = None,
        filters: dict[str, Any] | None = None,
    ) -> str:
        run_id = uuid.uuid4().hex
        self.conn.execute(
            """
            INSERT INTO workflow_runs(
              id, run_type, status, started_at, requested_by, account_id, filter_json
            )
            VALUES (?, ?, 'running', ?, ?, ?, ?)
            """,
            (run_id, run_type, utc_now(), requested_by, account_id, _json_or_none(filters)),
        )
        self._commit()
        return run_id

    def attach_run_account(self, run_id: str, account_id: int) -> None:
        self.conn.execute("UPDATE workflow_runs SET account_id=? WHERE id=?", (account_id, run_id))
        self._commit()

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        stop_reason: str | None = None,
        summary: dict[str, Any] | None = None,
        request_count: int = 0,
    ) -> None:
        self.conn.execute(
            """
            UPDATE workflow_runs
            SET status=?,
                finished_at=?,
                stop_reason=?,
                summary_json=?,
                request_count=?
            WHERE id=?
            """,
            (status, utc_now(), stop_reason, stable_json_dumps(summary or {}), request_count, run_id),
        )
        self._commit()

    def list_runs(self, *, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM workflow_runs ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_row_to_dict(row) or {} for row in rows]

    def get_sync_checkpoint(
        self,
        *,
        account_id: int,
        stream: str,
        filter_hash: str,
    ) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT cursor_json, completed, updated_at
            FROM sync_checkpoints
            WHERE account_id=? AND stream=? AND filter_hash=?
            """,
            (account_id, stream, filter_hash),
        ).fetchone()
        if row is None:
            return None
        try:
            cursor = json.loads(row["cursor_json"])
        except json.JSONDecodeError:
            cursor = {}
        return {"cursor": cursor, "completed": bool(row["completed"]), "updated_at": row["updated_at"]}

    def set_sync_checkpoint(
        self,
        *,
        account_id: int,
        stream: str,
        filter_hash: str,
        cursor: dict[str, Any],
        completed: bool,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO sync_checkpoints(
              account_id, stream, filter_hash, cursor_json, completed, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id, stream, filter_hash) DO UPDATE SET
              cursor_json=excluded.cursor_json,
              completed=excluded.completed,
              updated_at=excluded.updated_at
            """,
            (account_id, stream, filter_hash, stable_json_dumps(cursor), int(completed), utc_now()),
        )
        self._commit()

    def record_sync_error(
        self,
        *,
        run_id: str,
        error_code: str,
        error_message: str,
        account_id: int | None = None,
        friend_id: int | None = None,
        page: int | None = None,
    ) -> int:
        self.conn.execute(
            """
            INSERT INTO sync_run_errors(
              run_id, account_id, friend_id, page, error_code,
              error_message_redacted, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, account_id, friend_id, page, error_code, redact_text(error_message), utc_now()),
        )
        self._commit()
        return int(self.conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])

    def record_selection(
        self,
        *,
        run_id: str,
        candidate_id: int,
        selected: bool,
        selection_source: str,
        reason_code: str | None = None,
    ) -> int:
        self.conn.execute(
            """
            INSERT INTO operator_selections(
              run_id, candidate_id, selected, selection_source, reason_code, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (run_id, candidate_id, int(selected), selection_source, reason_code, utc_now()),
        )
        self._commit()
        return int(self.conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])

    def append_event(
        self,
        *,
        event_type: str,
        summary: str,
        run_id: str | None = None,
        candidate_id: int | None = None,
        action_id: int | None = None,
        severity: str = "info",
        details: dict[str, Any] | str | None = None,
    ) -> int:
        now = utc_now()
        self.conn.execute(
            """
            INSERT INTO events (
              run_id, event_type, candidate_id, action_id, severity, summary,
              details_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, event_type, candidate_id, action_id, severity, redact_text(summary), _json_or_none(details), now),
        )
        self._commit()
        return int(self.conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])

    def get_action(self, action_id: int) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM outbound_actions WHERE id=?", (action_id,)).fetchone()
        return _row_to_dict(row)

    def get_action_by_idempotency_key(self, idempotency_key: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM outbound_actions WHERE idempotency_key=?",
            (idempotency_key,),
        ).fetchone()
        return _row_to_dict(row)

    def get_candidate(self, candidate_id: int) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
        return _row_to_dict(row)

    def _id_for(self, table: str, key_col: str, key_value: str) -> int:
        row = self.conn.execute(f"SELECT id FROM {table} WHERE {key_col}=?", (key_value,)).fetchone()
        return int(row["id"])


def _json_or_none(value: dict[str, Any] | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return stable_json_dumps(value)


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def _add_seconds(timestamp: str, seconds: int) -> str:
    if timestamp.endswith("Z"):
        base = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    else:
        base = datetime.fromisoformat(timestamp)
        if base.tzinfo is None:
            base = base.replace(tzinfo=timezone.utc)
    return (base + timedelta(seconds=seconds)).isoformat(timespec="seconds").replace("+00:00", "Z")
