@echo off
rem One double-click install of the Neuron Audio Router as a Windows service
rem (Scheduled Task as SYSTEM: starts at boot, no login needed, restarts on failure,
rem firewall rule for TCP 8101). Self-elevates via UAC, pauses so errors stay visible.
rem Updates later: Update-NeuronRouter.bat. Remove: router\uninstall-task-windows.ps1
setlocal
set "HERE=%~dp0"

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator rights...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

where python >nul 2>&1
if %errorlevel% neq 0 (
    echo Python was not found on PATH.
    echo Install Python 3.8+ from https://www.python.org/downloads/windows/ and tick "Add python.exe to PATH".
    echo.
    pause
    exit /b 1
)
where git >nul 2>&1
if %errorlevel% neq 0 echo NOTE: git was not found - Update-NeuronRouter.bat will download the GitHub zip instead.

powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%router\install-task-windows.ps1"
echo.
pause
