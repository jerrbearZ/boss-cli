# Batch Automation And Dashboard

> Historical schema v4 design. The operator-driven execution model was superseded by [continuous-automation.md](./continuous-automation.md) on 2026-07-14. The typed queue and verified browser actions remain in use, but the dashboard no longer syncs, selects candidates, enqueues, or runs the sender.

Date: 2026-07-14

## Purpose

The outbound workflow turns explicitly selected local candidates into durable, verified BOSS actions. It supports sending an operator-approved message, requesting WeChat through the visible BOSS Web `换微信` control, or running both actions in that order. It does not autonomously select candidates or generate message content.

## Operator Flow

```text
sync inbox
  -> filter and select candidates
  -> write and approve the exact message
  -> choose message and/or WeChat request
  -> confirm and persist the batch
  -> inspect the queue
  -> run a bounded number of actions
  -> review verification and audit events
```

Start the local dashboard with:

```bash
boss dashboard --db ~/.local/share/boss-cli/workflow.db --port 8765
```

The dashboard binds to `127.0.0.1` by default and is intended for one trusted local operator.

## Action Model

Schema version 4 defines two action types:

- `send_message`: sends the exact approved template through the existing browser-backed BOSS chat adapter.
- `exchange_wechat`: opens the selected conversation and executes the visible BOSS Web WeChat exchange control.

When both are selected, the WeChat action records a dependency on the message action. The worker can claim the WeChat action only after the message is `verified` or `skipped_duplicate`. A terminal message failure cancels its dependent exchange action.

Templates are required only for message actions. WeChat-only batches do not create placeholder templates.

## Idempotency And Recovery

Each action key includes the candidate, action type, optional template version, and latest trigger-message fingerprint. Replanning the same batch does not create duplicate rows.

Before sending text, the worker checks both local message hashes and the live latest-message API. This recovers from a process crash after BOSS accepted the message but before SQLite was updated.

Queue claims use leases. An expired `locked` action may be reclaimed. An expired `sending` action is moved to `needs_review` because its external outcome is uncertain and replaying it could duplicate a message or WeChat request.

## Verification

Message actions are complete only after the latest-message API contains the expected text.

WeChat actions are complete only after the visible conversation changes to a recognized success state such as waiting for the candidate's approval. A clicked control without a visible success state stops the worker as unverified.

The worker stops on login, target, browser, verification, or unsupported-action failures. Selected transient browser failures are persisted as retryable with a delayed retry time; target and verification failures require review.

## Safety Boundaries

- Candidate selection and batch confirmation remain manual.
- Do-not-contact and inactive candidates cannot be selected from the dashboard and are rejected by the planner.
- Queue reads and claims are scoped to the active recruiter account.
- The worker runs at most the requested action count and waits between actions.
- Message and WeChat results are stored as redacted audit events.
- Captcha, anti-automation warnings, login prompts, and uncertain target state stop processing.

## Current Boundary

The system is a controlled local batch tool, not an unattended responder. It has no scheduler, webhook, automatic candidate classifier, AI message generator, or public/multi-user dashboard authentication. Live BOSS UI selectors should be revalidated whenever BOSS changes the recruiter chat interface.
