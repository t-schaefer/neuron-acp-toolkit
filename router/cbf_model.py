"""
Audio-routing model of a Neuron Bridge / Convert-IP .cbf backup.

The Neuron audio matrix (the "Audio Stream Shuffler" page) is a flat ID space.
Every audio *source* has a fixed numeric Identifier, every *destination* has an
`Input Main` / `Input Backup` object whose VALUE is the Identifier it listens to:

    sources (Identifier)                       destinations (Input Main/Backup)
    1    .. 2048  IP audio inputs, 16ch         embedders   A1..H4 x 16
    2049 .. 3072  IP audio inputs, 64ch         delay banks 1..256 x 2
    3073 .. 3584  SDI de-embedders, 32 x 16     IP audio outputs (ST2110-30/31)
    3585 .. 4096  delay banks, 256 x 2          MADI outputs
    4097 .. 4352  MADI inputs, 4 x 64

Nothing here assumes those ranges: sources and destinations are found by walking
the file and reading each `Identifier` / `Input Main` / `Input Backup` object,
so a firmware that numbers differently is reported as it is, not as we expect.

Editing follows docs/cbf-format.md: collect (start, end, value) edits on the
VALUE="..." spans only, apply them in reverse offset order, then check
Object_Count and the UTF-16LE header before anything is written.
"""
import html
import re
from dataclasses import dataclass, field
from pathlib import Path

TOKEN_RE = re.compile(
    r'<GROUP NAME="(?P<group>[^"]*)"[^>]*>'
    r'|(?P<close></GROUP>)'
    r'|<OBJECT NAME="(?P<obj>[^"]*)" VALUE="(?P<val>[^"]*)"(?P<attrs>[^>]*)/>'
    r'|<DEVICE (?P<dev>[^>]*)>'
    r'|(?P<devclose></DEVICE>)'
)
ATTR_RE = re.compile(r'(\w+)="([^"]*)"')
SDP_NAME_RE = re.compile(r'(?:^|\n)s=([^\r\n]*)')
ROUTE_FIELDS = ("Input Main", "Input Backup")


@dataclass
class Obj:
    name: str
    value: str
    start: int  # span of the VALUE="..." contents in the decoded text
    end: int
    writable: bool
    enums: list
    minimum: str = ""
    maximum: str = ""
    oid: int = 0  # ACP2 object id when the model comes from a live device


@dataclass
class Group:
    path: tuple
    objects: dict = field(default_factory=dict)


def _num(name):
    m = re.search(r'(\d+)$', name)
    return int(m.group(1)) if m else None


BANKS_PER_GROUP = 8  # 8 banks x 2 channels = one 16-channel group, as one audio stream


def bank_group(bank, slot):
    """Delay banks are 2-channel; we show them in 16-channel groups so a group
    lines up with one 16-mono-channel stream. Banks 1-8 = group 1, 9-16 = group 2,
    ... - the same fixed port-based scheme as docs/routing-architecture.md
    (SDI port N uses banks (N-1)*8+1 .. N*8). Writing still happens per bank slot;
    the destination key stays bank:<bank>:<slot>."""
    g = (bank - 1) // BANKS_PER_GROUP + 1
    first = (g - 1) * BANKS_PER_GROUP + 1
    return {"block": g, "ch": (bank - first) * 2 + slot, "bank": bank, "slot": slot,
            "block_label": f"Delay {g} · B{first} (Banks {first}-{first + BANKS_PER_GROUP - 1})"}


def parse_input_label(label):
    """'SDI 07' -> ('SDI', 7), 'Stream 9' -> ('Stream', 9), 'Pattern' -> ('Pattern', None)."""
    m = re.match(r'^(SDI|Stream)\s+0*(\d+)$', label or "")
    if m:
        return m.group(1), int(m.group(2))
    return (label or "OTHER"), None


def channel_letter(path_name, channel_name):
    """('PATH C', 'CHANNEL 02') -> 'C2' - the label the Neuron UI uses."""
    return f"{path_name.split()[-1]}{_num(channel_name)}"


