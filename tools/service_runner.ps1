<#
.SYNOPSIS
    Installs, inspects, or removes Windows startup tasks (Task Scheduler or NSSM)
    for Vision Surveillance engine, FastAPI backend, go2rtc bridge, and weekly backups.

.EXAMPLE
    .\tools\service_runner.ps1 -Status
    .\tools\service_runner.ps1 -Install -WhatIf
    .\tools\service_runner.ps1 -Install -BackupDest "E:\VisionBackups"
    .\tools\service_runner.ps1 -Uninstall
#>

[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [switch]$Status,
    [switch]$Install,
    [switch]$Uninstall,
    [switch]$UseNssm,
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$PythonExe = "",
    [string]$BackupDest = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not $PythonExe) {
    $venvPy = Join-Path $ProjectRoot "venv\Scripts\python.exe"
    if (Test-Path $venvPy) {
        $PythonExe = $venvPy
    } else {
        $cmd = Get-Command python -ErrorAction SilentlyContinue
        $PythonExe = if ($cmd) { $cmd.Source } else { "python.exe" }
    }
}

$Services = @(
    @{ Name = "VisionGuard-Engine"; Args = "tools\run_engine.py" },
    @{ Name = "VisionGuard-API";    Args = "-m backend.app" },
    @{ Name = "VisionGuard-go2rtc"; Args = "tools\run_go2rtc.py" }
)

$BackupTaskName = "VisionGuard-WeeklyBackup"

function Show-TaskStatus {
    Write-Host "=== VisionGuard Scheduled Tasks ===" -ForegroundColor Cyan
    $names = ($Services | ForEach-Object { $_.Name }) + @($BackupTaskName)
    foreach ($taskName in $names) {
        $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        if ($null -eq $task) {
            Write-Host "  [NOT INSTALLED] $taskName" -ForegroundColor Yellow
        } else {
            Write-Host "  [$($task.State)] $taskName" -ForegroundColor Green
        }
    }
}

if (-not ($Status -or $Install -or $Uninstall)) {
    $Status = $true
}

if ($Status) {
    Show-TaskStatus
    if (-not ($Install -or $Uninstall)) {
        exit 0
    }
}

if ($Uninstall) {
    foreach ($svc in $Services) {
        $name = $svc.Name
        if ($UseNssm) {
            if ($PSCmdlet.ShouldProcess($name, "Remove NSSM service")) {
                & nssm stop $name 2>$null
                & nssm remove $name confirm 2>$null
            }
        } else {
            $existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
            if ($existing -and $PSCmdlet.ShouldProcess($name, "Unregister Scheduled Task")) {
                Unregister-ScheduledTask -TaskName $name -Confirm:$false
            }
        }
    }
    $backupExisting = Get-ScheduledTask -TaskName $BackupTaskName -ErrorAction SilentlyContinue
    if ($backupExisting -and $PSCmdlet.ShouldProcess($BackupTaskName, "Unregister Weekly Backup Task")) {
        Unregister-ScheduledTask -TaskName $BackupTaskName -Confirm:$false
    }
    Write-Host "Uninstalled VisionGuard startup tasks." -ForegroundColor Green
    exit 0
}

if ($Install) {
    if ($UseNssm) {
        $nssmCmd = Get-Command nssm -ErrorAction SilentlyContinue
        if (-not $nssmCmd) {
            Write-Error "nssm.exe not found on PATH. Omit -UseNssm to use built-in Windows Task Scheduler."
            exit 1
        }
        foreach ($svc in $Services) {
            $name = $svc.Name
            if ($PSCmdlet.ShouldProcess($name, "Install & configure NSSM auto-restart service")) {
                & nssm install $name $PythonExe $svc.Args
                & nssm set $name AppDirectory $ProjectRoot
                & nssm set $name Start SERVICE_AUTO_START
                & nssm set $name AppRestartDelay 5000
                & nssm set $name AppStdout (Join-Path $ProjectRoot "logs\$name.stdout.log")
                & nssm set $name AppStderr (Join-Path $ProjectRoot "logs\$name.stderr.log")
            }
        }
    } else {
        $settings = New-ScheduledTaskSettingsSet `
            -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries `
            -ExecutionTimeLimit ([TimeSpan]::Zero) `
            -RestartCount 999 `
            -RestartInterval (New-TimeSpan -Minutes 1) `
            -StartWhenAvailable

        $trigger = New-ScheduledTaskTrigger -AtStartup

        foreach ($svc in $Services) {
            $name = $svc.Name
            if ($PSCmdlet.ShouldProcess($name, "Register startup Scheduled Task with crash-restart")) {
                $action = New-ScheduledTaskAction `
                    -Execute $PythonExe `
                    -Argument $svc.Args `
                    -WorkingDirectory $ProjectRoot
                Register-ScheduledTask `
                    -TaskName $name `
                    -Action $action `
                    -Trigger $trigger `
                    -Settings $settings `
                    -Description "Vision Surveillance unattended startup service ($name)" `
                    -Force | Out-Null
            }
        }
    }

    $backupArgs = "tools\backup_db.py"
    if ($BackupDest) {
        $backupArgs = "$backupArgs --dest `"$BackupDest`""
    }
    if ($PSCmdlet.ShouldProcess($BackupTaskName, "Register weekly Sunday 03:30 AM SQLite backup task")) {
        $backupAction = New-ScheduledTaskAction `
            -Execute $PythonExe `
            -Argument $backupArgs `
            -WorkingDirectory $ProjectRoot
        $backupTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 3:30AM
        $backupSettings = New-ScheduledTaskSettingsSet `
            -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries `
            -StartWhenAvailable
        Register-ScheduledTask `
            -TaskName $BackupTaskName `
            -Action $backupAction `
            -Trigger $backupTrigger `
            -Settings $backupSettings `
            -Description "Weekly hot WAL backup of surveillance SQLite DB" `
            -Force | Out-Null
    }

    Write-Host "Installed VisionGuard startup and weekly backup tasks." -ForegroundColor Green
    Show-TaskStatus
}
