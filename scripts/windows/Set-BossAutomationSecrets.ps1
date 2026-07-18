[CmdletBinding()]
param(
    [string]$AppRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [string]$UserId = [Security.Principal.WindowsIdentity]::GetCurrent().Name,
    [switch]$SkipBossLogin,
    [switch]$ValidationOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'BossAutomation.Common.ps1')

Assert-BossWindows
$owner = Assert-BossOwner -UserId $UserId
$null = Assert-BossRegisteredOwner
$paths = Get-BossPaths -AppRoot $AppRoot
$null = Get-BossUv
if ($ValidationOnly) {
    Write-BossEvent -Event 'secret_setup_validation_succeeded' -Details @{ owner = $owner }
    exit 0
}

# The value is collected by Click with hidden input and never appears in this
# script's parameter block, process command line, or Task Scheduler XML.
Invoke-BossCli -AppRoot $paths.AppRoot -BossArguments @('secrets', 'set', '--api-key')
if (-not $SkipBossLogin) {
    Invoke-BossCli -AppRoot $paths.AppRoot -BossArguments @('login', '--cookie-source', 'chrome')
}
Invoke-BossCli -AppRoot $paths.AppRoot -BossArguments @('secrets', 'status')
Write-BossEvent -Event 'secret_setup_completed' -Details @{ owner = $owner; boss_login_requested = (-not $SkipBossLogin) }
