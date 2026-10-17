<#
.SYNOPSIS
  Stops the Neuron Audio Router service: the Scheduled Task "NeuronAudioRouterService" (waits until it
  is stopped) and any python still running router\server.py.
  It starts again at the next boot, with Update-NeuronRouter.bat,
  or by hand:  schtasks /run /tn NeuronAudioRouterService
  Usually started by double-clicking Stop-NeuronRouter.bat (self-elevates).
#>
$ErrorActionPreference = "Stop"
$TaskName = "NeuronAudioRouterService"
$ServerPy = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "router\server.py"

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Write-Host "Stopping task '$TaskName' ..."
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    for ($i = 0; $i -lt 30; $i++) {
        if ((Get-ScheduledTask -TaskName $TaskName).State -ne "Running") { break }
        Start-Sleep -Seconds 1
    }
    Write-Host "Task state: $((Get-ScheduledTask -TaskName $TaskName).State)"
} else {
    Write-Host "Task '$TaskName' is not installed." -ForegroundColor Yellow
}
Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
    Where-Object { $_.CommandLine -and $_.CommandLine -like "*$ServerPy*" } |
    ForEach-Object {
        Write-Host "Stopping python (PID $($_.ProcessId)) running $ServerPy"
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
Write-Host "Stopped. Start again: schtasks /run /tn $TaskName  (or Update-NeuronRouter.bat)" -ForegroundColor Green
