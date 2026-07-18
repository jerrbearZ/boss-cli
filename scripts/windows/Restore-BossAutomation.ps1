[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [Parameter(Mandatory = $true)][string]$BackupPath,
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
if (-not (Test-Path -LiteralPath $BackupPath -PathType Leaf)) { throw 'BackupPath does not exist.' }
if ($ValidationOnly) {
    Write-BossEvent -Event 'restore_validation_succeeded' -Details @{ backup = [IO.Path]::GetFullPath($BackupPath) }
    exit 0
}

if ($PSCmdlet.ShouldProcess($paths.Database, 'Stop tasks and restore the verified SQLite backup')) {
    & (Join-Path $PSScriptRoot 'Stop-BossAutomation.ps1') -AppRoot $paths.AppRoot -Confirm:$false
    Invoke-BossCli -AppRoot $paths.AppRoot -BossArguments @('workflow', 'restore', '--db', $paths.Database, '--backup', ([IO.Path]::GetFullPath($BackupPath)), '--yes')
}
Write-BossEvent -Event 'restore_completed' -Details @{ database = $paths.Database; tasks_restarted = $false }
