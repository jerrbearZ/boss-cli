# Windows Automation Deployment Plan

**Status:** Repository implementation completed on this branch; target-PC commissioning and certification remain pending.

**Implementation branch:** `cross-platform-automation-deployment`

**Prepared:** 2026-07-15

## Implementation Status

The repository portions of phases 1–4 are implemented: platform paths and API identity, current-user DPAPI
secrets, schema 6 lifecycle/health controls, guarded SQLite backup/restore, dry-by-default deployment config,
PowerShell Task Scheduler automation, Windows CI, tests, and the
[operator runbook](./windows-operator-runbook.md). Phases 5–6 require the physical Windows PC, interactive BOSS
account, approved canary, recovery drill, and supervised soak; this branch does not claim those external gates.

## Objective

Deploy the continuous BOSS recruiting workflow to a dedicated Windows PC so that it:

- starts predictably after the automation user logs in;
- polls BOSS every 30 seconds without requiring the dashboard to remain open;
- lets Qwen select only an exact, approved reply template;
- sends and verifies the reply before requesting WeChat;
- stores workflow state locally in SQLite;
- exposes the dashboard only on the Windows PC at `127.0.0.1`;
- survives ordinary process failures and reboots without duplicate execution;
- protects BOSS and Alibaba credentials with Windows user-scoped encryption;
- can be paused, stopped, updated, backed up, and rolled back safely.

This plan preserves the current safety model. It does not expand the LLM's authority, add generated outbound
text, expose the dashboard remotely, or allow uncertain browser actions to be replayed automatically.

## Current System Being Ported

The existing macOS-validated flow is:

```text
BOSS read APIs
  -> incremental inbox reader
  -> local SQLite state and audit log
  -> eligibility checks
  -> redacted context sent to Alibaba Qwen
  -> exact approved template selection
  -> durable message action
  -> headful Camoufox BOSS Web send
  -> latest-message API verification
  -> dependent visible WeChat exchange action
  -> dashboard monitoring and template approval
```

The daemon owns a database lease, runs one cycle at a time, and defaults to a 30-second polling interval. The
dashboard is not in the execution path. Closing it does not stop synchronization or sending.

## Windows Target

### Supported first deployment

- Windows 11 Pro, 64-bit, fully patched.
- A physical PC rather than a Windows service host, WSL VM, or container.
- One dedicated, non-administrator local Windows user for automation.
- One BOSS recruiter account and one daemon named `primary`.
- Native PowerShell 5.1 or PowerShell 7.
- Python 3.13 managed by `uv`.
- A persistent interactive desktop session for the automation user.
- A stable network connection and a power policy that prevents system sleep.
- Camoufox in headful mode as the production browser engine.
- Chrome or Edge installed for initial BOSS login and cookie extraction diagnostics.

The user may lock the workstation after login, but locked-session and disconnected-RDP behavior must pass
acceptance testing on the actual PC. Logging the automation user out stops browser-capable automation until
the next login.

### Explicitly unsupported in the first release

- Running live browser automation as `LocalSystem` or as a Session 0 Windows service.
- Running through WSL, Docker Desktop, or a headless Windows server.
- Multiple BOSS accounts or parallel send workers.
- A dashboard bound to a LAN address.
- Automatic git updates without a controlled stop, backup, migration, and canary.
- Machine-wide plaintext API keys or cookies.

## Target Runtime Architecture

```text
Windows logon: dedicated automation user
  |
  +-> Task Scheduler: BossAutomation-Daemon
  |     -> PowerShell launcher
  |     -> native Python/uv process
  |     -> DPAPI user-scoped secrets
  |     -> SQLite database in LocalAppData
  |     -> Qwen HTTPS API
  |     -> BOSS read APIs
  |     -> headful Camoufox only when a write is queued
  |
  +-> Task Scheduler: BossAutomation-Dashboard
        -> localhost HTTP server on 127.0.0.1:8765
        -> same SQLite database in read/supervisory mode

Operator browser on the same PC
  -> http://127.0.0.1:8765
  -> monitor, approve/retire templates, pause/resume
```

