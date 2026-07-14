# Work Log

## 2026-07-15: Alibaba Qwen And Curated Template Validation

### Process

- Replaced the OpenAI-specific selector with Alibaba Model Studio `qwen-plus` through the OpenAI-compatible
  Chat Completions endpoint.
- Configured non-thinking JSON mode and retained local validation of outcome, confidence, and approved IDs.
- Added and approved six immutable automotive/WeChat templates with intent-specific selection guidance.
- Retired the two overlapping legacy active templates in the local workflow database.
- Validated the supplied Alibaba credential with two synthetic conversations; the key was not persisted.

### Results

- The WeChat-request case selected `wechat_candidate_requested` (template ID 5).
- The business/role-context case selected `automotive_warranty_role_context` (template ID 8).
- Both responses were valid constrained selections from the six approved active templates.
- The daemon remained stopped and no BOSS message or WeChat request was sent.

### Verification

- Clean Python 3.13 full suite: `186 passed, 7 skipped`.
- Ruff lint checks passed.
- Wheel and source distribution builds passed.
- Local database and dashboard both reported six active curated templates.
- Repository scan confirmed that the supplied API key was not written into tracked or untracked project files.

### Live Acceptance Test

- Added `--friend-id` so both LLM decisions and queue claims can be restricted to one canary conversation.
- Added inbound `contact_request` eligibility so a candidate's structured WeChat request reaches the selector.
- A dry canary selected `wechat_candidate_requested` (template ID 5) with `0.95` confidence and queued nothing.
- Browser target resolution matched the intended conversation before live mode.
- The exact approved reply was sent through `dom.chat-composer` and verified by the latest-message API.
- The first WeChat attempt stopped because the BOSS UI now requires `同意` before `确定`; no success state
  was observed and the adapter did not claim verification.
- Updated the flow to `换微信` -> `同意` -> `确定`, added completed-exchange indicators, and retried only the
  dependent WeChat action. The already verified reply was not repeated.
- The dependent action verified through the visible `查看微信` indicator. Final states were message
  `verified`, WeChat `verified`, no executable canary queue rows, and daemon `stopped`.

### Additional Hardening

- Missing browser extras now produce `failed_retryable` without cancelling the dependent action.
- Credential health now checks the recruiter API; the live account reports `authenticated=true` even though
  the optional JS-generated token remains unavailable to one job-search endpoint.
- Final verification: `192 passed, 7 skipped`, Ruff passed, and wheel/source builds passed.
- Dashboard cumulative delivery state after the canary: three verified messages, one verified WeChat
  exchange, one older terminal failure, and no queued/locked/sending actions.
- The API key remains absent from repository files and the daemon remains stopped.

### Mac Continuous-Daemon Smoke Test

- Assessed Windows portability. Core libraries have Windows support, but the repo lacks Windows CI, verified
  DPAPI cookie extraction, an interactive-session supervisor, and a real Windows browser acceptance test.
- Started the daemon on macOS in dry mode with one `friendId` canary, a two-second poll interval, and
  `max-actions=0`.
- Completed three polling cycles with `failures=0`, `eligible=0`, `sent=0`, and `wechat_verified=0`.
- Confirmed the running lease, owner ID, heartbeat, completed cycle state, and next-poll timestamp through
  both `daemon-status` and the dashboard health endpoint.
- Sent `SIGINT`; the process exited `0`, recorded `stopped`, cleared its owner and lease, and left no next poll.
- Queue and delivery counts were unchanged by the dry run. No message or WeChat action was performed.

## 2026-07-14: Continuous Automation Pivot

### Process

- Created branch `continuous-recruiting-automation` from the stable batch/dashboard implementation.
- Added schema version 5 for constrained automation decisions, template guidance/retirement, and a single-owner daemon lease with heartbeat state.
- Added an OpenAI Responses selector that can choose only from active approved template IDs and sends only redacted conversation context.
- Added one-cycle orchestration and a continuous daemon around sync, decision, durable planning, verified message sending, and dependent WeChat requests.
- Added dry-by-default `boss workflow daemon`, explicit `--live`, `--once`, signal handling, backoff, and `daemon-status`.
- Removed dashboard sync, selection, enqueue, and sender endpoints; rebuilt it around runtime monitoring, decisions, deliveries, audit state, pause/resume, and immutable template approval.

