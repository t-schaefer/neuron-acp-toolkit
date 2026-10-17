#!/usr/bin/env python3
"""
Neuron Audio Router - unattended fleet service + graphical audio routing for
EVS Neuron Bridge / Convert-IP (IPG) units over ACP2.

Same structure as the PLS Fusion Dashboard (PROMPT-fleet-dashboard-service.md):
- Standard library only, ThreadingHTTPServer on 0.0.0.0, one dashboard.html.
  The browser talks only to this service, never to the devices.
- A background loop keeps STATE fresh; viewers only read /api/state (every 4 s),
  so they cost the devices nothing.
- Gentle on the devices: ACP2 health = ONE AN2 "get version" per device every
  30 s (8 workers, sweeps never overlap, hard timeouts). Slow config (firmware,
  card, NMOS label) every 10 min. The heavy routing read over ACP2 (~41,000
  get-object requests, ~5 min) and channel checks run ONLY on a button click.
- Read-only: acp2/client.py has no set-property code path. Take/Undo answer
  403 until writing is implemented and enabled per device (devices.json "write").

    python server.py               # http://<this pc>:8101/
    python server.py --port 9000
"""
import argparse
import gzip
import json
import logging
import logging.handlers
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR.parent / "acp2"))

from cbf_model import CbfModel                         # noqa: E402
from checks import analyse_channel                     # noqa: E402
from client import Acp2Client, Acp2Error               # noqa: E402  (read-only)
from live_check import run_check                       # noqa: E402
from live_model import LiveModel                       # noqa: E402
from route_lists import parse_list, stream_names, normalize_device, device_index, normalize_paste  # noqa: E402
import walk as acp2_walk                               # noqa: E402
import take as take_mod                                # noqa: E402  (the only writer, gated by devices.json "write")

# ---------------- settings ----------------
HOST = "0.0.0.0"
PORT = 8101
HEALTH_INTERVAL_SEC = 30
CONFIG_REFRESH_INTERVAL_SEC = 600
CONCURRENCY = 8
ACP2_TIMEOUT_SEC = 4
STATE_CACHE_SEC = 1.0
# Blue (backup) control address. The Neuron control network rule is not known yet,
# so no rule is applied: set "blue" per device in devices.json, or a rule here.
BLUE_RULE = None   # e.g. lambda ip: re.sub(r"^(\d+)\.(\d+)", lambda m: f"{m[1]}.{int(m[2]) + 1}", ip)

DASHBOARD_FILE = BASE_DIR / "dashboard.html"
DEVICES_FILE = BASE_DIR / "devices.json"
DEVICES_EXAMPLE_FILE = BASE_DIR / "devices.example.json"
SEEDED_FILE = BASE_DIR / "devices.seeded.json"
LOG_FILE = BASE_DIR / "service.log"
CBF_DIR = BASE_DIR / "cbf"          # offline .cbf exports (illustration / fallback)
EXPORT_DIR = BASE_DIR / "exports"
LISTS_DIR = BASE_DIR / "lists"      # Cerebrum SRC/DST route lists
LIVE_DIR = BASE_DIR / "live"        # ACP2 routing snapshots, one per device
MAX_UPLOAD = 64 * 1024 * 1024

# ---------------- logging ----------------
log = logging.getLogger("neuron-router")
log.setLevel(logging.INFO)
_fh = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=3)
_fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
log.addHandler(_fh)
_ch = logging.StreamHandler()
_ch.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
log.addHandler(_ch)

# ---------------- state ----------------
STATE_LOCK = threading.Lock()
STATE = {"devices": {}, "last_sweep": None, "sweep_seconds": None, "started": None}
POLLING_PAUSED = threading.Event()
_devices_lock = threading.Lock()
_read_lock = threading.Lock()          # one heavy ACP2 read at a time
_jobs = {}                             # device name -> {"kind","state","progress",...}
_models_lock = threading.Lock()
_models = {}
_lists_cache = {}
_state_cache = {"t": 0, "raw": b"", "gz": b""}

SAFE_CBF_RE = re.compile(r'^[\w .\-()]+\.cbf$', re.IGNORECASE)
SAFE_LIST_RE = re.compile(r'^[\w .\-()]+\.(rtf|csv|tsv|txt)$', re.IGNORECASE)
IP_RE = re.compile(r'^\d{1,3}(\.\d{1,3}){3}$')
NAME_RE = re.compile(r'^[\w .\-]{1,40}$')


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


