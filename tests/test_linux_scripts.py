"""Static and validation-only checks for the Linux user-systemd deployment."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / "scripts" / "linux"
SYSTEMD = SCRIPTS / "systemd"


def test_required_linux_scripts_are_strict_and_do_not_use_sudo():
    required = {
        "install-boss-automation.sh",
        "set-boss-automation-secrets.sh",
        "start-boss-automation.sh",
        "stop-boss-automation.sh",
        "get-boss-automation-status.sh",
        "backup-boss-automation.sh",
        "restore-boss-automation.sh",
        "update-boss-automation.sh",
        "uninstall-boss-automation.sh",
        "invoke-boss-automation-daemon.sh",
        "invoke-boss-automation-dashboard.sh",
        "validate-boss-automation-runtime.sh",
    }
    assert required.issubset({path.name for path in SCRIPTS.glob("*.sh")})
    for path in SCRIPTS.glob("*.sh"):
        text = path.read_text(encoding="utf-8")
        assert text.startswith("#!/usr/bin/env bash")
        assert "set -Eeuo pipefail" in text
        assert "sudo " not in text
        if sys.platform != "win32":
            subprocess.run(["bash", "-n", str(path)], check=True)


def test_linux_scripts_never_accept_secret_values_as_arguments():
    forbidden = ("--api-key-value", "--boss-cookies", "DASHSCOPE_API_KEY=", "BOSS_COOKIES=")
    for path in SCRIPTS.glob("*.sh"):
        text = path.read_text(encoding="utf-8")
        assert all(value not in text for value in forbidden)


def test_linux_install_uses_digest_pinned_browser_runtime():
    install = (SCRIPTS / "install-boss-automation.sh").read_text(encoding="utf-8")
    update = (SCRIPTS / "update-boss-automation.sh").read_text(encoding="utf-8")
    for text in (install, update):
        assert "boss_cli.camoufox_runtime install --smoke" in text
        assert "python -m camoufox fetch" not in text
    common = (SCRIPTS / "boss-automation-common.sh").read_text(encoding="utf-8")
    assert "run --frozen --no-sync boss" in common


def test_systemd_units_are_user_scoped_hardened_and_interactive():
    daemon = (SYSTEMD / "boss-automation-daemon.service.in").read_text(encoding="utf-8")
    dashboard = (SYSTEMD / "boss-automation-dashboard.service.in").read_text(encoding="utf-8")
    timer = (SYSTEMD / "boss-automation-backup.timer.in").read_text(encoding="utf-8")

    assert "graphical-session.target" in daemon
    assert "--require-session --require-secrets" in daemon
    assert "Restart=on-failure" in daemon
    assert "KillSignal=SIGTERM" in daemon
    assert "NoNewPrivileges=yes" in daemon
    assert "UMask=0077" in daemon
    assert "ReadWritePaths=@CONFIG_ROOT@ @DATA_ROOT@ @STATE_ROOT@ @CAMOUFOX_CACHE_ROOT@" in daemon
    assert "--no-open" not in daemon
    assert "WantedBy=default.target" in dashboard
    assert "Persistent=true" in timer
    assert "OnCalendar=" in timer


@pytest.mark.skipif(sys.platform == "win32", reason="systemd rendering requires POSIX path semantics")
def test_systemd_renderer_quotes_spaces_percent_and_unicode(tmp_path):
    module_path = SCRIPTS / "render-systemd-units.py"
    spec = importlib.util.spec_from_file_location("boss_linux_systemd_renderer", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    app_root = tmp_path / "招聘 automation % app"
    app_root.mkdir()
    uv_path = tmp_path / "uv tool"
    uv_path.touch(mode=0o700)
    output = tmp_path / "units"

    rendered = module.render_units(
        app_root,
        uv_path,
        output,
        xdg_config_home=tmp_path / "config home",
        xdg_data_home=tmp_path / "data home",
        xdg_state_home=tmp_path / "state home",
        xdg_cache_home=tmp_path / "cache home",
    )
    daemon = (output / "boss-automation-daemon.service").read_text(encoding="utf-8")

    assert len(rendered) == 4
    assert "@APP_ROOT@" not in daemon
    assert "招聘 automation %% app" in daemon
    assert f'--uv "{uv_path}"' in daemon
    assert 'Environment="XDG_CONFIG_HOME=' in daemon
    assert 'Environment="XDG_CACHE_HOME=' in daemon
    assert 'ReadWritePaths="' in daemon
    assert oct((output / "boss-automation-daemon.service").stat().st_mode & 0o777) == "0o600"


@pytest.mark.skipif(sys.platform != "linux", reason="validation-only paths require native Linux")
def test_linux_scripts_run_validation_only_paths():
    uv_path = shutil.which("uv")
    assert uv_path
    scripts = [
        "install-boss-automation.sh",
        "set-boss-automation-secrets.sh",
        "start-boss-automation.sh",
        "stop-boss-automation.sh",
        "get-boss-automation-status.sh",
        "backup-boss-automation.sh",
        "update-boss-automation.sh",
        "uninstall-boss-automation.sh",
        "validate-boss-automation-runtime.sh",
    ]
    for name in scripts:
        subprocess.run(
            [
                "bash",
                str(SCRIPTS / name),
                "--app-root",
                str(ROOT),
                "--uv",
                uv_path,
                "--validation-only",
            ],
            check=True,
            env={**os.environ, "LC_ALL": "C.UTF-8"},
        )
    subprocess.run(
        [
            "bash",
            str(SCRIPTS / "restore-boss-automation.sh"),
            "--app-root",
            str(ROOT),
            "--uv",
            uv_path,
            "--backup",
            str(ROOT / "uv.lock"),
            "--validation-only",
        ],
        check=True,
    )
