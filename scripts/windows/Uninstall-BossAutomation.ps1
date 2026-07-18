[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [string]$AppRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [switch]$PurgeState,
    [switch]$ValidationOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'BossAutomation.Common.ps1')

Assert-BossWindows
$null = Assert-BossRegisteredOwner
$paths = Get-BossPaths -AppRoot $AppRoot
if ($ValidationOnly) {
    Write-BossEvent -Event 'uninstall_validation_succeeded' -Details @{ purge_state = [bool]$PurgeState }
    exit 0
}

if ((Test-Path -LiteralPath $paths.Database) -and $PSCmdlet.ShouldProcess('\BossAutomation', 'Request graceful stop before uninstall')) {
    & (Join-Path $PSScriptRoot 'Stop-BossAutomation.ps1') -AppRoot $paths.AppRoot -Confirm:$false
}
foreach ($name in @('BossAutomation-Daemon', 'BossAutomation-Dashboard', 'BossAutomation-Backup')) {
    if ($PSCmdlet.ShouldProcess("\BossAutomation\$name", 'Unregister scheduled task')) {
        Unregister-ScheduledTask -TaskPath '\BossAutomation\' -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    }
}

if ($PurgeState -and $PSCmdlet.ShouldProcess($paths.StateRoot, 'Permanently delete database, backups, logs, config, and DPAPI envelope')) {
    Remove-Item -LiteralPath $paths.StateRoot -Recurse -Force -ErrorAction SilentlyContinue
}
Write-BossEvent -Event 'uninstall_completed' -Details @{ state_retained = (-not $PurgeState); state_root = $paths.StateRoot }
