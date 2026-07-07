# Work Log

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
