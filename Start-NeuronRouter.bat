@echo off
rem Runs the Neuron Audio Router in this window (no service, no admin rights) -
rem for a first test on the server. Close the window or press Ctrl+C to stop.
rem Do not run it while the service (Install-NeuronRouter.bat) is running: same port 8101.
setlocal
set "HERE=%~dp0"
cd /d "%HERE%router"

where python >nul 2>&1
if %errorlevel% neq 0 (
    echo Python was not found on PATH.
    echo Install Python 3.8+ from https://www.python.org/downloads/windows/ and tick "Add python.exe to PATH".
    pause
    exit /b 1
)

echo Neuron Audio Router - http://localhost:8101/  (other PCs: http://%COMPUTERNAME%:8101/)
echo Ctrl+C stops it.
echo.
python server.py
echo.
pause