### Results

- New inbound text is processed without an operator keeping the dashboard open.
- The LLM never generates outbound text; invalid, uncertain, or low-confidence output cannot enqueue an action.
- Dry decisions cannot suppress a later live decision, unchanged trigger/catalog combinations are not repeatedly evaluated, and catalog changes permit reconsideration.
- Message actions are durably queued before decisions are finalized; WeChat remains dependent on verified message delivery.
- Transient model failures back off and retry, while malformed model output becomes review.
- Approving an edited template retires the previous active version with the same name.
- Schema v4 pending queue rows are quarantined as `needs_review`; eight existing local pending actions were quarantined when this already-migrated development database was audited.

### Verification

- `uv run ruff check boss_cli tests/test_workflow_automation.py tests/test_workflow_dashboard.py tests/test_workflow_db.py` passed.
- The repository's existing Python 3.12 pytest runtime crashes in pytest's capture initialization with exit 139 before collection.
- A clean Python 3.13 runner completed the final full suite with `183 passed, 7 skipped`; the focused automation/database/dashboard suite completed with `38 passed`.
- `uv build` produced the `0.5.0` wheel and source distribution.
- Schema version 5 initialized with no foreign-key violations.
- No live BOSS message or WeChat request was sent during this implementation pass.

### Design Notes

- Polling was selected because this repository has no BOSS webhook/event source.
- The dashboard is intentionally outside the execution plane.
- OpenAI requests set `store: false`; only validated decisions and response hashes are persisted.
- The daemon is a foreground long-running process; launchd/systemd/container supervision remains a deployment concern.

## 2026-07-14: Incremental Reading Layer

### Process

- Replaced the one-page dashboard poller with an account-scoped incremental reader.
- Added ordered schema version 3 migration support for existing workflow databases.
- Integrated stable recruiter identity, timestamp, direction, job, message-history, and profile-summary normalization.
- Exposed the same synchronization service through `boss workflow sync` and the dashboard.

### Results

- Inbox pagination is bounded by candidate/page limits and repeated-page detection.
- Candidate details and latest messages are read in batches of at most 50.
- Chat history is fetched only for changed or incomplete conversations and is controlled by a per-run budget.
- Page checkpoints support restart after interruption.
- Candidate workflow stage and do-not-contact state survive synchronization.
- Complete unfiltered scans can mark missing candidates inactive; partial scans cannot.
- Dashboard candidate reads are scoped to the latest synchronized account.

### Verification

- Added migration, account identity, pagination, idempotency, history budget, resume, privacy, profile, inactivity, and CLI integration tests.
- Full non-smoke suite and package build are required before release.
- No live Boss read was run during implementation because a valid authenticated session was not available as a deterministic test fixture.

### Design Notes

- Full resume reads remain outside the synchronization path because they may trigger candidate-visible side effects.
- The next workflow component should consume normalized SQLite state rather than re-reading Boss independently.

## 2026-07-14: Typed Batch Automation And Integrated Dashboard

### Process

- Added schema version 4 with optional templates and action dependencies.
- Expanded the planner to create `send_message` and `exchange_wechat` actions independently or as an ordered pair.
- Added a visible browser-backed WeChat exchange adapter with success-state verification.
- Hardened the queue worker with active-account claims, live latest-message preflight, typed dispatch, delayed retries, dependency cancellation, and uncertain-outcome recovery.
- Rebuilt the local dashboard around inbox sync, candidate filtering and selection, message approval, action composition, queue inspection, and bounded execution.

### Results

- The same approved message can be queued for a selected batch without duplicate action rows.
- WeChat-only actions no longer require placeholder templates.
- Message-plus-WeChat batches cannot execute the exchange until the message is verified or already present.
- Do-not-contact and inactive candidates are excluded from dashboard selection, and the planner enforces the same boundary.
- Expired sending leases move to `needs_review` rather than being replayed.
- Queue summaries, reads, and claims are scoped to the active recruiter account.

