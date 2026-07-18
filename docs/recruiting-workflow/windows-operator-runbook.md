# Windows Automation Operator Runbook

**Repository implementation:** complete for schema 6

**PC certification:** pending target-PC commissioning, canary, recovery drill, and 24-hour soak

This runbook operates the native Windows 11 deployment described in
[windows-deployment-plan.md](./windows-deployment-plan.md). Run every command from a native PowerShell session
as the same dedicated, non-administrator Windows user that owns the BOSS browser session, scheduled tasks, and
DPAPI secrets. Do not use WSL, a service account, `LocalSystem`, or a logged-out session.

Before installation, use a physical Windows 11 Pro PC, install Chrome or Edge, fully patch Windows, connect a
stable network, and configure AC/battery policy so the PC does not sleep. Lock/RDP behavior is still an acceptance
test; do not assume that a disconnected graphical session can run Camoufox reliably.

## Install In Dry Mode

Place the pinned repository checkout at `C:\BossAutomation\app`, log in as the automation user, and run:

```powershell
Set-Location C:\BossAutomation\app
./scripts/windows/Install-BossAutomation.ps1 -AppRoot $PWD
./scripts/windows/Set-BossAutomationSecrets.ps1 -AppRoot $PWD
./scripts/windows/Start-BossAutomation.ps1 -AppRoot $PWD
./scripts/windows/Get-BossAutomationStatus.ps1 -AppRoot $PWD
```

Installation always rewrites `deployment.json` with `live: false`, synchronizes `uv.lock`, downloads and
SHA-256-verifies the exact Camoufox artifact, passes a blank-context browser smoke test, initializes schema 6,
installs the approved template catalog, and idempotently registers the interactive tasks. It never accepts a
secret as a PowerShell parameter.

The tasks are:

- `\BossAutomation\BossAutomation-Daemon`, triggered at user logon;
- `\BossAutomation\BossAutomation-Dashboard`, independently triggered at user logon;
- `\BossAutomation\BossAutomation-Backup`, triggered daily at 03:00.

Daemon and dashboard use `Interactive` logon, least privilege, `IgnoreNew`, bounded restart attempts, absolute
launchers, and an explicit working directory. The dashboard launcher obtains its bind address only from validated
configuration, which requires `127.0.0.1`.

## Local State And Secrets

Mutable files live outside the checkout:

```text
%LOCALAPPDATA%\BossCLI\
  data\workflow.db
  logs\daemon.log
  logs\dashboard.log
  logs\operations.log
  backups\
  secrets\secrets.dpapi
  config\deployment.json
```

The one DPAPI file contains a versioned envelope for the Alibaba API key and serialized BOSS credential. DPAPI
uses the current Windows user and does not use machine scope. A copied envelope cannot be restored on another PC
or under another user. Recovery is to re-enter the API key and run `boss login`, never to export plaintext.

Useful presence-only checks are:

```powershell
uv run boss secrets status --json
uv run boss deployment validate --json
uv run boss deployment diagnostics --db "$env:LOCALAPPDATA\BossCLI\data\workflow.db" --json
```

`BOSS_COOKIES` and `DASHSCOPE_API_KEY` remain process-only recovery/CI overrides and take precedence without
being persisted. Never set them machine-wide.

## Health And Dashboard

Open `http://127.0.0.1:8765` only on the automation PC. Closing the browser tab does not stop the daemon.

`boss workflow healthcheck` and `Get-BossAutomationStatus.ps1` use these process exit codes:

| Code | Meaning | Operator action |
| ---: | --- | --- |
| 0 | Fresh running heartbeat | None. |
| 2 | Stale, stopped, or not started | Check task/session state and logs. |
| 3 | Paused | Complete maintenance/review, then resume. |
| 4 | BOSS authentication failure | Run `boss login`, verify status, then resume and start. |
| 5 | Uncertain or review-required action | Inspect the queue; never replay blindly. |
| 6 | Database open/integrity failure | Keep tasks stopped and begin restore diagnosis. |