The daemon and dashboard are separate scheduled tasks. A dashboard failure therefore cannot stop inbox
polling. SQLite WAL, the existing busy timeout, and the daemon lease remain the concurrency controls.

## Windows File Layout

The repository and mutable state must be separated:

```text
C:\BossAutomation\app\
  repository checkout and .venv

%LOCALAPPDATA%\BossCLI\
  data\workflow.db
  data\workflow.db-wal
  data\workflow.db-shm
  logs\daemon.log
  logs\dashboard.log
  backups\
  secrets\secrets.dpapi
  config\deployment.json
```

Decisions:

- `%LOCALAPPDATA%` is user-scoped and appropriate for machine-specific application state.
- The existing macOS and Linux paths will not change.
- `BOSS_WORKFLOW_DB` and explicit `--db` remain supported overrides.
- No database, logs, secrets, or generated task files live inside the git checkout.
- Paths in scripts are resolved to absolute paths before Task Scheduler registration.

## How It Works On The PC

### Startup

1. The dedicated automation user signs into Windows.
2. Task Scheduler starts the daemon and dashboard tasks with an interactive user token.
3. Each PowerShell launcher sets a fixed working directory and invokes the repository's pinned environment.
4. The daemon opens the SQLite database, applies idempotent migrations, and claims the `primary` lease.
5. The daemon reads user-scoped DPAPI secrets, checks configuration, and records a heartbeat.
6. The dashboard binds only to `127.0.0.1:8765` and reads the same database.
7. Task Scheduler ignores duplicate task starts and restarts a process after unexpected exit.

### Every daemon cycle

1. Read the current BOSS inbox through the existing API client.
2. Incrementally persist candidates and changed messages in SQLite.
3. Exclude already-completed, outbound-only, ambiguous, or otherwise ineligible conversations.
4. Redact sensitive context and send only the minimum selection payload to Qwen.
5. Accept only a valid ID from the active approved template catalog.
6. Persist the decision and durable action pair before performing a write.
7. Open a headful Camoufox session for the selected candidate.
8. Verify the candidate target, type the exact immutable template, and send it.
9. Poll the BOSS latest-message API until the exact reply is verified.
10. Only after reply verification, execute the visible `换微信` flow.
11. Verify the BOSS success state, persist the outcome, close the browser, and update the heartbeat.
12. Wait until the next 30-second probe.

### Operator workflow

1. Open `http://127.0.0.1:8765` on the automation PC.
2. Inspect heartbeat freshness, queue state, decisions, verified deliveries, and errors.
3. Use `Pause` before maintenance or investigation. Paused mode still reads the inbox but does not decide or send.
4. Approve a new immutable template version or retire an old one through the dashboard.
5. Use the Windows control script for a graceful daemon stop before updates or shutdown.
6. Run `boss login` interactively when BOSS authentication expires, then restart the daemon.

## Required Code Changes

### 1. Platform paths and request identity

Implement a small platform module used by authentication, workflow state, logs, and deployment commands.

- Use `%LOCALAPPDATA%\BossCLI` on Windows.
- Preserve `~/.config/boss-cli` and `~/.local/share/boss-cli` on current platforms.
- Route the credential file, workflow database, logs, backups, and the existing index cache through the same
  platform helpers instead of scattering `Path.home()` and XDG assumptions.
- Replace the hard-coded macOS API `User-Agent` and `sec-ch-ua-platform` values with a platform-aware header
  builder. Windows API requests must advertise a consistent Windows desktop identity.
- Keep browser-generated Camoufox headers separate from API client headers.
- Do not rely on POSIX `chmod(0600)` as a Windows confidentiality control; use DPAPI for secrets and user-scoped
  directories for non-secret local state.
- Add tests using patched platform/environment values, including paths with spaces and non-ASCII usernames.

### 2. Windows secret storage

Add a provider abstraction instead of reading secrets directly from files or machine-wide environment variables.

