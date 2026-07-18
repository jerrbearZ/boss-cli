"""Static safety checks for Windows deployment scripts."""

import re
from pathlib import Path


SCRIPTS = Path(__file__).parents[1] / "scripts" / "windows"


def test_required_windows_scripts_exist_and_enable_strict_failure_mode():
    required = {
        "Install-BossAutomation.ps1",
        "Set-BossAutomationSecrets.ps1",
        "Start-BossAutomation.ps1",
        "Stop-BossAutomation.ps1",
        "Get-BossAutomationStatus.ps1",
        "Backup-BossAutomation.ps1",
        "Update-BossAutomation.ps1",
        "Uninstall-BossAutomation.ps1",
    }
    assert required.issubset({path.name for path in SCRIPTS.glob("*.ps1")})
    for path in SCRIPTS.glob("*.ps1"):
        text = path.read_text(encoding="utf-8")
        assert "Set-StrictMode -Version Latest" in text
        assert "$ErrorActionPreference = 'Stop'" in text


def test_scripts_never_accept_secret_values_as_parameters():
    for path in SCRIPTS.glob("*.ps1"):
        text = path.read_text(encoding="utf-8")
        parameter_block = re.search(r"param\((.*?)\)\s*\n", text, re.DOTALL)
        if parameter_block:
            lowered = parameter_block.group(1).lower()
            assert "$apikey" not in lowered
            assert "$bosscookies" not in lowered
            assert "$credential" not in lowered


def test_task_registration_is_interactive_local_and_idempotent():
    text = (SCRIPTS / "Install-BossAutomation.ps1").read_text(encoding="utf-8")
    assert "-LogonType Interactive" in text
    assert "-RunLevel Limited" in text
    assert "-MultipleInstances IgnoreNew" in text
    assert "Register-ScheduledTask" in text
    assert "-Force" in text
    assert "Invoke-BossAutomationDaemon.ps1" in text
    assert "Invoke-BossAutomationDashboard.ps1" in text


def test_windows_install_uses_digest_pinned_browser_and_frozen_runtime():
    install = (SCRIPTS / "Install-BossAutomation.ps1").read_text(encoding="utf-8")
    update = (SCRIPTS / "Update-BossAutomation.ps1").read_text(encoding="utf-8")
    for text in (install, update):
        assert "boss_cli.camoufox_runtime' 'install' '--smoke'" in text
        assert "'camoufox' 'fetch'" not in text
    common = (SCRIPTS / "BossAutomation.Common.ps1").read_text(encoding="utf-8")
    assert "'run' '--frozen' '--no-sync' 'boss'" in common


def test_windows_update_requires_full_commit_clean_checkout_and_quality_gates():
    update = (SCRIPTS / "Update-BossAutomation.ps1").read_text(encoding="utf-8")
    assert "^[0-9a-fA-F]{40}$" in update
    assert "status' '--porcelain' '--untracked-files=normal" in update
    assert "'ruff' 'format' '--check'" in update
    assert "'pyright' 'boss_cli'" in update
    assert "[System.Management.Automation.Language.Parser]::ParseFile" in update
    assert "'build'" in update