The dashboard and logs expose run IDs, action IDs, status, and redacted error codes. They must not expose API
keys, cookies, candidate message text, contact values, or names.

## Graceful Stop And Restart

```powershell
./scripts/windows/Stop-BossAutomation.ps1 -AppRoot $PWD
uv run boss workflow resume --db "$env:LOCALAPPDATA\BossCLI\data\workflow.db"
./scripts/windows/Start-BossAutomation.ps1 -AppRoot $PWD
```

Stop first pauses writes, persists a daemon stop request, and waits for lease release. Resume is deliberately a
separate operator action; omit it when the deployment should remain paused. Only after timeout does the stop script
force-stop the task. The daemon checks the durable request before a cycle, before selection, before queue claims,
between browser actions, and during poll/action delays. A new controlled start clears the old request.

If a process dies while an action is `sending`, a forced supervisor stop moves it immediately to `needs_review`;
an expired-lease takeover provides the same protection with `uncertain_after_process_exit`. Do not manually
change either result to `queued`; check BOSS state and reconcile it through the operator workflow.

## Authentication Repair

An expired BOSS session persists `operator_required=not_authenticated`, pauses writes, releases the daemon as
`authentication_failure`, and makes health return 4.

```powershell
uv run boss login --cookie-source chrome
uv run boss status --json
uv run boss workflow resume --db "$env:LOCALAPPDATA\BossCLI\data\workflow.db"
./scripts/windows/Start-BossAutomation.ps1 -AppRoot $PWD
```

If browser extraction fails, close Chrome and retry Edge/Chrome diagnostics in the interactive console. Stop on
captcha, login prompts, risk-control warnings, target mismatch, missing controls, or uncertain send state.

## Backup, Restore, Update, And Rollback

Create an online backup without copying a live WAL database:

```powershell
./scripts/windows/Backup-BossAutomation.ps1 -AppRoot $PWD -Kind manual
```

Daily retention is seven files and pre-update retention is three files. Restore requires stopped tasks, validates
SQLite integrity/schema, and preserves the replaced database:

```powershell
./scripts/windows/Restore-BossAutomation.ps1 -AppRoot $PWD -BackupPath <verified-backup.db>
```

Update only to a reviewed commit already available locally:

```powershell
./scripts/windows/Update-BossAutomation.ps1 -AppRoot $PWD -Revision <full-commit-sha>
```

Update requires a clean checkout and full commit SHA, then stops, backs up, performs a locked sync, verifies the
pinned browser, migrates, and runs lint, format, type, PowerShell parse, test, and build gates before restarting
dry. On failure it restores the previous commit and locked environment but leaves live mode disabled. Restore the
database only if the new migration cannot be used safely; retain the failed database for diagnosis.

Uninstall retains state unless purge is explicitly confirmed:

```powershell
./scripts/windows/Uninstall-BossAutomation.ps1 -AppRoot $PWD
./scripts/windows/Uninstall-BossAutomation.ps1 -AppRoot $PWD -PurgeState
```

## Live Enablement And PC Certification

Do not enable live mode merely because installation and CI pass. First complete the plan's PC commissioning
matrix: three dry cycles, headful no-send resolution, reboot recovery, console/lock/RDP session tests, temporary
network/Qwen outage tests, forced-exit review behavior, backup/restore drill, one explicitly approved `friendId`
canary, bounded supervised live operation, and a 24-hour soak.

After those gates pass:

```powershell
uv run boss deployment enable-live --yes
./scripts/windows/Stop-BossAutomation.ps1 -AppRoot $PWD
./scripts/windows/Start-BossAutomation.ps1 -AppRoot $PWD
```

Record `boss deployment diagnostics --json`, the tested Windows session state, canary action IDs, recovery drill,
and soak result. Only then tag the certified commit. No target-PC or live BOSS acceptance evidence is claimed by
the repository implementation alone.
