"""
Tools landing page - one start page for the web tools running on this server.

- Standard library only (Python 3.8+), ThreadingHTTPServer, default port 80.
- tools.json (git-ignored, created from tools.example.json on first start):
  [{"name", "description", "port", "path", "host"}]. "host" empty = the same
  host the browser used to open this page, so links work from every PC.
- Every 15 s the service checks each tool with one HTTP GET (2 s timeout) and
  shows up / down. The browser talks only to this service.
- The tool list can be edited in the page (add / change / remove).

    python server.py               # http://<this pc>/
    python server.py --port 8100
"""
import argparse
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PAGE_FILE = BASE_DIR / "index.html"
TOOLS_FILE = BASE_DIR / "tools.json"
TOOLS_EXAMPLE_FILE = BASE_DIR / "tools.example.json"
LOG_FILE = BASE_DIR / "service.log"
CHECK_INTERVAL_SEC = 15
CHECK_TIMEOUT_SEC = 2
HOST_RE = re.compile(r'^[A-Za-z0-9.\-]{0,253}$')
PATH_RE = re.compile(r'^/[\w\-./?=&%#~]*$')

log = logging.getLogger("landing")
log.setLevel(logging.INFO)
_h = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
_h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
log.addHandler(_h)
log.addHandler(logging.StreamHandler())

_tools_lock = threading.Lock()
STATUS = {}          # tool key -> {"up", "ms", "code", "error", "checked"}


def load_tools():
    with _tools_lock:
        if not TOOLS_FILE.exists():
            TOOLS_FILE.write_text(TOOLS_EXAMPLE_FILE.read_text(encoding="utf-8"), encoding="utf-8")
            log.info("created tools.json from tools.example.json")
        return json.loads(TOOLS_FILE.read_text(encoding="utf-8"))


def save_tools(tools):
    with _tools_lock:
        tmp = TOOLS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(tools, indent=2) + "\n", encoding="utf-8")
        tmp.replace(TOOLS_FILE)


def clean_tool(t):
    name = str(t.get("name", "")).strip()
    if not 1 <= len(name) <= 60:
        raise ValueError("name: 1-60 characters")
    port = int(t.get("port") or 0)
    if not 1 <= port <= 65535:
        raise ValueError("port: 1-65535")
    host = str(t.get("host", "") or "").strip()
    if not HOST_RE.match(host):
        raise ValueError("host: name or IP, or empty for this server")
    path = str(t.get("path", "") or "/").strip() or "/"
    if not PATH_RE.match(path):
        raise ValueError("path must start with /")
    return {"name": name, "description": str(t.get("description", "")).strip()[:200],
            "port": port, "path": path, "host": host}


def key(t):
    return f"{t.get('host') or 'localhost'}:{t['port']}{t.get('path') or '/'}"


def check(t):
    url = f"http://{t.get('host') or '127.0.0.1'}:{t['port']}{t.get('path') or '/'}"
    t0 = time.time()
    try:
        with urllib.request.urlopen(url, timeout=CHECK_TIMEOUT_SEC) as r:
            r.read(64)
            code = r.status
        return {"up": True, "code": code, "ms": round((time.time() - t0) * 1000), "error": None}
    except urllib.error.HTTPError as e:            # it answers, just not with 200
        return {"up": True, "code": e.code, "ms": round((time.time() - t0) * 1000), "error": None}
    except Exception as e:
        return {"up": False, "code": None, "ms": None, "error": str(e)[:120]}


def check_loop():
    pool = ThreadPoolExecutor(max_workers=8)
    while True:
        try:
            tools = load_tools()
            for t, res in zip(tools, pool.map(check, tools)):
                STATUS[key(t)] = {**res, "checked": datetime.now().isoformat(timespec="seconds")}
        except Exception as e:
            log.warning("check failed: %s", e)
        time.sleep(CHECK_INTERVAL_SEC)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, body, ctype="application/json", code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(json.dumps(obj).encode(), code=code)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(PAGE_FILE.read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/api/tools":
            tools = load_tools()
            self._json({"tools": [{**t, "status": STATUS.get(key(t))} for t in tools], "interval": CHECK_INTERVAL_SEC})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 100_000:
                raise ValueError("request too large")
            req = json.loads(self.rfile.read(n) or b"{}")
            if self.path == "/api/tools":
                # the whole list, as edited in the page
                tools = [clean_tool(t) for t in req.get("tools", [])]
                if len(tools) > 50:
                    raise ValueError("at most 50 tools")
                save_tools(tools)
                log.info("tool list saved: %s", ", ".join(f"{t['name']}:{t['port']}" for t in tools))
                for t in tools:
                    STATUS[key(t)] = {**check(t), "checked": datetime.now().isoformat(timespec="seconds")}
                self._json({"ok": True})
            else:
                self._json({"error": "not found"}, 404)
        except (ValueError, TypeError, json.JSONDecodeError) as e:
            self._json({"error": str(e)}, 400)


class Server(ThreadingHTTPServer):
    request_queue_size = 128
    daemon_threads = True


def main():
    ap = argparse.ArgumentParser(description="Tools landing page")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=80)
    args = ap.parse_args()
    load_tools()
    threading.Thread(target=check_loop, daemon=True).start()
    srv = Server((args.host, args.port), Handler)
    log.info("Tools landing page on http://%s:%d/", args.host, args.port)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
