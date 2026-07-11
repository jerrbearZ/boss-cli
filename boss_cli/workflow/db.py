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

SCHEMA_VERSION = 2
SCHEMA_PATH = Path(__file__).with_name("schema.sql")


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
    ) -> int:
        now = utc_now()
        self.conn.execute(
            """
            INSERT INTO accounts (
              platform, account_hash, status, paused_until, last_auth_ok_at,
              last_error_code, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_hash) DO UPDATE SET
              platform=excluded.platform,
              status=excluded.status,
              paused_until=excluded.paused_until,
              last_auth_ok_at=COALESCE(excluded.last_auth_ok_at, accounts.last_auth_ok_at),
              last_error_code=excluded.last_error_code,
              updated_at=excluded.updated_at
            """,
            (platform, account_hash, status, paused_until, last_auth_ok_at, last_error_code, now, now),
        )
        self._commit()
        return self._id_for("accounts", "account_hash", account_hash)

    def upsert_job(
        self,
        *,
        account_id: int,
        enc_job_id: str,
        job_name: str | None = None,
        active: bool = True,
        last_seen_at: str | None = None,
    ) -> int:
        now = utc_now()
        seen_at = last_seen_at or now
        self.conn.execute(
            """
            INSERT INTO jobs (
              account_id, enc_job_id, job_name, active, last_seen_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id, enc_job_id) DO UPDATE SET
              job_name=COALESCE(excluded.job_name, jobs.job_name),
              active=excluded.active,
              last_seen_at=excluded.last_seen_at,
              updated_at=excluded.updated_at
            """,
            (account_id, enc_job_id, job_name, int(active), seen_at, now, now),
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
    ) -> int:
        now = utc_now()
        first_seen = first_seen_at or now
        last_seen = last_seen_at or now
        self.conn.execute(
            """
            INSERT INTO candidates (
              account_id, friend_id, uid, friend_source, encrypt_uid, encrypt_geek_id,
              security_id_present, job_id, encrypt_job_id, job_name, name_redacted,
              name_hash, do_not_contact, current_stage, first_seen_at, last_seen_at,
              last_inbound_at, last_outbound_at, last_message_preview,
              last_message_fingerprint, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id, friend_id, friend_source) DO UPDATE SET
              uid=COALESCE(excluded.uid, candidates.uid),
              encrypt_uid=COALESCE(excluded.encrypt_uid, candidates.encrypt_uid),
              encrypt_geek_id=COALESCE(excluded.encrypt_geek_id, candidates.encrypt_geek_id),
              security_id_present=excluded.security_id_present,
              job_id=COALESCE(excluded.job_id, candidates.job_id),
              encrypt_job_id=COALESCE(excluded.encrypt_job_id, candidates.encrypt_job_id),
              job_name=COALESCE(excluded.job_name, candidates.job_name),
              name_redacted=COALESCE(excluded.name_redacted, candidates.name_redacted),
              name_hash=COALESCE(excluded.name_hash, candidates.name_hash),
              do_not_contact=excluded.do_not_contact,
              current_stage=excluded.current_stage,
              last_seen_at=excluded.last_seen_at,
              last_inbound_at=COALESCE(excluded.last_inbound_at, candidates.last_inbound_at),
              last_outbound_at=COALESCE(excluded.last_outbound_at, candidates.last_outbound_at),
              last_message_preview=COALESCE(excluded.last_message_preview, candidates.last_message_preview),
              last_message_fingerprint=COALESCE(excluded.last_message_fingerprint, candidates.last_message_fingerprint),
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
    ) -> int:
        now = utc_now()
        self.conn.execute(
            """
            INSERT INTO messages (
              candidate_id, boss_msg_id, direction, msg_type, sent_at, text_hash,
              text_redacted, fingerprint, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(candidate_id, fingerprint) DO UPDATE SET
              boss_msg_id=COALESCE(excluded.boss_msg_id, messages.boss_msg_id),
              direction=excluded.direction,
              msg_type=COALESCE(excluded.msg_type, messages.msg_type),
              sent_at=COALESCE(excluded.sent_at, messages.sent_at),
              text_hash=COALESCE(excluded.text_hash, messages.text_hash),
              text_redacted=COALESCE(excluded.text_redacted, messages.text_redacted),
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
    ) -> int:
        now = utc_now()
        self.conn.execute(
            """
            INSERT INTO message_templates (
              name, version, body, body_hash, approved, active, approved_by,
              approved_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name, version) DO UPDATE SET
              body=excluded.body,
              body_hash=excluded.body_hash,
              approved=excluded.approved,
              active=excluded.active,
              approved_by=excluded.approved_by,
              approved_at=excluded.approved_at,
              updated_at=excluded.updated_at
            """,
            (name, version, body, sha256_text(body), int(approved), int(active), approved_by, approved_at, now, now),
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
        template_id: int,
        idempotency_key: str,
        status: str = "queued",
        priority: int = 100,
        available_at: str | None = None,
        max_attempts: int = 3,
    ) -> int:
        now = utc_now()
        self.conn.execute(
            """
            INSERT INTO outbound_actions (
              candidate_id, action_type, template_id, idempotency_key, status,
              priority, available_at, max_attempts, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(idempotency_key) DO NOTHING
            """,
            (candidate_id, action_type, template_id, idempotency_key, status, priority, available_at, max_attempts, now, now),
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
    ) -> dict[str, Any] | None:
        if self._transaction_depth > 0:
            raise RuntimeError("claim_next_action manages its own lease transaction")
        claim_time = now or utc_now()
        locked_until = _add_seconds(claim_time, lease_seconds)
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute(
                """
                SELECT *
                FROM outbound_actions
                WHERE status='queued'
                  AND (available_at IS NULL OR available_at <= ?)
                ORDER BY priority ASC, created_at ASC
                LIMIT 1
                """,
                (claim_time,),
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

    def queue_summary(self) -> QueueSummary:
        summary: QueueSummary = {status: 0 for status in ALL_ACTION_STATUSES}  # type: ignore[misc]
        rows = self.conn.execute("SELECT status, COUNT(*) AS count FROM outbound_actions GROUP BY status").fetchall()
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

    def mark_action_status(
        self,
        action_id: int,
        *,
        status: str,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        now = utc_now()
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
            JOIN message_templates t ON t.id = a.template_id
            WHERE a.id=?
            """,
            (action_id,),
        ).fetchone()
        return _row_to_dict(row)

    def list_candidates(self, *, limit: int = 200, status: str | None = None) -> list[dict[str, Any]]:
        params: list[Any] = []
        where = ""
        if status:
            where = "WHERE c.current_stage=?"
            params.append(status)
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

    def list_queue(self, *, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
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
            JOIN message_templates t ON t.id = a.template_id
            ORDER BY a.created_at DESC
            LIMIT ?
            """,
            (limit,),
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

    def get_template(self, template_id: int) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM message_templates WHERE id=?", (template_id,)).fetchone()
        return _row_to_dict(row)

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

    def create_run(self, *, run_type: str, requested_by: str | None = None) -> str:
        run_id = uuid.uuid4().hex
        self.conn.execute(
            """
            INSERT INTO workflow_runs(id, run_type, status, started_at, requested_by)
            VALUES (?, ?, 'running', ?, ?)
            """,
            (run_id, run_type, utc_now(), requested_by),
        )
        self._commit()
        return run_id

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        stop_reason: str | None = None,
        summary: dict[str, Any] | None = None,
    ) -> None:
        self.conn.execute(
            """
            UPDATE workflow_runs
            SET status=?,
                finished_at=?,
                stop_reason=?,
                summary_json=?
            WHERE id=?
            """,
            (status, utc_now(), stop_reason, stable_json_dumps(summary or {}), run_id),
        )
        self._commit()

    def list_runs(self, *, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM workflow_runs ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_row_to_dict(row) or {} for row in rows]

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
