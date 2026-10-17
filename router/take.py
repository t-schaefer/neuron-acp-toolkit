"""
TAKE (Preset -> Active) and UNDO over ACP2 - the only place the service writes.

take(model, ip, changes, undo_path, log_path):
  1. validate every change: known routing/delay target, writable, number type
     u32/float, routing source id exists in this device, delay within the
     object's MIN..MAX, no duplicates, at most MAX_CHANGES
  2. read every target fresh (get property). If any value differs from the
     model (= the "Active" the preset was built on), STOP - nothing is written
  3. save the fresh values as the undo state (undo_path), then write each
     change and read it back immediately; the first failure stops the take
  4. append a record to the take log and update the model with the read-backs

undo(...) writes the saved "before" values back the same way, but only if the
targets still hold what the take wrote (otherwise someone changed them since).
"""
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "acp2"))
from writer import Acp2Writer, WRITABLE_TYPES   # noqa: E402

MAX_CHANGES = 200
DELAY_LIMITS = {"Delay": 3000.0, "Stream Delay": 5000.0}   # device MAX values (IPG03A), used if MAX is missing
PACE_SEC = 0.01


class TakeError(Exception):
    def __init__(self, msg, result=None):
        super().__init__(msg)
        self.result = result


def _same(ntype, a, b):
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) < 1e-3 if ntype == 8 else int(a) == int(b)


def resolve(model, changes):
    """Validate the changes and attach the ACP2 object to each. Raises TakeError."""
    if not changes:
        raise TakeError("nothing to take - the preset is empty")
    if len(changes) > MAX_CHANGES:
        raise TakeError(f"{len(changes)} changes - at most {MAX_CHANGES} per take")
    out, seen = [], set()
    for c in changes:
        field = c.get("field")
        if field not in ("main", "backup", "delay"):
            raise TakeError(f"invalid field {field!r}")
        try:
            o = model.target(c)
        except Exception as e:
            raise TakeError(f"{c.get('key')}: {e}")
        if o.oid in seen:
            raise TakeError(f"{c['key']} {field}: the same object is changed twice")
        seen.add(o.oid)
        if not o.writable or o.ntype not in WRITABLE_TYPES:
            raise TakeError(f"{c['key']} {field}: object is not a writable u32/float value")
        if field == "delay":
            v = float(c["value"])
            lo = float(o.minimum or 0)
            hi = float(o.maximum) if o.maximum not in ("", None) and float(o.maximum) > 0 else DELAY_LIMITS.get(o.name)
            if hi is None:
                raise TakeError(f"{c['key']}: no known maximum for {o.name} - refusing")
            if not lo <= v <= hi:
                raise TakeError(f"{c['key']}: delay {v} outside {lo}..{hi} ms")
        else:
            v = int(c["value"])
            if v not in model.sources:
                raise TakeError(f"{c['key']} {field}: source id {v} does not exist on this device")
        old = float(o.value) if o.ntype == 8 else int(float(o.value))
        out.append({"key": c["key"], "field": field, "value": v, "oid": o.oid, "ntype": o.ntype,
                    "model": old, "name": o.name})
    return out


def _apply(model, ip, items, target_key, expect_key, slot):
    """Fresh read (must equal expect_key), then write target_key with read-back."""
    result = {"ip": ip, "items": [], "written": 0, "ok": False, "started": datetime.now().isoformat(timespec="seconds")}
    t0 = time.time()
    with Acp2Writer(ip, pace=PACE_SEC) as w:
        fresh = {it["oid"]: w.get_property(slot, it["oid"], 8) for it in items}
        # already at the target (e.g. the part of an interrupted take that was never written): nothing to do
        result["skipped"] = [{"key": it["key"], "field": it["field"]} for it in items
                             if _same(it["ntype"], fresh[it["oid"]], it[target_key])]
        items = [it for it in items if not _same(it["ntype"], fresh[it["oid"]], it[target_key])]
        changed = [it for it in items if not _same(it["ntype"], fresh[it["oid"]], it[expect_key])]
        if changed:
            result["error"] = (f"{len(changed)} value(s) on the device differ from what the preset was built on "
                               f"(e.g. {changed[0]['key']} {changed[0]['field']}: device {fresh[changed[0]['oid']]}, "
                               f"expected {changed[0][expect_key]}). Nothing was written - refresh the routing read.")
            result["changed_on_device"] = [{"key": it["key"], "field": it["field"], "device": fresh[it["oid"]],
                                            "expected": it[expect_key]} for it in changed]
            raise TakeError(result["error"], result)
        for it in items:
            it["before"] = fresh[it["oid"]]
        result["before"] = [{**it} for it in items]
        result["undo_ready"] = True
        yield result                     # caller saves the undo state BEFORE anything is written
        for it in items:
            entry = {"key": it["key"], "field": it["field"], "before": it["before"], "want": it[target_key]}
            try:
                reply = w.set_value(slot, it["oid"], it[target_key], it["ntype"])
                back = w.get_property(slot, it["oid"], 8)
                entry.update(reply=reply, after=back, ok=_same(it["ntype"], back, it[target_key]))
            except Exception as e:
                entry.update(ok=False, error=str(e))
            result["items"].append(entry)
            if not entry["ok"]:
                result["error"] = f"stopped at {it['key']} {it['field']}: " + (entry.get("error") or
                                  f"read back {entry.get('after')} instead of {it[target_key]}")
                break
            result["written"] += 1
    result["ok"] = result["written"] == len(items)
    result["seconds"] = round(time.time() - t0, 2)
    model.apply_values({it["oid"]: e["after"] for it, e in zip(items, result["items"]) if "after" in e})
    yield result


