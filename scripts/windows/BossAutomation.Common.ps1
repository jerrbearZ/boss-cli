Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Assert-BossWindows {
    if ($env:OS -ne 'Windows_NT') {
        throw 'Boss Automation deployment scripts require native Windows.'
    }
    if (-not [Environment]::Is64BitOperatingSystem) {
        throw 'Boss Automation requires 64-bit Windows.'
    }
}

function Assert-BossWindowsTarget {
    $os = Get-CimInstance -ClassName Win32_OperatingSystem
    if ([Environment]::OSVersion.Version.Build -lt 22000) {
        throw 'The first deployment supports Windows 11 only.'
    }
    if ([int]$os.OperatingSystemSKU -notin @(48, 49)) {
        throw "The first deployment supports Windows 11 Pro; detected SKU $($os.OperatingSystemSKU) ('$($os.Caption)')."
    }
    $principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'The dedicated automation user must not be a member of the local Administrators group.'
    }
    $browserCandidates = @(
        (Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'),
        (Join-Path $env:ProgramFiles 'Microsoft\Edge\Application\msedge.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'),
        (Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe')
    )
    if (-not ($browserCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1)) {
        throw 'Install Chrome or Edge before commissioning BOSS authentication.'
    }
}

function Get-BossPaths {
    param([Parameter(Mandatory = $true)][string]$AppRoot)

    if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        throw 'LOCALAPPDATA is required for user-scoped state.'
    }
    $stateRoot = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'BossCLI'))
    return [ordered]@{
        AppRoot = [IO.Path]::GetFullPath($AppRoot)
        StateRoot = $stateRoot
        Data = Join-Path $stateRoot 'data'
        Logs = Join-Path $stateRoot 'logs'
        Backups = Join-Path $stateRoot 'backups'
        Secrets = Join-Path $stateRoot 'secrets'
        Config = Join-Path $stateRoot 'config'
        Database = Join-Path $stateRoot 'data\workflow.db'
        DeploymentConfig = Join-Path $stateRoot 'config\deployment.json'
        DaemonLog = Join-Path $stateRoot 'logs\daemon.log'
        DashboardLog = Join-Path $stateRoot 'logs\dashboard.log'
    }
}

function Assert-BossOwner {
    param([string]$UserId)

    $current = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    if (-not [string]::IsNullOrWhiteSpace($UserId) -and $current -ne $UserId) {
        throw "The current user '$current' must match the task and DPAPI owner '$UserId'."
    }
    return $current
}

function Assert-BossRegisteredOwner {
    $current = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $task = Get-ScheduledTask -TaskPath '\BossAutomation\' -TaskName 'BossAutomation-Daemon' -ErrorAction SilentlyContinue
    if ($null -ne $task -and [string]$task.Principal.UserId -ne $current) {
        throw "The current user '$current' does not own the registered daemon task '$($task.Principal.UserId)'."
    }
    return $current
}

function Get-BossUv {
    $command = Get-Command 'uv' -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        throw 'uv is not installed or is not available on PATH.'
    }
    return $command.Source
}

function Invoke-BossCli {
    param(
        [Parameter(Mandatory = $true)][string]$AppRoot,
        [Parameter(Mandatory = $true)][string[]]$BossArguments,
        [switch]$AllowFailure
    )

    $uv = Get-BossUv
    & $uv '--directory' $AppRoot 'run' '--frozen' '--no-sync' 'boss' @BossArguments
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0 -and -not $AllowFailure) {
        throw "boss command failed with exit code $exitCode."
    }
    return $exitCode
}

function Write-BossEvent {
    param(
        [Parameter(Mandatory = $true)][string]$Event,
        [ValidateSet('info', 'warning', 'error')][string]$Level = 'info',
        [hashtable]$Details = @{}
    )

    $record = [ordered]@{
        timestamp = [DateTime]::UtcNow.ToString('o')
        level = $Level
        event = $Event
        details = $Details
    }
    $json = $record | ConvertTo-Json -Compress -Depth 5
    if (-not [string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        $logDirectory = Join-Path $env:LOCALAPPDATA 'BossCLI\logs'
        $logPath = Join-Path $logDirectory 'operations.log'
        $null = New-Item -ItemType Directory -Path $logDirectory -Force
        if ((Test-Path -LiteralPath $logPath) -and (Get-Item -LiteralPath $logPath).Length -gt 2097152) {
            Move-Item -LiteralPath $logPath -Destination "$logPath.1" -Force
        }
        Add-Content -LiteralPath $logPath -Value $json -Encoding UTF8
    }
    Write-Output $json
}

function New-BossTaskFolder {
    $service = New-Object -ComObject 'Schedule.Service'
    $service.Connect()
    $root = $service.GetFolder('\')
    try {
        $null = $root.GetFolder('BossAutomation')
    }
    catch {
        $null = $root.CreateFolder('BossAutomation')
    }
}

function Get-BossPowerShellExecutable {
    return (Get-Process -Id $PID).Path
}

function Assert-BossScriptSet {
    param([Parameter(Mandatory = $true)][string]$AppRoot)

    $required = @(
        'Install-BossAutomation.ps1',
        'Set-BossAutomationSecrets.ps1',
        'Start-BossAutomation.ps1',
        'Stop-BossAutomation.ps1',
        'Get-BossAutomationStatus.ps1',
        'Backup-BossAutomation.ps1',
        'Update-BossAutomation.ps1',
        'Uninstall-BossAutomation.ps1',
        'Restore-BossAutomation.ps1',
        'Invoke-BossAutomationDaemon.ps1',
        'Invoke-BossAutomationDashboard.ps1'
    )
    foreach ($name in $required) {
        $path = Join-Path $AppRoot "scripts\windows\$name"
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "Required deployment script is missing: $path"
        }
    }
}
