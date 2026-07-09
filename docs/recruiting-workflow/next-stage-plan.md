# Next Stage Plan

Date: 2026-07-08

This plan focuses only on the Boss Zhipin portion of the requested recruiting workflow. WeChat and other social platform automation should be treated as separate adapter work after the Boss-side MVP is stable.

## Stage 0: Repository Context and Git Hygiene

Status: started.

Goals:

- Preserve the upstream repository as the source reference.
- Work on a dedicated branch for local project context and automation extensions.
- Keep documentation in `docs/recruiting-workflow`.
- Push work to a user-owned GitHub remote, not directly to the upstream author repository.

Deliverables:

- Context documentation folder.
- Capability assessment.
- Next-stage implementation plan.
- Work log and documentation standard.

## Stage 1: Boss-Only Workflow MVP

Goal: automate the inbound Boss message follow-up loop while keeping all WeChat handling out of scope.

Initial command:

```bash
boss workflow dry-run --rules-file docs/recruiting-workflow/examples/boss-dry-run-rules.json --limit 10 --json
```

The command is read-only and does not send messages. By default it uses only saved credentials or `BOSS_COOKIES`; it does not auto-read browser cookies because browser/keychain access can block unattended automation. Run `boss login` once first, or explicitly pass `--allow-browser-auth` when interactive browser cookie extraction is acceptable.

By default it also avoids full resume/profile viewing because that may trigger Boss-side "candidate viewed" notifications. Use `--fetch-resume` only after accepting that product-side effect.

Proposed flow:

```text
scheduled worker
  -> boss recruiter inbox --json
  -> normalize candidate records
  -> skip candidates already processed
  -> boss recruiter chat <friendId> --json
  -> optionally boss recruiter resume <encryptGeekId> --json
  -> evaluate against JD rules
  -> if matched: boss recruiter reply-browser <friendId> <approved template> -y
  -> write state transition to SQLite
```

Normal outbound chat should use `reply-browser`, not the legacy `reply` command. The legacy command targets a Boss fast-reply HTTP endpoint and is not reliable for arbitrary typed messages.

Minimum data model:

```text
candidates
  id
  friend_id
  encrypt_geek_id
  name_hash_or_redacted_name
  job_id
  job_name
  source_platform
  current_stage
  first_seen_at
  last_seen_at
  last_processed_at
  decision
  decision_reason

events
  id
  candidate_id
  event_type
  event_time
  payload_summary
  result
  error_message

message_templates
  id
  name
  body
  approved
  approved_at
  version
```

## Stage 2: Rules and Template Configuration

Goal: move business logic out of code and into reviewable configuration.

Recommended configuration:

- JD matching keywords.
- Required or preferred years of experience.
- Role-specific accept/reject signals.
- Standard first-reply template.
- WeChat guidance text.
- Optional branch for candidates without relevant experience.

Recommended format:

- Use YAML or JSON for rules and templates.
- Store sensitive values outside source control.
- Require explicit `approved: true` before a template is used for outbound messaging.

## Stage 3: Observability and Safety

Goal: make the automation auditable and safe to operate.

Required behavior:

- Dry-run mode for every outbound action.
- Per-candidate event log.
- Rate-limit controls.
- Retry policy with capped attempts.
- Redaction for logs.
- Clear stop switch.
- Manual review queue for ambiguous candidates.

## Stage 4: Platform Adapter Boundary

Goal: prepare for later WeChat, Maimai, Lagou, LinkedIn, or other platform integrations.

Suggested interface:

```text
PlatformAdapter
  list_conversations()
  get_conversation_history(conversation_id)
  get_profile(conversation_id)
  send_message(conversation_id, message)
  request_contact_exchange(conversation_id)
```

Boss Zhipin becomes one adapter. WeChat should become another adapter, implemented separately because its automation mechanics and compliance risks are different.

## Open Inputs Needed

- Confirmed Boss initial screening message.
- JD fields and matching rules.
- Whether candidates with no relevant experience follow a different path.
- Which jobs/JDs should be monitored first.
- Persistence preference: SQLite, spreadsheet, or another database.
- Deployment mode: local scheduled process, server daemon, or manual CLI runner.