### Verification

- `uv run ruff check .` passed.
- `uv run python -m pytest -p no:capture -q -m 'not smoke'` passed with `171 passed, 7 deselected`.
- `uv build` produced the `0.4.0` wheel and source distribution.
- A schema v3 database with an existing action migrated to v4 with no foreign-key violations.
- A seeded dashboard rendered at desktop and mobile widths with no console errors or body overflow.
- Browser interaction verification confirmed filtering, do-not-contact exclusion, action-mode switching, confirmation, and queue creation.

### Design Notes

- No live BOSS messages or WeChat requests were sent during this implementation pass.
- The `换微信` selectors and success labels are based on the validated BOSS Web workflow and must be rechecked after material BOSS UI changes.
- The dashboard remains a trusted localhost, single-operator tool rather than a public service.

## 2026-07-08: Initial Boss-Side Context Layer

### Process

- Cloned and inspected `jackwener/boss-cli`.
- Reviewed the CLI command registration, Boss API client, authentication flow, recruiter commands, structured output helpers, tests, and repository metadata.
- Assessed the requested recruiting workflow against the Boss-only capabilities in this repo.
- Created this documentation folder as the repository-level context layer for future work.

### Results

- Confirmed the repo can read Boss recruiter inboxes, candidate chat history, candidate resumes/profiles, posted jobs, and can send recruiter replies.
- Confirmed the repo does not include an always-running worker, event listener, JD matching rules, workflow persistence, or WeChat automation.
- Added structured documentation:
  - `docs/recruiting-workflow/README.md`
  - `docs/recruiting-workflow/capability-assessment.md`
  - `docs/recruiting-workflow/next-stage-plan.md`
  - `docs/recruiting-workflow/documentation-practice.md`
  - `docs/recruiting-workflow/work-log.md`

### Verification

- Ran repository inspection commands including `git status`, `git remote -v`, `rg`, and targeted source reads.
- Attempted `python -m pytest -q -m 'not smoke'`; collection failed because the current Python environment did not have the declared dependency `qrcode` installed.
- Did not run live Boss smoke tests because they require valid authenticated Boss cookies and may trigger real API activity.

### Design Notes

- Treat `boss-cli` as the Boss Zhipin adapter layer.
- Build orchestration, rules, templates, and persistence as a separate workflow layer.
- Use a user-owned GitHub remote for project-specific documentation and changes instead of pushing directly to the upstream author repository.
- Keep outbound messaging behind dry-run and approved-template controls in any next implementation stage.

### Next Steps

- Push this documentation branch to a user-owned GitHub remote.
- Add a small Boss-only workflow runner with dry-run mode.
- Add SQLite-backed candidate state tracking.
- Add configurable JD matching rules and approved message templates.
- Add tests using mocked Boss CLI/API responses before enabling live actions.

## 2026-07-08: Boss Dry-Run Runner

### Process

- Added a new `boss workflow dry-run` command under the existing Click CLI.
- Kept the first runner read-only: it reads recruiter inbox data, candidate details, last messages, and optionally chat history.
- Left full resume viewing behind an explicit `--fetch-resume` flag because the existing recruiter workflow notes that profile viewing can trigger candidate-facing "viewed" notifications.
- Disabled browser cookie extraction by default for dry-run automation; the command uses saved credentials or `BOSS_COOKIES` unless `--allow-browser-auth` is explicitly passed.
- Added a JSON rules-file example for the effect-commerce/business-development use case.

### Results

- `boss workflow dry-run` can classify candidates as `matched`, `needs_review`, or `rejected`.
- The command reports `sent_messages=0` and marks every candidate with `would_send_message=false`.
- A default keyword rule set is embedded, and a configurable example exists at `docs/recruiting-workflow/examples/boss-dry-run-rules.json`.

### Verification

