#!/usr/bin/env python3
"""
Neuron Audio Router - graphical audio routing for Neuron Bridge / Convert-IP.

Works on .cbf backup files only: load an export, route graphically in the
browser, export a patched copy, re-import it on the device. It never talks to
a Neuron. (Live access would need ACP2 on TCP 2072, for which there is no
protocol documentation - see ../acp2/README.md.)

Safety rules built in:
- The loaded file is never overwritten. Exports go to exports/ under a new name,
  with a JSON change log next to them.
- Only `Input Main` / `Input Backup` VALUEs are edited, via offset splices.
- Every export re-checks Object_Count and the UTF-16LE header before writing,
  and re-parses the written file to confirm each change landed.

Standard library only - nothing to pip install.

    python server.py                 # http://localhost:8091/
    python server.py --port 9000
"""
import argparse
import json
import logging
import re
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

from cbf_model import CbfModel

BASE_DIR = Path(__file__).resolve().parent
DASHBOARD_FILE = BASE_DIR / "dashboard.html"
CBF_DIR = BASE_DIR / "cbf"          # drop .cbf exports here (or upload in the browser)
EXPORT_DIR = BASE_DIR / "exports"
HOST = "127.0.0.1"
PORT = 8091
MAX_UPLOAD = 64 * 1024 * 1024

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("neuron-router")

_lock = threading.Lock()
_models = {}  # file name -> CbfModel

SAFE_NAME_RE = re.compile(r'^[\w .\-()]+\.cbf$', re.IGNORECASE)


def safe_name(name):
    name = Path(unquote(name or "")).name
    if not SAFE_NAME_RE.match(name):
        raise ValueError(f"not a valid .cbf file name: {name!r}")
    return name


def list_files():
    CBF_DIR.mkdir(exist_ok=True)
    out = []
    for p in sorted(CBF_DIR.glob("*.cbf"), key=lambda p: p.stat().st_mtime, reverse=True):
        st = p.stat()
        out.append({"name": p.name, "size": st.st_size,
                    "modified": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")})
    return out


def get_model(name):
    name = safe_name(name)
    path = CBF_DIR / name
    with _lock:
        m = _models.get(name)
        if m is None or m.mtime != path.stat().st_mtime:
            t = time.time()
            m = CbfModel(path)
            m.mtime = path.stat().st_mtime
            _models[name] = m
            log.info("loaded %s in %.1fs (%d sources, %d destinations)",
                     name, time.time() - t, len(m.sources), len(m.destinations))
        return m


def model_payload(m):
    src_cols = ["id", "kind", "block", "ch", "block_label", "name", "enabled", "delay"]
    dst_cols = ["key", "kind", "block", "ch", "block_label", "name", "enabled",
                "main", "backup", "current", "channels", "delay"]
    return {
        "info": m.info(),
        "sources": {"cols": src_cols, "rows": [[s.get(c) for c in src_cols] for s in m.sources.values()]},
        "destinations": {"cols": dst_cols, "rows": [[d.get(c) for c in dst_cols] for d in m.destinations.values()]},
    }


def export_patched(name, changes, note):
    m = get_model(name)
    text, applied = m.build_patched(changes)
    if not applied:
        raise ValueError("nothing to export - every change already matches the file")
    EXPORT_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = EXPORT_DIR / f"{Path(name).stem}_routed_{stamp}.cbf"
    size = CbfModel.write(out, text)

    # read the written file back and confirm every change is really in it
    check = CbfModel(out)
    bad = []
    for c in applied:
        got = check.destinations[c["key"]][c["field"]]
        if got != int(c["value"]):
            bad.append(f"{c['key']} {c['field']}: wanted {c['value']}, file has {got}")
    if bad:
        out.unlink()
        raise ValueError("read-back mismatch, export deleted:\n" + "\n".join(bad))

    report = {"source_file": name, "export_file": out.name, "created": stamp, "note": note,
              "bytes": size, "changes": applied}
    out.with_suffix(".changes.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    log.info("exported %s (%d changes)", out.name, len(applied))
    return report


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log.debug(fmt, *args)

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, e, status=400):
        log.warning("%s %s -> %s", self.command, self.path, e)
        self._json({"error": str(e)}, status)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_UPLOAD:
            raise ValueError("upload too large")
        return self.rfile.read(n)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path in ("/", "/index.html"):
                body = DASHBOARD_FILE.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif u.path == "/api/files":
                self._json({"files": list_files(), "cbf_dir": str(CBF_DIR), "export_dir": str(EXPORT_DIR)})
            elif u.path == "/api/model":
                self._json(model_payload(get_model(q.get("file", [""])[0])))
            elif u.path == "/api/download":
                name = safe_name(q.get("file", [""])[0])
                path = EXPORT_DIR / name
                body = path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Disposition", f'attachment; filename="{name}"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif u.path == "/api/live":
                self._json({"available": False,
                            "reason": "Live access needs ACP2 (TCP 2072); no protocol documentation available."}, 501)
            else:
                self._json({"error": "not found"}, 404)
        except FileNotFoundError as e:
            self._error(e, 404)
        except Exception as e:
            self._error(e)

    def do_POST(self):
        u = urlparse(self.path)
        try:
            if u.path == "/api/upload":
                name = safe_name(self.headers.get("X-File-Name", ""))
                data = self._body()
                if data[:2] != b'\xff\xfe':
                    raise ValueError("not a UTF-16LE .cbf export (missing BOM)")
                CBF_DIR.mkdir(exist_ok=True)
                dest = CBF_DIR / name
                if dest.exists():
                    dest = CBF_DIR / f"{dest.stem}_{datetime.now():%Y%m%d-%H%M%S}.cbf"
                dest.write_bytes(data)
                self._json({"name": dest.name})
            elif u.path == "/api/export":
                req = json.loads(self._body() or b"{}")
                self._json(export_patched(req.get("file", ""), req.get("changes", []), req.get("note", "")))
            else:
                self._json({"error": "not found"}, 404)
        except Exception as e:
            self._error(e)


def main():
    ap = argparse.ArgumentParser(description="Neuron Audio Router (offline .cbf routing)")
    ap.add_argument("--host", default=HOST, help="bind address (default 127.0.0.1; 0.0.0.0 to share on the network)")
    ap.add_argument("--port", type=int, default=PORT)
    args = ap.parse_args()
    CBF_DIR.mkdir(exist_ok=True)
    EXPORT_DIR.mkdir(exist_ok=True)
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    log.info("Neuron Audio Router on http://%s:%d/  (cbf dir: %s)", args.host, args.port, CBF_DIR)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