# ---------------- devices.json (seeded from the tracked example) ----------------
def load_devices():
    with _devices_lock:
        if not DEVICES_FILE.exists():
            DEVICES_FILE.write_text(DEVICES_EXAMPLE_FILE.read_text(encoding="utf-8"), encoding="utf-8")
            log.info("created devices.json from devices.example.json")
        return json.loads(DEVICES_FILE.read_text(encoding="utf-8"))


def save_devices(devices):
    with _devices_lock:
        tmp = DEVICES_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(devices, indent=2) + "\n", encoding="utf-8")
        tmp.replace(DEVICES_FILE)


def merge_new_seed_devices():
    """Append devices that were newly added to devices.example.json since the last
    start. Offered IPs are remembered in devices.seeded.json so a device deleted
    by hand from devices.json does not come back."""
    example = json.loads(DEVICES_EXAMPLE_FILE.read_text(encoding="utf-8"))
    first_run = not DEVICES_FILE.exists()
    devices = load_devices()
    seeded = set(json.loads(SEEDED_FILE.read_text())) if SEEDED_FILE.exists() else set()
    have = {d["ip"] for d in devices}
    added = [d for d in example if d["ip"] not in have and d["ip"] not in seeded]
    if added and not first_run:
        devices += added
        save_devices(devices)
        log.info("added %d new device(s) from devices.example.json", len(added))
    SEEDED_FILE.write_text(json.dumps(sorted(seeded | {d["ip"] for d in example})))


def find_device(name, need_access=True):
    """need_access: the device must be released for ACP2 access (devices.json "poll": true)."""
    key = normalize_device(name)
    for d in load_devices():
        if normalize_device(d["name"]) == key:
            if need_access and not d.get("poll"):
                raise ValueError(f"{d['name']} is not released for device access (devices.json \"poll\": false)")
            return d
    raise ValueError(f"unknown device {name!r} - not in devices.json")


def snapshot_path(name):
    return LIVE_DIR / f"{normalize_device(name)}.json"


# ---------------- red/blue failover ----------------
def blue_ip(device):
    if device.get("blue"):
        return device["blue"]
    return BLUE_RULE(device["ip"]) if BLUE_RULE else None


def call_with_failover(device, fn):
    """fn(ip) -> result. Tries red, then blue. Returns (result, "red"|"blue")."""
    try:
        return fn(device["ip"]), "red"
    except (OSError, Acp2Error) as e:
        b = blue_ip(device)
        if not b:
            raise
        log.info("%s red %s failed (%s), trying blue %s", device["name"], device["ip"], e, b)
        return fn(b), "blue"


# ---------------- polling ----------------
def poll_health(device):
    """ONE cheap AN2 request: get version."""
    def probe(ip):
        t = time.time()
        with Acp2Client(ip, timeout=ACP2_TIMEOUT_SEC) as c:
            v = c.an2_version()
        return {"an2_version": v, "rtt_ms": round((time.time() - t) * 1000)}
    try:
        res, link = call_with_failover(device, probe)
        return {"online": True, "link": link, **res, "checked": now_iso(), "error": None}
    except Exception as e:
        return {"online": False, "link": None, "checked": now_iso(), "error": str(e)[:200]}


def poll_config(device):
    """Slow data: slots and identity. Uses the routing snapshot's object ids when
    there is one (3 get-property requests), else only AN2 slot info."""
    def fetch(ip):
        out = {}
        with Acp2Client(ip, timeout=ACP2_TIMEOUT_SEC) as c:
            n = c.an2_slot_count()
            out["slots"] = [c.an2_slot_info(s) for s in range(n)]
            if snapshot_path(device["name"]).exists():
                m = get_live_model(device["name"])
                slot = m.snapshot_info["slot"]
                for key, path, name in (("firmware", ("IDENTITY",), "Product Version"),
                                        ("card", ("IDENTITY",), "Card Name"),
                                        ("nmos_label", ("GENERAL", "NMOS"), "Label")):
                    o = m.obj(path, name)
                    if o is not None and o.oid:
                        out[key] = c.get_property(slot, o.oid, 8)
        return out
    try:
        res, _ = call_with_failover(device, fetch)
        return {**res, "config_checked": now_iso()}
    except Exception as e:
        return {"config_error": str(e)[:200], "config_checked": now_iso()}


