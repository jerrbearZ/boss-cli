[CmdletBinding()]
param(
    [string]$AppRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
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
    Write-BossEvent -Event 'status_validation_succeeded' -Details @{ database = $paths.Database }
    exit 0
}

$taskStates = @{}
foreach ($name in @('BossAutomation-Daemon', 'BossAutomation-Dashboard', 'BossAutomation-Backup')) {
    $task = Get-ScheduledTask -TaskPath '\BossAutomation\' -TaskName $name -ErrorAction SilentlyContinue
    $taskStates[$name] = if ($null -eq $task) { 'NotInstalled' } else { [string]$task.State }
}

$uv = Get-BossUv
$healthOutput = & $uv '--directory' $paths.AppRoot 'run' '--frozen' '--no-sync' 'boss' 'workflow' 'healthcheck' '--db' $paths.Database '--json'
$healthExitCode = $LASTEXITCODE
$health = $null
try { $health = ($healthOutput | ConvertFrom-Json).data } catch { $health = @{ status = 'unparseable' } }
$record = [ordered]@{
    timestamp = [DateTime]::UtcNow.ToString('o')
    tasks = $taskStates
    health_exit_code = $healthExitCode
    health = $health
}
Write-Output ($record | ConvertTo-Json -Depth 8)
exit $healthExitCode
