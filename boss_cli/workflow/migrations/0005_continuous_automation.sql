BEGIN IMMEDIATE;

ALTER TABLE message_templates ADD COLUMN selection_guidance TEXT NOT NULL DEFAULT '';
ALTER TABLE message_templates ADD COLUMN retired_at TEXT;

CREATE TABLE automation_decisions (
  id INTEGER PRIMARY KEY,
  run_id TEXT,
  account_id INTEGER NOT NULL,
  candidate_id INTEGER NOT NULL,
  trigger_fingerprint TEXT NOT NULL,
  catalog_hash TEXT NOT NULL,
  decision_key TEXT NOT NULL,
  template_id INTEGER,
  outcome TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 0,
  reason_redacted TEXT NOT NULL,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  response_hash TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(candidate_id, decision_key),
  FOREIGN KEY(account_id) REFERENCES accounts(id),
  FOREIGN KEY(candidate_id) REFERENCES candidates(id),
  FOREIGN KEY(template_id) REFERENCES message_templates(id)
);

CREATE TABLE automation_daemon_state (
  daemon_name TEXT PRIMARY KEY,
  owner_id TEXT,
  account_id INTEGER,
  status TEXT NOT NULL DEFAULT 'stopped',
  live_mode INTEGER NOT NULL DEFAULT 0,
  started_at TEXT,
  heartbeat_at TEXT,
  lease_expires_at TEXT,
  last_cycle_started_at TEXT,
  last_cycle_finished_at TEXT,
  last_cycle_status TEXT,
  last_run_id TEXT,
  next_poll_at TEXT,
  last_error_code TEXT,
  last_error_redacted TEXT,
  cycle_summary_json TEXT,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(account_id) REFERENCES accounts(id)
);

CREATE INDEX idx_automation_decisions_account_created
  ON automation_decisions(account_id, created_at);
CREATE INDEX idx_automation_decisions_candidate_trigger
  ON automation_decisions(candidate_id, trigger_fingerprint);
CREATE INDEX idx_automation_decisions_outcome
  ON automation_decisions(outcome, created_at);

UPDATE outbound_actions
SET status='needs_review',
    locked_by=NULL,
    locked_at=NULL,
    locked_until=NULL,
    last_error_code='schema_v5_migration_review',
    last_error_message_redacted='Legacy pending action requires review before continuous automation',
    updated_at=strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
WHERE status IN ('queued', 'locked', 'sending', 'failed_retryable');

INSERT INTO schema_migrations(version, name)
VALUES (5, 'continuous automation decisions and daemon state');

COMMIT;
