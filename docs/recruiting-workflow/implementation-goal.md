# Implementation Goal: Production Boss Follow-Up Engine

Date: 2026-07-11

## Goal Statement

Build a production-ready Boss-only follow-up engine around the proven read/send primitives.

The engine must support high-volume incoming Boss conversations by scanning inbox state, persisting candidate state, deciding which candidates should receive an approved template, queueing sends with idempotency, sending through the existing browser-backed reply adapter, verifying delivery through Boss APIs, and exposing operator commands for status and recovery.

## Non-Goals

- Do not automate WeChat in this phase.
- Do not build multi-platform adapters in this phase.
- Do not reverse-engineer Boss websocket/protobuf sending in this phase.
- Do not build an always-on daemon before manual scan/plan/send commands are stable.
- Do not bulk-send without SQLite idempotency and verification.

## Existing Primitives To Reuse

- Boss API client: `boss_cli/client.py`
- Dry-run rules/classification: `boss_cli/commands/workflow.py`
- Browser-backed verified send: `boss_cli/browser_reply.py`
- Recruiter CLI group: `boss_cli/commands/recruiter.py`
- Structured output helpers: `boss_cli/commands/_common.py`

## Target Operator Flow

```bash
boss workflow init-db
boss workflow sync --limit 100 --json
boss workflow classify --rules-file docs/recruiting-workflow/examples/boss-dry-run-rules.json --json
boss workflow enqueue --decision matched --template first_wechat_exchange:v1 --dry-run
boss workflow queue ls --json
boss workflow send --max-actions 20 --engine camoufox --json
boss workflow health --json
```

## Implementation Status

- Agent 1 state foundation: implemented on 2026-07-12.
- Dashboard MVP service/UI layer: implemented on 2026-07-12.
- Partially implemented service modules: sync normalizer, inbox poller, planner/enqueue, and queue sender.
- Next unimplemented package: CLI parity and persisted classification commands.

## Acceptance Criteria

The production MVP is complete when all of these are true:

- A SQLite database can be initialized and migrated.
- Candidate sync can be rerun without duplicating candidate or message records.
- Classification and enqueue can be rerun without duplicating outbound action rows.
- A queue item has a stable idempotency key.
- Already-verified idempotency keys are never sent again.
- Existing chat history containing the exact template body is marked `skipped_duplicate`.
- `send --max-actions N` sends at most `N` queued items.
- Each sent item is verified through latest-message API.
- Failed sends persist status, error code, error message, and attempt count.
- Verification mismatch stops the worker instead of continuing through the queue.
- Operator status shows queued, verified, failed, skipped, and review counts.
- Unit tests cover state transitions and duplicate prevention.
- Live sending remains opt-in through `boss workflow send`, not part of `sync`, `classify`, or `enqueue`.

## Agent Work Packages

These are designed for coding agents to implement with minimal overlap.

### Agent 1: SQLite State Layer And Redaction

Ownership:

- Create `boss_cli/workflow/schema.sql`
- Create `boss_cli/workflow/db.py`
- Create `boss_cli/workflow/models.py`
- Create `boss_cli/workflow/redaction.py`
- Create tests in `tests/test_workflow_db.py` and `tests/test_workflow_redaction.py`
- Do not edit browser sending code.

Responsibilities:

- Define database path resolution.
- Implement `init_db` with WAL, foreign keys, busy timeout, and migrations.
- Implement schema creation and `schema_migrations` table.
- Implement candidate, job, and message upsert.
- Implement event append.
- Implement ruleset and template upsert/read.
- Implement outbound action insert/read/update helpers.
- Implement redaction and hashing helpers for candidate names, message text, and payload fingerprints.

Required APIs:

```python
init_db(path: Path | None = None) -> WorkflowStore
WorkflowStore.upsert_account(...)
WorkflowStore.upsert_job(...)
WorkflowStore.upsert_candidate(...)
WorkflowStore.upsert_message(...)
WorkflowStore.record_decision(...)
WorkflowStore.enqueue_action(...)
WorkflowStore.claim_next_action(...)
WorkflowStore.mark_action_verified(...)
WorkflowStore.mark_action_failed(...)
WorkflowStore.queue_summary()
WorkflowStore.append_event(...)
```

Acceptance tests:

- `init_db` creates all tables.
- candidate upsert is idempotent by `(account_id, friend_id, friend_source)`.
- message upsert is idempotent by `(candidate_id, fingerprint)`.
- outbound action insert is idempotent by `idempotency_key`.
- action status transitions persist.
- redaction helpers do not return raw phone/contact-looking values in summaries.

### Agent 2: Sync, Normalizer, And Classification Commands

Ownership:

- Extend `boss_cli/commands/workflow.py`
- Create `boss_cli/workflow/normalizer.py`
- Create `boss_cli/workflow/poller.py`
- Create `boss_cli/workflow/rules.py`
- Tests in `tests/test_workflow_production_commands.py`
- Use workflow store APIs from Agent 1.

Responsibilities:

- Add `boss workflow init-db`.
- Add `boss workflow sync`.
- Add `boss workflow classify`.
- Reuse existing dry-run candidate collection/classification where practical.
- Store redacted candidate summaries and event rows.
- Fetch chat history only when latest-message fingerprint changes.
- Do not send and do not enqueue from `sync`.

Command behavior:

```bash
boss workflow init-db --db .local/boss-workflow/state.db
boss workflow sync --limit 100 --db .local/boss-workflow/state.db --json
boss workflow classify --rules-file <path> --db <path> --json
```

Acceptance tests:

- `sync` calls Boss API mocks and upserts candidates/messages.
- `sync` sends no messages.
- `sync` does not duplicate rows on rerun.
- `classify` records decisions for changed inputs.
- `classify` does not duplicate decisions for identical input fingerprints.

### Agent 3: Planner And Queue Commands

Ownership:

- Extend `boss_cli/commands/workflow.py`
- Create `boss_cli/workflow/planner.py`
- Tests in `tests/test_workflow_planner.py`

Responsibilities:

- Add `boss workflow enqueue`.
- Add `boss workflow queue ls`.
- Load approved message templates.
- Convert `send` decisions into outbound actions.
- Create idempotency keys from candidate/action/template/trigger message fingerprint.
- Mark exact-message duplicates as `skipped_duplicate`.
- Keep enqueue dry-run available.

Command behavior:

```bash
boss workflow enqueue --decision matched --template first_wechat_exchange:v1 --db <path> --json
boss workflow queue ls --db <path> --json
```

Acceptance tests:

- enqueue creates outbound actions for matching decisions.
- enqueue refuses unapproved or missing templates.
- enqueue does not duplicate actions on rerun.
- exact template already seen in messages marks `skipped_duplicate`.

### Agent 4: Queue Sender Command

Ownership:

- Extend `boss_cli/commands/workflow.py`
- Create `boss_cli/workflow/sender.py`
- Create `boss_cli/workflow/ratelimit.py`
- Tests in `tests/test_workflow_sender.py`
- Reuse `boss_cli/browser_reply.py`; do not reimplement browser sending.

Responsibilities:

- Add `boss workflow send`.
- Claim queued items one at a time.
- Resolve candidate send target.
- Call `send_boss_message_via_browser`.
- Mark verified/failed states.
- Enforce `--max`, attempt limits, and stop-on-terminal failure.
- Re-read latest messages before sending to recover from crash-after-click states and skip already-sent messages.
- Add jittered delay options, but keep tests deterministic by injectable sleeper/random.

Command behavior:

```bash
boss workflow send --max-actions 20 --engine camoufox --db <path> --json
```

Acceptance tests:

- sends no more than `--max-actions`.
- verified send updates queue row to `verified`.
- browser failure updates row to retryable or terminal based on error code.
- verification mismatch stops the worker.
- already verified rows are skipped.
- hourly/daily caps prevent sending.

### Agent 5: Status, Retry, And Operator Recovery

Ownership:

- Extend `boss_cli/commands/workflow.py`
- Tests in `tests/test_workflow_operations.py`
- Documentation updates under `docs/recruiting-workflow`.

Responsibilities:

- Add `boss workflow health`.
- Add `boss workflow queue retry`.
- Add `boss workflow queue cancel`.
- Add `boss workflow candidate <friendId>`.
- Add `boss workflow pause` and `boss workflow resume` using a settings table or lock file.
- Write recovery runbook for login/captcha/rate-limit failures.

Acceptance tests:

- status reports counts by queue status and current pause state.
- retry-failed requeues retryable failures under max attempts.
- candidate command redacts private fields by default.
- pause prevents send from processing items.

### Agent 6: Production Documentation And Verification

Ownership:

- `docs/recruiting-workflow/production-runbook.md`
- Update `README.md` docs map if needed.
- Cross-check tests and command help.

Responsibilities:

- Document daily operator workflow.
- Document first-run setup.
- Document failure recovery.
- Document safe volume ramp.
- Document what not to automate yet.

Acceptance checks:

- `uv run ruff check .`
- `uv run python -m pytest -p no:capture -q -m 'not smoke'`
- `boss workflow --help` shows production commands.

## Suggested Implementation Order

1. Agent 1: SQLite state layer.
2. Agent 2: sync/classify commands.
3. Agent 3: planner/enqueue/queue commands.
4. Agent 4: send command and rate limits.
5. Agent 5: status/retry/pause operations.
6. Agent 6: production runbook and final verification.

Do not start Agent 4 before Agents 1-3 exist. Sending must be backed by persisted queue state and idempotent outbound actions.

## Error Policy

Classify browser/API failures into stable operational outcomes:

| Error class | Queue result | Worker behavior |
| --- | --- | --- |
| login required | `failed_terminal` | stop run |
| captcha/risk control | `failed_terminal` | stop run |
| target not visible | `failed_retryable` | continue only if under mismatch threshold |
| target not verified | `failed_terminal` | stop run |
| editor verify failed | `failed_terminal` | stop run |
| latest-message mismatch | `sent_unverified` | stop run |
| network timeout before click | `failed_retryable` | continue if attempts remain |
| exact message already seen | `skipped_duplicate` | continue |
| hourly/daily quota exhausted | leave queued | stop run cleanly |

## Data Privacy Policy

- Store candidate names as redacted display text and optional hash.
- Do not store full chat transcripts by default.
- Store message previews only when needed for operator review.
- Never store cookies in the workflow database.
- Do not print raw candidate data in final summaries.

## Definition Of Done

This goal is done when a local operator can safely process a high-volume Boss inbox with:

```text
sync -> classify -> enqueue -> inspect queue -> send limited batch -> verify status -> retry or review failures
```

and every candidate/message decision is persisted in SQLite with a non-duplicating idempotency key.