def health_poll_loop():
    pool = ThreadPoolExecutor(max_workers=CONCURRENCY)
    last_config = {}
    while True:
        if not POLLING_PAUSED.is_set():
            t0 = time.time()
            devices = [d for d in load_devices() if d.get("poll")]   # only devices released for polling
            for d, h in zip(devices, pool.map(poll_health, devices)):
                with STATE_LOCK:
                    STATE["devices"].setdefault(d["ip"], {}).update(health=h)
            due = [d for d in devices if STATE["devices"].get(d["ip"], {}).get("health", {}).get("online")
                   and time.time() - last_config.get(d["ip"], 0) > CONFIG_REFRESH_INTERVAL_SEC]
            for d, cfg in zip(due, pool.map(poll_config, due)):
                last_config[d["ip"]] = time.time()
                with STATE_LOCK:
                    STATE["devices"].setdefault(d["ip"], {}).update(config=cfg)
            with STATE_LOCK:
                STATE["last_sweep"] = now_iso()
                STATE["sweep_seconds"] = round(time.time() - t0, 1)
        time.sleep(HEALTH_INTERVAL_SEC)


# ---------------- route lists ----------------
def load_lists():
    LISTS_DIR.mkdir(exist_ok=True)
    out, errors = [], []
    for p in sorted(LISTS_DIR.iterdir()):
        if not SAFE_LIST_RE.match(p.name):
            continue
        mt = p.stat().st_mtime
        cached = _lists_cache.get(p.name)
        if cached is None or cached[0] != mt:
            try:
                cached = (mt, parse_list(p))
                log.info("parsed list %s (%s, %d devices)", p.name, cached[1]["kind"], len(cached[1]["devices"]))
            except Exception as e:
                errors.append(f"{p.name}: {e}")
                continue
            _lists_cache[p.name] = cached
        out.append(cached[1])
    return out, errors


# ---------------- models ----------------
def get_live_model(name):
    d = find_device(name, need_access=False)   # reading a stored snapshot touches no device
    sp = snapshot_path(d["name"])
    if not sp.exists():
        raise FileNotFoundError(f"{d['name']} has not been read yet - press 'Read from device'")
    key = "live:" + d["name"]
    with _models_lock:
        m = _models.get(key)
        if m is None or m.mtime != sp.stat().st_mtime:
            m = LiveModel(sp, d["name"])
            m.mtime = sp.stat().st_mtime
            _models[key] = m
        return m


def get_cbf_model(name):
    name = safe_name(name, SAFE_CBF_RE, ".cbf")
    path = CBF_DIR / name
    with _models_lock:
        m = _models.get("cbf:" + name)
        if m is None or m.mtime != path.stat().st_mtime:
            m = CbfModel(path)
            m.mtime = path.stat().st_mtime
            _models["cbf:" + name] = m
        return m


def safe_name(name, pattern, what):
    name = Path(unquote(name or "")).name
    if not pattern.match(name):
        raise ValueError(f"not a valid {what} file name: {name!r}")
    return name


def model_payload(m, device_label):
    src_cols = ["id", "kind", "block", "ch", "block_label", "name", "enabled", "delay", "bank", "slot", "stream_delay",
                "stream_max_delay", "stream_info"]
    dst_cols = ["key", "kind", "block", "ch", "block_label", "name", "enabled",
                "main", "backup", "current", "channels", "delay", "bank", "slot", "own_id"]
    lists, errors = load_lists()
    names = stream_names(lists, device_label or m.info()["nmos_label"])
    names["errors"] = errors
    analyses = {}
    for c in m.channels():
        try:
            a = analyse_channel(m, names, c["n"])
            analyses[c["n"]] = {k: a[k] for k in ("case", "status", "sides", "rx_stream", "tx_stream", "findings", "changes")}
        except Exception as e:
            analyses[c["n"]] = {"error": str(e)}
    return {
        "info": m.info(), "names": names, "channels": m.channels(), "analysis": analyses,
        "sources": {"cols": src_cols, "rows": [[s.get(c) for c in src_cols] for s in m.sources.values()]},
        "destinations": {"cols": dst_cols, "rows": [[d.get(c) for c in dst_cols] for d in m.destinations.values()]},
    }


