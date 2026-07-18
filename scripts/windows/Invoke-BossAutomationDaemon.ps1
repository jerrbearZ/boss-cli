param([string]$AppRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'BossAutomation.Common.ps1')

$paths = Get-BossPaths -AppRoot $AppRoot
try {
    Invoke-BossCli -AppRoot $paths.AppRoot -BossArguments @(
        '--log-file', $paths.DaemonLog,
        'workflow', 'daemon',
        '--db', $paths.Database,
        '--deployment-config', $paths.DeploymentConfig
    )
}
catch {
    Write-BossEvent -Event 'daemon_launcher_failed' -Level 'error' -Details @{ error_code = $_.Exception.GetType().Name }
    exit 1
}
