[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$AppRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [string]$UserId = [Security.Principal.WindowsIdentity]::GetCurrent().Name,
    [switch]$ValidationOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'BossAutomation.Common.ps1')

Assert-BossWindows
if (-not $ValidationOnly) { Assert-BossWindowsTarget }
$owner = Assert-BossOwner -UserId $UserId
$paths = Get-BossPaths -AppRoot $AppRoot
$null = Get-BossUv
Assert-BossScriptSet -AppRoot $paths.AppRoot

if ($ValidationOnly) {
    Write-BossEvent -Event 'install_validation_succeeded' -Details @{ app_root = $paths.AppRoot; owner = $owner }
    exit 0
}

if ($PSCmdlet.ShouldProcess($paths.StateRoot, 'Create user-scoped application directories')) {
    foreach ($path in @($paths.Data, $paths.Logs, $paths.Backups, $paths.Secrets, $paths.Config)) {
        $null = New-Item -ItemType Directory -Path $path -Force
    }
}

if ($PSCmdlet.ShouldProcess($paths.AppRoot, 'Synchronize locked Python and browser dependencies')) {
    $uv = Get-BossUv
    & $uv '--directory' $paths.AppRoot 'sync' '--locked' '--extra' 'browser' '--extra' 'dev'
    if ($LASTEXITCODE -ne 0) { throw 'uv sync --locked --extra browser --extra dev failed.' }
    & $uv '--directory' $paths.AppRoot 'run' '--frozen' '--no-sync' 'python' '-m' 'boss_cli.camoufox_runtime' 'install' '--smoke'
    if ($LASTEXITCODE -ne 0) { throw 'Pinned Camoufox runtime install or smoke test failed.' }
}

if ($PSCmdlet.ShouldProcess($paths.DeploymentConfig, 'Write a validated dry-mode deployment configuration')) {
    if (Test-Path -LiteralPath $paths.DeploymentConfig) {
        Invoke-BossCli -AppRoot $paths.AppRoot -BossArguments @('deployment', 'validate', '--config', $paths.DeploymentConfig)
        Invoke-BossCli -AppRoot $paths.AppRoot -BossArguments @('deployment', 'disable-live', '--config', $paths.DeploymentConfig)
    }
    else {
        Invoke-BossCli -AppRoot $paths.AppRoot -BossArguments @('deployment', 'init', '--config', $paths.DeploymentConfig)
    }
    Invoke-BossCli -AppRoot $paths.AppRoot -BossArguments @('workflow', 'init-db', '--db', $paths.Database)
    Invoke-BossCli -AppRoot $paths.AppRoot -BossArguments @('workflow', 'install-templates', '--db', $paths.Database)
}

if ($PSCmdlet.ShouldProcess('\BossAutomation', 'Register interactive scheduled tasks')) {
    New-BossTaskFolder
    $powershell = Get-BossPowerShellExecutable
    $principal = New-ScheduledTaskPrincipal -UserId $owner -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet `
        -MultipleInstances IgnoreNew `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -StartWhenAvailable `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries

    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $owner
    $trigger.Delay = 'PT15S'
    $daemonScript = Join-Path $paths.AppRoot 'scripts\windows\Invoke-BossAutomationDaemon.ps1'
    $dashboardScript = Join-Path $paths.AppRoot 'scripts\windows\Invoke-BossAutomationDashboard.ps1'
    $daemonAction = New-ScheduledTaskAction -Execute $powershell -Argument "-NoProfile -File `"$daemonScript`" -AppRoot `"$($paths.AppRoot)`"" -WorkingDirectory $paths.AppRoot
    $dashboardAction = New-ScheduledTaskAction -Execute $powershell -Argument "-NoProfile -File `"$dashboardScript`" -AppRoot `"$($paths.AppRoot)`"" -WorkingDirectory $paths.AppRoot

    Register-ScheduledTask -TaskPath '\BossAutomation\' -TaskName 'BossAutomation-Daemon' -Action $daemonAction -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
    Register-ScheduledTask -TaskPath '\BossAutomation\' -TaskName 'BossAutomation-Dashboard' -Action $dashboardAction -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null

    $backupScript = Join-Path $paths.AppRoot 'scripts\windows\Backup-BossAutomation.ps1'
    $backupAction = New-ScheduledTaskAction -Execute $powershell -Argument "-NoProfile -File `"$backupScript`" -AppRoot `"$($paths.AppRoot)`"" -WorkingDirectory $paths.AppRoot
    $backupTrigger = New-ScheduledTaskTrigger -Daily -At '03:00'
    Register-ScheduledTask -TaskPath '\BossAutomation\' -TaskName 'BossAutomation-Backup' -Action $backupAction -Trigger $backupTrigger -Principal $principal -Settings $settings -Force | Out-Null
}

Write-BossEvent -Event 'install_completed' -Details @{ app_root = $paths.AppRoot; state_root = $paths.StateRoot; live = $false }