# ---------------- heavy jobs (button only) ----------------
def start_read(name):
    d = find_device(name)
    if not _read_lock.acquire(blocking=False):
        raise ValueError("another device is being read right now - one ACP2 routing read at a time")
    job = {"kind": "read", "state": "running", "progress": 0, "started": now_iso(), "error": None}
    _jobs[d["name"]] = job

    def run():
        try:
            log.info("ACP2 routing read of %s (%s) started", d["name"], d["ip"])
            snap, link = call_with_failover(d, lambda ip: acp2_walk.walk(
                ip, snapshot_path(d["name"]), progress=lambda n, q, t: job.update(progress=n)))
            job.update(state="done", progress=snap["info"]["objects"], seconds=snap["info"]["seconds"],
                       link=link, finished=now_iso())
            log.info("ACP2 routing read of %s done: %s", d["name"], snap["info"])
        except Exception as e:
            job.update(state="error", error=str(e)[:300])
            log.warning("ACP2 routing read of %s failed: %s", d["name"], e)
        finally:
            _read_lock.release()

    threading.Thread(target=run, daemon=True).start()
    return job


_take_lock = threading.Lock()   # one Take/Undo at a time, ever


def _take_paths(d):
    key = normalize_device(d["name"])
    return LIVE_DIR / f"{key}.undo.json", LIVE_DIR / f"{key}.takes.jsonl"


def _after_write(d, m, result):
    written = {}
    for e in result.get("items", []):
        if "after" in e:
            try:
                written[m.target({"key": e["key"], "field": e["field"]}).oid] = e["after"]
            except Exception:
                pass
    written.update(result.get("status_values") or {})      # stream status read after a delay take
    if written:
        take_mod.update_snapshot(snapshot_path(d["name"]), written)


def do_take(d, changes):
    """Preset -> Active over ACP2 (take.py: validate, fresh read, undo state, write + read-back)."""
    if not _take_lock.acquire(blocking=False):
        raise take_mod.TakeError("another Take/Undo is running")
    try:
        m = get_live_model(d["name"])
        undo_path, log_path = _take_paths(d)
        log.warning("TAKE %s: %d change(s) requested", d["name"], len(changes))
        # no red/blue failover for writes: a retry on blue after a partial take would overwrite the undo state
        link = "red"
        result = take_mod.take(m, d["ip"], changes, undo_path, log_path, m.snapshot_info["slot"])
        _after_write(d, m, result)
        log.warning("TAKE %s via %s: %d written, ok=%s %s", d["name"], link, result["written"], result["ok"],
                    result.get("error") or "")
        return {**result, "link": link}
    finally:
        _take_lock.release()


def do_undo(d):
    if not _take_lock.acquire(blocking=False):
        raise take_mod.TakeError("another Take/Undo is running")
    try:
        m = get_live_model(d["name"])
        undo_path, log_path = _take_paths(d)
        log.warning("UNDO %s requested", d["name"])
        link = "red"
        result = take_mod.undo(m, d["ip"], undo_path, log_path, m.snapshot_info["slot"])
        _after_write(d, m, result)
        log.warning("UNDO %s via %s: %d restored, ok=%s %s", d["name"], link, result["written"], result["ok"],
                    result.get("error") or "")
        return {**result, "link": link}
    finally:
        _take_lock.release()


def check_channel(name, n):
    d = find_device(name)
    if not 1 <= n <= 32:
        raise ValueError("channel must be 1..32")
    m = get_live_model(d["name"])
    lists, _ = load_lists()
    names = stream_names(lists, m.info()["nmos_label"])
    report, link = call_with_failover(d, lambda ip: run_check(m, names, n, ip=ip))
    report["link"] = link
    values = report.pop("values", {})
    if values:      # keep the fresh values, so the view (and a service restart) shows what was just read
        take_mod.update_snapshot(snapshot_path(d["name"]), values)
    log.info("check %s ch %d: %s", d["name"], n, report["verdict"])
    return report


