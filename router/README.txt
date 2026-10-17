Neuron Audio Router - background service
=========================================

What this is
------------
A small always-on service for the EVS Neuron IPG units (Bridge / Convert IP).
It keeps an eye on the units it is allowed to talk to and serves a web page
for graphical audio routing: sources left, destinations right, Case A / Case B
templates, Active / Preset view. It is designed to run as a Windows background
service under the SYSTEM account, so it keeps running whether or not anyone is
logged in - open it from any browser on the network.

It talks to the devices over ACP2 (AN2 on TCP 2072, Axon's protocol) and,
for the channel check, NMOS IS-04/IS-05 (GET only).

WRITING (TAKE / UNDO) IS OFF BY DEFAULT. Reading uses ../acp2/client.py, which
cannot write. Only TAKE and "Undo last Take" write, through ../acp2/writer.py
and router/take.py, and only to a device with "write": true in devices.json:
- only `Input Main` / `Input Backup` (u32) and `Delay` / `Stream Delay` (float);
  source ids must exist on the device, delays within the device MIN..MAX,
  no duplicates, at most 200 changes per take
- every target is read fresh first; if the device no longer matches the
  "Active" the preset was built on, the take is refused and NOTHING is written
- those fresh values are saved as the undo state (live/<device>.undo.json)
  before the first write; each value is read back right after writing and
  the first failure stops the take
- every take/undo is logged to service.log and live/<device>.takes.jsonl
- no red/blue failover for writes (a retry on blue would overwrite the undo state)
"Undo last Take" restores exactly the saved values, the same way. Test the
first write on a unit that is not on air.

Files
-----
server.py                   the service (Python standard library only, no pip installs)
dashboard.html              the web page (talks only to this service, never to the devices)
cbf_model.py                routing model (sources / destinations / channels) of a .cbf export
live_model.py               the same model built from an ACP2 read of a live device
checks.py                   per-channel Case A / Case B analysis and templates
live_check.py               live, read-only check of one channel (ACP2 + NMOS)
route_lists.py              Cerebrum SRC / DST route lists -> stream and channel names
devices.example.json        the tracked starting device list (20 IPG units)
devices.json                (auto-created, not in git) the LIVE device list - edit this one
devices.seeded.json         (auto-created, not in git) remembers which example entries were offered
lists/                      (not in git) Cerebrum SRC.rtf / DST.rtf (or .csv/.tsv/.txt)
live/                       (not in git) ACP2 routing snapshots, one per device
cbf/, exports/              (not in git) optional offline .cbf files and patched copies
install-task-windows.ps1    one-time setup: registers the Windows Scheduled Task
uninstall-task-windows.ps1  removes the task and the firewall rule again
../update-windows.ps1       git pull + restart (see Upgrading)
../Update-NeuronRouter.bat  double-click wrapper for the update (asks for admin rights)
../Install-NeuronRouter.bat double-click install of the service (asks for admin rights)
../Start-NeuronRouter.bat   run it in a console window for a quick test
../Stop-NeuronRouter.bat    stop the service (starts again at boot, with Update-NeuronRouter.bat
                            or: schtasks /run /tn NeuronAudioRouterService)
tests/                      offline tests, load test, JS check, live channel check

Requirements
------------
Python 3.8 or newer. Check (PowerShell): python --version
If missing: https://www.python.org/downloads/windows/ (tick "Add python.exe to
PATH"), or unzip the "Windows embeddable package" anywhere and put it on PATH.

Install as a service (once)
---------------------------
1. Clone the repo on the Windows server (git is needed for updates later):
       git clone https://github.com/t-schaefer/neuron-acp-toolkit.git
2. Double-click Install-NeuronRouter.bat in the repo root (asks for admin rights).
   By hand instead: PowerShell as Administrator in the router folder,
       powershell -ExecutionPolicy Bypass -File .\install-task-windows.ps1
3. Open http://localhost:8101/ (or http://<that-PC>:8101/ from another PC).
Only a quick test, no service: double-click Start-NeuronRouter.bat (runs in its
window until you close it; not at the same time as the service - same port).
The script finds Python, opens TCP 8101 in the firewall, registers a task that
starts at boot as SYSTEM and restarts on failure, and starts it right away.

Uninstall: powershell -ExecutionPolicy Bypass -File .\uninstall-task-windows.ps1

Upgrading
---------
Double-click ..\Update-NeuronRouter.bat in the repo root (asks for admin
rights). It stops the router service, stops a leftover python running
router\server.py, updates the files
(`git pull --ff-only`, or the GitHub main.zip copied over when git is not
installed - nothing is deleted) and starts the service again. devices.json,
lists/ and live/ are not in git, so an update never touches them.

Landing page
------------
The start page for all web tools on the server (router, Fusion Dashboard,
MNset, MuoN config) is its own repo: https://github.com/t-schaefer/tools-landing

Devices and access (devices.json)
---------------------------------
Each entry: {"ip", "name", "tag", "type", "poll", "write"}.
- name must match the device in the SRC/DST lists (normalised: IPG03A =
  "DCR.IPG 03A" = IPG3A) and the device's NMOS label.
- "poll": true  = the service may contact this device (health, routing read,
  channel check). false = it is never contacted. Every device starts with
  false: release it with the "Access" switch in the device list.
- "write": true = TAKE / Undo may write to this device ("Write" switch, only
  with Access on). Every device starts with false.
- "blue": optional backup (blue) control address. There is no blue rule set,
  because the Neuron control-network rule is not known yet (BLUE_RULE in server.py).
New devices from an updated devices.example.json are appended to devices.json
on the next start; a device you delete by hand does not come back. The "+ Device"
form in the page adds a device with poll=false, write=false.

What the service does to the devices (why it is safe)
------------------------------------------------------
- Health: ONE AN2 "get version" per released device every 30 s, 8 workers,
  one sweep at a time, 4 s timeout. Viewers do not cause any device traffic:
  the page reads /api/state from the service every 4 s.
- Slow data every 10 min: AN2 slot info + 3 ACP2 get-property reads
  (firmware, card name, NMOS label).
- Only on a button click:
  "Read from device" = ACP2 get-object walk of the routing tree (IPG03A:
  ~41,000 requests, one at a time, ~5 min). Stored in live/<device>.json.
  "Check live" = ~120-190 ACP2 get-property reads for one channel + a few NMOS GETs.
- One routing read at a time, ever.

Settings (top of server.py)
---------------------------
PORT 8101, HEALTH_INTERVAL_SEC 30, CONFIG_REFRESH_INTERVAL_SEC 600, CONCURRENCY 8,
ACP2_TIMEOUT_SEC 4, BLUE_RULE None. After a change restart the task:
    Restart-ScheduledTask -TaskName NeuronAudioRouterService
NMOS registry for the channel check: NMOS_REGISTRY in live_check.py.

Many viewers
------------
/api/state is built at most once per second, shared and sent gzip-compressed;
the server accepts a backlog of 128 connections. Load test: 200 parallel
viewers polling every second for 20 s -> 4000 requests, 0 errors, median 4 ms,
p95 17 ms (tests/load_test.py).

Using the router
----------------
- Devices: collapsible list with status badges (filter), groups (DCR / DCB),
  red/blue badges. Click a row -> Read from device / Open router.
- Video paths: 32 tiles A1..H4, case per side Main/Backup (A = IP, B = SDI,
  - = Pattern/unused), colour = check status, orange dot = preset staged.
- Assisted: pick an IP In (Case A) or SDI (Case B) source or a tile. Only the
  destinations of that channel are shown, as a signal flow per side. Main and
  Backup are resolved separately from their own video input; if both use the
  same source they are shown as one "Main + Backup" flow, otherwise side by side.
  Case A: IP In -> Stream Delay on the input -> embedder -> SDI out.
  Case B: SDI de-embedder -> 16ch delay group (8 banks, first bank shown) ->
  delay -> embedder -> SDI out, and the same group -> IP out (main + backup).
  Delay targets (19.5 / 18.0 ms by default) are editable in the delay boxes.
  "Stage template into Preset" puts the routes (+ delays) into the Preset.
- Manual: all sources / destinations, fill 16ch blocks, drag & drop, click-shuffle.
- View Active | Preset: Active = what the device runs now, Preset = Active +
  staged changes. TAKE activates the Preset (not enabled yet).
- Mono channel IDs are shown in brackets everywhere, names come from the
  SRC / DST lists (channel rows TXnn / RXnn and audio rows .A-TXnn).

Tests
-----
python3 -m unittest discover -s router/tests -p "test_*.py"     offline, simulated ACP2 device
python3 router/tests/check_js.py                                JS syntax of dashboard.html
python3 router/tests/load_test.py http://127.0.0.1:8101          200 viewers
python3 router/tests/check_channel_live.py IPG03A 32             LIVE, read-only, one channel

Unverified / not done
---------------------
- Take / Undo: implemented and tested against the simulated device only
  (tests/test_offline.py TakeTests). Not yet run against a real Neuron.
- Red/blue failover: code path exists, no blue addresses known.
- Only IPG03A has been read live so far. Other units: release them in
  devices.json one at a time and read them once.
