"""
Routing model of a LIVE Neuron, built from an ACP2 read (acp2/walk.py snapshot).

The ACP2 tree of the BRIDGE/CONVERT IP slot is the same GROUP/OBJECT tree the
.cbf export contains (verified on IPG03A), so this rebuilds the same
`groups` structure and reuses CbfModel's routing logic unchanged: sources,
destinations, 16ch delay groups, channels, Case A/B inputs. Every object also
carries its ACP2 object id (`Obj.oid`), which a later Take needs.

Read-only: nothing here talks to the device. Reading is acp2/walk.py.
"""
import json
from pathlib import Path

from cbf_model import CbfModel, Group, Obj


def _value_str(rec):
    v = rec.get("value")
    if isinstance(v, list):          # preset depth: active index value first
        v = v[0] if v else ""
    if isinstance(v, float):
        return f"{v:.6f}"
    if v is None:
        return ""
    return str(v)


class LiveModel(CbfModel):
    def __init__(self, snapshot_path, device_name=""):
        self.path = Path(snapshot_path)
        snap = json.loads(self.path.read_text(encoding="utf-8"))
        self.snapshot_info = snap["info"]
        self.device_name = device_name
        groups = {}
        for rec in snap["objects"]:
            parts = tuple(rec["path"].split("/")) if rec["path"] else ()
            if rec.get("object_type") == 0:          # node -> group
                groups.setdefault(parts, Group(parts))
                continue
            parent = parts[:-1]
            enums = []
            if rec.get("object_type") == 2 and rec.get("options"):
                opts = {int(k): v for k, v in rec["options"].items()}
                enums = [opts.get(i, "") for i in range(max(opts) + 1)]
            groups.setdefault(parent, Group(parent)).objects[rec["label"]] = Obj(
                name=rec["label"], value=_value_str(rec), start=0, end=0,
                writable=bool((rec.get("access") or 0) & 2), enums=enums,
                minimum=str(rec.get("min", "")), maximum=str(rec.get("max", "")), oid=rec["id"])
        self.text = ""
        self.devices = []
        ident = groups.get(("IDENTITY",))
        card = ident.objects.get("Card Name").value if ident and "Card Name" in ident.objects else ""
        ver = ident.objects.get("Product Version").value if ident and "Product Version" in ident.objects else ""
        self.device = {"attrs": {"IP": self.snapshot_info.get("host", ""), "DEVICE": card, "VERSION": ver},
                       "groups": groups, "start": 0, "end": 0}
        self.groups = groups
        self.sources, self.destinations = {}, {}
        self._build_routing()

    def apply_values(self, values):
        """Overwrite object values with fresh reads {oid: decoded ACP2 value} and
        rebuild sources/destinations. Returns {oid: (old, new)} for what changed."""
        by_oid = {o.oid: o for g in self.groups.values() for o in g.objects.values() if o.oid}
        changed = {}
        for oid, v in values.items():
            o = by_oid.get(oid)
            if o is None:
                continue
            new = f"{v:.6f}" if isinstance(v, float) else str(v)
            if new != o.value:
                changed[oid] = (o.value, new)
                o.value = new
        self.sources, self.destinations = {}, {}
        self._build_routing()
        return changed

    def oids_for(self, paths_and_names):
        """[(group path tuple, object name)] -> {oid: (path, name)} for objects that exist."""
        out = {}
        for path, name in paths_and_names:
            o = self.obj(path, name)
            if o is not None and o.oid:
                out[o.oid] = (path, name)
        return out

    def info(self):
        i = super().info()
        i["file"] = f"live {self.device_name or self.snapshot_info.get('host')}"
        i["source"] = "live"
        i["read_at"] = self.snapshot_info.get("read_at")
        i["acp2_slot"] = self.snapshot_info.get("slot")
        return i

    def target(self, change):
        """ACP2 object for a change ({key, field, value}) - for a later Take.
        Returns (Obj, path) or raises."""
        if change["field"] == "delay":
            o = self._delay_obj(change["key"])
        else:
            dst = self.destinations[change["key"]]
            o = self.obj(dst["path"], {"main": "Input Main", "backup": "Input Backup"}[change["field"]])
        if o is None or not o.oid:
            raise ValueError(f"no ACP2 object for {change}")
        return o

    def build_patched(self, changes):
        raise ValueError("a live model is not written as a file - use Take (not enabled yet)")
