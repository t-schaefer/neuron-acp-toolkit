<#
.SYNOPSIS
  Installs the Neuron Audio Router service as a Windows Scheduled Task that
  runs under the SYSTEM account - starts at boot, keeps running whether or not
  any user is logged in, and restarts itself if it ever crashes.

.NOTES
  Run from an elevated (Administrator) PowerShell, inside this folder:

      cd C:\path\to\neuron-acp-toolkit\router
      powershell -ExecutionPolicy Bypass -File .\install-task-windows.ps1

  Built-in Windows features only (Task Scheduler) - no NSSM, no service wrapper.
#>

$ErrorActionPreference = "Stop"

$TaskName = "NeuronAudioRouterService"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ServerPy = Join-Path $ScriptDir "server.py"
$Port = 8101

if (-not (Test-Path $ServerPy)) {
    throw "server.py not found next to this script ($ServerPy). Run this from inside the router folder."
}

# pythonw.exe runs without a console window; fall back to python.exe.
$python = Get-Command pythonw.exe -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command python.exe -ErrorAction SilentlyContinue }
if (-not $python) {
    Write-Host "Python was not found on PATH." -ForegroundColor Yellow
    Write-Host "Install it from https://www.python.org/downloads/windows/ (tick 'Add python.exe to PATH')," -ForegroundColor Yellow
    Write-Host "or unzip the 'Windows embeddable package' anywhere and put that folder on PATH." -ForegroundColor Yellow
    throw "Python not found."
}
Write-Host "Using Python at: $($python.Source)"

# Inbound firewall rule so other PCs can open the dashboard.
$fwRuleName = "Neuron Audio Router (TCP $Port)"
if (-not (Get-NetFirewallRule -DisplayName $fwRuleName -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName $fwRuleName -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow | Out-Null
    Write-Host "Firewall rule created for TCP port $Port."
} else {
    Write-Host "Firewall rule for TCP port $Port already exists."
}

$action = New-ScheduledTaskAction -Execute $python.Source -Argument "`"$ServerPy`"" -WorkingDirectory $ScriptDir
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Write-Host "Existing task '$TaskName' found - replacing it."
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings `
    -Description "Neuron Audio Router - ACP2 health of released Neuron IPG units and graphical audio routing on port $Port. Writes only to devices with write=true (TAKE/Undo). Runs as SYSTEM, no login needed." | Out-Null

Write-Host "Task '$TaskName' registered. Starting it now..."
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 3

$state = (Get-ScheduledTask -TaskName $TaskName).State
Write-Host "Task state: $state"
Write-Host ""
Write-Host "Dashboard: http://localhost:$Port/  (from other PCs: http://<this-PC>:$Port/)"
Write-Host "Logs:      $ScriptDir\service.log"
Write-Host "Devices:   $ScriptDir\devices.json  (only entries with ""poll"": true are ever contacted)"