# ---------------- pasted route lists ----------------
def paste_list(text, kind, save):
    """Route master copied out of Cerebrum -> check (save=False) or store as the SRC/DST list.
    Saving moves the other lists of the same kind to lists/replaced/ (kept, no longer used)."""
    if kind not in (None, "", "SRC", "DST"):
        raise ValueError("kind must be SRC or DST")
    tsv, info = normalize_paste(text, kind or None)
    LISTS_DIR.mkdir(exist_ok=True)
    tmp = LISTS_DIR / "paste.part"
    tmp.write_text(tsv, encoding="utf-8")
    try:
        parsed = parse_list(tmp)
    except Exception:
        tmp.unlink()
        raise
    if kind and parsed["kind"] != kind:
        tmp.unlink()
        raise ValueError(f"this looks like a {parsed['kind']} list (it has {'ATX' if parsed['kind'] == 'SRC' else 'ARX'} "
                         f"streams), not {kind} - pick {parsed['kind']} or paste the other list")
    devs = parsed["devices"]
    res = {**info, "kind": parsed["kind"], "devices": len(devs),
           "channels": sum(len(d["TX"]) + len(d["RX"]) for d in devs.values()),
           "streams": sum(len(d["ATX"]) + len(d["ARX"]) for d in devs.values()),
           "device_names": sorted(d["label"] for d in devs.values())[:60], "saved": None, "replaced": []}
    if not save:
        tmp.unlink()
        return res
    name = f"{parsed['kind']}-pasted.txt"
    old_dir = LISTS_DIR / "replaced"
    for p in sorted(LISTS_DIR.iterdir()):
        if p.is_file() and SAFE_LIST_RE.match(p.name) and p.name != name:
            try:
                if parse_list(p)["kind"] != parsed["kind"]:
                    continue
            except Exception:
                continue
            old_dir.mkdir(exist_ok=True)
            p.replace(old_dir / p.name)
            res["replaced"].append(p.name)
    tmp.replace(LISTS_DIR / name)
    res["saved"] = name
    log.info("route list pasted: %s, %d rows, %d devices, replaced %s", name, info["rows"], len(devs), res["replaced"])
    return res


# ---------------- /api/state ----------------
# Devices where the assisted templates must not be used (normalised name -> reason shown in the UI).
# devices.json can set "assisted_off": "<reason>" for further units.
ASSISTED_OFF = {
    "IPG3B": "Delay banks 1–8 (delay group 1) are used for the TX chain on this device.",
    "IPG4B": "Delay banks 1–8 (delay group 1) are used for the TX chain on this device.",
}
TX_CHAIN_GROUPS = {"IPG3B": [1], "IPG4B": [1]}     # delay groups reserved for the TX chain


def state_payload():
    lists, _ = load_lists()
    idx = device_index(lists)
    devs = []
    with STATE_LOCK:
        st = json.loads(json.dumps(STATE))
    for d in load_devices():
        s = st["devices"].get(d["ip"], {})
        sp = snapshot_path(d["name"])
        devs.append({**d, "blue": blue_ip(d), "health": s.get("health"), "config": s.get("config"),
                     "in_lists": normalize_device(d["name"]) in idx,
                     "snapshot": datetime.fromtimestamp(sp.stat().st_mtime).isoformat(timespec="seconds")
                     if sp.exists() else None,
                     "undo_available": _take_paths(d)[0].exists(),
                     "job": _jobs.get(d["name"]),
                     "assisted_off": d.get("assisted_off") or ASSISTED_OFF.get(normalize_device(d["name"])),
                     "tx_chain_groups": d.get("tx_chain_groups") or TX_CHAIN_GROUPS.get(normalize_device(d["name"]), [])})
    return {"devices": devs, "paused": POLLING_PAUSED.is_set(), "last_sweep": st["last_sweep"],
            "sweep_seconds": st["sweep_seconds"], "started": st["started"], "now": now_iso(),
            "lists": [l["name"] for l in lists],
            "settings": {"health_sec": HEALTH_INTERVAL_SEC, "config_sec": CONFIG_REFRESH_INTERVAL_SEC}}


def cached_state():
    if time.time() - _state_cache["t"] > STATE_CACHE_SEC:
        raw = json.dumps(state_payload()).encode()
        _state_cache.update(t=time.time(), raw=raw, gz=gzip.compress(raw, 5))
    return _state_cache["raw"], _state_cache["gz"]


