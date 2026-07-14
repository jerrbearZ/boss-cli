# BOSS Recruiting Automation: Project Evolution Visual Brief

Date: 2026-07-15

## Purpose Of This Document

This document summarizes how the repository evolved from a BOSS Zhipin command-line client into a locally
stateful, continuously running recruiting automation system. It is written as source material for an image
generator producing a polished architecture-and-timeline graphic.

## One-Sentence Story

We transformed a collection of authenticated BOSS CLI commands into a privacy-aware automation loop that
continuously reads recruiter conversations, stores normalized state in local SQLite, uses Alibaba Qwen to
select only pre-approved replies, verifies message and WeChat actions through BOSS Web, and exposes a
dashboard for supervision rather than execution.

## Project Timeline

| Date | Milestone | Outcome |
| --- | --- | --- |
| March 2026 | Core CLI foundation | Authentication, search, recommendations, structured output, browser support, tests, and packaging. |
| April 2026 | Recruiter mode | Jobs, recruiter inbox, chat history, profiles/resumes, and recruiter-side action commands. |
| July 8 | Workflow assessment | Repository review, capability map, target recruiting flow, documentation standards, and first implementation plan. |
| July 8-9 | Dry-run and live foundations | Read-only workflow evaluation, first real-account read, reply-path investigation, and browser-backed verified sending. |
| July 10 | Early bulk validation | Confirmed a bounded live batch reply run and documented the need for persistent production controls. |
| July 11-12 | State and dashboard | Production architecture, versioned SQLite store, dashboard design, and first operator-driven dashboard implementation. |
| July 14 | Reader and typed actions | Incremental account-scoped ingestion, migrations, typed reply/WeChat actions, dependencies, recovery, and rebuilt operations dashboard. |
| July 14 | Continuous-automation pivot | Single-owner polling daemon, constrained model decisions, durable planning, and dashboard moved outside execution. |
| July 15 | Qwen and approved catalog | Alibaba `qwen-plus`, seven immutable templates, privacy boundary, dry/live isolation, and candidate-scoped canary controls. |
| July 15 | Live acceptance and full run | End-to-end canary, macOS daemon smoke test, 10 verified live replies, 9 new verified WeChat requests, and one existing exchange safely skipped. |

## 1. The Existing Structure At The Beginning

### Inherited BOSS CLI foundation

The repository began as a Python CLI and reverse-engineered BOSS Zhipin API client. Its original strengths
were authenticated access and individual commands, not workflow orchestration.

It already provided:

- Cookie, browser-cookie, environment, and QR-code authentication.
- Candidate search and recommendation commands.
- Recruiter-side jobs, inbox, candidate details, chat history, and resume access.
- Individual recruiter actions such as greetings, replies, resume requests, interviews, phone exchange, and
  WeChat exchange.
- JSON/YAML output, request pacing, error handling, and cross-platform packaging work.

It did not yet provide:

- A durable local workflow database.
- Incremental inbox checkpoints or message deduplication.
- A dashboard.
- A continuously running process.
- Approved message-template governance.
- Reliable browser-backed normal chat sending with post-send verification.
- Idempotent batch planning, dependency ordering, audit events, or recovery state.

### First workflow concept: three operator-driven components

Our initial architecture separated the recruiting workflow into three sections:

1. **Reader:** read BOSS recruiter data and convert it into a local SQL-like SQLite database.
2. **Dashboard:** display candidates, messages, workflow state, templates, and queue state from SQLite.
3. **Automation:** let an operator select conversations, approve a reply, run batch sends, and request WeChat.

Initial flow:

```text
BOSS CLI/API -> incremental reader -> local SQLite -> dashboard -> operator action -> reply + WeChat request
```

This was stateful and recoverable, but still semi-automatic. New messages were only acted on when an operator
was watching the dashboard and started the next step.

## 2. What We Built Before The Pivot

### Discovery and documentation

- Reviewed the complete repository, recruiter commands, authentication, tests, and existing documentation.
- Assessed supported and missing recruiting capabilities.
- Created a durable documentation layer covering architecture, implementation goals, dashboard design,
  operational procedures, safety rules, and a chronological work log.

