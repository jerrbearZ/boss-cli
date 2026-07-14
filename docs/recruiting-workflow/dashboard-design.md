# Production Dashboard Design

> Historical operator-driven design. Schema v5 pivots the dashboard to monitoring, pause/resume, and template approval only. See [continuous-automation.md](./continuous-automation.md).

Date: 2026-07-12

## Implementation Status

Implemented MVP on 2026-07-12:

- Local dashboard command: `boss dashboard`.
- Local API over SQLite workflow state.
- Inbox sync endpoint.
- Candidate review and mass selection UI.
- Template approval UI.
- Enqueue selected candidates into persisted outbound actions.
- Start/pause/resume/stop controls for a single background sender.
- Queue, health, event, and run monitoring views.

Remaining production hardening:

- Add CLI parity for all workflow service operations.
- Add richer classification/rules UI.
- Add explicit retry/cancel queue controls in the UI.
- Add authentication if serving beyond `127.0.0.1`.
- Add browser-based visual regression tests for the dashboard UI.

## Purpose

The dashboard should be the human operator surface for the Boss production workflow.

It has two jobs:

1. Control when the production workflow reads, queues, sends, pauses, resumes, or stops.
2. Show exactly what happened: what was read, what was selected, what message was approved, what was queued, what was sent, what was verified, what failed, and why.

The dashboard must sit on top of the same durable workflow state and queue used by the CLI. It must not send directly from selected UI rows.

## Non-Negotiable Safety Rule

No UI button should combine reading, selecting, composing, queueing, and sending into one untracked action.

The production path must stay:

```text
sync/read -> review/select -> approve template -> enqueue -> start sender -> verify -> audit
```

This keeps bulk sending recoverable and prevents accidental duplicate outreach.

## Recommended Product Shape

Build a local-only web dashboard served from the operator machine.

Initial deployment:

```text
127.0.0.1 dashboard
  -> FastAPI or Flask backend
  -> SQLite workflow database
  -> Boss CLI/workflow package
  -> one controlled sender process
  -> Boss Web/Camoufox browser session
```

The first version should be local and private. Do not expose it publicly until authentication, authorization, and remote process hardening exist.

## Main Screens

### 1. Control Room

Purpose: show the current system state and provide safe start/stop controls.

Primary controls:

- `Sync inbox`: reads current Boss messages and candidate state into SQLite. No sending.
- `Classify`: runs rules and records decisions. No sending.
- `Open review`: takes the operator to candidate selection.
- `Start sender`: starts sending already queued actions, one at a time.
- `Pause`: stops claiming new queue items after the current item finishes.
- `Emergency stop`: terminates the active sender and leaves locked actions recoverable.
- `Resume`: clears pause state and allows the sender to continue.

Status indicators:

- Boss auth status.
- Browser sender status.
- Current run id.
- Last sync time.
- Queue depth.
- Sends today/hour.
- Current cap remaining.
- Oldest queued item age.
- Stop reason, if any.

Important behavior:

- `Start sender` should be disabled when there are no queued actions.
- `Start sender` should require a visible summary of how many messages will be sent and which template version will be used.
- `Emergency stop` should never delete queue rows. It should only stop active processing.

### 2. Inbox Review

Purpose: read existing messages, inspect candidates, and select in mass which conversations should receive a reply.

Table columns:

- Selection checkbox.
- Candidate display name, redacted by default.
- Job.
- Last inbound time.
- Last message preview, redacted.
- Decision status: matched, needs review, rejected, duplicate, already replied.
- Current workflow stage.
- Existing outbound status.
- Risk flags: duplicate template seen, do-not-contact, missing target data, auth needed.

Filters:

- Job.
- Decision status.
- New since last sync.
- Unread/latest inbound.
- Not yet replied.
- Needs review.
- Already replied.
- Failed/retryable.

Mass selection controls:

- Select all visible.
- Select all matching filter.
- Clear selection.
- Exclude candidates already containing the exact template text.
- Exclude candidates with failed terminal status.
- Exclude candidates marked do-not-contact.

Candidate detail side panel:

- Redacted message timeline.
- Candidate/job metadata.
- Latest classification decision and evidence.
- Existing outbound actions.
- Attempt history.
- Event timeline.

Important behavior:

- Selecting rows does not send and does not enqueue by itself.
- The UI must display the exact number selected before the operator can enqueue.
- The UI should always show whether a row was selected manually or by mass-selection rule.

### 3. Message Composer

Purpose: decide exactly what message content will be used.

Controls:

- Template name.
- Template version.
- Message body editor.
- Approval checkbox or explicit `Approve template` action.
- Preview selected candidates.
- Duplicate check against stored messages.
- Character count.

Rules:

- A sendable message must become an approved `message_templates` row.
- Editing text creates a new template version.
- The dashboard should not mutate an existing approved template body.
- The outbound idempotency key must include template id/version and trigger message fingerprint.

Recommended message flow:

```text
draft message -> preview -> approve template version -> enqueue selected candidates
```

The dashboard should show:

- Exact body that will be sent.
- Template version.
- Number of candidates selected.
- Number skipped because exact message was already seen.
- Number requiring review.
- Number that will become queued outbound actions.

### 4. Queue And Sending

Purpose: show what has been queued and control active sending.

Queue statuses:

- queued
- locked
- sending
- sent_unverified
- verified
- failed_retryable
- failed_terminal
- skipped_duplicate
- cancelled
- needs_review

Actions:

- Start sender for max N actions.
- Pause after current action.
- Emergency stop.
- Retry retryable failures.
- Cancel selected queued actions.
- Recheck latest messages for selected items.