# ---------------- offline .cbf export ----------------
def export_patched(name, changes, note):
    """Offline path: write a patched copy of a .cbf (never the original)."""
    m = get_cbf_model(name)
    text, applied = m.build_patched(changes)
    if not applied:
        raise ValueError("nothing to export - every change already matches the file")
    EXPORT_DIR.mkdir(exist_ok=True)
    out = EXPORT_DIR / f"{Path(name).stem}_routed_{datetime.now():%Y%m%d-%H%M%S}.cbf"
    CbfModel.write(out, text)
    check = CbfModel(out)
    bad = [c for c in applied if abs(float(check.current_value(c)) - float(c["value"])) > 1e-6]
    if bad:
        out.unlink()
        raise ValueError(f"read-back mismatch on {len(bad)} change(s), export deleted")
    out.with_suffix(".changes.json").write_text(json.dumps({"source_file": name, "note": note, "changes": applied}, indent=2))
    return {"export_file": out.name, "changes": applied}


# ---------------- HTTP ----------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        if args and "/api/state" in str(args[0]):
            return
        log.debug(fmt, *args)

    def _send(self, body, ctype="application/json", status=200, gz=None, extra=None):
        use_gz = gz is not None and "gzip" in (self.headers.get("Accept-Encoding") or "")
        data = gz if use_gz else body
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if use_gz:
            self.send_header("Content-Encoding", "gzip")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, status=200):
        raw = json.dumps(obj).encode()
        self._send(raw, status=status, gz=gzip.compress(raw, 5) if len(raw) > 4096 else None)

    def _error(self, e, status=400):
        log.warning("%s %s -> %s", self.command, self.path, e)
        self._json({"error": str(e)}, status)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_UPLOAD:
            raise ValueError("request too large")
        return self.rfile.read(n)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path in ("/", "/index.html"):
                self._send(DASHBOARD_FILE.read_bytes(), "text/html; charset=utf-8")
            elif u.path == "/api/state":
                raw, gz = cached_state()
                self._send(raw, gz=gz)
            elif u.path == "/api/model":
                if q.get("device"):
                    d = find_device(q["device"][0], need_access=False)
                    self._json(model_payload(get_live_model(d["name"]), d["name"]))
                else:
                    self._json(model_payload(get_cbf_model(q.get("file", [""])[0]), None))
            elif u.path == "/api/files":
                CBF_DIR.mkdir(exist_ok=True)
                self._json({"files": [p.name for p in sorted(CBF_DIR.glob("*.cbf"))]})
            elif u.path == "/api/download":
                name = safe_name(q.get("file", [""])[0], SAFE_CBF_RE, ".cbf")
                body = (EXPORT_DIR / name).read_bytes()
                self._send(body, "application/octet-stream",
                           extra={"Content-Disposition": f'attachment; filename="{name}"'})
            else:
                self._json({"error": "not found"}, 404)
        except FileNotFoundError as e:
            self._error(e, 404)
        except Exception as e:
            self._error(e)

    def do_POST(self):
        u = urlparse(self.path)
        try:
            req = {}
            if u.path not in ("/api/upload-list", "/api/upload"):
                req = json.loads(self._body() or b"{}")
            if u.path == "/api/polling":
                (POLLING_PAUSED.set if req.get("paused") else POLLING_PAUSED.clear)()
                log.info("polling %s", "paused" if POLLING_PAUSED.is_set() else "resumed")
                self._json({"paused": POLLING_PAUSED.is_set()})
            elif u.path == "/api/devices":
                ip, name = (req.get("ip") or "").strip(), (req.get("name") or "").strip()
                tag = (req.get("tag") or "").strip()
                if not IP_RE.match(ip) or not NAME_RE.match(name) or (tag and not NAME_RE.match(tag)):
                    raise ValueError("invalid ip/name/tag")
                devices = load_devices()
                if any(d["ip"] == ip or normalize_device(d["name"]) == normalize_device(name) for d in devices):
                    raise ValueError("device already in the list")
                devices.append({"ip": ip, "name": name, "tag": tag, "type": "", "poll": False, "write": False})
                save_devices(devices)
                log.info("device added: %s %s", name, ip)
                self._json({"ok": True, "devices": len(devices)})
            elif u.path == "/api/devices/write":
                # the "Write ON/OFF" switch in the router header; same effect as editing devices.json
                name, on = req.get("device", ""), bool(req.get("write"))
                devices = load_devices()
                hit = [d for d in devices if normalize_device(d["name"]) == normalize_device(name)]
                if not hit:
                    raise ValueError(f"unknown device {name!r}")
                if on and not hit[0].get("poll"):
                    raise ValueError(f"{hit[0]['name']} is not released for device access (poll=false) - cannot enable writing")
                hit[0]["write"] = on
                save_devices(devices)
                log.warning("WRITE %s for %s (switched in the UI)", "ENABLED" if on else "disabled", hit[0]["name"])
                _state_cache["t"] = 0
                self._json({"device": hit[0]["name"], "write": on})
            elif u.path == "/api/devices/access":
                # the "Access" switch in the device list; same effect as editing "poll" in devices.json
                name, on = req.get("device", ""), bool(req.get("poll"))
                devices = load_devices()
                hit = [d for d in devices if normalize_device(d["name"]) == normalize_device(name)]
                if not hit:
                    raise ValueError(f"unknown device {name!r}")
                hit[0]["poll"] = on
                if not on:
                    hit[0]["write"] = False          # no access means no writing either
                save_devices(devices)
                log.warning("ACCESS %s for %s (switched in the UI)", "released" if on else "withdrawn", hit[0]["name"])
                _state_cache["t"] = 0
                self._json({"device": hit[0]["name"], "poll": on, "write": hit[0].get("write", False)})
            elif u.path == "/api/read":
                self._json(start_read(req.get("device", "")))
            elif u.path == "/api/check":
                self._json(check_channel(req.get("device", ""), int(req.get("channel", 0))))
            elif u.path in ("/api/take", "/api/undo"):
                d = find_device(req.get("device", ""), need_access=False)
                if not d.get("write") or not d.get("poll"):
                    self._json({"error": f"{u.path[5:].capitalize()} refused: writing to {d['name']} is disabled "
                                         f"(devices.json \"write\": false). Nothing was sent."}, 403)
                    return
                try:
                    if u.path == "/api/take":
                        self._json(do_take(d, req.get("changes") or []))
                    else:
                        self._json(do_undo(d))
                except take_mod.TakeError as e:
                    log.warning("%s %s refused/stopped: %s", u.path, d["name"], e)
                    self._json({"error": str(e), "result": e.result}, 409)
            elif u.path == "/api/upload-list":
                name = safe_name(self.headers.get("X-File-Name", ""), SAFE_LIST_RE, "route list")
                data = self._body()
                LISTS_DIR.mkdir(exist_ok=True)
                tmp = LISTS_DIR / (name + ".part")
                tmp.write_bytes(data)
                try:
                    parsed = parse_list(tmp)
                except Exception:
                    tmp.unlink()
                    raise
                tmp.replace(LISTS_DIR / name)
                self._json({"name": name, "kind": parsed["kind"], "devices": len(parsed["devices"])})
            elif u.path == "/api/paste-list":
                self._json(paste_list(req.get("text", ""), req.get("kind"), bool(req.get("save"))))
            elif u.path == "/api/upload":
                name = safe_name(self.headers.get("X-File-Name", ""), SAFE_CBF_RE, ".cbf")
                data = self._body()
                if data[:2] != b"\xff\xfe":
                    raise ValueError("not a UTF-16LE .cbf export")
                CBF_DIR.mkdir(exist_ok=True)
                (CBF_DIR / name).write_bytes(data)
                self._json({"name": name})
            elif u.path == "/api/export":
                self._json(export_patched(req.get("file", ""), req.get("changes", []), req.get("note", "")))
            else:
                self._json({"error": "not found"}, 404)
        except FileNotFoundError as e:
            self._error(e, 404)
        except Exception as e:
            self._error(e)


class Server(ThreadingHTTPServer):
    request_queue_size = 128
    daemon_threads = True


def main():
    ap = argparse.ArgumentParser(description="Neuron Audio Router service")
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--no-poll", action="store_true", help="start with polling paused (offline/testing)")
    args = ap.parse_args()
    for d in (CBF_DIR, EXPORT_DIR, LISTS_DIR, LIVE_DIR):
        d.mkdir(exist_ok=True)
    merge_new_seed_devices()
    STATE["started"] = now_iso()
    if args.no_poll:
        POLLING_PAUSED.set()
    threading.Thread(target=health_poll_loop, daemon=True).start()
    srv = Server((args.host, args.port), Handler)
    log.info("Neuron Audio Router on http://%s:%d/ (%d devices, polling %s)", args.host, args.port,
             len(load_devices()), "paused" if POLLING_PAUSED.is_set() else f"every {HEALTH_INTERVAL_SEC}s")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
