<#
.SYNOPSIS
  Installs the Tools landing page as a Windows Scheduled Task (SYSTEM, at boot,
  restarts on failure) and opens its port in the firewall.

.NOTES
  Usually started by double-clicking Install-Landing.bat in the repo root.
  By hand, from an elevated PowerShell in this folder:
      powershell -ExecutionPolicy Bypass -File .\install-task-windows.ps1            # port 80
      powershell -ExecutionPolicy Bypass -File .\install-task-windows.ps1 -Port 8080
#>
param([int]$Port = 80)
$ErrorActionPreference = "Stop"

$TaskName = "ToolsLandingPage"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ServerPy = Join-Path $ScriptDir "server.py"
if (-not (Test-Path $ServerPy)) { throw "server.py not found next to this script ($ServerPy)." }

$python = Get-Command pythonw.exe -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command python.exe -ErrorAction SilentlyContinue }
if (-not $python) { throw "Python not found on PATH - install it from https://www.python.org/downloads/windows/ (tick 'Add python.exe to PATH')." }
Write-Host "Using Python at: $($python.Source)"

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Existing task '$TaskName' found - replacing it."
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Start-Sleep -Seconds 2
}

# Port already taken by something else (IIS, another web server ...)?
$busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($busy) {
    $owner = ($busy | Select-Object -First 1).OwningProcess
    $pname = (Get-Process -Id $owner -ErrorAction SilentlyContinue).ProcessName
    throw "Port $Port is already in use by '$pname' (PID $owner). Run Install-Landing.bat 8080 (or another free port)."
}

$fwRuleName = "Tools Landing Page (TCP $Port)"
if (-not (Get-NetFirewallRule -DisplayName $fwRuleName -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName $fwRuleName -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow | Out-Null
    Write-Host "Firewall rule created for TCP port $Port."
}

$action = New-ScheduledTaskAction -Execute $python.Source -Argument "`"$ServerPy`" --port $Port" -WorkingDirectory $ScriptDir
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings `
    -Description "Tools landing page on port $Port - links to the web tools on this server. Runs as SYSTEM." | Out-Null

Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 3
Write-Host "Task state: $((Get-ScheduledTask -TaskName $TaskName).State)"
$suffix = if ($Port -eq 80) { "" } else { ":$Port" }
Write-Host ""
Write-Host "Landing page: http://localhost$suffix/  (from other PCs: http://$env:COMPUTERNAME$suffix/)"
Write-Host "Tool list:    $ScriptDir\tools.json  (or 'Edit list' in the page)"
