# Production Architecture For Boss Recruiting Automation

Date: 2026-07-11

## Purpose

This document defines the production shape for the Boss-only recruiting follow-up system. The proven low-level primitive is:

```text
Boss API read -> resolve candidate -> Boss Web/Camoufox send -> latest-message verification
```

Production work should now wrap that primitive with durable state, idempotency, controlled queueing, operator controls, and failure recovery.

## Production Principle

Do not let scanning, decisioning, and sending happen in one untracked loop.

The system must first persist what it saw, then persist what it intends to do, then send one queued item at a time, then verify and record the outcome.

```text
scan -> normalize -> decide -> enqueue -> send -> verify -> audit
```

## Target Components

| Component | Responsibility | Current status | Production requirement |
| --- | --- | --- | --- |
| Boss API reader | Read jobs, inbox, candidate details, latest messages, chat history | Exists as CLI/client primitives | Wrap in resumable scanner with per-run audit |
| Normalizer | Convert Boss payloads into stable internal records | Missing | Required for hash-based dedupe and redacted storage |
| Rules engine | Decide send / skip / review | Exists only in dry-run classification | Move to config-driven decision records |
| SQLite state store | Persist candidates, messages, queue, events | Missing | Required before bulk sending |
| Send queue | Hold one intended outbound action per candidate/template | Missing | Required before production sends |
| Browser sender | Send through Boss Web/Camoufox and verify | Exists for one-candidate command | Reuse as queue worker send primitive |
| Operator CLI | Scan, queue, send, retry, status | Partial dry-run only | Add production workflow commands |
| Observability | Counts, failures, stop reasons, audit log | Missing | Required for daily operation |

## High-Level Flow

```text
1. boss workflow sync
   -> reads Boss inbox and latest messages
   -> upserts candidates
   -> stores message fingerprints
   -> records scan events

2. boss workflow classify
   -> evaluates changed candidates against rulesets
   -> records decisions
   -> does not send

3. boss workflow enqueue
   -> turns approved send decisions into outbound action records
   -> creates outbound_actions records or review records
   -> does not send

4. boss workflow send
   -> locks one queued item
   -> resolves fresh candidate context
   -> rechecks duplicate/latest-message state
   -> sends through reply-browser primitive
   -> verifies latest Boss message
   -> marks sent/verified or failed/retryable

5. boss workflow health / queue ls
   -> reports queued, sent, verified, failed, skipped, review

6. boss workflow queue retry
   -> requeues retryable failures within configured attempt limits
```

The word "high volume" should mean durable backlog and backpressure, not parallel sends from one Boss account.

## Code Package Boundary

Create a dedicated workflow package instead of growing `commands/workflow.py` into the engine:

```text
boss_cli/workflow/
  schema.sql
  db.py
  models.py
  redaction.py
  normalizer.py
  poller.py
  rules.py
  planner.py
  sender.py
  ratelimit.py
  metrics.py
```

`boss_cli/commands/workflow.py` should stay the CLI surface. It should call the workflow package and keep the existing `dry-run` command.

## SQLite Data Model

Use SQLite first. It is enough for a local Mac production worker and keeps deployment simple.

Database settings:

```sql
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;
```

Use one transaction per poll batch and one transaction per outbound action transition.

### accounts

One row per Boss account.

```sql
CREATE TABLE accounts (
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
```

### jobs

Boss job/JD records seen under an account.

```sql
CREATE TABLE jobs (
  id INTEGER PRIMARY KEY,
  account_id INTEGER NOT NULL,
  enc_job_id TEXT NOT NULL,
  job_name TEXT,
  active INTEGER NOT NULL DEFAULT 1,
  last_seen_at TEXT NOT NULL,
  UNIQUE(account_id, enc_job_id),
  FOREIGN KEY(account_id) REFERENCES accounts(id)
);
```

### candidates

One row per Boss candidate conversation.

```sql
CREATE TABLE candidates (
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
  FOREIGN KEY(account_id) REFERENCES accounts(id)
);
```

### messages

Deduped message fingerprints. Store hashes and redacted previews by default, not full transcripts.

```sql
CREATE TABLE messages (
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
  UNIQUE(candidate_id, fingerprint),
  FOREIGN KEY(candidate_id) REFERENCES candidates(id)
);
```

### candidate_snapshots

Optional payload summaries for debugging and future rules. Raw encrypted payload storage can be added later if needed, but should default off.

```sql
CREATE TABLE candidate_snapshots (
  id INTEGER PRIMARY KEY,
  candidate_id INTEGER NOT NULL,
  source TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  payload_hash TEXT NOT NULL,
  summary_json TEXT,
  FOREIGN KEY(candidate_id) REFERENCES candidates(id)
);
```

### rulesets

```sql
CREATE TABLE rulesets (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  version TEXT NOT NULL,
  config_json TEXT NOT NULL,
  config_hash TEXT NOT NULL UNIQUE,
  approved INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 0,
  approved_at TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(name, version)
);
```

### decisions

One row per rules evaluation.

```sql
CREATE TABLE decisions (
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
```

Allowed `decision` values:

```text
send
skip_duplicate
needs_review
rejected
error
```

### message_templates

Approved outbound templates.

```sql
CREATE TABLE message_templates (
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
  UNIQUE(name, version)
);
```

### outbound_actions

The production send queue.

```sql
CREATE TABLE outbound_actions (
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
```

Allowed `status` values:

