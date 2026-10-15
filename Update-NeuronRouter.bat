@echo off
rem One double-click update of the Neuron Audio Router (git pull + service restart).
rem Self-elevates via UAC, works from any folder depth, pauses so errors stay visible.
setlocal
set "HERE=%~dp0"

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator rights...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%update-windows.ps1"
echo.
pause