- Added unit tests for command registration, classification, rules-file validity, and the no-send/no-resume default behavior.
- Live dry-run execution still depends on valid Boss authentication cookies.
- A first live dry-run attempt was interrupted after producing no output because no saved credential existed and automatic browser cookie extraction was blocking. The command now fails fast in that situation unless `--allow-browser-auth` is passed.
- Final local dry-run command completed with a structured `not_authenticated` result because this machine has no saved Boss credential and no `BOSS_COOKIES`. No messages were sent.

### Design Notes

- This is not yet the persistent worker. It is a safe command-level MVP used to validate data access and classification behavior before adding SQLite state and outbound replies.
- The next code step should add persistence so repeated dry runs can skip already-seen candidates and maintain a clear audit trail.

## 2026-07-09: First Real Boss Account Dry Run

### Process

- Started Boss QR login with `uv run boss login --qrcode`.
- The QR login succeeded and saved a local credential file.
- The optional Camoufox `__zp_stoken__` hydration step began downloading a browser runtime, but the dry-run workflow worked with the saved QR credential, so the leftover download/login process was stopped.
- Ran the Boss workflow against the real account in read-only mode with `--limit 1`, then with `--limit 5`.

### Results

- `--limit 1` dry run completed successfully:
  - `ok=true`
  - `total=1`
  - `matched=1`
  - `needs_review=0`
  - `rejected=0`
  - `errors=0`
  - `sent_messages=0`
- `--limit 5` dry run completed successfully:
  - `ok=true`
  - `total=2`
  - `matched=2`
  - `needs_review=0`
  - `rejected=0`
  - `errors=0`
  - `sent_messages=0`
- No replies were sent.
- No full resume/profile fetch was requested.
- No candidate names, IDs, contact values, or message text were recorded in this log.

### Verification

- Confirmed the saved credential exists and contains 5 cookies.
- Confirmed `__zp_stoken__` is still absent from the saved credential.
- Confirmed the dry-run command can read enough recruiter-side data to classify candidates without `__zp_stoken__` for this account/state.
- Confirmed no long-running Boss login or Camoufox process remained after the run.

### Design Notes

- The current MVP is live-readable but still non-sending.
- Because the saved QR credential lacks `__zp_stoken__`, future endpoints may still fail if they require browser-generated token state.
- Before adding send mode, add SQLite state so the workflow can avoid duplicate outreach and preserve a non-PII audit trail.

## 2026-07-09: Reply Capability Test

### Process

- Attempted to send one Boss reply to a single user-selected candidate using `boss recruiter reply`.
- The target was selected from the previously fetched inbox list.
- After the direct reply command failed, attempted a fuller sequence that first enters the chat session with candidate/job/security context and then sends the message.
- Retried the session-entry sequence with both numeric and encrypted candidate identifiers.

### Results

- No message was sent.
- Boss returned `invalid_params` / `缺少必要参数` for the direct CLI reply path.
- The session-entry sequence also failed with `缺少必要参数`, before the send step.
- A read-only last-message check confirmed the candidate conversation did not update to the attempted outbound message.

### Verification

- Checked the CLI reply result JSON.
- Checked the custom session-entry/send sequence result JSON.
- Checked the latest message preview after the attempts.

### Design Notes

- The existing `boss recruiter reply` command is not reliable with the current QR-saved credential.
- The saved credential still lacks `__zp_stoken__`, which may be required by Boss for chat-send endpoints.
- Before enabling workflow send mode, the send primitive must be fixed and tested with full browser-derived Boss web cookies or replaced by verified browser UI automation.

## 2026-07-09: Browser-Backed Reply Adapter

### Process

- Reviewed Boss Web's current JavaScript bundle to understand the normal send path.
- Confirmed normal typed chat messages are sent through Boss Web's websocket client, not the `fastReply/sendReplyMsg` HTTP endpoint.
- Implemented a new browser-backed send adapter in `boss_cli/browser_reply.py`.
- Added `boss recruiter reply-browser` as the preferred command for normal recruiter replies.
- Left the old `boss recruiter reply` command in place but clarified it is legacy/fast-reply behavior.
- Added a dedicated design note at `docs/recruiting-workflow/browser-backed-send.md`.

