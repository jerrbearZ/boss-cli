[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$AppRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [ValidateSet('daily', 'pre-update', 'manual')][string]$Kind = 'daily',
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
    Write-BossEvent -Event 'backup_validation_succeeded' -Details @{ kind = $Kind; database = $paths.Database }
    exit 0
}
if ($PSCmdlet.ShouldProcess($paths.Database, "Create $Kind SQLite backup")) {
    Invoke-BossCli -AppRoot $paths.AppRoot -BossArguments @('workflow', 'backup', '--db', $paths.Database, '--kind', $Kind)
}
Write-BossEvent -Event 'backup_completed' -Details @{ kind = $Kind }
