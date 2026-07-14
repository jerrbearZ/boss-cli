BEGIN IMMEDIATE;

ALTER TABLE accounts ADD COLUMN external_id_hash TEXT;
ALTER TABLE accounts ADD COLUMN identity_source TEXT NOT NULL DEFAULT 'credential';

ALTER TABLE jobs ADD COLUMN boss_job_id INTEGER;
ALTER TABLE jobs ADD COLUMN last_seen_run_id TEXT;
ALTER TABLE jobs ADD COLUMN inactive_at TEXT;
ALTER TABLE jobs ADD COLUMN metadata_json TEXT;

ALTER TABLE candidates ADD COLUMN last_observed_at TEXT;
ALTER TABLE candidates ADD COLUMN last_activity_at TEXT;
ALTER TABLE candidates ADD COLUMN last_seen_run_id TEXT;
ALTER TABLE candidates ADD COLUMN history_synced_through TEXT;
ALTER TABLE candidates ADD COLUMN history_sync_status TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE candidates ADD COLUMN inactive_at TEXT;

ALTER TABLE messages ADD COLUMN source TEXT NOT NULL DEFAULT 'latest';
ALTER TABLE messages ADD COLUMN content_kind TEXT NOT NULL DEFAULT 'text';
ALTER TABLE messages ADD COLUMN payload_hash TEXT;

ALTER TABLE workflow_runs ADD COLUMN account_id INTEGER REFERENCES accounts(id);
ALTER TABLE workflow_runs ADD COLUMN filter_json TEXT;
ALTER TABLE workflow_runs ADD COLUMN request_count INTEGER NOT NULL DEFAULT 0;

CREATE TABLE sync_checkpoints (
  account_id INTEGER NOT NULL,
  stream TEXT NOT NULL,
  filter_hash TEXT NOT NULL,
  cursor_json TEXT NOT NULL,
  completed INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(account_id, stream, filter_hash),
  FOREIGN KEY(account_id) REFERENCES accounts(id)
);

CREATE TABLE sync_run_errors (
  id INTEGER PRIMARY KEY,
  run_id TEXT NOT NULL,
  account_id INTEGER,
  friend_id INTEGER,
  page INTEGER,
  error_code TEXT NOT NULL,
  error_message_redacted TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(run_id) REFERENCES workflow_runs(id),
  FOREIGN KEY(account_id) REFERENCES accounts(id)
);

CREATE UNIQUE INDEX idx_accounts_external_id_hash
  ON accounts(external_id_hash)
  WHERE external_id_hash IS NOT NULL;
CREATE INDEX idx_candidates_account_observed
  ON candidates(account_id, last_observed_at);
CREATE INDEX idx_candidates_account_activity
  ON candidates(account_id, last_activity_at);
CREATE INDEX idx_messages_candidate_sent
  ON messages(candidate_id, sent_at);
CREATE INDEX idx_sync_run_errors_run
  ON sync_run_errors(run_id, created_at);

INSERT INTO schema_migrations(version, name)
VALUES (3, 'incremental account-scoped reader');

COMMIT;
