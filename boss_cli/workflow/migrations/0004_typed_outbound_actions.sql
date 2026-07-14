PRAGMA foreign_keys=OFF;

BEGIN IMMEDIATE;

CREATE TABLE outbound_actions_v4 (
  id INTEGER PRIMARY KEY,
  candidate_id INTEGER NOT NULL,
  action_type TEXT NOT NULL,
  template_id INTEGER,
  depends_on_action_id INTEGER,
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
  FOREIGN KEY(template_id) REFERENCES message_templates(id),
  FOREIGN KEY(depends_on_action_id) REFERENCES outbound_actions_v4(id)
);

INSERT INTO outbound_actions_v4 (
  id, candidate_id, action_type, template_id, idempotency_key, status,
  priority, available_at, attempts, max_attempts, locked_by, locked_at,
  locked_until, last_attempt_at, next_retry_at, sent_at, verified_at,
  last_error_code, last_error_message_redacted, created_at, updated_at
)
SELECT
  id,
  candidate_id,
  CASE WHEN action_type = 'send_template' THEN 'send_message' ELSE action_type END,
  template_id,
  idempotency_key,
  status,
  priority,
  available_at,
  attempts,
  max_attempts,
  locked_by,
  locked_at,
  locked_until,
  last_attempt_at,
  next_retry_at,
  sent_at,
  verified_at,
  last_error_code,
  last_error_message_redacted,
  created_at,
  updated_at
FROM outbound_actions;

DROP TABLE outbound_actions;
ALTER TABLE outbound_actions_v4 RENAME TO outbound_actions;

CREATE INDEX idx_outbound_actions_status_available
  ON outbound_actions(status, available_at, priority, created_at);
CREATE INDEX idx_outbound_actions_dependency
  ON outbound_actions(depends_on_action_id, status);

INSERT INTO schema_migrations(version, name)
VALUES (4, 'typed outbound message and WeChat actions');

COMMIT;

PRAGMA foreign_keys=ON;
