[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [Parameter(Mandatory = $true)][ValidateNotNullOrEmpty()][string]$Revision,
    [string]$AppRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [switch]$ValidationOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'BossAutomation.Common.ps1')

Assert-BossWindows
$null = Assert-BossRegisteredOwner
$paths = Get-BossPaths -AppRoot $AppRoot
$uv = Get-BossUv
Assert-BossScriptSet -AppRoot $paths.AppRoot
if ($Revision -cnotmatch '^[0-9a-fA-F]{40}$') {
    throw 'Revision must be a full 40-character commit SHA.'
}
& git '-C' $paths.AppRoot 'rev-parse' '--is-inside-work-tree' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'AppRoot is not a git worktree.' }
& git '-C' $paths.AppRoot 'cat-file' '-e' "$Revision`^{commit}"
if ($LASTEXITCODE -ne 0) { throw 'The requested pinned revision is not available locally.' }

if ($ValidationOnly) {
    Write-BossEvent -Event 'update_validation_succeeded' -Details @{ revision = $Revision; app_root = $paths.AppRoot }
    exit 0
}

$worktreeChanges = @(& git '-C' $paths.AppRoot 'status' '--porcelain' '--untracked-files=normal')
if ($LASTEXITCODE -ne 0) { throw 'Unable to inspect deployment worktree state.' }
if ($worktreeChanges.Count -gt 0) { throw 'The deployment checkout must be clean before update.' }

$previousRevision = (& git '-C' $paths.AppRoot 'rev-parse' 'HEAD').Trim()
if (-not $PSCmdlet.ShouldProcess($paths.AppRoot, "Stop, back up, and update to pinned revision $Revision")) {
    exit 0
}

try {
    & (Join-Path $PSScriptRoot 'Stop-BossAutomation.ps1') -AppRoot $paths.AppRoot -Confirm:$false
    if ($LASTEXITCODE -ne 0) { throw 'Controlled stop failed.' }
    & (Join-Path $PSScriptRoot 'Backup-BossAutomation.ps1') -AppRoot $paths.AppRoot -Kind 'pre-update' -Confirm:$false
    if ($LASTEXITCODE -ne 0) { throw 'Pre-update backup failed.' }
    Invoke-BossCli -AppRoot $paths.AppRoot -BossArguments @('deployment', 'disable-live', '--config', $paths.DeploymentConfig)

    & git '-C' $paths.AppRoot 'checkout' '--detach' $Revision
    if ($LASTEXITCODE -ne 0) { throw 'Unable to check out the pinned revision.' }
    & $uv '--directory' $paths.AppRoot 'sync' '--locked' '--extra' 'browser' '--extra' 'dev'
    if ($LASTEXITCODE -ne 0) { throw 'Locked dependency synchronization failed.' }
    & $uv '--directory' $paths.AppRoot 'run' '--frozen' '--no-sync' 'python' '-m' 'boss_cli.camoufox_runtime' 'install' '--smoke'
    if ($LASTEXITCODE -ne 0) { throw 'Pinned Camoufox runtime install or smoke test failed.' }

    Invoke-BossCli -AppRoot $paths.AppRoot -BossArguments @('workflow', 'init-db', '--db', $paths.Database)
    & $uv '--directory' $paths.AppRoot 'run' '--frozen' '--no-sync' 'ruff' 'check' '.'
    if ($LASTEXITCODE -ne 0) { throw 'Ruff validation failed.' }
    & $uv '--directory' $paths.AppRoot 'run' '--frozen' '--no-sync' 'ruff' 'format' '--check' '.'
    if ($LASTEXITCODE -ne 0) { throw 'Ruff formatting validation failed.' }
    & $uv '--directory' $paths.AppRoot 'run' '--frozen' '--no-sync' 'pyright' 'boss_cli'
    if ($LASTEXITCODE -ne 0) { throw 'Pyright validation failed.' }
    Get-ChildItem (Join-Path $paths.AppRoot 'scripts\windows') -Filter '*.ps1' | ForEach-Object {
        $tokens = $null
        $parseErrors = $null
        [void][System.Management.Automation.Language.Parser]::ParseFile($_.FullName, [ref]$tokens, [ref]$parseErrors)
        if ($parseErrors.Count -gt 0) { throw "PowerShell parse error in $($_.Name): $($parseErrors[0].Message)" }
    }
    & $uv '--directory' $paths.AppRoot 'run' '--frozen' '--no-sync' 'python' '-m' 'pytest' '-p' 'no:capture' '-q' '-m' 'not smoke'
    if ($LASTEXITCODE -ne 0) { throw 'Non-live test suite failed.' }
    & $uv '--directory' $paths.AppRoot 'build'
    if ($LASTEXITCODE -ne 0) { throw 'Package build failed.' }

    Invoke-BossCli -AppRoot $paths.AppRoot -BossArguments @('workflow', 'resume', '--db', $paths.Database)
    & (Join-Path $PSScriptRoot 'Start-BossAutomation.ps1') -AppRoot $paths.AppRoot -Confirm:$false
    if ($LASTEXITCODE -ne 0) { throw 'Dry-mode restart failed.' }
}
catch {
    Write-BossEvent -Event 'update_failed_rollback_started' -Level 'error' -Details @{ error_code = $_.Exception.GetType().Name; previous_revision = $previousRevision }
    & git '-C' $paths.AppRoot 'checkout' '--detach' $previousRevision
    & $uv '--directory' $paths.AppRoot 'sync' '--locked' '--extra' 'browser' '--extra' 'dev'
    & $uv '--directory' $paths.AppRoot 'run' '--frozen' '--no-sync' 'python' '-m' 'boss_cli.camoufox_runtime' 'install' '--smoke'
    throw
}

Write-BossEvent -Event 'update_completed' -Details @{ previous_revision = $previousRevision; revision = $Revision; live = $false }