### Results

- New command:

```bash
boss recruiter reply-browser <friendId> "message" -y --json
```

- New dry-run preview:

```bash
boss recruiter reply-browser <friendId> "message" --dry-run --json
```

- The command resolves target context through the Boss API, then sends through Boss Web's in-page chat bridge:

```text
iBossRoot.chat.sendMessage(message, "text", { uid, friendSource, encryptUid })
```

- After sending, the adapter polls the latest-message API to verify the expected text appears.

### Verification

- `uv run ruff check .` passed.
- `uv run python -m pytest -p no:capture -q tests/test_browser_reply.py` passed.
- `uv run python -m pytest -p no:capture -q -m 'not smoke'` passed.
- Ran one real-account `reply-browser --dry-run --json` against the previously selected candidate. It resolved target context and did not send.

### Design Notes

- Chosen strategy is speed/reliability: API for reading, browser-backed Boss Web for sending.
- Native websocket/protobuf send remains a possible later optimization, but it is not the next practical step.
- Plain Playwright/Chrome can be detected by Boss Web and redirected to `about:blank`; Camoufox is the preferred browser engine for live trials.
- The first live send trial should still be one candidate, one approved message, with verification enabled.

## 2026-07-09: First Live Browser-Backed Reply

### Process

- Ran `reply-browser --dry-run --json` to resolve the selected candidate and confirm no-send mode still worked.
- Ran the first live send using Camoufox and the approved one-candidate message.
- The first live attempt failed before sending because Boss Web loaded the chat page but did not expose `window.iBossRoot.chat`.
- Diagnosed the page state and found Boss Web was usable through the visible DOM:
  - a desktop app download prompt needed to be closed,
  - candidate rows had stable ids using `_<friendId>-<friendSource>`,
  - the composer was exposed as `#boss-chat-editor-input`,
  - the official send button was visible in the chat composer.
- Added a DOM fallback to `boss_cli/browser_reply.py`:
  - close non-critical dialogs,
  - click the resolved candidate row,
  - verify the right-side conversation shows the expected candidate,
  - fill the official composer,
  - verify the composer text,
  - click the official send button,
  - verify through the latest-message API.

### Results

- The second live command succeeded.
- Returned result:
  - `sent=true`
  - `verified=true`
  - `engine=camoufox`
  - `method=dom.chat-composer`
  - latest message matched the exact approved outbound text.

### Verification

- Before the successful send, the latest message still showed the candidate's prior reply, confirming the failed bridge-only attempt did not send.
- After the successful send, the command's latest-message verification matched the approved outbound text.

### Design Notes

- The reliable path is now two-layer browser-backed sending:
  1. use the in-page Boss send bridge when available,
  2. fall back to visible DOM controls when the bridge is not exposed.
- Bulk sending is still not enabled. The next automation step should add state tracking and rate limits before processing multiple candidates.

## 2026-07-12: Workflow State Foundation Implementation

### Process

- Implemented the first production coding slice from `implementation-goal.md`.
- Added a dedicated `boss_cli.workflow` package instead of expanding the CLI command module into the workflow engine.
- Kept the implementation safe: no Boss network reads, no browser automation, and no message sending were added in this step.
- Reviewed the store design so future `sync` workers can run candidate/message upserts inside one explicit transaction.

### Results

- Added SQLite schema and initialization for:
  - accounts, jobs, candidates, messages, candidate snapshots,
  - rulesets, decisions, templates,
  - outbound actions, action attempts, events, and rate-limit buckets.
- Added `WorkflowStore` helpers for:
  - database initialization,
  - account/job/candidate/message upserts,
  - ruleset/template storage,
  - decision recording,
  - outbound action enqueue/claim/verified/failed transitions,
  - queue summaries and event append.
- Added redaction and idempotency helpers for candidate names, message previews, contact-like values, message fingerprints, JSON hashes, and outbound action keys.
- Added `boss workflow init-db --db <path> --json` as the first production workflow command.

