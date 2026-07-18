BEGIN IMMEDIATE;

ALTER TABLE automation_daemon_state ADD COLUMN stop_requested_at TEXT;
ALTER TABLE automation_daemon_state ADD COLUMN operator_required_code TEXT;
ALTER TABLE automation_daemon_state ADD COLUMN operator_required_at TEXT;

INSERT INTO schema_migrations(version, name)
VALUES (6, 'cross-platform automation control plane');

COMMIT;
