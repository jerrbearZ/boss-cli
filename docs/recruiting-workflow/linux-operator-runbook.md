# Linux Automation Operator Runbook

**Supported baseline:** Ubuntu 24.04 LTS x86_64 desktop, dedicated non-root user, `systemd --user`

**Repository implementation:** complete

**PC certification:** pending target-PC commissioning, controlled canary, recovery drill, and 24-hour soak

Run all commands from the same logged-in desktop user that owns the browser profile, Secret Service collection,
systemd user manager, and workflow database. Do not use `sudo`, a root service, a logged-out session, or SSH as
the production browser session.

## Install In Dry Mode

Fully patch Ubuntu, install Chrome/Chromium/Edge/Brave/Firefox, `uv`, and the Camoufox runtime libraries, disable
automatic sleep, then place a pinned clean checkout at a stable path such as `~/.local/opt/boss-cli/app`:

```bash
sudo apt-get install libgtk-3-0t64 libx11-xcb1 libasound2t64
```

Then, as the dedicated non-root desktop user:

```bash
cd ~/.local/opt/boss-cli/app
scripts/linux/install-boss-automation.sh --app-root "$PWD"
scripts/linux/set-boss-automation-secrets.sh --app-root "$PWD" --cookie-source chrome
scripts/linux/start-boss-automation.sh --app-root "$PWD"
scripts/linux/get-boss-automation-status.sh --app-root "$PWD"
```

The installer performs a locked sync, downloads the exact manifest-pinned Camoufox archive, verifies its size
and SHA-256 digest, passes a real browser-context smoke test, validates Linux Secret Service access, initializes
schema 6 and approved templates, writes dry deployment configuration, renders hardened user units, and enables
them. It does not start the daemon or enable browser writes.

If an older `~/.config/boss-cli/credential.json` exists, review it and rerun secret setup with
`--migrate-credential`. Successful migration verifies the credential, saves it in Secret Service, reloads it, and
then deletes the plaintext file.

## Services And State

```bash
systemctl --user status boss-automation-daemon.service
systemctl --user status boss-automation-dashboard.service
systemctl --user status boss-automation-backup.timer
journalctl --user -u boss-automation-daemon.service -n 100 --no-pager
```

The dashboard is available only at `http://127.0.0.1:8765`. It validates loopback Host/Origin headers, requires
JSON for writes, emits restrictive browser security headers, and has no endpoint that manually sends messages.

Mutable state defaults to:

```text
~/.config/boss-cli/deployment.json
~/.local/share/boss-cli/workflow.db
~/.local/share/boss-cli/backups/
~/.local/state/boss-cli/logs/
~/.cache/camoufox/
```

The systemd sandbox makes the host filesystem read-only to the services except for the exact application and
Camoufox roots they require. Runtime commands use the already synchronized lockfile environment with
`--frozen --no-sync`.

Use `boss secrets status --json` for presence-only secret diagnostics. Never export secrets machine-wide or put
them in systemd `Environment=`/`EnvironmentFile=` directives.

## Health Codes

`get-boss-automation-status.sh` combines user-service state with application health and exits with:

| Code | Meaning | Operator action |
| ---: | --- | --- |
| 0 | Fresh running heartbeat | None. |
| 2 | Stale, stopped, or not started | Check services, desktop session, and logs. |
| 3 | Paused | Complete maintenance/review, then resume. |
| 4 | BOSS authentication failure | Repair login in the desktop session, then resume. |
| 5 | Review-required or terminal action | Reconcile BOSS state; never replay blindly. |
| 6 | Database open/integrity failure | Keep services stopped and diagnose/restore. |

## Graceful Stop And Restart

```bash
scripts/linux/stop-boss-automation.sh --app-root "$PWD"
uv run boss workflow resume --db "${XDG_DATA_HOME:-$HOME/.local/share}/boss-cli/workflow.db"
scripts/linux/start-boss-automation.sh --app-root "$PWD"
```

Stop pauses writes, persists a daemon stop request, asks systemd to stop without blocking, and waits for lease
release. Only after timeout does it kill the service and force-release ownership; any in-flight `sending` action
moves to `needs_review`. Resume is intentionally separate.

## Authentication Repair

Run repair from the unlocked graphical session so browser cookies and Secret Service are available:

```bash
uv run boss login --cookie-source chrome
uv run boss status --json
uv run boss workflow resume --db "${XDG_DATA_HOME:-$HOME/.local/share}/boss-cli/workflow.db"
scripts/linux/start-boss-automation.sh --app-root "$PWD"
```

Stop on captcha, risk-control warnings, or keyring prompts that cannot be resolved in the dedicated desktop
session. A service must never open an unattended interactive login flow.

## Backup, Restore, Update, And Uninstall

```bash
scripts/linux/backup-boss-automation.sh --app-root "$PWD" --kind manual
scripts/linux/restore-boss-automation.sh --app-root "$PWD" --backup <verified-backup.db>
scripts/linux/update-boss-automation.sh --app-root "$PWD" --revision <40-character-commit-sha>
scripts/linux/uninstall-boss-automation.sh --app-root "$PWD"
```

Restore leaves services stopped and automation paused. Update requires a clean checkout, creates an integrity-
checked backup, moves to a locally available pinned commit, runs lint/format/type/tests and migrations, and
restarts dry. Uninstall retains config, database, backups, logs, browser cache, and keyring secrets by default.
Permanent removal of those user-scoped assets requires:

```bash
scripts/linux/uninstall-boss-automation.sh --app-root "$PWD" --purge-state --yes
```

## Live Certification

Keep `deployment.json` dry while completing three dry cycles, headful no-send resolution, reboot recovery,
desktop lock/display/remote-session tests, outage and auth-expiry tests, forced-exit behavior, and restore drill.

Run the isolated direct canary only with an explicitly approved `friendId` and the scheduled daemon stopped:

```bash
uv run boss workflow daemon \
  --once \
  --live \
  --request-wechat \
  --friend-id <friendId> \
  --candidate-limit 1 \
  --max-actions 2 \
  --action-delay 60 \
  --json
```

After independent reply and WeChat verification, use conservative deployment limits for the supervised live
window, explicitly enable live mode, restart, and observe the 24-hour soak:

```bash
uv run boss deployment enable-live --yes
scripts/linux/stop-boss-automation.sh --app-root "$PWD"
uv run boss workflow resume --db "${XDG_DATA_HOME:-$HOME/.local/share}/boss-cli/workflow.db"
scripts/linux/start-boss-automation.sh --app-root "$PWD"
```

Record `boss deployment diagnostics --json`, commit SHA, desktop/session state, canary action IDs, backup/restore
evidence, and soak result. Only then tag the Linux runtime as production-certified.