### Verification

- `uv run python -m pytest -p no:capture -q tests/test_workflow_db.py tests/test_workflow_redaction.py tests/test_workflow.py -m 'not smoke'`
  - `19 passed`
- `uv run ruff check .`
  - passed
- `uv run python -m pytest -p no:capture -q -m 'not smoke'`
  - `135 passed, 7 deselected`
- Plain pytest capture still segfaults in the local pytest capture plugin before tests run; using `-p no:capture` remains the verified local test path.

### Design Notes

- This completes the Agent 1 foundation from the implementation plan.
- The next coding step should add read-only `boss workflow sync` using the new store, normalizer, and Boss API mocks.
- Live sending should remain blocked until `sync`, `classify`, and `enqueue` are backed by persisted, idempotent outbound actions.

## 2026-07-12: Production Dashboard Planning

### Process

- Reviewed the production architecture, implementation goal, and current SQLite state foundation.
- Designed a dashboard as an operator surface over the same durable workflow state and queue, not as a separate direct-send tool.
- Mapped the requested start/stop and monitoring capabilities to safe workflow phases: sync, review, select, approve template, enqueue, send, verify, audit.

### Results

- Added `docs/recruiting-workflow/dashboard-design.md`.
- Updated the documentation index in `docs/recruiting-workflow/README.md`.
- Defined the dashboard's main screens:
  - Control Room
  - Inbox Review
  - Message Composer
  - Queue And Sending
  - Events And Audit
- Defined proposed dashboard API endpoints and SQLite additions for run tracking, operator selections, and workflow settings.

### Verification

- This was a design/documentation step only.
- No Boss reads, browser automation, or message sending were run.

### Design Notes

- The dashboard should not be implemented as a bulk sender that bypasses persisted outbound actions.
- The dashboard MVP should wait until `sync`, `classify`, `enqueue`, `send`, and `health` exist as workflow services or CLI commands.
- The first deployable dashboard should be local-only on `127.0.0.1` until authentication and remote process controls are explicitly added.

## 2026-07-12: Production Dashboard MVP Implementation

### Process

- Built the local dashboard as a control layer over the SQLite workflow state and outbound queue.
- Added workflow service modules for inbox sync, normalization, planning/enqueue, dashboard summaries, and queue sending.
- Added a stdlib HTTP server and static frontend instead of adding web framework dependencies.
- Kept live sending behind persisted queue actions and a single sender thread.

### Results

- Added `boss dashboard --db <path> --host 127.0.0.1 --port 8765`.
- Added dashboard screens for:
  - system status,
  - sync controls,
  - candidate review and mass selection,
  - message template approval,
  - queue monitoring,
  - event monitoring,
  - sender start/pause/resume/stop.
- Added dashboard API endpoints for health, sync, candidates, templates, enqueue, queue, events, runs, and sender controls.
- Added SQLite v2 dashboard control tables:
  - `workflow_runs`
  - `operator_selections`
  - `workflow_settings`

### Verification

- `uv run ruff check .`
  - passed
- `uv run python -m pytest -p no:capture -q -m 'not smoke'`
  - `140 passed, 7 deselected`
- `uv build`
  - built sdist and wheel successfully
- Local dashboard smoke:
  - started `boss dashboard --db /tmp/boss-dashboard-smoke.db --host 127.0.0.1 --port 8876 --no-open`
  - `GET /api/health` returned schema version 2 and an empty queue summary
- Confirmed the wheel includes:
  - `boss_cli/workflow/schema.sql`
  - dashboard `index.html`
  - dashboard `app.css`
  - dashboard `app.js`

### Design Notes

- The dashboard can start a real browser-backed sender, but only for rows already enqueued as outbound actions.
- The dashboard does not bypass idempotency keys, duplicate checks, queue status, or event logging.
- Plain pytest capture still segfaults locally before tests run; continue using `-p no:capture`.
- Future work should add CLI parity for the new service modules, richer classification, retry/cancel queue controls, and UI visual tests.