def update_snapshot(snapshot_path, values):
    """Write read-back values into the stored ACP2 snapshot so 'Active' stays right."""
    p = Path(snapshot_path)
    snap = json.loads(p.read_text(encoding="utf-8"))
    for o in snap["objects"]:
        if o["id"] in values:
            o["value"] = values[o["id"]]
    tmp = p.with_suffix(".part")
    tmp.write_text(json.dumps(snap), encoding="utf-8")
    tmp.replace(p)


def _log(log_path, record):
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def take(model, ip, changes, undo_path, log_path, slot):
    items = resolve(model, changes)
    gen = _apply(model, ip, items, "value", "model", slot)
    pre = next(gen)                                      # fresh read passed
    Path(undo_path).write_text(json.dumps({"taken": datetime.now().isoformat(timespec="seconds"), "ip": ip,
                                           "items": [{k: it[k] for k in ("key", "field", "oid", "ntype", "before", "value")}
                                                     for it in pre["before"]]}, indent=1), encoding="utf-8")
    result = next(gen)
    check_effect(model, ip, slot, result)
    _log(log_path, {"kind": "take", **{k: result.get(k) for k in ("started", "ip", "ok", "written", "seconds", "error")},
                    "items": result["items"]})
    return result


def check_effect(model, ip, slot, result):
    """A Stream Delay is only applied while the stream receives media: the device
    reports the delay it can apply as 'Maximum Stream Delay' (0 = no media). Read it
    (and Info, Stream) after writing and warn when the written delay cannot act."""
    from client import Acp2Client
    status = {}
    items = [e for e in result.get("items", []) if e.get("ok") and e["key"].startswith("streamdelay:")]
    if not items:
        return
    with Acp2Client(ip) as c:
        for e in items:
            path = ("INPUT", "IP", "AUDIO", f"STREAM {e['key'].split(':')[1]}")
            vals = {}
            for name in ("Maximum Stream Delay", "Info", "Stream"):
                o = model.obj(path, name)
                if o is not None and o.oid:
                    v = c.get_property(slot, o.oid, 8)
                    status[o.oid] = v
                    vals[name] = (o.enums[v] if o.enums and isinstance(v, int) and v < len(o.enums) and o.enums[v] else v)
            e["status"] = vals
            mx = vals.get("Maximum Stream Delay")
            if isinstance(mx, (int, float)) and mx < float(e["after"]):
                e["warning"] = (f"written, but NOT effective: the device can apply at most {mx} ms on this stream "
                                f"(Info: {vals.get('Info') or '-'}, Stream: {vals.get('Stream') or '-'}). "
                                f"It applies once the stream receives media.")
    result["status_values"] = status
    model.apply_values(status)


def undo(model, ip, undo_path, log_path, slot):
    p = Path(undo_path)
    if not p.exists():
        raise TakeError("no take to undo")
    saved = json.loads(p.read_text(encoding="utf-8"))
    # what the take wrote is what must be on the device now; restore "before"
    items = [{"key": s["key"], "field": s["field"], "oid": s["oid"], "ntype": s["ntype"],
              "model": s["value"], "value": s["before"]} for s in reversed(saved["items"])]
    gen = _apply(model, ip, items, "value", "model", slot)
    next(gen)
    result = next(gen)
    _log(log_path, {"kind": "undo", "of_take": saved["taken"],
                    **{k: result.get(k) for k in ("started", "ip", "ok", "written", "seconds", "error")},
                    "items": result["items"]})
    if result["ok"]:
        p.rename(p.with_suffix(".undone.json"))
    return result