- On Windows, encrypt one versioned secret envelope with user-scoped DPAPI through `CryptProtectData` and
  decrypt it with `CryptUnprotectData`.
- Do not use `CRYPTPROTECT_LOCAL_MACHINE`; secrets must remain tied to the dedicated user and PC.
- Store the Alibaba API key and serialized BOSS credential in the encrypted envelope.
- Continue to support process-scoped `DASHSCOPE_API_KEY` and `BOSS_COOKIES` as explicit overrides for CI and
  recovery, without writing them to logs.
- Add `boss secrets set`, `boss secrets status`, and `boss secrets clear` commands. Secret input must use a
  hidden prompt and status output must report presence only.
- Migrate an existing Windows plaintext credential only after explicit confirmation, then remove the plaintext
  file after successful validation.
- Never include secret values in task arguments, task XML, `deployment.json`, logs, dashboard responses, or git.

DPAPI user scope means a copied encrypted file will not work under another user or on another PC. Recovery
therefore requires re-entering the API key and re-running BOSS login, not backing up secret plaintext.

### 3. Configuration and lifecycle control

Introduce a non-secret, validated Windows deployment configuration:

```json
{
  "schema_version": 1,
  "live": false,
  "poll_interval_seconds": 30,
  "error_backoff_seconds": 120,
  "candidate_limit": 20,
  "max_actions_per_cycle": 10,
  "action_delay_seconds": 60,
  "request_wechat": true,
  "model": "qwen-plus",
  "dashboard_host": "127.0.0.1",
  "dashboard_port": 8765
}
```

- Installation writes `live: false` regardless of caller defaults.
- A separate, explicit command enables live mode after acceptance gates pass.
- Add a durable daemon stop request and `boss workflow daemon-stop` command.
- The daemon checks for stop requests before selection, before claiming an action, between browser actions, and
  while waiting for the next cycle.
- A graceful stop releases the lease and records `stopped`; it does not rely on Task Scheduler terminating Python.
- Add `boss workflow healthcheck` with useful process exit codes for fresh heartbeat, stale heartbeat, paused,
  authentication failure, review-required actions, and database failure.
- Authentication failures should automatically stop writes and surface a durable operator-required state.

### 4. Windows deployment scripts

Create signed-ready, idempotent PowerShell scripts under `scripts/windows/`:

| Script | Responsibility |
| --- | --- |
| `Install-BossAutomation.ps1` | Validate Windows, install/sync dependencies, initialize paths, register tasks in dry mode. |
| `Set-BossAutomationSecrets.ps1` | Invoke hidden secret prompts and BOSS login as the automation user. |
| `Start-BossAutomation.ps1` | Start dashboard and daemon tasks after preflight checks. |
| `Stop-BossAutomation.ps1` | Pause, request graceful stop, wait for lease release, then stop the task only if necessary. |
| `Get-BossAutomationStatus.ps1` | Combine Task Scheduler state with application health and heartbeat status. |
| `Backup-BossAutomation.ps1` | Create a consistent SQLite backup and enforce retention. |
| `Update-BossAutomation.ps1` | Stop, backup, move to a pinned revision, sync locked dependencies, migrate, test, and restart dry. |
| `Uninstall-BossAutomation.ps1` | Unregister tasks; retain state by default and require an explicit purge flag to delete it. |

Script requirements:

- Use `Set-StrictMode -Version Latest` and stop on errors.
- Never accept secret values as normal command-line arguments.
- Quote all paths and set the action working directory explicitly.
- Support `-WhatIf` or a validation-only mode where destructive or task-registration actions are involved.
- Write structured, timestamped operational output without candidate text or credentials.
- Be rerunnable without creating duplicate tasks.
- Validate that the caller is the same user that will own the interactive tasks and DPAPI secrets.

### 5. Task Scheduler definitions

Register two tasks in a `BossAutomation` task folder:

**Daemon task**

