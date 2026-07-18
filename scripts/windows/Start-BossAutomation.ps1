[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$AppRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [switch]$ValidationOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'BossAutomation.Common.ps1')

Assert-BossWindows
$paths = Get-BossPaths -AppRoot $AppRoot
$null = Assert-BossOwner -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name)
$null = Assert-BossRegisteredOwner
$null = Get-BossUv
Assert-BossScriptSet -AppRoot $paths.AppRoot

if ($ValidationOnly) {
    Write-BossEvent -Event 'start_validation_succeeded' -Details @{ app_root = $paths.AppRoot }
    exit 0
}

Invoke-BossCli -AppRoot $paths.AppRoot -BossArguments @('deployment', 'validate', '--config', $paths.DeploymentConfig)
$uv = Get-BossUv
$secretJson = & $uv '--directory' $paths.AppRoot 'run' '--frozen' '--no-sync' 'boss' 'secrets' 'status' '--json'
if ($LASTEXITCODE -ne 0) { throw 'Unable to inspect protected secret status.' }
$secretStatus = $secretJson | ConvertFrom-Json
if (-not $secretStatus.data.dashscope_api_key_present -or -not $secretStatus.data.boss_credential_present) {
    throw 'Both the Alibaba API key and BOSS credential must be present before start.'
}
$authJson = & $uv '--directory' $paths.AppRoot 'run' '--frozen' '--no-sync' 'boss' 'status' '--json'
if ($LASTEXITCODE -ne 0) { throw 'BOSS authentication preflight failed.' }
$authStatus = $authJson | ConvertFrom-Json
$authHealthy = $false
if ($null -ne $authStatus.PSObject.Properties['authenticated']) {
    $authHealthy = [bool]$authStatus.authenticated
}
elseif ($null -ne $authStatus.PSObject.Properties['data']) {
    $authHealthy = [bool]$authStatus.data.authenticated
}
if (-not $authHealthy) {
    throw 'BOSS authentication is not healthy. Run Set-BossAutomationSecrets.ps1 or boss login.'
}

if ($PSCmdlet.ShouldProcess('\BossAutomation\BossAutomation-Dashboard and Daemon', 'Start scheduled tasks')) {
    Invoke-BossCli -AppRoot $paths.AppRoot -BossArguments @('workflow', 'daemon-start', '--db', $paths.Database)
    Start-ScheduledTask -TaskPath '\BossAutomation\' -TaskName 'BossAutomation-Dashboard'
    Start-ScheduledTask -TaskPath '\BossAutomation\' -TaskName 'BossAutomation-Daemon'
}
Write-BossEvent -Event 'start_requested' -Details @{ live = ((Get-Content -Raw -LiteralPath $paths.DeploymentConfig | ConvertFrom-Json).live) }