### Verified BOSS reading and reply foundations

- Added a dry-run workflow command for evaluating recruiter conversations without sending.
- Ran the first real-account inbox read and documented payload behavior and authentication constraints.
- Confirmed that the legacy fast-reply HTTP endpoint was unsuitable for normal typed chat.
- Built a browser-backed reply adapter using BOSS Web and Camoufox.
- Added a DOM composer fallback and verified sent text through the BOSS latest-message API.
- Completed an early confirmed bulk reply run before introducing the final persistent workflow engine.

### Local workflow state and reader

- Added a versioned SQLite workflow store.
- Added normalized tables for accounts, jobs, candidates, messages, snapshots, templates, decisions, actions,
  runs, events, settings, and daemon state.
- Built an account-scoped incremental inbox reader with pagination bounds, page checkpoints, duplicate-page
  detection, bounded history enrichment, deterministic message fingerprints, and restart recovery.
- Kept full resume reads outside automatic synchronization because they may create candidate-visible effects.
- Added redaction and hashing boundaries so cookies, raw API payloads, full transcripts, phone numbers, and
  WeChat IDs are not stored by this workflow.

### First dashboard and typed batch automation

- Built a localhost dashboard reading the same SQLite state.
- Added template creation and approval, candidate filtering, queue inspection, and execution monitoring.
- Added typed `send_message` and `exchange_wechat` actions.
- Enforced `send_message -> exchange_wechat` dependencies.
- Added idempotency keys, live duplicate preflight, delayed retries, terminal dependency cancellation, and
  uncertain-outcome review states.
- Added visible BOSS Web `换微信` execution and success-state verification.

At this point the system was much safer, but the dashboard still acted as the execution engine. The operator
had to keep checking it for new inbound messages.

## 3. The Pivot

### Why we changed direction

The dashboard-centered architecture solved storage, visibility, and controlled batch execution, but not
continuous responsiveness. A new BOSS message could sit unread indefinitely unless a person opened the
dashboard, synchronized the inbox, selected a reply, and started the worker.

The new product intent became:

- Continuously probe BOSS for incoming messages.
- Persist changes locally without depending on an open dashboard.
- Let an LLM choose from a strictly pre-approved reply catalog.
- Automatically send the selected exact message.
- Automatically run the dependent BOSS Web WeChat exchange action.
- Use the dashboard only for monitoring, pausing, and template governance.

### New execution model

```text
continuous daemon
  -> acquire one SQLite lease
  -> poll BOSS every 30 seconds
  -> incrementally synchronize changed conversations
  -> find eligible latest inbound messages
  -> send redacted context + approved catalog to Alibaba Qwen
  -> validate the selected approved template locally
  -> persist decision and durable action pair
  -> send exact approved reply through BOSS Web
  -> verify reply through latest-message state
  -> execute dependent WeChat request
  -> verify visible BOSS success state
  -> persist heartbeat, delivery status, and audit events
  -> repeat
```

### Core pivot decisions

- **The model selects; it never writes.** Qwen cannot generate arbitrary outbound text. It can only select one
  immutable approved template ID, request review, or skip.
- **Dry mode is the default.** Live external actions require explicit `--live` operation.
- **SQLite is the source of operational truth.** Decisions and actions are persisted before external effects.
- **Message verification gates WeChat.** The exchange action cannot run until the reply is verified or already
  present.
- **One daemon owns the inbox.** A heartbeat and expiring lease prevent competing workers.
- **The dashboard is supervisory.** It monitors health, decisions, deliveries, queue state, and audit events;
  its writes are limited to pause/resume and immutable template lifecycle management.
- **Candidate privacy remains local and redacted.** Qwen receives only redacted recent context, job context,
  and the approved catalog, never BOSS credentials or raw contact details.

## 4. Current Repository Structure

### A. BOSS integration layer

Primary modules: `auth.py`, `client.py`, `browser_login.py`, `browser_reply.py`, and `commands/`.

