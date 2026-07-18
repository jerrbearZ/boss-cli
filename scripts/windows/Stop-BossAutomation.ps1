[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$AppRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [ValidateRange(5, 600)][int]$TimeoutSeconds = 90,
    [switch]$ValidationOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'BossAutomation.Common.ps1')

Assert-BossWindows
$null = Assert-BossRegisteredOwner
$paths = Get-BossPaths -AppRoot $AppRoot
$null = Get-BossUv
if ($ValidationOnly) {
    Write-BossEvent -Event 'stop_validation_succeeded' -Details @{ timeout_seconds = $TimeoutSeconds }
    exit 0
}
if ($WhatIfPreference) {
    Write-BossEvent -Event 'stop_whatif_completed' -Details @{ database = $paths.Database }
    exit 0
}

if ($PSCmdlet.ShouldProcess($paths.Database, 'Pause automation and request a graceful daemon stop')) {
    Invoke-BossCli -AppRoot $paths.AppRoot -BossArguments @('workflow', 'pause', '--db', $paths.Database, '--reason', 'windows_maintenance')
    Invoke-BossCli -AppRoot $paths.AppRoot -BossArguments @('workflow', 'daemon-stop', '--db', $paths.Database, '--reason', 'windows_maintenance')
}

$released = $false
$deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
$uv = Get-BossUv
while ([DateTime]::UtcNow -lt $deadline) {
    $statusJson = & $uv '--directory' $paths.AppRoot 'run' '--frozen' '--no-sync' 'boss' 'workflow' 'daemon-status' '--db' $paths.Database '--json'
    if ($LASTEXITCODE -eq 0) {
        $status = $statusJson | ConvertFrom-Json
        if ($null -eq $status.data.PSObject.Properties['owner_id'] -or $null -eq $status.data.owner_id) {
            $released = $true
            break
        }
    }
    Start-Sleep -Seconds 1
}

if (-not $released -and $PSCmdlet.ShouldProcess('\BossAutomation\BossAutomation-Daemon', 'Force-stop task after graceful timeout')) {
    Stop-ScheduledTask -TaskPath '\BossAutomation\' -TaskName 'BossAutomation-Daemon' -ErrorAction SilentlyContinue
    Invoke-BossCli -AppRoot $paths.AppRoot -BossArguments @('workflow', 'daemon-force-release', '--db', $paths.Database, '--yes')
}
if ($PSCmdlet.ShouldProcess('\BossAutomation\BossAutomation-Dashboard', 'Stop dashboard task')) {
    Stop-ScheduledTask -TaskPath '\BossAutomation\' -TaskName 'BossAutomation-Dashboard' -ErrorAction SilentlyContinue
}
Write-BossEvent -Event 'stop_completed' -Level $(if ($released) { 'info' } else { 'warning' }) -Details @{ lease_released = $released; forced = (-not $released) }