## 2026-07-12: Dashboard Live Connection Attempt

### Process

- Confirmed the dashboard backend was running on `127.0.0.1:8765`.
- Called dashboard `/api/health`; schema version 2 loaded with sender idle.
- Called dashboard `/api/sync` to connect the UI to live Boss inbox reads.
- Boss returned `code=7`, meaning the saved login state had expired.
- Updated the Boss client to treat `code=7` as an authentication/session-expired condition, matching `code=37`.
- Updated dashboard sync to use the CLI's existing `run_client_action` auth refresh path.
- Started two fresh QR login attempts for the operator to scan and confirm; both expired before phone confirmation.

### Results

- Dashboard backend is connected to the workflow database and operational.
- Live Boss sync is blocked only by missing/expired Boss authentication.
- The dashboard recorded failed sync events with redacted summaries.

### Verification

- `uv run boss status --json`
  - returned `authenticated=false` and `credential_present=false`
- `uv run ruff check .`
  - passed
- `uv run python -m pytest -p no:capture -q -m 'not smoke'`
  - `141 passed, 7 deselected`

### Design Notes

- No candidates were synced because Boss authentication is not currently valid.
- No messages were enqueued or sent.
- Next operator step is a successful `boss login --qrcode`, then rerun dashboard `Sync Inbox`.

## 2026-07-11: Production Architecture Planning

### Process

- Created a tracked goal for the production-level Boss recruiting automation design.
- Spawned a read-only production-design sub-agent to review the current repo state and propose a high-volume architecture.
- Inspected the existing workflow dry-run code and browser-backed send adapter.
- Wrote production architecture documentation and an implementation-ready goal for coding agents.
- Integrated the sub-agent's recommendations for WAL-backed SQLite, workflow package boundaries, richer queue/action tables, idempotency, rate limits, and operator commands.

### Results

- Added `docs/recruiting-workflow/production-architecture.md`.
- Added `docs/recruiting-workflow/implementation-goal.md`.
- Updated the documentation index in `docs/recruiting-workflow/README.md`.
- Defined the production command direction:

```bash
boss workflow init-db
boss workflow sync
boss workflow classify
boss workflow enqueue
boss workflow send
boss workflow queue ls
boss workflow health
```

### Verification

- The planning documents are grounded in current working primitives:
  - Boss API reads in `boss_cli/client.py`
  - dry-run classifier in `boss_cli/commands/workflow.py`
  - verified browser send in `boss_cli/browser_reply.py`

### Design Notes

- Production should be queue-backed and stateful before any bulk sending.
- The next coding goal is not "send more"; it is SQLite state, redaction, normalization, and idempotent sync.
- Bulk send should only happen through persisted outbound actions with exact-message dedupe and latest-message verification.

## 2026-07-10: Confirmed Bulk Send To Existing Boss Conversations

### Process

- Received explicit confirmation to send the approved Boss reply to all pending existing conversations.
- Re-ran a read-only preflight before sending:
  - checked current Boss conversations,
  - checked recent history for the exact approved message,
  - skipped conversations that already had the exact message,
  - stopped before sending if the pending count changed from the confirmed count.
- Used one Camoufox browser session and the DOM chat-composer fallback.
- Sent sequentially with short delays between candidates.
- Verified each candidate immediately after send through the latest-message API.
- Stopped condition was configured as first send/verification failure; no failure occurred.

### Results

- Current conversations checked in preflight: 12.
- Already had the exact approved message: 1.
- Pending sends confirmed: 11.
- Sent: 11.
- Verified after send: 11.
- Skipped as already sent: 1.
- Final read-only history verification:
  - checked: 12.
  - matched exact approved message: 12.
  - missing: 0.

### Verification

- Every live send returned `verified=True` from the latest-message API.
- A final read-only history pass confirmed all current conversations contained the exact approved message.

### Design Notes

- The browser-backed DOM composer path is validated for one-by-one and small batch sending.
- A production bulk mode should still be added as a first-class CLI command with persisted state, rate limits, run IDs, and redacted audit logs before doing larger batches.