- Trigger: dedicated user logon, with a short startup delay.
- Principal: dedicated user, `Interactive` logon type, least privilege.
- Action: PowerShell launcher using an absolute script path.
- Multiple instances: `IgnoreNew`.
- Restart: limited retries at a short interval after unexpected process exit.
- Execution time limit: disabled for the long-running process.
- Start when available and do not stop merely because the PC becomes idle.
- Do not embed the Windows password or any application secret in task arguments.

**Dashboard task**

- Same logon trigger and user.
- Bind only to `127.0.0.1`.
- Use `--no-open` so task startup does not create an extra browser tab.
- Restart independently from the daemon.

The first release intentionally uses Task Scheduler instead of a Windows service. Microsoft's interactive task
mode runs only while the configured user is logged on, which matches the headful browser requirement.

### 6. Logging, backup, and recovery

- Add rotating UTF-8 daemon and dashboard logs under `%LOCALAPPDATA%\BossCLI\logs`.
- Retain a bounded number of files and redact cookies, API keys, candidate message text, names, and contact data.
- Keep detailed workflow outcomes in SQLite; logs should identify run/action IDs and error codes, not private text.
- Use SQLite's backup API for online-consistent backups rather than copying the main file while WAL is active.
- Back up before every code update and schema migration, plus one scheduled daily backup.
- Default retention: seven daily backups and three pre-update backups.
- Add a restore command that requires stopped tasks, verifies schema/integrity, and preserves the replaced database.
- Treat `sending` actions left by a forced termination as `needs_review`; never auto-replay them.

### 7. Dependency reproducibility

- Keep `uv.lock` committed and require `uv sync --locked --extra browser` for deployment.
- Pin the supported Python minor version in `.python-version` after Windows validation.
- Validate the exact locked Camoufox and Playwright versions on Windows before changing them.
- Install the manifest-pinned Camoufox artifact with SHA-256 verification and pass a real browser-context smoke
  test before registering tasks.
- Do not silently upgrade Camoufox on the deployment PC; browser-runtime upgrades require a new canary.
- Record app commit, Python, uv, Camoufox, browser binary, and schema versions in diagnostics.

## Implementation Phases

### Phase 1: Portability foundation

1. Add platform-aware path helpers and Windows defaults.
2. Add platform-aware API headers.
3. Add path/header unit tests for Windows, macOS, and Linux.
4. Verify no behavior or path changes on the validated Mac deployment.

**Exit gate:** full existing suite passes on macOS/Linux and the new Windows path tests pass in CI.

### Phase 2: Secrets and control plane

1. Add the secret-provider abstraction and DPAPI provider.
2. Add secret CLI commands and Windows credential migration.
3. Add durable daemon stop requests and healthcheck exit codes.
4. Add authentication-failure operator state and redaction tests.

**Exit gate:** no application secret is required in plaintext configuration, process arguments, or task XML.

### Phase 3: Deployment automation

1. Add validated `deployment.json` loading.
2. Add the PowerShell installation, task registration, control, backup, update, and uninstall scripts.
3. Register separate daemon and dashboard tasks in dry mode.
4. Add script syntax/idempotency checks and operator documentation.

**Exit gate:** a clean Windows sandbox can install, start dry, report healthy, stop gracefully, restart, and uninstall
without manual file editing.

### Phase 4: Windows CI

Extend GitHub Actions with a `windows-latest` job:

1. Install the locked Python version and uv.
2. Run `uv sync --locked --all-extras`.
3. Run Ruff, the full non-live test suite, migrations, CLI help/smoke commands, and package build.
4. Run Windows-only path, DPAPI round-trip, signal/control, and SQLite WAL tests.
5. Parse all PowerShell scripts and run their validation-only paths.
6. Verify the pinned Camoufox artifact digest and launch a blank browser context without performing a BOSS write.

Live BOSS and WeChat tests must not run in public CI because they require a private interactive account and
could send real actions.

**Exit gate:** Linux and Windows CI are both required checks for the branch.

### Phase 5: PC commissioning

Perform the following on the actual automation PC, in order:

