PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

INSERT OR IGNORE INTO schema_migrations(version, name)
VALUES (1, 'initial workflow state');

INSERT OR IGNORE INTO schema_migrations(version, name)
VALUES (2, 'dashboard control state');

CREATE TABLE IF NOT EXISTS accounts (
  id INTEGER PRIMARY KEY,
  platform TEXT NOT NULL DEFAULT 'boss',
  account_hash TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'active',
  paused_until TEXT,
  last_auth_ok_at TEXT,
  last_error_code TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY,
  account_id INTEGER NOT NULL,
  enc_job_id TEXT NOT NULL,
  job_name TEXT,
  active INTEGER NOT NULL DEFAULT 1,
  last_seen_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(account_id, enc_job_id),
  FOREIGN KEY(account_id) REFERENCES accounts(id)
);

CREATE TABLE IF NOT EXISTS candidates (
  id INTEGER PRIMARY KEY,
  account_id INTEGER NOT NULL,
  friend_id INTEGER NOT NULL,
  uid INTEGER,
  friend_source INTEGER NOT NULL DEFAULT 0,
  encrypt_uid TEXT,
  encrypt_geek_id TEXT,
  security_id_present INTEGER NOT NULL DEFAULT 0,
  job_id INTEGER,
  encrypt_job_id TEXT,
  job_name TEXT,
  name_redacted TEXT,
  name_hash TEXT,
  do_not_contact INTEGER NOT NULL DEFAULT 0,
  current_stage TEXT NOT NULL DEFAULT 'seen',
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  last_inbound_at TEXT,
  last_outbound_at TEXT,
  last_message_preview TEXT,
  last_message_fingerprint TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(account_id, friend_id, friend_source),
  FOREIGN KEY(account_id) REFERENCES accounts(id),
  FOREIGN KEY(job_id) REFERENCES jobs(id)
);

CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY,
  candidate_id INTEGER NOT NULL,
  boss_msg_id TEXT,
  direction TEXT NOT NULL,
  msg_type TEXT,
  sent_at TEXT,
  text_hash TEXT,
  text_redacted TEXT,
  fingerprint TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(candidate_id, fingerprint),
  FOREIGN KEY(candidate_id) REFERENCES candidates(id)
);

CREATE TABLE IF NOT EXISTS candidate_snapshots (
  id INTEGER PRIMARY KEY,
  candidate_id INTEGER NOT NULL,
  source TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  payload_hash TEXT NOT NULL,
  summary_json TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(candidate_id, source, payload_hash),
  FOREIGN KEY(candidate_id) REFERENCES candidates(id)
);

CREATE TABLE IF NOT EXISTS rulesets (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  version TEXT NOT NULL,
  config_json TEXT NOT NULL,
  config_hash TEXT NOT NULL UNIQUE,
  approved INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 0,
  approved_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(name, version)
);

CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY,
  candidate_id INTEGER NOT NULL,
  ruleset_id INTEGER NOT NULL,
  input_fingerprint TEXT NOT NULL,
  decision TEXT NOT NULL,
  reason_code TEXT NOT NULL,
  evidence_json TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(candidate_id, ruleset_id, input_fingerprint),
  FOREIGN KEY(candidate_id) REFERENCES candidates(id),
  FOREIGN KEY(ruleset_id) REFERENCES rulesets(id)
);

CREATE TABLE IF NOT EXISTS message_templates (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  version TEXT NOT NULL,
  body TEXT NOT NULL,
  body_hash TEXT NOT NULL,
  approved INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 0,
  approved_by TEXT,
  approved_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(name, version)
);

CREATE TABLE IF NOT EXISTS outbound_actions (
  id INTEGER PRIMARY KEY,
  candidate_id INTEGER NOT NULL,
  action_type TEXT NOT NULL,
  template_id INTEGER NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'queued',
  priority INTEGER NOT NULL DEFAULT 100,
  available_at TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 3,
  locked_by TEXT,
  locked_at TEXT,
  locked_until TEXT,
  last_attempt_at TEXT,
  next_retry_at TEXT,
  sent_at TEXT,
  verified_at TEXT,
  last_error_code TEXT,
  last_error_message_redacted TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(candidate_id) REFERENCES candidates(id),
  FOREIGN KEY(template_id) REFERENCES message_templates(id)
);

CREATE TABLE IF NOT EXISTS action_attempts (
  id INTEGER PRIMARY KEY,
  action_id INTEGER NOT NULL,
  run_id TEXT NOT NULL,
  attempt_no INTEGER NOT NULL,
  status TEXT NOT NULL,
  engine TEXT,
  method TEXT,
  verification_status TEXT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  error_code TEXT,
  error_message_redacted TEXT,
  FOREIGN KEY(action_id) REFERENCES outbound_actions(id)
);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY,
  run_id TEXT,
  event_type TEXT NOT NULL,
  candidate_id INTEGER,
  action_id INTEGER,
  severity TEXT NOT NULL DEFAULT 'info',
  summary TEXT NOT NULL,
  details_json TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(candidate_id) REFERENCES candidates(id),
  FOREIGN KEY(action_id) REFERENCES outbound_actions(id)
);

CREATE TABLE IF NOT EXISTS rate_limit_buckets (
  account_id INTEGER NOT NULL,
  bucket TEXT NOT NULL,
  window_start TEXT NOT NULL,
  used INTEGER NOT NULL DEFAULT 0,
  limit_value INTEGER NOT NULL,
  blocked_until TEXT,
  PRIMARY KEY(account_id, bucket, window_start),
  FOREIGN KEY(account_id) REFERENCES accounts(id)
);

CREATE TABLE IF NOT EXISTS workflow_runs (
  id TEXT PRIMARY KEY,
  run_type TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  requested_by TEXT,
  stop_reason TEXT,
  summary_json TEXT
);

CREATE TABLE IF NOT EXISTS operator_selections (
  id INTEGER PRIMARY KEY,
  run_id TEXT NOT NULL,
  candidate_id INTEGER NOT NULL,
  selected INTEGER NOT NULL,
  selection_source TEXT NOT NULL,
  reason_code TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(candidate_id) REFERENCES candidates(id)
);

CREATE TABLE IF NOT EXISTS workflow_settings (
  key TEXT PRIMARY KEY,
  value_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_candidates_account_last_seen ON candidates(account_id, last_seen_at);
CREATE INDEX IF NOT EXISTS idx_messages_candidate_created ON messages(candidate_id, created_at);
CREATE INDEX IF NOT EXISTS idx_decisions_candidate_created ON decisions(candidate_id, created_at);
CREATE INDEX IF NOT EXISTS idx_outbound_actions_status_available ON outbound_actions(status, available_at, priority, created_at);
CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_started ON workflow_runs(started_at);
CREATE INDEX IF NOT EXISTS idx_operator_selections_run ON operator_selections(run_id, candidate_id);
