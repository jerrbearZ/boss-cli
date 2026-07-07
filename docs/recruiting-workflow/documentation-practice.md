# Documentation Practice for Future Work

Date: 2026-07-08

All future implementation work for this recruiting automation project should leave a durable context trail in this folder. The goal is to make the project understandable from the repository, without relying on chat history.

## Required Documentation For Each Meaningful Change

Every non-trivial change should update [work-log.md](./work-log.md) with:

- Process: what was inspected, changed, or run.
- Result: what is now true, including files changed and behavior added.
- Verification: commands run, tests passed, tests skipped, and why.
- Design notes: tradeoffs, assumptions, and risks discovered.
- Next steps: concrete follow-up actions.

## Design Documentation Standard

When a change introduces or modifies workflow behavior, add or update a design note covering:

- Entry point and trigger.
- Data inputs and outputs.
- State transitions.
- Error handling and retry behavior.
- Rate-limit and compliance considerations.
- Sensitive data handling.
- How the behavior can be tested without sending real messages.

## Result Documentation Standard

Results should be factual and reproducible:

- Link to changed files.
- Record command names, not raw secrets or private payloads.
- Summarize relevant command output.
- Note gaps directly instead of implying completion.

## Process Documentation Standard

The process section should capture the path taken:

- Repository state before the change.
- Important files reviewed.
- Reason for the chosen approach.
- Any rejected alternatives that matter for future decisions.

## Sensitive Data Rules

Do not commit:

- Raw Boss cookies.
- Phone numbers.
- WeChat IDs.
- Full private chat transcripts.
- Candidate resumes with identifying details.
- Access tokens or QR login artifacts.

Use hashes, redacted names, synthetic fixtures, or local-only encrypted stores for testing and documentation.
