"""
Live, read-only check of the audio tied to one video path/channel (shared by
the service's "Check channel" button and tests/check_channel_live.py).

ACP2: get property only (acp2/client.py cannot write). NMOS: GET only.
"""
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "acp2"))
from client import Acp2Client          # noqa: E402
from checks import analyse_channel     # noqa: E402

NMOS_REGISTRY = "http://10.12.81.11:8081/x-nmos/query/v1.3"   # from the device's GENERAL > NMOS settings; None = skip NMOS


def http_json(url, timeout=5):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def channel_objects(model, analysis):
    """(group path, object name) for everything tied to this channel."""
    L = analysis["letter"]
    cp = ("PROCESSING VIDEO", f"PATH {L[0]}", f"CHANNEL {int(L[1:]):02d}")
    want = [(cp, n) for n in ("Main Input", "Backup Input", "Input Selection", "Switch Status",
                              "Audio Embedder Mode", "Audio Embedder Group Select")]
    want.append((cp + ("EMBEDDER",), "Input Selection"))
    for k in range(1, 17):
        want += [(cp + ("EMBEDDER", f"AUDIO CHANNEL {k}"), f) for f in ("Input Main", "Input Backup", "Input Current")]
    rx, tx = analysis.get("rx_row") or {}, analysis.get("tx_row") or {}
    for s in rx.get("refs", []):
        sp = ("INPUT", "IP", "AUDIO", f"STREAM {s}")
        want += [(sp, n) for n in ("Stream", "Stream Delay", "SDP File", "Info")]
    for s in tx.get("refs", [])[:1]:
        sp = ("OUTPUT", "IP", "AUDIO", f"STREAM {s}")
        want += [(sp, n) for n in ("Stream", "Channels", "Take", "Info", "SDP File")]
        for k in range(1, 17):
            want += [(sp + ("AUDIO CHANNELS", f"AUDIO CHANNEL {k}"), f) for f in ("Input Main", "Input Backup", "Input Current")]
    for side in analysis["sides"].values():
        g = side.get("group")
        if side.get("case") == "B" and g:
            for b in range((g - 1) * 8 + 1, g * 8 + 1):
                bp = ("PROCESSING AUDIO", "AUDIO DELAY", f"BANK {b}")
                want += [(bp, "Delay"), (bp, "Input Selection")]
                for k in (1, 2):
                    want += [(bp + (f"AUDIO CHANNEL {k}",), f) for f in ("Input Main", "Input Backup", "Input Current")]
    return want


def nmos_state(node_label, labels):
    """IS-05 `active` for the given sender/receiver labels of this node (GET only)."""
    out = {}
    node = http_json(f"{NMOS_REGISTRY}/nodes?label={node_label}")
    if not node:
        return {"error": f"node {node_label} not in NMOS registry"}
    node = node[0]
    ep = next((e for e in node["api"]["endpoints"] if not e["host"].startswith("192.168.")), node["api"]["endpoints"][0])
    base = f"http://{ep['host']}:{ep['port']}/x-nmos/connection/v1.1/single"
    devs = {d["id"] for d in http_json(f"{NMOS_REGISTRY}/devices?node_id={node['id']}")}
    for kind in ("receivers", "senders"):
        for item in http_json(f"{NMOS_REGISTRY}/{kind}?paging.limit=2000"):
            if item["device_id"] in devs and item["label"] in labels:
                a = http_json(f"{base}/{kind}/{item['id']}/active")
                tp = (a.get("transport_params") or [{}])[0]
                sdp = (a.get("transport_file") or {}).get("data") or ""
                s = re.search(r"\ns=([^\r\n]*)", sdp)
                out[item["label"]] = {"enabled": a.get("master_enable"),
                                      "multicast": tp.get("multicast_ip") or tp.get("destination_ip"),
                                      "source": s.group(1) if s else ""}
                time.sleep(0.05)
    return out



def run_check(model, names, n, ip=None, nmos=True, pace=0.005):
    """Fresh read of every object tied to channel n, re-analysis, NMOS state.
    Mutates `model` with the fresh values. Returns the report dict."""
    before = analyse_channel(model, names, n)
    oids = model.oids_for(channel_objects(model, before))
    ip = ip or model.snapshot_info["host"]
    t0 = time.time()
    with Acp2Client(ip, pace=pace) as c:
        values = {oid: c.get_property(model.snapshot_info["slot"], oid, 8) for oid in oids}
        nreq = c.requests
    changed = model.apply_values(values)
    after = analyse_channel(model, names, n)
    by_oid = {o.oid: "/".join(path) + "/" + o.name for path, g in model.groups.items() for o in g.objects.values() if o.oid}
    report = {"device": model.device_name, "channel": n, "analysis": after, "ip": ip,
              "acp2_requests": nreq, "seconds": round(time.time() - t0, 2),
              "snapshot_read_at": model.snapshot_info.get("read_at"),
              "values": values,
              "fresh_changed": [{"object": by_oid.get(k, k), "old": a, "new": b} for k, (a, b) in changed.items()],
              "embedder_current": sorted({model.destinations[f"emb:{after['letter']}:{k}"]["current"] for k in range(1, 17)})}
    if nmos and NMOS_REGISTRY is None:
        report["nmos_error"] = "NMOS check skipped (no registry configured)"
    elif nmos:
        labels = {f"VRX-{n:02d}"} | {f"ARX-{s:02d}" for s in (after.get("rx_row") or {}).get("refs", [])} \
                 | {f"ATX-{s:02d}" for s in (after.get("tx_row") or {}).get("refs", [])[:1]}
        try:
            nm = nmos_state(model.info()["nmos_label"], labels)
            report["nmos"] = nm
            vrx = nm.get(f"VRX-{n:02d}", {})
            arx_on = [lab for lab, v in nm.items() if lab.startswith("ARX") and v.get("enabled")]
            if vrx and not vrx.get("enabled") and not arx_on:
                report["verdict"] = "channel not in use: video and audio receivers are disabled"
            elif vrx.get("enabled") and not arx_on:
                report["verdict"] = "VIDEO IS RECEIVED BUT NO AUDIO RECEIVER IS ACTIVE - audio silent on this path"
        except Exception as e:
            report["nmos_error"] = str(e)
    fails = [f["text"] for f in after["findings"] if f["level"] == "fail"]
    warns = [f["text"] for f in after["findings"] if f["level"] == "warn"]
    report["verdict"] = report.get("verdict") or ("routing problem: " + "; ".join(fails) if fails else
                                                  f"routing OK, {len(warns)} warning(s)" if warns else "routing OK")
    return report
