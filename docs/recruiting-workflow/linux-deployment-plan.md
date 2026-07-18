# Native Linux Automation Deployment Plan

**Target:** Ubuntu 24.04 LTS x86_64 desktop

**Repository implementation:** complete on this branch

**Production certification:** pending execution on the target Linux PC, controlled canary, recovery drill, and
24-hour supervised soak

## Quality Gate Findings

The Linux deployment was added only after auditing the existing application, workflow engine, tests, packaging,
security posture, and deployment controls. The audit found and addressed these material issues:

- Linux runtime paths existed but did not honor all XDG locations.
- Linux persisted BOSS cookies in a mode-600 plaintext JSON file and had no protected store for the Alibaba key.
- Database migration backups were Windows-only.
- Python formatting was inconsistent and one Rich-output assertion depended on terminal ANSI behavior.
- The localhost dashboard accepted cross-origin writes and arbitrary request content types.
- The dependency lock contained packages with published security fixes available.
- Linux browser-profile discovery used macOS-style Chrome paths.
- No Linux supervisor, recovery scripts, native validation, or operator runbook existed.

The resulting implementation uses XDG paths, Linux Secret Service, private file modes, all-platform guarded
migrations, origin/host validation for dashboard writes, a refreshed lockfile, native Linux browser paths,
user-systemd units, tests, and CI validation.

## Supported Deployment Shape

- Ubuntu 24.04 LTS or newer, x86_64, fully patched.
- Dedicated non-root user with a normal interactive X11 or Wayland desktop session.
- `systemd --user`; no root system service, Docker container, SSH-only session, or lingering headless user manager.
- Chrome, Chromium, Edge, Brave, or Firefox installed and logged into the intended BOSS account.
- Ubuntu Camoufox runtime libraries `libgtk-3-0t64`, `libx11-xcb1`, and `libasound2t64` installed.
- An unlocked Secret Service provider such as GNOME Keyring in the same desktop session.
- Python 3.13 selected by the committed `.python-version`, with dependencies synchronized from `uv.lock`.
- Camoufox and Playwright installed at exact package versions; the exact per-platform browser archive is
  downloaded from the recorded release URL, checked against its size and SHA-256 digest, and smoke-tested.
- Stable network and a power policy that prevents sleep during automation.

The headful browser requirement makes a container or system-level daemon a poor first production target. A user
service attached to `graphical-session.target` retains the desktop and keyring boundaries while still providing
restart, journal, timer, and graceful termination semantics.

Camoufox's own [current project guidance](https://camoufox.com/) warns that 2026 browser releases are actively
changing and may be experimental. The exact artifact pin prevents silent drift, but it does not replace the
target-PC BOSS canary and supervised soak.

## Runtime Layout

Default paths follow the XDG base-directory specification and honor its environment overrides:

```text
${XDG_CONFIG_HOME:-~/.config}/boss-cli/
  deployment.json

${XDG_DATA_HOME:-~/.local/share}/boss-cli/
  workflow.db
  backups/

${XDG_STATE_HOME:-~/.local/state}/boss-cli/
  logs/daemon.log
  logs/dashboard.log

${XDG_CACHE_HOME:-~/.cache}/camoufox/
  pinned browser runtime

${XDG_CONFIG_HOME:-~/.config}/systemd/user/
  boss-automation-daemon.service
  boss-automation-dashboard.service
  boss-automation-backup.service
  boss-automation-backup.timer
```

The Alibaba key and BOSS credential are stored together in one versioned item in the current user's Secret
Service collection. The item is not written to the checkout, deployment JSON, systemd unit, environment file,
SQLite database, or logs. `DASHSCOPE_API_KEY` and `BOSS_COOKIES` remain explicit process-only overrides for CI
and recovery, not a production persistence mechanism.

## Supervisor And Safety Controls

- Daemon: user service tied to the graphical session, `Restart=on-failure`, bounded start rate, `SIGTERM`, and a
  180-second stop window.
- Dashboard: independent user service bound only to `127.0.0.1`.
- Backup: oneshot user service with a persistent daily timer and randomized start delay.
- Units use `UMask=0077`, `NoNewPrivileges`, private temporary storage, read-only system mounts with explicit
  write access only to required application/cache roots, restricted address families, and kernel/control-group
  hardening that does not block the desktop browser.
- Service-time `uv` commands are frozen and `--no-sync`; dependency changes occur only during a stopped,
  pinned install or update transaction.
- The floating default Camoufox add-on is disabled because the recruiting flow does not need it; this prevents
  an unpinned extension download during first browser launch.
- The SQLite daemon lease prevents duplicate owners. A force-stop moves any `sending` action to `needs_review`.
- Installation and update always write or restore `live: false`; live mode requires an explicit operator action.
- Updates require a clean checkout and a full locally available commit SHA, stop gracefully, back up, test, and
  roll back the code/environment on failure.

## Acceptance And Rollout

1. Require green Linux and Windows CI on the pinned commit.
2. Install on the target Linux PC in dry mode and record OS, kernel, desktop/session type, browser, keyring,
   Python, uv, Camoufox, and commit diagnostics.
3. Prove Secret Service round-trip and ensure no legacy plaintext `credential.json` remains.
4. Validate browser cookie extraction with the browser open and closed.
5. Run Camoufox headfully and resolve the exact target conversation without sending.
6. Run three dry daemon cycles with a fresh heartbeat, stable identity, correct approved-template selection,
   zero outbound actions, and a clean queue.
7. Reboot and log in; prove exactly one daemon and dashboard recover.
8. Test unlocked, locked, display-off, and remote-session states; document the one supported production state.
9. Exercise temporary network/Qwen failure, authentication expiry, idle/process termination, uncertain-send
   recovery, backup/restore, and pinned update/rollback.
10. Run one explicitly approved `friendId` canary: one immutable reply followed by its dependent visible WeChat
    request, both independently verified.
11. Run a bounded supervised live window with conservative candidate/action caps and long delays.
12. Complete a 24-hour supervised soak with no duplicate sends, uncertain replay, stale lease, missing backup, or
    unhandled failure.
13. Record and tag the certified commit and exact runtime/session requirements.

Stop immediately on a login prompt, captcha, risk-control warning, target mismatch, missing browser control,
unverified message, or uncertain send. Those conditions require operator review and must not be retried blindly.

## Definition Of Done

Linux is production-certified only when the target-PC evidence—not merely repository tests—shows:

- clean scripted install, dry start, status, graceful stop, restart, update, restore, and uninstall;
- protected secrets and private state paths under the dedicated user;
- reliable browser execution in a documented desktop-session state;
- reboot, outage, auth-expiry, crash, and recovery behavior without duplicate ownership or sends;
- a verified isolated reply/WeChat canary;
- a successful restore drill and daily timer execution;
- a clean 24-hour supervised soak; and
- a tagged commit with recorded runtime versions and rollback point.
