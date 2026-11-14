<#
.SYNOPSIS
    Hardens Windows Defender Firewall rules and audits listening ports for Vision Surveillance.

.DESCRIPTION
    Ensures API, dashboard, and streaming ports (8000, 5173, 1984, 8555, 554) are never
    accessible from the local LAN or public internet. Loopback (127.0.0.1) and Tailscale Serve
    loopback proxies continue to work normally.

.EXAMPLE
    .\tools\harden_firewall.ps1 -Status
    .\tools\harden_firewall.ps1 -Apply -WhatIf
    .\tools\harden_firewall.ps1 -Apply
    .\tools\harden_firewall.ps1 -Remove
#>

[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [switch]$Status,
    [switch]$Apply,
    [switch]$Remove,
    [int[]]$ProtectedPorts = @(8000, 5173, 1984, 8555, 554),
    [string]$RulePrefix = "VisionGuard-BlockInbound"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Show-ListenerAudit {
    Write-Host "=== Listening TCP Endpoints (Protected Ports: $($ProtectedPorts -join ', ')) ===" -ForegroundColor Cyan
    $listeners = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
        Where-Object { $ProtectedPorts -contains $_.LocalPort } |
        Select-Object LocalAddress, LocalPort, OwningProcess

    if (-not $listeners) {
        Write-Host "No active listeners on protected ports." -ForegroundColor Green
    } else {
        $hasInsecure = $false
        foreach ($row in $listeners) {
            $addr = $row.LocalAddress
            $isSafe = ($addr -eq "127.0.0.1" -or $addr -eq "::1" -or $addr -match "^100\.(6[4-9]|[7-9]\d|1[0-1]\d|12[0-7])\.")
            if (-not $isSafe) {
                $hasInsecure = $true
                Write-Warning "NON-LOCAL LISTENER DETECTED: $($addr):$($row.LocalPort) (PID $($row.OwningProcess))"
            } else {
                Write-Host "OK (local/Tailscale only): $($addr):$($row.LocalPort) (PID $($row.OwningProcess))" -ForegroundColor Green
            }
        }
        if ($hasInsecure) {
            Write-Warning "Reconfigure services to bind only to 127.0.0.1 or a Tailscale 100.64.0.0/10 IP."
        }
    }

    Write-Host "`n=== Active Firewall Rules ($RulePrefix*) ===" -ForegroundColor Cyan
    $rules = Get-NetFirewallRule -DisplayName "$RulePrefix*" -ErrorAction SilentlyContinue
    if (-not $rules) {
        Write-Host "No $RulePrefix firewall rules installed yet." -ForegroundColor Yellow
    } else {
        $rules | Select-Object DisplayName, Enabled, Direction, Action, Profile | Format-Table -AutoSize
    }
}

if (-not ($Status -or $Apply -or $Remove)) {
    $Status = $true
}

if ($Status) {
    Show-ListenerAudit
    if (-not ($Apply -or $Remove)) {
        exit 0
    }
}

if (-not $WhatIfPreference -and -not (Test-IsAdministrator)) {
    Write-Error "Administrator privileges are required to modify Windows Defender Firewall rules. Re-run PowerShell as Administrator or pass -WhatIf."
    exit 1
}

if ($Remove -or $Apply) {
    $existing = Get-NetFirewallRule -DisplayName "$RulePrefix*" -ErrorAction SilentlyContinue
    foreach ($rule in $existing) {
        if ($PSCmdlet.ShouldProcess($rule.DisplayName, "Remove existing firewall rule")) {
            Remove-NetFirewallRule -Name $rule.Name
        }
    }
}

if ($Apply) {
    $tcpRuleName = "$RulePrefix-TCP"
    $udpRuleName = "$RulePrefix-UDP"

    if ($PSCmdlet.ShouldProcess($tcpRuleName, "Create inbound TCP block rule for LAN/Internet on ports $($ProtectedPorts -join ', ')")) {
        New-NetFirewallRule `
            -DisplayName $tcpRuleName `
            -Description "Blocks external LAN and internet inbound TCP traffic to local Vision Surveillance services." `
            -Direction Inbound `
            -Action Block `
            -Enabled True `
            -Profile Any `
            -Protocol TCP `
            -LocalPort ($ProtectedPorts | ForEach-Object { $_.ToString() }) `
            -RemoteAddress @("LocalSubnet", "Internet", "Any") | Out-Null
    }

    if ($PSCmdlet.ShouldProcess($udpRuleName, "Create inbound UDP block rule for LAN/Internet on port 8555")) {
        New-NetFirewallRule `
            -DisplayName $udpRuleName `
            -Description "Blocks external LAN and internet inbound UDP WebRTC traffic unless explicitly allowed on Tailscale interface." `
            -Direction Inbound `
            -Action Block `
            -Enabled True `
            -Profile Any `
            -Protocol UDP `
            -LocalPort @("8555") `
            -RemoteAddress @("LocalSubnet", "Internet", "Any") | Out-Null
    }

    Write-Host "Applied Windows Firewall inbound block rules ($RulePrefix-TCP, $RulePrefix-UDP)." -ForegroundColor Green
    Show-ListenerAudit
}
