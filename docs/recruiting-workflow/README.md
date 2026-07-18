# Recruiting Workflow Context Layer

Date: 2026-07-14

This folder documents the recruiting automation context for this repository. It is intended to be the durable project memory for future work: what the repository can do today, what the target workflow is, what remains to be built, and how process, results, and design decisions should be recorded.

## Project Purpose

The target project is an automated recruiting follow-up workflow. The full business workflow spans Boss Zhipin message response, candidate screening, WeChat handoff, standardized follow-up, file distribution, tagging, and archival.

This repository only covers the Boss Zhipin side. It provides a Python CLI and API client for BOSS direct-hire workflows, including recruiter inbox access, candidate profile/resume reads, chat history reads, and recruiter replies.

## Current Repository Role

`boss-cli` now contains both the Boss Zhipin adapter and a local continuous workflow engine.

It can:

- Authenticate with Boss Zhipin through saved cookies, browser cookie extraction, environment cookies, and QR login.
- Read recruiter jobs, candidate inbox entries, candidate details, resumes, and chat history.
- Send recruiter-side replies to candidates.
- Store workflow state in SQLite for candidates, messages, templates, queue actions, events, and dashboard controls.
- Run a leased continuous daemon that polls, selects approved replies, queues verified actions, and requests WeChat.
- Run a local supervisory dashboard for monitoring, pause/resume, and immutable template approval.
- Export structured JSON/YAML output that another automation service can consume.
- Apply conservative request pacing and retry behavior around reverse-engineered Boss web APIs.

It does not:

- Listen to incoming messages through webhook or event subscription.
- Provide complete production JD/rules classification commands.
- Manage WeChat friend requests, remarks, tags, files, or cards.
- Provide a platform-agnostic workflow abstraction for Boss, WeChat, Maimai, Lagou, LinkedIn, or other channels.

## Documentation Map

- [capability-assessment.md](./capability-assessment.md): Boss-only requirement mapping against the current repo.
- [next-stage-plan.md](./next-stage-plan.md): Recommended next implementation stages and architecture.
- [browser-backed-send.md](./browser-backed-send.md): Current verified strategy for sending Boss replies through Boss Web/Camoufox.
- [production-architecture.md](./production-architecture.md): Production system design for high-volume Boss follow-up.
- [implementation-goal.md](./implementation-goal.md): Implementation-ready goal and work packages for coding agents.
- [dashboard-design.md](./dashboard-design.md): Operator dashboard design for controlled sending and audit monitoring.
- [reader-architecture.md](./reader-architecture.md): Current incremental reader behavior, privacy boundaries, and recovery model.
- [automation-dashboard.md](./automation-dashboard.md): Typed batch messages, WeChat exchange execution, dashboard flow, and recovery behavior.
- [continuous-automation.md](./continuous-automation.md): Core continuous daemon design, safety decisions, operations, and recovery.
- [windows-deployment-plan.md](./windows-deployment-plan.md): Native Windows architecture, implementation phases, acceptance, rollout, and rollback.
- [windows-operator-runbook.md](./windows-operator-runbook.md): Install, operate, update, restore, diagnose, and certify the Windows deployment.
- [linux-deployment-plan.md](./linux-deployment-plan.md): Native Ubuntu architecture, quality gate, acceptance, rollout, and rollback.
- [linux-operator-runbook.md](./linux-operator-runbook.md): Install, operate, update, restore, diagnose, and certify the Linux deployment.
- [project-evolution-visual-brief.md](./project-evolution-visual-brief.md): End-to-end project history and image-generation brief for the architecture evolution.
- [documentation-practice.md](./documentation-practice.md): Required documentation standard for future work.
- [work-log.md](./work-log.md): Chronological record of process, results, verification, and open gaps.

## Working Assumptions

- Boss Zhipin actions rely on reverse-engineered web APIs and valid cookies, especially `__zp_stoken__`.
- The first practical milestone should avoid WeChat automation and focus on a Boss-only MVP.
- The workflow should maintain its own local state store, likely SQLite, instead of relying only on Boss UI state.
- All candidate data should be treated as sensitive personal data. Logs and docs should avoid raw cookies, phone numbers, WeChat IDs, and full private chat contents unless explicitly required and stored securely.