Responsibilities:

- Authenticate the recruiter account.
- Read BOSS recruiter APIs.
- Resolve exact browser chat targets.
- Send normal replies through the official BOSS Web context.
- Execute the visible WeChat exchange workflow.
- Verify external results before reporting success.

### B. Incremental reading layer

Primary modules: `workflow/reader.py`, `poller.py`, `normalizer.py`, and `redaction.py`.

Responsibilities:

- Read jobs, inbox pages, candidate details, latest messages, and bounded changed history.
- Normalize unstable BOSS payloads into stable account-scoped records.
- Deduplicate by message IDs or deterministic fingerprints.
- Persist page checkpoints and recover from interrupted scans.
- Redact names, contact values, URLs, and sensitive text before durable storage.

### C. SQLite state layer

Primary module: `workflow/db.py`, schema, and ordered migrations through schema version 5.

Responsibilities:

- Store candidates, messages, templates, decisions, actions, runs, events, settings, and daemon state.
- Enforce idempotency and action dependencies.
- Track retries, leases, verified outcomes, review states, and completed exchanges.
- Scope all operational data to the active recruiter account.

Default database:

```text
~/.local/share/boss-cli/workflow.db
```

### D. Constrained intelligence layer

Primary modules: `workflow/selector.py` and `templates.py`.

Responsibilities:

- Call Alibaba Model Studio through its OpenAI-compatible endpoint.
- Use `qwen-plus` in non-thinking JSON mode.
- Supply only redacted conversation context and active approved templates.
- Validate outcome, confidence, and selected template ID locally.
- Route uncertain or invalid decisions to review without sending.

Current catalog: seven immutable templates, consisting of three direct WeChat/contact responses and four
automotive extended-warranty introductions or follow-ups.

### E. Planning and verified execution layer

Primary modules: `workflow/automation.py`, `planner.py`, and `sender.py`.

Responsibilities:

- Poll on a default 30-second cadence.
- Select eligible latest inbound conversations.
- Queue exact approved messages and dependent exchange actions.
- Execute sequential browser writes with configurable delay.
- Verify each reply and exchange state.
- Stop on target mismatch, authentication failure, risk control, or uncertain outcomes.
- Avoid duplicate messages and completed WeChat exchanges.

### F. Supervisory dashboard

Primary modules: `dashboard/server.py`, `dashboard_service.py`, and `dashboard/static/`.

Responsibilities:

- Show daemon heartbeat, mode, cycle status, queue state, decisions, deliveries, errors, and audit events.
- Save, approve, and retire immutable template versions.
- Pause or resume automation.
- Remain outside the execution path so closing the dashboard does not stop the daemon.

## 5. Current State

### Proven capabilities

- Real BOSS recruiter login and incremental account synchronization work on macOS.
- Qwen selects only from the approved reply catalog.
- Normal BOSS messages send through Camoufox/BOSS Web and verify through latest-message state.
- The `换微信 -> 同意 -> 确定` flow and multiple visible completion states are supported.
- The local queue survives process boundaries and prevents duplicate execution.
- The dashboard and daemon safely share SQLite using WAL, busy timeout, and one daemon lease.
- A controlled single-conversation live canary passed end to end.
- A full current-inbox live run passed across all actionable conversations.

### Latest live-run result

- 23 inbox rows observed.
- 19 complete conversations persisted; 4 incomplete BOSS rows lacked safe target details and were excluded.
- 10 conversations had actionable latest inbound messages.
- 10 of 10 approved replies were sent and verified.
- 9 new WeChat exchange requests were verified.
- 1 conversation already had a completed WeChat handoff and was safely marked `skipped_duplicate`.
- 0 executable queue actions remained afterward.
- Final read-only closure cycle: 0 eligible, 0 errors, 0 sends.

### Engineering verification

- Python 3.13 suite: **196 passed, 7 skipped**.
- Ruff checks passed.
- Wheel and source distribution builds passed.
- API credentials were process-only and were not persisted in repository files or SQLite.
- Current development branch: `continuous-recruiting-automation`.

