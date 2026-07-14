# Continuous Recruiting Automation

Date: 2026-07-14

## Purpose

This is the authoritative design and operations guide for the unattended BOSS recruiting workflow introduced in schema version 5.

The system continuously probes the recruiter inbox, persists new messages, asks an LLM to select one reply from an operator-approved catalog, queues the exact approved text, sends it through the verified BOSS Web adapter, then requests WeChat only after message delivery is verified. The dashboard monitors this process and manages template approval; it is no longer the execution engine.

## Runtime Flow

```text
continuous daemon
  -> acquire single-owner SQLite lease
  -> read and persist bounded BOSS inbox changes
  -> stop decisioning here when globally paused
  -> find conversations whose latest message is inbound text
  -> send redacted context plus approved template catalog to the selector
  -> validate selected template ID and confidence locally
  -> persist review/skip, or durably queue the selected message
  -> persist the selection decision
  -> execute queued message through BOSS Web and verify latest-message state
  -> execute dependent WeChat request and verify visible success state
  -> persist heartbeat, cycle summary, queue outcomes, and audit events
  -> sleep, then repeat
```

There is no BOSS webhook in this implementation. Continuous operation is bounded polling with a default 30-second interval.

## Safety Decisions

### The model selects; it never writes

The LLM receives a catalog containing immutable approved template IDs and exact message bodies. It can return only:

- `selected` with one catalog ID.
- `review` with no template ID.
- `skipped` with no template ID.

The API request uses strict structured output, and the application validates the result again. An ID outside the active approved catalog, an invalid confidence, malformed JSON, or a contradictory result cannot enqueue a message. A low-confidence selection becomes `review`.

### Dry mode is the default

`boss workflow daemon` reads and records model decisions but does not enqueue or send. `--live` is required to create and execute outbound actions. Dry and live decisions use separate prompt namespaces, so a dry decision does not prevent the same inbound message from being reconsidered after live mode is enabled.

### Message delivery gates WeChat

For a live selection, the planner creates:

```text
send_message -> exchange_wechat
```

The WeChat action cannot be claimed until the message action is `verified` or `skipped_duplicate`. A terminal message failure cancels the dependent request. An uncertain browser outcome stops the sender and requires review.

### Persist intent before external effects

Live actions are durably queued before the automation decision is finalized. A crash between queueing and decision persistence is recoverable because action idempotency keys include candidate, action type, template version, and inbound trigger fingerprint. A crash after an external send is handled by the sender's live duplicate preflight and verification checks.

### One daemon owns an inbox

`automation_daemon_state` stores an owner, heartbeat, and expiring lease. A second process cannot run the same named daemon while that lease is valid. SQLite WAL and a busy timeout allow the dashboard to read state while the daemon writes it.

## Template Lifecycle

The dashboard supports three states:

- `draft`: saved but unavailable to the selector.
- `approved`: active and eligible for automatic selection.
- `retired`: unavailable to future decisions.

Template versions are immutable. Editing means saving a new version. Approving a new version retires the prior active version with the same template name, preventing obsolete wording from remaining selectable. Different template names may remain active together as separate reply intents.

Selection guidance should state when a template applies. It guides selection but is never outbound message content.

When a schema v4 database is upgraded, pending actions from the operator-driven queue are quarantined as `needs_review`. The continuous daemon will not silently execute work planned under the prior operating model.

## Decision Eligibility And Idempotency

A conversation is eligible when:

- It belongs to the active synchronized account.
- It is active and not marked do-not-contact.
- Its latest stored message is non-empty inbound text.
- No decision exists for the same candidate, inbound fingerprint, approved catalog hash, and prompt version.

A new inbound message becomes a new trigger. Changing the approved catalog also changes its hash, allowing previously reviewed conversations to be reconsidered against the new catalog. An unchanged conversation and catalog are not repeatedly sent to the model.

## Model And Privacy Boundary

The selector uses Alibaba Cloud Model Studio's OpenAI-compatible Chat Completions API through `httpx`.
It calls Qwen in non-thinking JSON mode, then independently validates the returned outcome, confidence,
and approved template ID before anything can be queued. Configuration:

```bash
export DASHSCOPE_API_KEY='<key>'
# Optional overrides; these are the defaults:
export BOSS_LLM_MODEL='qwen-plus'
export DASHSCOPE_BASE_URL='https://dashscope.aliyuncs.com/compatible-mode/v1'
```

The model receives:

- Redacted recent message text already stored in SQLite.
- Message direction and content kind.
- Job name and workflow stage.
- Active approved template bodies and selection guidance.

It does not receive BOSS cookies, candidate IDs used by BOSS, raw names, phone numbers, WeChat IDs, full resumes, or unredacted transcripts from this workflow. Raw provider responses are not persisted; only a response hash, validated result, confidence, and redacted reason are stored.

