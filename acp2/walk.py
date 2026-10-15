"""
Read-only discovery walk of a Neuron's ACP2 tree, limited to the routing areas.

Every node is fetched with `get object` (so its label and type are known), but
only nodes whose path matches ROUTING_PATHS are descended into. LEG/STATISTICS,
SDI, data paths etc. are seen as one node and skipped. The result is a JSON
snapshot {objects: [{id, path, label, type, access, value, options, ...}]}
that router/live_model.py turns into sources/destinations.

    python3 acp2/walk.py 10.12.80.20 router/live/IPG03A.json

Uses only acp2/client.py, which cannot write. Resumable: on a dropped
connection it reconnects and continues with the pending queue.
"""
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from client import Acp2Client, Acp2Error  # noqa: E402

N = r'\d+'
ROUTING_PATHS = [re.compile('^' + p + '$') for p in (
    r'', r'IDENTITY', r'GENERAL', r'GENERAL/NMOS',
    r'INPUT', r'INPUT/IP', r'INPUT/IP/AUDIO',
    rf'INPUT/IP/AUDIO/STREAM {N}', rf'INPUT/IP/AUDIO/STREAM {N}/AUDIO CHANNELS(/AUDIO CHANNEL {N})?',
    r'INPUT/MADI', rf'INPUT/MADI/OPTICAL {N}(/AUDIO CHANNELS(/AUDIO CHANNEL {N})?)?',
    r'PROCESSING VIDEO', r'PROCESSING VIDEO/PATH [A-Z]', rf'PROCESSING VIDEO/PATH [A-Z]/CHANNEL {N}',
    rf'PROCESSING VIDEO/PATH [A-Z]/CHANNEL {N}/(DE-EMBEDDER|EMBEDDER)(/AUDIO CHANNEL {N})?',
    r'PROCESSING AUDIO', r'PROCESSING AUDIO/AUDIO DELAY', rf'PROCESSING AUDIO/AUDIO DELAY/BANK {N}(/AUDIO CHANNEL {N})?',
    r'OUTPUT', r'OUTPUT/IP', r'OUTPUT/IP/AUDIO',
    rf'OUTPUT/IP/AUDIO/STREAM {N}', rf'OUTPUT/IP/AUDIO/STREAM {N}/AUDIO CHANNELS(/AUDIO CHANNEL {N})?',
    r'OUTPUT/MADI', rf'OUTPUT/MADI/OPTICAL {N}(/AUDIO CHANNELS(/AUDIO CHANNEL {N})?)?',
)]
KEEP_PROPS = ("object_type", "label", "access", "value", "options", "number_type", "min", "max",
              "unit", "preset_depth", "string_max_length")


def descend(path):
    return any(r.match("/".join(path)) for r in ROUTING_PATHS)


def find_slot(c):
    """The slot whose ACP2 root has an IDENTITY + PROCESSING AUDIO child = the BRIDGE card."""
    for s in range(c.an2_slot_count()):
        info = c.an2_slot_info(s)
        if info["status"] != "present" or 2 not in info["protocols"]:
            continue
        root = c.get_object(s, 1)
        labels = {c.get_object(s, ch).label for ch in root.children}
        if {"IDENTITY", "PROCESSING AUDIO", "OUTPUT"} <= labels:
            return s
    raise Acp2Error("no slot with a BRIDGE/CONVERT IP menu found")


def walk(host, out_path, pace=0.002, slot=None, progress=None):
    t0 = time.time()
    objects, queue, errors = {}, [(1, ())], []
    c = Acp2Client(host, pace=pace).connect()
    try:
        slot = find_slot(c) if slot is None else slot
        info = {"host": host, "slot": slot, "an2_version": c.an2_version(), "acp2_version": c.acp2_version(slot)}
        retries = 0
        while queue:
            oid, parent = queue.pop()
            if oid in objects:
                continue
            try:
                o = c.get_object(slot, oid)
            except Acp2Error as e:
                if "connection closed" in str(e) and retries < 5:
                    retries += 1
                    time.sleep(2)
                    c.close()
                    c = Acp2Client(host, pace=pace).connect()
                    queue.append((oid, parent))
                    continue
                errors.append({"id": oid, "path": "/".join(parent), "error": str(e)})
                continue
            path = parent + (o.label,) if oid != 1 else ()
            rec = {"id": oid, "path": "/".join(path)}
            rec.update({k: o.props[k] for k in KEEP_PROPS if k in o.props})
            if "options" in rec:
                rec["options"] = {str(k): v for k, v in rec["options"].items()}
            if o.type == "node":
                rec["children"] = o.children
                if descend(path):
                    for ch in reversed(o.children):
                        queue.append((ch, path))
            objects[oid] = rec
            if progress and len(objects) % 500 == 0:
                progress(len(objects), len(queue), time.time() - t0)
    finally:
        info_req = c.requests
        c.close()
    snap = {"info": {**info, "objects": len(objects), "errors": len(errors), "requests": info_req,
                     "seconds": round(time.time() - t0, 1), "read_at": time.strftime("%Y-%m-%dT%H:%M:%S")},
            "objects": sorted(objects.values(), key=lambda r: r["id"]), "errors": errors}
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(out_path) + ".part")
    tmp.write_text(json.dumps(snap), encoding="utf-8")
    tmp.replace(out_path)
    return snap


if __name__ == "__main__":
    host, out = sys.argv[1], sys.argv[2]
    s = walk(host, out, progress=lambda n, q, t: print(f"{n} objects, queue {q}, {t:.0f}s", flush=True))
    print(json.dumps(s["info"]))