### Important remaining boundaries

- macOS is validated; Windows is an engineering target, not yet production-certified.
- Windows still needs CI, browser-cookie/DPAPI acceptance, Camoufox validation, and an interactive-session
  supervisor such as Task Scheduler.
- The BOSS integration relies on reverse-engineered APIs and current web selectors, which may change.
- There is no BOSS webhook; continuous behavior is bounded polling.
- Captcha, anti-automation warnings, target mismatches, and uncertain browser outcomes still require an
  operator to intervene.
- The dashboard is a trusted localhost tool and should not be exposed publicly without authentication.
- WeChat activity outside BOSS, such as friend management, tags, files, or follow-up campaigns, is not part of
  this repository.

## 6. Graphic Generation Brief

### Recommended format

- Wide 16:9 technical infographic.
- Clean, modern systems-architecture style.
- White or very light neutral background with dark readable labels.
- Use teal for BOSS/data ingestion, green for verified external actions, amber for governance/review, and a
  restrained red only for stop conditions.
- Use simple product-quality icons: terminal, cloud/message inbox, database cylinder, AI chip, queue arrows,
  browser window, WeChat symbol, dashboard monitor, shield, and check marks.
- Avoid decorative gradients, oversized text, mascots, and dense code blocks.

### Main composition

Create three large left-to-right eras connected by a strong timeline arrow:

#### Era 1: CLI Foundation

Visual elements:

- Terminal connected directly to BOSS cloud.
- Small command icons for auth, inbox, jobs, chat, resume, and individual actions.
- Caption: **“Powerful commands, no workflow memory.”**
- Missing-capability callouts: no database, no daemon, no dashboard, no orchestration.

#### Era 2: Stateful Semi-Automation

Visual elements:

- BOSS -> Reader -> SQLite -> Dashboard -> Human Operator -> Reply + WeChat.
- Show three labeled blocks: **Read**, **Monitor**, **Act**.
- Place a human-eye or manual-click symbol between dashboard and execution.
- Caption: **“Safe and observable, but dependent on constant operator attention.”**

#### Era 3: Continuous Automation

Visual elements:

- Circular flow centered on a **Continuous Daemon**.
- Flow: BOSS Inbox -> Incremental Reader -> Local SQLite -> Qwen Selector -> Durable Queue -> Verified BOSS
  Reply -> Verified WeChat Request -> Audit/Heartbeat -> back to polling.
- Place **Approved Templates Only** as a guarded catalog feeding Qwen.
- Place **Supervisory Dashboard** beside the loop, connected to SQLite with a dotted monitoring line rather
  than inside the execution path.
- Add a shield labeled **Redaction + Idempotency + Verification + Stop-on-Uncertainty**.
- Caption: **“Autonomous execution with constrained AI and local operational control.”**

### Current-state metrics panel

Add a compact verification panel on the right or bottom:

- **30 sec** default polling.
- **7** approved immutable templates.
- **10/10** live replies verified.
- **9** new WeChat requests verified.
- **1** existing exchange safely skipped.
- **0** executable actions remaining.
- **196 passed / 7 skipped** tests.
- **macOS validated**.
- **Windows certification pending**.

### Exact short labels to prioritize

Image generators often struggle with large amounts of text. Prioritize these labels and render supporting
detail as icons or very short captions:

- `BOSS Inbox`
- `Incremental Reader`
- `Local SQLite`
- `Approved Templates`
- `Alibaba Qwen`
- `Durable Queue`
- `Verified Reply`
- `Verified WeChat`
- `Audit + Heartbeat`
- `Supervisory Dashboard`
- `Pause / Resume`
- `30s Polling`
- `10/10 Verified`

### Visual message

The final graphic should make one contrast unmistakable:

> The old system required a person to move work from the dashboard into action. The current system runs the
> verified workflow continuously, while the person governs templates and monitors outcomes from outside the
> execution loop.

Do not include real candidate names, chat content, contact details, API keys, cookies, database IDs, or
screenshots from the live account.