## Current Validation Status

As of 2026-07-15, the Alibaba Model Studio credential has been validated against the configured Beijing
endpoint using `qwen-plus`. The credential was supplied only to the test process and was not written to the
repository or workflow database. Because it was shared in plaintext, rotate it before the first production
live test.

Two synthetic, non-candidate conversations verified the complete model boundary:

- A direct request to continue on WeChat selected approved template ID 5, `wechat_candidate_requested`.
- A question about the business and role selected approved template ID 8, `automotive_warranty_role_context`.
- Both calls returned valid JSON, selected only IDs from the six active approved templates, and passed local
  outcome, confidence, and catalog validation.

The candidate-isolated live canary completed on 2026-07-15:

- `--friend-id` constrained both decision eligibility and queue claiming to one consenting conversation.
- Qwen selected `wechat_candidate_requested` (template ID 5) with `0.95` confidence.
- The exact approved message `可以，方便的话我们交换一下微信。` was sent through `dom.chat-composer`
  and matched by the BOSS latest-message API before the dependent action was eligible.
- The browser then executed `换微信` -> `同意` -> `确定` and verified the completed exchange through the
  visible `查看微信` indicator.
- The message and WeChat actions both finished as `verified`, with the WeChat action depending on the message
  action and a later verification timestamp.
- The final canary cycle completed with no stop reason, the daemon released its lease, and no executable
  canary actions remained queued.

## Operations

### 1. Prepare authentication and state

```bash
boss login
boss workflow init-db
```

Saved BOSS credentials are loaded without interactive browser extraction unless `--allow-browser-auth` is explicitly supplied. A long-running daemon does not open an interactive login flow after session expiry; repair authentication with `boss login`, then restart it.

### 2. Create and approve templates

```bash
boss dashboard --port 8765
```

Save drafts, inspect their exact text and guidance, then approve them in the template catalog. The daemon will not decide or send when no approved template exists.

The repository includes an approved automotive/WeChat catalog. Install it idempotently with:

```bash
boss workflow install-templates --retire-existing --json
```

`--retire-existing` explicitly retires active templates outside this catalog. Omit it when adding the
catalog alongside other intentionally active templates. The catalog contains three contact-exchange replies
and three automotive extended-warranty introductions. Each has separate selection guidance, and the model
can only return one of their approved IDs.

| Template | Approved message | Selection intent |
| --- | --- | --- |
| `wechat_agreement_direct` | `OK，好的，交换个微信。` | Candidate explicitly accepts an earlier WeChat exchange suggestion. |
| `wechat_agreement_warm` | `好的，可以的，我们交换个微信吧。` | Candidate agrees to continue on WeChat and a warmer acknowledgement fits. |
| `wechat_candidate_requested` | `可以，方便的话我们交换一下微信。` | Candidate directly asks to add or exchange WeChat. |
| `automotive_warranty_intro_direct` | `我们在做汽车延长保修服务，感兴趣的话，交换一个微信。` | Candidate asks what the business or opportunity does. |
| `automotive_warranty_intro_conversational` | `我们主要做汽车延长保修服务，如果你感兴趣，可以交换微信进一步沟通。` | Candidate asks for context before continuing. |
| `automotive_warranty_role_context` | `这个岗位与汽车延长保修服务相关，如果你想进一步了解，我们可以交换微信详聊。` | Candidate asks specifically what the role is related to. |

### 3. Validate one dry cycle

```bash
boss workflow daemon --once --model '<model>' --json
```

Inspect decisions in the dashboard. No outbound action is created in dry mode.

### 4. Run continuously without sending

```bash
boss workflow daemon --model '<model>' --poll-interval 30
```

### 5. Enable verified live execution

```bash
boss workflow daemon \
  --model '<model>' \
  --live \
  --request-wechat \
  --poll-interval 30 \
  --candidate-limit 20 \
  --max-actions 10 \
  --action-delay 60
```

Live mode is suitable only after the browser-backed message and `换微信` selectors have been checked against the current BOSS Web UI. Start with `--once --live --max-actions 2` to exercise one message and its dependent WeChat request.

### 6. Inspect status

```bash
boss workflow daemon-status --json
```

The dashboard shows heartbeat freshness, mode, last cycle, next poll, decisions, total verified deliveries, queue state, errors, and audit events.

## Next Acceptance Test: Reply And WeChat Exchange

The next milestone is one controlled end-to-end canary proving that an approved reply is sent and verified,
then the dependent visible `换微信` control is executed and verified for the same conversation.

### Preconditions

