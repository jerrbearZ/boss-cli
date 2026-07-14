# Incremental Boss Reader

Date: 2026-07-14

## Purpose

The reader is the read-only ingestion boundary between Boss recruiter APIs and the local workflow database. It discovers recruiter conversations, normalizes sensitive payloads, and persists account-scoped state for the dashboard and later classification. It never selects candidates, creates outbound actions, or sends messages.

## Operator Command

```bash
boss workflow sync \
  --db ~/.local/share/boss-cli/workflow.db \
  --limit 100 \
  --max-pages 20 \
  --history changed \
  --history-budget 20 \
  --json
```

Default behavior:

- Uses saved credentials or `BOSS_COOKIES` without interactive browser extraction.
- Resolves a stable recruiter account from current-user data or message participants.
- Synchronizes recruiter jobs, inbox pages, candidate details, and latest messages.
- Reads chat history only for new, changed, pending, or deferred conversations.
- Stores message hashes and redacted previews rather than raw transcripts.
- Does not read full resumes. `--include-profile` reads only the lower-impact chat profile summary.
- Commits one inbox page at a time and resumes an interrupted run from the next committed page.

## Identity And Isolation

The local account key must survive cookie rotation. The reader prefers a hashed Boss user id and falls back to a cookie-derived hash only when no stable API identity can be established. A later stable identity promotes the existing fallback account where the relationship is unambiguous.

All candidates and jobs remain scoped by `account_id`. The dashboard stores the most recently synchronized account as `active_account_id` and filters candidate reads to that account.

## Synchronization Flow

```text
validate auth
  -> resolve recruiter identity
  -> read recruiter jobs
  -> resume inbox checkpoint when needed
  -> read one inbox page
  -> batch candidate details
  -> batch latest messages (maximum 50 ids)
  -> conditionally read bounded history
  -> normalize and redact
  -> commit page and checkpoint
  -> repeat until complete or bounded stop
```

The scanner stops on an explicit end-of-pages response, empty page, configured candidate limit, page limit, or repeated-page fingerprint. Repeated pages are recorded as a partial scan and reset the checkpoint so the next run starts from page one.

## History Policy

`--history changed` is the production default. A conversation is eligible when it is new, its latest fingerprint changed, or its previous history state is `pending`, `deferred`, or `failed`. `--history-budget` limits the number of conversations enriched in one run; deferred conversations are drained by later runs even when their latest message remains unchanged.

History messages are deduplicated by Boss message id when available, otherwise by a deterministic fingerprint. Direction is derived from the history `received` flag or recruiter/candidate participant ids. Multimedia and system rows are persisted as metadata even when they have no text.

## Full Scans

`--full-scan` may mark missing candidates inactive only when all of these are true:

- The scan completed normally.
- No job filter is active.
- The label is `0`.
- The run did not resume midway through an earlier scan.

Filtered, truncated, failed, duplicate-page, and resumed scans never mark unseen candidates inactive.

## Privacy Boundary

The workflow database must not contain cookies, security tokens, raw API payloads, complete resumes, phone numbers, WeChat ids, email addresses, or full private chat transcripts. Candidate names and previews are redacted. Raw payloads are used only in memory to calculate deterministic hashes and normalized summaries.

Full resume access remains an explicit separate operation because it may produce a candidate-visible "viewed" event on Boss.

## Recovery

- Authentication failure: renew login, then rerun the same command.
- Network or API failure: rerun with resume enabled; committed pages are retained.
- Repeated-page response: inspect the run stop reason and payload contract before increasing limits.
- Per-candidate parse failure: inspect `sync_run_errors`; other candidates on the page remain committed.
- Schema upgrade: opening the database applies ordered migrations through schema version 3.

## Verification Contract

The reader is considered stable when repeated runs are idempotent, cookie rotation does not duplicate accounts, history reads are bounded and incremental, operator state survives sync, v2 databases migrate in place, and all read tests pass without any Boss write method being available to the fake client.
