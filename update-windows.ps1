<#
.SYNOPSIS
  Updates the Neuron Audio Router from git and restarts the service.
  Stops the scheduled task (and waits for it), kills a stuck python running
  exactly this router\server.py, runs `git pull --ff-only`, starts the task.

  Usually started by double-clicking Update-NeuronRouter.bat (self-elevates).
  By hand, from an elevated PowerShell in the repo root:
      powershell -ExecutionPolicy Bypass -File .\update-windows.ps1

  devices.json, live/, lists/, cbf/, exports/ are git-ignored and never touched.
#>
$ErrorActionPreference = "Stop"
$TaskName = "NeuronAudioRouterService"
$RepoDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ServerPy = Join-Path $RepoDir "router\server.py"

Set-Location $RepoDir
Write-Host "Repository: $RepoDir"

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    Write-Host "Stopping task '$TaskName'..."
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    for ($i = 0; $i -lt 30; $i++) {
        if ((Get-ScheduledTask -TaskName $TaskName).State -ne "Running") { break }
        Start-Sleep -Seconds 1
    }
} else {
    Write-Host "Task '$TaskName' is not installed - only updating the files." -ForegroundColor Yellow
}

# A python that still runs exactly this server.py (e.g. stuck, or started by hand)
$stuck = Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
    Where-Object { $_.CommandLine -and $_.CommandLine -like "*$ServerPy*" }
foreach ($p in $stuck) {
    Write-Host "Stopping leftover python (PID $($p.ProcessId)) running $ServerPy"
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}

Write-Host "git pull --ff-only ..."
git pull --ff-only
if ($LASTEXITCODE -ne 0) { throw "git pull failed (local changes or diverged branch?) - service NOT restarted." }

if ($task) {
    Write-Host "Starting task '$TaskName'..."
    Start-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds 3
    Write-Host "Task state: $((Get-ScheduledTask -TaskName $TaskName).State)"
}
Write-Host "Done."