1. Record OS build, hardware architecture, Windows user, installed browsers, and network conditions.
2. Install from a pinned commit with `live: false`.
3. Store the replacement Alibaba key through the secret command; never reuse a key exposed in chat or logs.
4. Log into BOSS and validate Chrome and Edge cookie extraction with the browser open and closed.
5. Run all local tests and initialize the production database.
6. Verify the pinned Camoufox receipt, launch it headfully, open BOSS Web, and perform a no-send target resolution.
7. Run three dry daemon cycles and verify lease, heartbeat, Qwen decisions, SQLite state, and dashboard health.
8. Restart Windows, log in, and prove both tasks recover without duplicate daemon ownership.
9. Test an unlocked console session, locked workstation, and disconnected RDP session. Record which states keep
   Camoufox reliable; production must use a proven state.
10. Run one explicitly approved `friendId` canary with at most two actions: one exact reply and its dependent
    WeChat request.
11. Verify the reply through the latest-message API and the WeChat request through the changed BOSS UI state.
12. Run a bounded supervised live window, then a 24-hour soak with heartbeat and queue review.

Stop immediately on login prompts, captcha, risk-control warnings, target mismatch, missing controls, or uncertain
send state. Those conditions are operator work, not retry conditions.

### Phase 6: Production handoff

1. Enable live mode only after the canary and soak gates pass.
2. Document the known-good versions and PC session requirements.
3. Schedule daily database backups and a weekly health review.
4. Train the operator on pause, graceful stop, login repair, review handling, update, and restore.
5. Tag the certified revision and retain the prior Mac runtime as rollback until Windows has operated reliably.

## Windows Acceptance Matrix

| Area | Test | Pass condition |
| --- | --- | --- |
| Install | Clean checkout and locked sync | No manual dependency edits; package and CLI load. |
| Paths | User path contains spaces/non-ASCII | Database, logs, config, and secrets resolve correctly. |
| Secrets | DPAPI round trip | Same user can read; another Windows user cannot decrypt; stored bytes contain no plaintext. |
| Auth | Chrome and Edge extraction | Valid BOSS credential with actionable diagnostics on failure. |
| Read | Three continuous dry cycles | No writes, no failures, fresh heartbeat, stable account identity. |
| Qwen | Approved catalog selection | Only approved IDs; malformed/failed responses create no actions. |
| Browser | Camoufox launch and no-send target check | Correct BOSS conversation is resolved without sending. |
| Reply | One approved canary | Exact immutable message is sent and API-verified once. |
| WeChat | Dependent canary action | Runs only after reply verification and visible success is verified. |
| Scheduler | Reboot and login | One daemon and one dashboard recover; no duplicate lease. |
| Session | Lock/RDP-disconnect matrix | Proven session state keeps headful actions reliable. |
| Network | Temporary outage | Cycle backs off; trigger is not consumed; no duplicate send. |
| Qwen outage | Timeout/5xx | Cycle records failure and no action is queued. |
| Auth expiry | Invalid BOSS session | Writes stop, operator state is visible, login repair works. |
| Forced exit | Kill during idle and during simulated send | Idle recovers; uncertain action becomes review, not replay. |
| Backup | Online backup and restore drill | SQLite integrity passes and dashboard totals match. |
| Update | Stop, backup, upgrade, dry restart | Previous commit/database can be restored. |

## Rollout Stages

1. **Development:** Windows CI only; no account credentials.
2. **Installation:** Real PC, dry mode, no browser writes.
3. **Browser acceptance:** Headful no-send tests and one approved canary.
4. **Bounded live:** Low action cap, long delay, operator present.
5. **Supervised continuous:** Normal polling with daily review.
6. **Certified Windows runtime:** Tag known-good revision after the soak and recovery drills pass.

The action cap and delay should remain conservative at first. Throughput can be increased only after BOSS account
health, target verification, and session reliability are demonstrated on the Windows PC.

## Update And Rollback Procedure

Every production update follows this order:

