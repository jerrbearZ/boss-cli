"""SQLite online backup, retention, integrity checking, and guarded restore."""

from __future__ import annotations

import os
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..platform import PATHS, ensure_private_directory, ensure_private_file
from .db import SCHEMA_VERSION, default_db_path, init_db


class MaintenanceError(RuntimeError):
    """Raised when a backup or restore safety gate fails."""


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def verify_database(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        raise MaintenanceError(f"Database does not exist: {path}")
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(str(path))
        connection.row_factory = sqlite3.Row
        result = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        row = connection.execute("SELECT MAX(version) AS version FROM schema_migrations").fetchone()
        version = int(row["version"] or 0)
    except (sqlite3.Error, OSError) as exc:
        raise MaintenanceError(f"Database verification failed: {type(exc).__name__}") from exc
    finally:
        if connection is not None:
            connection.close()
    if result != "ok":
        raise MaintenanceError(f"Database integrity check failed: {result}")
    if version > SCHEMA_VERSION:
        raise MaintenanceError(f"Database schema {version} is newer than supported schema {SCHEMA_VERSION}")
    return {"integrity": result, "schema_version": version}


def create_backup(
    db_path: Path | str | None = None,
    *,
    destination: Path | str | None = None,
    kind: str = "daily",
    retention: int | None = None,
) -> dict[str, Any]:
    if kind not in {"daily", "pre-update", "manual"}:
        raise MaintenanceError("Backup kind must be daily, pre-update, or manual")
    source = Path(db_path).expanduser() if db_path else default_db_path()
    if not source.exists() or not source.is_file():
        raise MaintenanceError(f"Source database does not exist: {source}")
    target = Path(destination).expanduser() if destination else PATHS.backup_dir / f"workflow-{kind}-{_timestamp()}.db"
    ensure_private_directory(target.parent)
    if target.exists():
        raise MaintenanceError(f"Backup destination already exists: {target}")
    with init_db(source) as store:
        backup_connection = sqlite3.connect(str(target))
        try:
            store.conn.backup(backup_connection)
            backup_connection.commit()
            ensure_private_file(target)
        finally:
            backup_connection.close()
    verification = verify_database(target)
    removed: list[str] = []
    keep = retention if retention is not None else (7 if kind == "daily" else 3 if kind == "pre-update" else 0)
    if keep > 0:
        candidates = sorted(target.parent.glob(f"workflow-{kind}-*.db"), key=lambda item: item.stat().st_mtime, reverse=True)
        for expired in candidates[keep:]:
            expired.unlink()
            removed.append(str(expired))
    return {
        "source": str(source),
        "backup": str(target),
        "kind": kind,
        "retention": keep,
        "removed": removed,
        **verification,
    }


def restore_backup(
    backup_path: Path | str,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """Restore only while daemon ownership is released, preserving the old DB."""
    source = Path(backup_path).expanduser()
    target = Path(db_path).expanduser() if db_path else default_db_path()
    source_verification = verify_database(source)
    preserved: Path | None = None

    if target.exists():
        current = sqlite3.connect(str(target))
        current.row_factory = sqlite3.Row
        try:
            current_integrity = str(current.execute("PRAGMA integrity_check").fetchone()[0])
            if current_integrity != "ok":
                raise MaintenanceError(f"Current database integrity check failed: {current_integrity}")
            has_daemon_state = current.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='automation_daemon_state'"
            ).fetchone()
            if has_daemon_state:
                state = current.execute("SELECT owner_id FROM automation_daemon_state WHERE daemon_name='primary'").fetchone()
                if state and state["owner_id"]:
                    raise MaintenanceError("Daemon ownership is still active; stop scheduled tasks before restore")
            current.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            current.close()
        preserved = target.with_name(f"{target.name}.replaced-{_timestamp()}-{uuid.uuid4().hex[:8]}")
        os.replace(target, preserved)
        for suffix in ("-wal", "-shm"):
            Path(str(target) + suffix).unlink(missing_ok=True)

    ensure_private_directory(target.parent)
    temporary = target.with_suffix(target.suffix + ".restore-tmp")
    try:
        shutil.copy2(source, temporary)
        verify_database(temporary)
        os.replace(temporary, target)
        ensure_private_file(target)
        with init_db(target) as restored:
            restored_version = restored.current_schema_version()
    except Exception:
        temporary.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        if preserved and preserved.exists():
            os.replace(preserved, target)
        raise
    return {
        "backup": str(source),
        "db": str(target),
        "preserved_database": str(preserved) if preserved else None,
        "source_schema_version": source_verification["schema_version"],
        "schema_version": restored_version,
        "integrity": "ok",
    }
