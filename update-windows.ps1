<#
.SYNOPSIS
  Updates the toolkit on this server and restarts its services:
    NeuronAudioRouterService (router\server.py, port 8101)
    ToolsLandingPage         (landing\server.py, port 80 or as installed)

  1. stops the installed tasks and waits until they are stopped
  2. stops a leftover python that still runs one of these server.py files
  3. updates the files: `git pull --ff-only` when this is a git clone, otherwise
     downloads main.zip from GitHub and copies it over (no files are deleted)
  4. starts the tasks again and shows their state

  Usually started by double-clicking Update-NeuronRouter.bat (self-elevates).
  Local data is never touched: router\devices.json, lists\, live\, cbf\,
  exports\, landing\tools.json, service.log (git-ignored, not in the zip).
#>
$ErrorActionPreference = "Stop"
$RepoDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ZipUrl = "https://github.com/t-schaefer/neuron-acp-toolkit/archive/refs/heads/main.zip"
$Services = @(
    @{ Task = "NeuronAudioRouterService"; Py = (Join-Path $RepoDir "router\server.py") },
    @{ Task = "ToolsLandingPage";         Py = (Join-Path $RepoDir "landing\server.py") }
)
Set-Location $RepoDir
Write-Host "Folder: $RepoDir"

# ---- 1 + 2: stop ----
$installed = @()
foreach ($s in $Services) {
    if (Get-ScheduledTask -TaskName $s.Task -ErrorAction SilentlyContinue) {
        $installed += $s.Task
        Write-Host "Stopping task '$($s.Task)' ..."
        Stop-ScheduledTask -TaskName $s.Task -ErrorAction SilentlyContinue
        for ($i = 0; $i -lt 30; $i++) {
            if ((Get-ScheduledTask -TaskName $s.Task).State -ne "Running") { break }
            Start-Sleep -Seconds 1
        }
    } else {
        Write-Host "Task '$($s.Task)' is not installed - skipped." -ForegroundColor Yellow
    }
    Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
        Where-Object { $_.CommandLine -and $_.CommandLine -like "*$($s.Py)*" } |
        ForEach-Object {
            Write-Host "Stopping leftover python (PID $($_.ProcessId)) running $($s.Py)"
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
}

# ---- 3: update the files ----
$updated = $false
try {
    $git = Get-Command git.exe -ErrorAction SilentlyContinue
    if ($git -and (Test-Path (Join-Path $RepoDir ".git"))) {
        Write-Host "git pull --ff-only ..."
        git pull --ff-only
        if ($LASTEXITCODE -ne 0) { throw "git pull failed (local changes or diverged branch?)" }
    } else {
        Write-Host "No git clone here - downloading $ZipUrl ..."
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $tmp = Join-Path $env:TEMP ("nat-update-" + [guid]::NewGuid().ToString("N"))
        New-Item -ItemType Directory -Path $tmp | Out-Null
        $zip = Join-Path $tmp "main.zip"
        Invoke-WebRequest -Uri $ZipUrl -OutFile $zip -UseBasicParsing
        Expand-Archive -Path $zip -DestinationPath $tmp -Force
        $src = Get-ChildItem -Path $tmp -Directory | Select-Object -First 1
        # copy over, never delete (robocopy exit codes below 8 mean success)
        robocopy $src.FullName $RepoDir /E /NFL /NDL /NJH /NJS /NP | Out-Null
        if ($LASTEXITCODE -ge 8) { throw "copying the downloaded files failed (robocopy $LASTEXITCODE)" }
        Remove-Item -Recurse -Force $tmp
    }
    $updated = $true
    Write-Host "Files updated." -ForegroundColor Green
} catch {
    Write-Host "UPDATE FAILED: $_" -ForegroundColor Red
    Write-Host "The services are started again with the files as they were." -ForegroundColor Yellow
}

# ---- 4: start ----
foreach ($t in $installed) {
    Write-Host "Starting task '$t' ..."
    Start-ScheduledTask -TaskName $t
}
if ($installed.Count) { Start-Sleep -Seconds 3 }
foreach ($t in $installed) { Write-Host ("  {0,-26} {1}" -f $t, (Get-ScheduledTask -TaskName $t).State) }
if (-not ($installed -contains "NeuronAudioRouterService")) {
    Write-Host "Neuron Audio Router is not installed as a service - run Install-NeuronRouter.bat once." -ForegroundColor Yellow
}
if (-not $updated) { exit 1 }
Write-Host "Done."
