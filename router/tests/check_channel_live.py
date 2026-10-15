#!/usr/bin/env python3
"""
LIVE, READ-ONLY test of the audio that belongs to one video path/channel.

    python3 router/tests/check_channel_live.py IPG03A 32
    python3 router/tests/check_channel_live.py IPG03A 32 --json report.json

Uses the device's ACP2 snapshot (router/live/, from "Read from device") for
object ids and the SRC/DST lists (router/lists/), re-reads every object tied to
the channel over ACP2 (get property only), re-runs the Case A/B analysis and
reads NMOS IS-05 `active` (GET) for VRX-n, the RX row's ARX and the TX row's
ATX. Nothing is written. Exit 0 = no FAIL, 1 = FAIL, 2 = could not run.
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROUTER = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROUTER))
from live_check import run_check              # noqa: E402
from live_model import LiveModel               # noqa: E402
from route_lists import normalize_device, parse_list, stream_names  # noqa: E402

COLORS = {"ok": "\033[32m", "info": "\033[90m", "warn": "\033[33m", "fail": "\033[31m"}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("device")
    ap.add_argument("channel", type=int, help="video path/channel 1..32 (A1=1 .. H4=32)")
    ap.add_argument("--ip", help="device IP (default: from the snapshot)")
    ap.add_argument("--no-nmos", action="store_true")
    ap.add_argument("--json", help="write the full report as JSON")
    args = ap.parse_args()
    snap = ROUTER / "live" / f"{normalize_device(args.device)}.json"
    if not snap.exists():
        print(f"no ACP2 snapshot {snap} - run 'Read from device' first")
        return 2
    model = LiveModel(snap, args.device)
    lists = [parse_list(p) for p in sorted((ROUTER / "lists").glob("*"))
             if p.suffix.lower() in (".rtf", ".csv", ".tsv", ".txt")]
    names = stream_names(lists, model.info()["nmos_label"])
    r = run_check(model, names, args.channel, ip=args.ip, nmos=not args.no_nmos)
    a = r["analysis"]
    print(f"\n{args.device} channel {args.channel} ({a['letter']})  video {a['main']} / {a['backup']}  ->  case {a['case']}")
    print(f"ACP2 fresh read: {r['acp2_requests']} get-property requests in {r['seconds']}s from {r['ip']}, "
          f"snapshot of {r['snapshot_read_at']}")
    print(f"changed since the snapshot: {len(r['fresh_changed'])}")
    for c in r["fresh_changed"][:20]:
        print(f"   {c['object']}: {c['old']} -> {c['new']}")
    cur = r["embedder_current"]
    print(f"Embedder {a['letter']} Input Current (on air now): {cur[0]}..{cur[-1]}\n")
    for f in a["findings"]:
        print(f"  {COLORS[f['level']]}[{f['level']:4}]\033[0m {f['text']}")
    if a["changes"]:
        print("\n  would change (template, NOT applied):", dict(Counter(ch["why"] for ch in a["changes"])))
    if "nmos" in r:
        print("\n  NMOS IS-05 (live):")
        for lab in sorted(r["nmos"]):
            v = r["nmos"][lab]
            print(f"   {lab:8} {'enabled ' if v.get('enabled') else 'DISABLED'} {v.get('multicast') or '-':16} {v.get('source', '')}")
    elif "nmos_error" in r:
        print(f"\n  NMOS not readable: {r['nmos_error']}")
    print(f"\n  VERDICT: {r['verdict']}")
    if args.json:
        Path(args.json).write_text(json.dumps(r, indent=2))
    return 1 if a["status"] == "fail" else 0


if __name__ == "__main__":
    sys.exit(main())