class CbfModel:
    def __init__(self, path):
        self.path = Path(path)
        raw = self.path.read_bytes()
        if raw[:2] != b'\xff\xfe':
            raise ValueError(f"{self.path.name}: not a UTF-16LE .cbf (missing BOM)")
        self.text = raw.decode('utf-16-le')
        self.devices = []  # [{attrs, start, end, groups:{path: Group}}]
        self._parse()
        self.device = self._pick_device()
        self.groups = self.device["groups"]
        self.sources = {}       # id -> source dict
        self.destinations = {}  # key -> destination dict
        self._build_routing()

    # ---------- parsing ----------

    def _parse(self):
        stack, dev = [], None
        for m in TOKEN_RE.finditer(self.text):
            if m.group('dev') is not None:
                dev = {"attrs": dict(ATTR_RE.findall(m.group('dev'))), "start": m.start(), "groups": {}}
                stack = []
            elif m.group('devclose') is not None:
                if dev is not None:
                    dev["end"] = m.end()
                    self.devices.append(dev)
                dev = None
            elif dev is None:
                continue
            elif m.group('group') is not None:
                stack.append(html.unescape(m.group('group')))
                dev["groups"].setdefault(tuple(stack), Group(tuple(stack)))
            elif m.group('close') is not None:
                stack.pop()
            else:
                attrs = dict(ATTR_RE.findall(m.group('attrs')))
                enums = attrs.get("ENUMS", "")
                grp = dev["groups"].setdefault(tuple(stack), Group(tuple(stack)))
                grp.objects[m.group('obj')] = Obj(
                    name=m.group('obj'), value=m.group('val'),
                    start=m.start('val'), end=m.end('val'),
                    writable=attrs.get("WRITABLE", "1") != "0",
                    enums=[html.unescape(e) for e in enums.split(",")] if enums else [],
                    minimum=attrs.get("MIN", ""), maximum=attrs.get("MAX", ""),
                )

    def _pick_device(self):
        for wanted in ("BRIDGE", "CONVERT IP"):
            for d in self.devices:
                if d["attrs"].get("DEVICE") == wanted:
                    return d
        if not self.devices:
            raise ValueError("no <DEVICE> block found")
        return self.devices[0]

    # ---------- helpers ----------

    def obj(self, path, name):
        g = self.groups.get(tuple(path))
        return g.objects.get(name) if g else None

    def enum_label(self, o):
        if o is None:
            return ""
        try:
            return o.enums[int(o.value)] if o.enums else o.value
        except (ValueError, IndexError):
            return o.value

    def sdp_name(self, path):
        o = self.obj(path, "SDP File")
        if not o:
            return ""
        m = SDP_NAME_RE.search(o.value)
        return html.unescape(m.group(1)).strip() if m else ""

    # ---------- routing model ----------

    def _build_routing(self):
        for path, g in self.groups.items():
            ident = g.objects.get("Identifier")
            if ident and ident.value.lstrip('-').isdigit():
                src = self._classify_source(path)
                if src:
                    src["id"] = int(ident.value)
                    self.sources[src["id"]] = src
            if all(f in g.objects for f in ROUTE_FIELDS):
                dst = self._classify_destination(path)
                if dst:
                    dst["path"] = list(path)
                    dst["main"] = int(g.objects["Input Main"].value)
                    dst["backup"] = int(g.objects["Input Backup"].value)
                    cur = g.objects.get("Input Current")
                    dst["current"] = int(cur.value) if cur and cur.value.lstrip('-').isdigit() else None
                    own = g.objects.get("Identifier")  # delay-bank slots are also sources
                    dst["own_id"] = int(own.value) if own and own.value.isdigit() else None
                    self.destinations[dst["key"]] = dst

    def _classify_source(self, p):
        # INPUT/IP/AUDIO/STREAM n/AUDIO CHANNELS/AUDIO CHANNEL k
        if p[:3] == ("INPUT", "IP", "AUDIO") and len(p) == 6:
            n, k = _num(p[3]), _num(p[5])
            sp = p[:4]
            return {"kind": "ipin", "block": n, "ch": k,
                    "block_label": f"IP In {n}", "name": self.sdp_name(sp),
                    "stream_delay": self._float(self.obj(sp, "Stream Delay")),
                    "enabled": self.enum_label(self.obj(sp, "Stream")) == "Enabled"}
        # PROCESSING VIDEO/PATH X/CHANNEL nn/DE-EMBEDDER/AUDIO CHANNEL k
        if p[0] == "PROCESSING VIDEO" and len(p) == 5 and p[3] == "DE-EMBEDDER":
            letter = channel_letter(p[1], p[2])
            return {"kind": "deemb", "block": letter, "ch": _num(p[4]),
                    "block_label": f"De-Emb {letter}", "name": "", "enabled": True}
        # PROCESSING AUDIO/AUDIO DELAY/BANK n/AUDIO CHANNEL k
        if p[:2] == ("PROCESSING AUDIO", "AUDIO DELAY") and len(p) == 4:
            return {"kind": "bank", **bank_group(_num(p[2]), _num(p[3])), "name": "",
                    "delay": self._float(self.obj(p[:3], "Delay")), "enabled": True}
        # INPUT/MADI/OPTICAL n/AUDIO CHANNELS/AUDIO CHANNEL k
        if p[:2] == ("INPUT", "MADI") and len(p) == 5:
            n = _num(p[2])
            return {"kind": "madiin", "block": n, "ch": _num(p[4]),
                    "block_label": f"MADI In {n}", "name": "",
                    "enabled": self.enum_label(self.obj(p[:3], "Mode")) == "On"}
        return None

    def _classify_destination(self, p):
        # PROCESSING VIDEO/PATH X/CHANNEL nn/EMBEDDER/AUDIO CHANNEL k
        if p[0] == "PROCESSING VIDEO" and len(p) == 5 and p[3] == "EMBEDDER":
            letter, k = channel_letter(p[1], p[2]), _num(p[4])
            ch_path = p[:3]
            return {"key": f"emb:{letter}:{k}", "kind": "emb", "block": letter, "ch": k,
                    "block_label": f"Embedder {letter}",
                    "name": f"{self.enum_label(self.obj(ch_path, 'Main Input'))} / "
                            f"{self.enum_label(self.obj(ch_path, 'Backup Input'))}",
                    "enabled": self.enum_label(self.obj(ch_path, "Audio Embedder Mode")) != "Off"}
        if p[:2] == ("PROCESSING AUDIO", "AUDIO DELAY") and len(p) == 4:
            n, k = _num(p[2]), _num(p[3])
            return {"key": f"bank:{n}:{k}", "kind": "bank", **bank_group(n, k), "name": "",
                    "delay": self._float(self.obj(p[:3], "Delay")), "enabled": True}
        # OUTPUT/IP/AUDIO/STREAM m/AUDIO CHANNELS/AUDIO CHANNEL k
        if p[:3] == ("OUTPUT", "IP", "AUDIO") and len(p) == 6:
            m, k = _num(p[3]), _num(p[5])
            sp = p[:4]
            return {"key": f"ipout:{m}:{k}", "kind": "ipout", "block": m, "ch": k,
                    "block_label": f"IP Out {m}", "name": self.sdp_name(sp),
                    "channels": self.enum_label(self.obj(sp, "Channels")),
                    "enabled": self.enum_label(self.obj(sp, "Stream")) == "Enabled"}
        # OUTPUT/MADI/OPTICAL n/AUDIO CHANNELS/AUDIO CHANNEL k
        if p[:2] == ("OUTPUT", "MADI") and len(p) == 5:
            n, k = _num(p[2]), _num(p[4])
            return {"key": f"madiout:{n}:{k}", "kind": "madiout", "block": n, "ch": k,
                    "block_label": f"MADI Out {n}", "name": "",
                    "enabled": self.enum_label(self.obj(p[:3], "Mode")) == "On"}
        return None

    @staticmethod
    def _float(o):
        try:
            return float(o.value) if o else None
        except ValueError:
            return None

    def channels(self):
        """The 32 processing channels (A1..H4 = 1..32, path/channel order) with
        their video Main/Backup Input - this decides Case A (Stream) vs Case B (SDI),
        see docs/routing-architecture.md."""
        out, n = [], 0
        pv = [p for p in self.groups if len(p) == 3 and p[0] == "PROCESSING VIDEO"
              and p[1].startswith("PATH ") and p[2].startswith("CHANNEL ")]
        for p in sorted(pv, key=lambda p: (p[1], _num(p[2]))):
            n += 1
            main, backup = self.enum_label(self.obj(p, "Main Input")), self.enum_label(self.obj(p, "Backup Input"))
            mk, mn = parse_input_label(main)
            bk, bn = parse_input_label(backup)
            out.append({"n": n, "letter": channel_letter(p[1], p[2]),
                        "main": main, "main_kind": mk, "main_num": mn,
                        "backup": backup, "backup_kind": bk, "backup_num": bn,
                        "selection": self.enum_label(self.obj(p, "Input Selection")),
                        "embedder_mode": self.enum_label(self.obj(p, "Audio Embedder Mode"))})
        return out

    def info(self):
        a = self.device["attrs"]
        ident = ("IDENTITY",)
        return {
            "file": self.path.name,
            "ip": a.get("IP", ""),
            "device": a.get("DEVICE", ""),
            "version": a.get("VERSION", ""),
            "card": self.enum_label(self.obj(ident, "Card Name")),
            "user_label": self.enum_label(self.obj(ident, "User Label 1")),
            "nmos_label": self.enum_label(self.obj(("GENERAL", "NMOS"), "Label")),
            "sources": len(self.sources),
            "destinations": len(self.destinations),
        }

    # ---------- writing ----------

    DELAY_TARGETS = {
        # change key prefix -> (group path for number n, object name)
        "bankdelay": (lambda n: ("PROCESSING AUDIO", "AUDIO DELAY", f"BANK {n}"), "Delay"),
        "streamdelay": (lambda n: ("INPUT", "IP", "AUDIO", f"STREAM {n}"), "Stream Delay"),
    }

    def _delay_obj(self, key):
        kind, _, n = key.partition(":")
        if kind not in self.DELAY_TARGETS or not n.isdigit():
            raise ValueError(f"unknown delay target {key}")
        path_fn, name = self.DELAY_TARGETS[kind]
        o = self.obj(path_fn(int(n)), name)
        if o is None or not o.writable:
            raise ValueError(f"{key}: {name} missing or read-only")
        return o

    def current_value(self, change):
        """The value a change targets, as it is in this model (used for read-back)."""
        if change["field"] == "delay":
            return float(self._delay_obj(change["key"]).value)
        return self.destinations[change["key"]][change["field"]]

    def build_patched(self, changes):
        """changes: [{"key": dest key, "field": "main"|"backup", "value": int}].
        Returns (new_text, applied) - never touches self.text, never writes a file.
        Raises on anything that does not resolve cleanly, so a partly wrong patch
        is never produced."""
        edits, applied = [], []
        for c in changes:
            if c["field"] == "delay":
                o = self._delay_obj(c["key"])
                value = float(c["value"])
                lo = float(o.minimum or 0)
                hi = float(o.maximum or 0) or float("inf")
                if not lo <= value <= hi:
                    raise ValueError(f"{c['key']}: delay {value} outside {lo}..{hi}")
                if abs(float(o.value) - value) < 1e-6:
                    continue
                edits.append((o.start, o.end, f"{value:.6f}"))
                applied.append({**c, "value": value, "old": float(o.value)})
                continue
            dst = self.destinations.get(c["key"])
            if dst is None:
                raise ValueError(f"unknown destination {c['key']}")
            field_name = {"main": "Input Main", "backup": "Input Backup"}[c["field"]]
            value = int(c["value"])
            if value != 0 and value not in self.sources:
                raise ValueError(f"{c['key']} {c['field']}: source id {value} does not exist in this file")
            o = self.obj(dst["path"], field_name)
            if o is None or not o.writable:
                raise ValueError(f"{c['key']}: {field_name} missing or read-only")
            if str(value) == o.value:
                continue
            edits.append((o.start, o.end, str(value)))
            applied.append({**c, "old": int(o.value)})
        if len({e[0] for e in edits}) != len(edits):
            raise ValueError("two changes target the same object")
        text = self.text
        for start, end, val in sorted(edits, key=lambda e: -e[0]):
            text = text[:start] + val + text[end:]
        self.verify_text(text)
        return text, applied

    def verify_text(self, text):
        if not text.startswith('﻿'):
            raise ValueError("BOM marker lost - refusing to write")
        delta = len(text) - len(self.text)
        s, e = self.device["start"], self.device["end"] + delta
        block = text[s:e]
        m = re.search(r'Object_Count" VALUE="(\d+)"', block)
        if m:
            stated, actual = int(m.group(1)), block.count('<OBJECT NAME=')
            if stated != actual:
                raise ValueError(f"Object_Count mismatch after patch: stated {stated}, actual {actual}")

    @staticmethod
    def write(path, text):
        raw = text.encode('utf-16-le')
        if raw[:4] != b'\xff\xfe<\x00':
            raise ValueError("unexpected header after encode - not writing")
        Path(path).write_bytes(raw)
        return len(raw)