1. Pause automation in the dashboard.
2. Request a graceful daemon stop and confirm lease release.
3. Create and integrity-check a SQLite backup.
4. Record the current commit and dependency/runtime versions.
5. Move to the reviewed, pinned commit.
6. Run `uv sync --locked --extra browser` and database migrations.
7. Run unit tests and a dry one-cycle health check.
8. Start the dashboard and daemon in dry mode.
9. Run a browser canary if selectors or browser dependencies changed.
10. Explicitly restore live mode.

Rollback restores the previous commit and locked environment first. Restore the database only when the migration
cannot run backward safely; preserve the failed database for diagnosis. Never solve a rollback by deleting queue
or audit rows manually.

## Key Risks And Controls

| Risk | Control |
| --- | --- |
| Windows service cannot display browser UI | Use an interactive-at-logon scheduled task, not Session 0. |
| User logs out or PC sleeps | Dedicated session, no-sleep power policy, stale-heartbeat health signal. |
| RDP disconnect affects rendering | Test session matrix; use the proven local/locked state. |
| Browser or BOSS UI changes | Pin runtime, no-send check, one-candidate canary, stop on uncertain state. |
| Chrome cookie DPAPI changes | Interactive login repair, clear diagnostics, environment override only for recovery. |
| Secrets leak through scripts/tasks | DPAPI user scope, hidden prompts, no secret arguments, log redaction. |
| Task Scheduler force-stops Python | Add durable graceful stop; uncertain in-flight actions go to review. |
| Duplicate task/process | `IgnoreNew` plus the existing SQLite daemon lease. |
| SQLite damage or migration error | WAL discipline, SQLite backup API, integrity check, tested restore. |
| Dependency drift | Committed lockfile, pinned Python/runtime, controlled updates only. |
| Public dashboard exposure | Hard-code/validate localhost binding for the deployment task. |
| Automatic duplicate reply | Durable idempotency keys, exact verification, no replay of uncertain sends. |

## Definition Of Done

Windows deployment is complete only when all of the following are true:

- Windows CI is green alongside the existing suite.
- Installation and removal are script-driven and idempotent.
- Secrets are DPAPI-protected and absent from files, task definitions, logs, and git in plaintext.
- The daemon and dashboard start at user logon as separate tasks.
- Reboot, crash, network outage, Qwen outage, and authentication-expiry behavior have been tested.
- The correct interactive-session requirements are documented from real PC evidence.
- One reply and one dependent WeChat request have passed the controlled Windows canary.
- The dashboard accurately reports Windows daemon heartbeat, decisions, deliveries, and errors.
- Backup and restore have been exercised, not merely documented.
- A 24-hour supervised soak has no duplicate sends, uncertain replays, stale leases, or unhandled failures.
- The certified commit and exact runtime versions are recorded and tagged.

## Planned Commit Sequence

Keep implementation history reviewable with one concern per commit:

1. `docs: define Windows deployment architecture`
2. `feat: add platform-aware Windows paths and headers`
3. `feat: protect Windows secrets with user-scoped DPAPI`
4. `feat: add daemon health and graceful stop controls`
5. `feat: add Windows deployment configuration and scripts`
6. `test: add Windows CI and platform coverage`
7. `docs: add Windows operator and recovery runbook`
8. `fix: address Windows PC acceptance findings` as narrowly scoped commits
9. `docs: certify Windows deployment baseline` only after all acceptance gates pass

## References

- Microsoft Task Scheduler interactive mode: <https://learn.microsoft.com/en-us/windows/win32/taskschd/schtasks>
- Microsoft Task Scheduler logon types: <https://learn.microsoft.com/en-us/windows/win32/taskschd/principal-logontype>
- Microsoft scheduled task restart and instance settings: <https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/new-scheduledtasksettingsset>
- Microsoft DPAPI `CryptProtectData`: <https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata>
- Microsoft Windows known folders: <https://learn.microsoft.com/en-us/windows/win32/shell/knownfolderid>
- uv Windows installation: <https://docs.astral.sh/uv/getting-started/installation/>
- Camoufox installation: <https://camoufox.com/python/installation/>