1. Rotate the credential used for the synthetic test and expose the replacement as `DASHSCOPE_API_KEY` only
   in the test process environment.
2. Install the browser extra and Camoufox runtime used by live execution:

```bash
uv sync --extra browser
uv run python -m camoufox fetch
```

3. Confirm BOSS authentication is valid and identify one consenting test conversation by `friendId`.
4. Confirm the daemon is stopped and the queue contains no older `queued`, `locked`, `sending`, or
   `failed_retryable` actions.
5. Isolate the canary candidate with `--friend-id <friendId>`. `--candidate-limit 1` alone selects the oldest
   eligible inbound conversation and is not a safe substitute for explicit canary targeting.
6. Resolve the exact target without sending:

```bash
boss recruiter reply-browser <friendId> '<approved-template-body>' --dry-run --json
```

### Controlled Execution

After candidate isolation and target verification:

```bash
boss workflow daemon \
  --once \
  --live \
  --request-wechat \
  --friend-id <friendId> \
  --candidate-limit 1 \
  --max-actions 2 \
  --action-delay 60 \
  --json
```

Do not substitute `boss recruiter exchange-wechat` for this acceptance test. That command uses the separate
BOSS HTTP exchange endpoint, while continuous automation uses the visible browser-backed `换微信` control.

### Pass Criteria

- Exactly one approved message action reaches `verified` and the BOSS latest-message read matches its exact
  immutable template body.
- Exactly one dependent WeChat action reaches `verified` only after the message action verifies.
- The browser observes a changed success indicator such as `等待对方同意` or `微信交换请求已发送`.
- The run reports `messages_verified=1`, `wechat_verified=1`, no uncertain send, and no terminal failure.
- Dashboard queue, delivery totals, and audit events all identify the same candidate and run.

Stop immediately on a target mismatch, login prompt, captcha, missing WeChat control, unverified latest
message, or any BOSS risk-control response. Leave uncertain actions in review; do not replay them manually.

## Pause And Shutdown

Dashboard `Pause` sets durable global state. A paused daemon still synchronizes the inbox so monitoring remains current, but it does not call the model, enqueue actions, or claim sends. `Resume` re-enables those stages.

`SIGINT` and `SIGTERM` request a clean stop. The daemon finishes or stops at its next safe check, releases its lease, and records `stopped`. Killing the process during an external browser action may leave an uncertain action; expired `sending` leases become `needs_review` rather than being replayed.

The CLI is a continuous foreground process. Reboot restart and crash restart require an external supervisor such as launchd, systemd, or a container runtime. Secrets should be supplied by that supervisor's credential mechanism, not committed to this repository.

## Failure Policy

- Model transport failure or incomplete provider response: fail the cycle, record daemon error state, wait for error backoff, and retry without consuming the candidate trigger.
- Missing or failed browser runtime: leave the message retryable and keep its dependent WeChat action queued.
- Invalid or unsafe model output: record `review`; never queue.
- No approved templates: sync succeeds, cycle records `needs_review`, and no model call occurs.
- BOSS authentication failure: cycle fails and backs off; perform login and restart if credentials cannot recover.
- Captcha, risk control, target mismatch, or uncertain browser state: sender stops; queue state and error code remain visible.
- Terminal message failure: cancel its dependent WeChat action.
- Clean duplicate detected before send: mark the message action `skipped_duplicate`; its verified dependency may allow the WeChat request to proceed.

## Dashboard Boundary

The dashboard server exposes no sync, candidate selection, manual enqueue, sender start, or sender stop endpoint. Its write operations are limited to:

- Save an immutable template draft.
- Approve a template version.
- Retire a template version.
- Pause or resume automation.

It remains a trusted localhost application without user authentication. Do not bind it to a public interface.

## Current Limitations

- Polling is not real-time and may be throttled by BOSS risk controls.
- One daemon name and one active BOSS account are the supported operating shape.
- The model provider is Alibaba Cloud Model Studio, using the `qwen-plus` alias by default.
- Browser UI selectors may change when BOSS Web changes.
- The dashboard does not resolve review decisions or retry individual actions yet.
- Process supervision, secrets management, and OS startup installation are deployment responsibilities.
- One Qwen-driven browser reply and its dependent WeChat exchange have passed the controlled live canary.

## Verification Baseline

The implementation includes tests for schema migration, approved-ID constraints, malformed output, transient retry behavior, dry/live isolation, low-confidence review, decision idempotency, catalog reconsideration, pause behavior, candidate-scoped queue claims, contact-request eligibility, message-to-WeChat dependency order, the `换微信` consent sequence, retryable browser setup failures, immutable template replacement, lease exclusion, and removal of manual dashboard execution endpoints. The current clean Python 3.13 baseline is `192 passed, 7 skipped`.
