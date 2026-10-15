<#
.SYNOPSIS
  Removes the Neuron Audio Router scheduled task and its firewall rule.
  Run from an elevated PowerShell:
      powershell -ExecutionPolicy Bypass -File .\uninstall-task-windows.ps1
  devices.json, live/ snapshots, lists/ and service.log are left in place.
#>
$ErrorActionPreference = "Stop"
$TaskName = "NeuronAudioRouterService"
$Port = 8101

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Task '$TaskName' removed."
} else {
    Write-Host "Task '$TaskName' was not installed."
}

$fwRuleName = "Neuron Audio Router (TCP $Port)"
if (Get-NetFirewallRule -DisplayName $fwRuleName -ErrorAction SilentlyContinue) {
    Remove-NetFirewallRule -DisplayName $fwRuleName
    Write-Host "Firewall rule removed."
}