Important behavior:

- The sender should claim one queued action at a time.
- The sender should re-read latest messages before sending.
- If the exact message is already present, mark `skipped_duplicate`.
- If latest-message verification mismatches after send, stop the worker.
- If login, captcha, risk-control, or browser blank-page conditions appear, stop immediately.

### 5. Events And Audit

Purpose: answer "what exactly happened?"

Views:

- Run timeline.
- Candidate timeline.
- Queue item timeline.
- Error timeline.
- Daily summary.

Every important action should create an event:

- dashboard opened
- sync started/completed/failed
- classification started/completed/failed
- rows selected
- template drafted
- template approved
- actions enqueued
- sender started
- action claimed
- duplicate skipped
- send attempted
- send verified
- send failed
- sender paused
- sender stopped
- operator retry/cancel

Each event should include:

- run id
- event type
- severity
- redacted summary
- candidate/action id when relevant
- operator id if available
- timestamp
- structured details JSON

Do not store raw cookies, raw phone numbers, raw WeChat ids, or full private chat transcripts in audit events.

## Backend Architecture

Recommended first implementation:

```text
boss_cli/dashboard/
  app.py
  routes.py
  schemas.py
  process_control.py
  views/
  static/

boss_cli/workflow/
  db.py
  normalizer.py
  poller.py
  rules.py
  planner.py
  sender.py
  metrics.py
```

Use the dashboard backend as an API and operator UI over `boss_cli.workflow`.

Do not duplicate business logic in the frontend. The backend should call workflow services that are also used by CLI commands.

## Process Control Model

The dashboard needs two different concepts:

### Pause

Pause is a persistent workflow setting.

Behavior:

- Do not claim new queue actions.
- Let the current action finish when possible.
- Record `paused` event.
- UI shows paused state and reason.

### Emergency Stop

Emergency stop is a process action.

Behavior:

- Stop the sender process.
- Leave current queue state recoverable.
- Record `emergency_stop_requested` and final sender result.
- On next start, stale locks should be recovered by checking latest messages before retrying.

## API Endpoints

Initial local dashboard API:

```text
GET  /api/health
POST /api/sync
POST /api/classify
GET  /api/candidates
GET  /api/candidates/{id}
POST /api/selections
POST /api/templates
POST /api/templates/{id}/approve
POST /api/enqueue
GET  /api/queue
POST /api/sender/start
POST /api/sender/pause
POST /api/sender/resume
POST /api/sender/stop
POST /api/queue/retry
POST /api/queue/cancel
GET  /api/events
GET  /api/runs
GET  /api/metrics/summary
```

CLI parity should remain:

```text
dashboard action              CLI equivalent
sync inbox                    boss workflow sync
classify                      boss workflow classify
enqueue selected              boss workflow enqueue
start sender                  boss workflow send
pause/resume                  boss workflow pause/resume
queue view                    boss workflow queue ls
health                        boss workflow health
```

## Data Model Additions

The current SQLite foundation already has candidates, messages, decisions, templates, outbound actions, action attempts, events, and rate-limit buckets.

Add these before dashboard production use:

### workflow_runs

Tracks sync, classify, enqueue, and send runs.

```sql
CREATE TABLE workflow_runs (
  id TEXT PRIMARY KEY,
  run_type TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  requested_by TEXT,
  stop_reason TEXT,
  summary_json TEXT
);
```

### operator_selections

Tracks mass selection decisions.

```sql
CREATE TABLE operator_selections (
  id INTEGER PRIMARY KEY,
  run_id TEXT NOT NULL,
  candidate_id INTEGER NOT NULL,
  selected INTEGER NOT NULL,
  selection_source TEXT NOT NULL,
  reason_code TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(candidate_id) REFERENCES candidates(id)
);
```

### workflow_settings

Tracks pause state and operational settings.

```sql
CREATE TABLE workflow_settings (
  key TEXT PRIMARY KEY,
  value_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
```

## Monitoring Metrics

Top-level dashboard metrics:

- candidates scanned today
- new candidates today
- selected candidates
- queued actions
- verified sends
- skipped duplicates
- retryable failures
- terminal failures
- current send rate
- sends used in hourly cap
- sends used in daily cap
- oldest queued age
- latest sync age
- current stop reason

Per-run summary:

```text
run_id
run_type
started_at
finished_at
status
scanned_count
new_candidate_count
selected_count
queued_count
sent_count
verified_count
skipped_duplicate_count
failed_retryable_count
failed_terminal_count
stop_reason
```

## Implementation Order

Do not build the dashboard before the missing workflow services exist.

Recommended order:

1. Add `sync`, `normalizer`, and persisted classification.
2. Add template approval, selection persistence, and enqueue service.
3. Add queue sender service with pause/resume/stop and run ids.
4. Add health/metrics queries.
5. Build a local dashboard backend over the same workflow services.
6. Build the UI screens.
7. Add end-to-end tests with fake Boss API and fake sender.

The first dashboard prototype can read from a seeded SQLite database, but it should not expose live sending until steps 1-4 are implemented.

## Recommended MVP

Build the dashboard MVP only after the CLI can do:

```bash
boss workflow sync --json
boss workflow classify --json
boss workflow enqueue --json
boss workflow send --max-actions N --json
boss workflow health --json
```

Dashboard MVP scope:

- Control Room.
- Inbox Review with filters and mass selection.
- Message Composer with template approval.
- Queue view.
- Sender start/pause/stop.
- Event timeline.

Keep authentication simple for local-only use at first. If exposed beyond localhost, add user authentication before any send controls are enabled.