```text
queued
locked
sending
sent_unverified
verified
failed_retryable
failed_terminal
skipped_duplicate
cancelled
needs_review
```

### action_attempts

One row per actual send attempt.

```sql
CREATE TABLE action_attempts (
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
```

### events

Append-only audit trail.

```sql
CREATE TABLE events (
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
```

### rate_limit_buckets

```sql
CREATE TABLE rate_limit_buckets (
  account_id INTEGER NOT NULL,
  bucket TEXT NOT NULL,
  window_start TEXT NOT NULL,
  used INTEGER NOT NULL DEFAULT 0,
  limit_value INTEGER NOT NULL,
  blocked_until TEXT,
  PRIMARY KEY(account_id, bucket, window_start),
  FOREIGN KEY(account_id) REFERENCES accounts(id)
);
```

## Idempotency

Every outbound action must have an idempotency key:

```text
sha256(candidate_id|action_type|template_id|template_version|trigger_message_fingerprint)
```

Rules:

- If an `outbound_actions` row with this key is `verified`, never send it again.
- If Boss chat history already contains the exact template body, mark `skipped_duplicate`.
- If verification fails after a browser click, do not blindly retry until latest-message state has been checked again.
- Template edits must create a new `template_version`.
- If a process crashes after clicking send but before updating SQLite, recovery must re-read latest messages and mark `verified` if the exact body is already present.

## Rate Limits And Volume Controls

Start conservative and only increase after multiple clean runs.

Initial recommended limits:

```text
poll interval: 60-180 seconds with jitter
send concurrency: 1
send delay: minimum 60 seconds until measured safe
max sends per run: 20
max sends per hour: 20
max sends per day: 80
max attempts per queue item: 3
captcha/login failure threshold: stop immediately
verification mismatch threshold: stop immediately
```

The sender must be a single-writer process at first. Parallel browser senders are not recommended until the UI path has stronger isolation.

Fetch chat history only when the latest-message fingerprint changes. Full resume/profile fetch should remain default-off or low-quota because it can trigger candidate-visible side effects.

## Stop Conditions

The queue worker must stop and leave a clear event when any of these happen:

- Boss login prompt appears.
- Captcha or security challenge appears.
- Boss page closes to `about:blank`.
- Candidate row cannot be found.
- Candidate row opens but right-side conversation does not match target.
- Composer text does not exactly match the intended message before clicking send.
- Latest-message verification does not match after send.
- Boss returns rate-limit or risk-control language.
- Daily cap is reached.

## Operator Commands

Production MVP should add these commands:

```bash
boss workflow init-db
boss workflow sync --limit 100 --json
boss workflow classify --rules-file docs/recruiting-workflow/examples/boss-dry-run-rules.json --json
boss workflow enqueue --decision matched --template first_wechat_exchange:v1 --dry-run
boss workflow send --max-actions 20 --engine camoufox --json
boss workflow queue ls --json
boss workflow queue retry --max 10 --json
boss workflow candidate <friendId> --json
boss workflow pause --until "2026-07-12T09:00:00+08:00"
boss workflow resume
boss workflow health --json
```

Optional later command:

```bash
boss workflow run-once --job <encJobId> --rules-file <path> --send-limit 3
boss workflow daemon --scan-interval 300 --send-max-per-cycle 10
```

Do not build the daemon first. Build manual commands first so each phase is inspectable.

## Configuration

Use JSON initially because the repo already has JSON rules examples.

Suggested production config:

```json
{
  "rules_version": "boss-auto-followup-v1",
  "job_filters": {
    "include_encrypt_job_ids": []
  },
  "templates": [
    {
      "template_key": "first_wechat_exchange",
      "version": "2026-07-11.1",
      "approved": true,
      "body": "..."
    }
  ],
  "send_limits": {
    "max_per_run": 20,
    "max_per_hour": 20,
    "max_per_day": 80,
    "min_delay_seconds": 60,
    "max_delay_seconds": 120
  },
  "dedupe": {
    "skip_if_exact_message_seen": true,
    "history_count": 30
  }
}
```

## Deployment Model

Recommended first production environment:

```text
one dedicated Mac user session
  -> boss-cli repo checkout
  -> saved Boss credential
  -> Camoufox runtime installed
  -> SQLite database under .local/boss-workflow/state.db
  -> manual scan/plan/send commands
  -> logs under .local/boss-workflow/logs
```

Only after stable manual operation should this become a scheduled process.

## Monitoring

Minimum daily report:

```text
date
scanned_count
new_candidates
queued_count
sent_count
verified_count
skipped_duplicate_count
needs_review_count
failed_retryable_count
failed_terminal_count
stop_reason
```

The report should be generated from SQLite, not from terminal output.

Alert conditions:

- auth failed
- captcha or risk-control prompt
- queue age exceeds SLA
- latest-message verification failure
- rate-limited more than 3 times in 15 minutes
- browser blocked or redirected to blank page
- repeated SQLite busy failures
- daily quota exhausted

## Production Readiness Checklist

- SQLite schema and migrations exist.
- Scan command upserts candidates without sending.
- Plan command creates queue items without sending.
- Queue command enforces idempotency.
- Send command processes one item at a time.
- Send command verifies latest message.
- Failure states are persisted.
- Operator status command exists.
- Tests cover duplicate prevention, queue transitions, send failures, and verification mismatch.
- Docs include recovery steps for login/captcha/risk-control failures.
