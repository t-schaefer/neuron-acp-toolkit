<#
.SYNOPSIS
  Removes the Tools landing page task and its firewall rule(s). tools.json stays.
      powershell -ExecutionPolicy Bypass -File .\uninstall-task-windows.ps1
#>
$ErrorActionPreference = "Stop"
$TaskName = "ToolsLandingPage"
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Task '$TaskName' removed."
} else { Write-Host "Task '$TaskName' was not installed." }
Get-NetFirewallRule -DisplayName "Tools Landing Page (TCP *" -ErrorAction SilentlyContinue | ForEach-Object {
    Remove-NetFirewallRule -Name $_.Name; Write-Host "Firewall rule '$($_.DisplayName)' removed." }
