# Recruiting Workflow Context Layer

Date: 2026-07-08

This folder documents the recruiting automation context for this repository. It is intended to be the durable project memory for future work: what the repository can do today, what the target workflow is, what remains to be built, and how process, results, and design decisions should be recorded.

## Project Purpose

The target project is an automated recruiting follow-up workflow. The full business workflow spans Boss Zhipin message response, candidate screening, WeChat handoff, standardized follow-up, file distribution, tagging, and archival.

This repository only covers the Boss Zhipin side. It provides a Python CLI and API client for BOSS direct-hire workflows, including recruiter inbox access, candidate profile/resume reads, chat history reads, and recruiter replies.

## Current Repository Role

`boss-cli` should be treated as a Boss Zhipin adapter layer, not as the complete workflow engine.

It can:

- Authenticate with Boss Zhipin through saved cookies, browser cookie extraction, environment cookies, and QR login.
- Read recruiter jobs, candidate inbox entries, candidate details, resumes, and chat history.
- Send recruiter-side replies to candidates.
- Export structured JSON/YAML output that another automation service can consume.
- Apply conservative request pacing and retry behavior around reverse-engineered Boss web APIs.

It does not yet:

- Run as a persistent daemon.
- Listen to incoming messages through webhook or event subscription.
- Classify candidates against JD rules automatically.
- Store per-candidate workflow state.
- Manage WeChat friend requests, remarks, tags, files, or cards.
- Provide a platform-agnostic workflow abstraction for Boss, WeChat, Maimai, Lagou, LinkedIn, or other channels.

## Documentation Map

- [capability-assessment.md](./capability-assessment.md): Boss-only requirement mapping against the current repo.
- [next-stage-plan.md](./next-stage-plan.md): Recommended next implementation stages and architecture.
- [documentation-practice.md](./documentation-practice.md): Required documentation standard for future work.
- [work-log.md](./work-log.md): Chronological record of process, results, verification, and open gaps.

## Working Assumptions

- Boss Zhipin actions rely on reverse-engineered web APIs and valid cookies, especially `__zp_stoken__`.
- The first practical milestone should avoid WeChat automation and focus on a Boss-only MVP.
- The workflow should maintain its own local state store, likely SQLite, instead of relying only on Boss UI state.
- All candidate data should be treated as sensitive personal data. Logs and docs should avoid raw cookies, phone numbers, WeChat IDs, and full private chat contents unless explicitly required and stored securely.
