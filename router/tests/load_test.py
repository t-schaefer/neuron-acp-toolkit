#!/usr/bin/env python3
"""
Load test: N parallel viewers polling GET /api/state (what every open browser does
every 4 s). Viewers only read the service's cached STATE - no device traffic.

    python3 router/tests/load_test.py http://127.0.0.1:8101 --clients 200 --seconds 20
"""
import argparse
import statistics
import threading
import time
import urllib.request


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--clients", type=int, default=200)
    ap.add_argument("--seconds", type=float, default=20)
    ap.add_argument("--interval", type=float, default=1.0, help="seconds between polls per client (browser: 4)")
    args = ap.parse_args()
    lat, errors, lock = [], [], threading.Lock()
    stop = time.time() + args.seconds

    def viewer():
        while time.time() < stop:
            t = time.time()
            try:
                req = urllib.request.Request(args.url.rstrip("/") + "/api/state", headers={"Accept-Encoding": "gzip"})
                with urllib.request.urlopen(req, timeout=10) as r:
                    r.read()
                with lock:
                    lat.append(time.time() - t)
            except Exception as e:
                with lock:
                    errors.append(str(e))
            time.sleep(max(0, args.interval - (time.time() - t)))

    threads = [threading.Thread(target=viewer) for _ in range(args.clients)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    lat.sort()
    print(f"{args.clients} clients, {args.seconds}s: {len(lat)} ok, {len(errors)} errors")
    if lat:
        print(f"latency median {statistics.median(lat)*1000:.0f} ms, p95 {lat[int(len(lat)*0.95)]*1000:.0f} ms, "
              f"max {lat[-1]*1000:.0f} ms")
    if errors:
        print("first errors:", errors[:3])
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
