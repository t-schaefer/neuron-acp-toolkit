@echo off
rem One double-click install of the Tools landing page (links to the tools on this
rem server, e.g. Neuron Audio Router, Fusion Dashboard) as a Windows service on port 80.
rem Other port: Install-Landing.bat 8080      Self-elevates via UAC, pauses at the end.
setlocal
set "HERE=%~dp0"
set "PORT=%~1"
if "%PORT%"=="" set "PORT=80"

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator rights...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '%PORT%' -Verb RunAs"
    exit /b
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%landing\install-task-windows.ps1" -Port %PORT%
echo.
pause
