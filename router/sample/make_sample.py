"""
Builds the anonymised sample for the router's "Sample" page from a real
routing read and the Cerebrum lists - run once on a machine that has them:

    python3 sample/make_sample.py live/IPG3A.json lists/SRC.rtf lists/DST.rtf IPG03A

Writes sample/demo-snapshot.json.gz, sample/DEMO-SRC.tsv, sample/DEMO-DST.tsv.
Kept: the whole routing structure and values (sources, destinations, delays,
enabled flags). Replaced: device name (-> DEMO), every IPv4 address, NMOS /
system UUIDs, PTP clock ids, the registry URL, all channel names (-> neutral
"DEMO IN 07" / "DEMO OUT 04"). Only the rows of that one device are taken
from the lists. Nothing here contacts a device.
"""
import csv
import gzip
import io
import json
import re
import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from route_lists import read_rows, normalize_device   # noqa: E402

IP_RE = re.compile(r'\b(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\b')
CLOCK_RE = re.compile(r'\b[0-9A-F]{2}(-[0-9A-F]{2}){7}\b')
UUID_RE = re.compile(r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b', re.I)


class Anon:
    def __init__(self, device):
        self.ips = {}
        self.sessions = {}
        dev = re.escape(device)
        short = re.escape(normalize_device(device))                      # IPG3A
        tail = re.escape(re.sub(r'^IPG0*', '', device.upper()))          # 3A
        self.dev_re = re.compile(rf'\b({dev}|{short})\b', re.I)
        self.tail_re = re.compile(rf'(?<=\s){tail}\b')

    def ip(self, m):
        a = m.group(0)
        if a not in self.ips:
            n = len(self.ips) + 1
            first = int(m.group(1))
            self.ips[a] = f"239.200.{n // 250}.{n % 250 + 1}" if 224 <= first <= 239 else f"10.200.{n // 250}.{n % 250 + 1}"
        return self.ips[a]

    def sdp(self, s):
        """SDP session name (s=) and description (i=) name other devices: neutral names."""
        def sess(m):
            v = m.group(1).strip()
            if v not in self.sessions:
                self.sessions[v] = f"DEMO SOURCE {len(self.sessions) + 1:02d}"
            return "s=" + self.sessions[v]
        s = re.sub(r"(?m)^s=(.*?)\r?$", lambda m: sess(m) + ("\r" if m.group(0).endswith("\r") else ""), s)
        return re.sub(r"(?m)^i=.*?(\r?)$", r"i=audio stream\1", s)

    def text(self, s):
        if not isinstance(s, str) or not s:
            return s
        if s.startswith("v=0"):
            s = self.sdp(s)
        s = IP_RE.sub(self.ip, s)
        s = CLOCK_RE.sub("00-00-5E-FF-FE-00-00-01", s)
        s = UUID_RE.sub(lambda m: str(uuid.uuid5(uuid.NAMESPACE_URL, "demo:" + m.group(0).lower())), s)
        s = self.dev_re.sub("DEMO", s)
        return self.tail_re.sub("DEMO", s)


def snapshot(src, device, an):
    snap = json.loads(Path(src).read_text(encoding="utf-8"))
    snap["info"]["host"] = "192.0.2.10"
    for o in snap["objects"]:
        if o.get("label") in ("Registry URL Status", "Registry Adress Override"):
            o["value"] = ""
        elif "value" in o:
            o["value"] = an.text(o["value"])
    return snap


def lists(src_path, dst_path, device, an):
    key = normalize_device(device)
    out = {}
    for kind, path in (("SRC", src_path), ("DST", dst_path)):
        rows = read_rows(path)
        head = [h.strip() for h in rows[0]]
        i_id, i_mn = head.index("_ID"), head.index("_Mnemonic")
        i_lvl = [i for i, h in enumerate(head) if h.startswith("_Level ID")]
        body, n_id = [], 9000
        for r in rows[1:]:
            refs = [r[i].strip() for i in i_lvl if i < len(r) and r[i].strip().startswith("NMOS")]
            if not refs or not any(normalize_device(x.split("|")[2]) == key for x in refs if x.count("|") >= 3):
                continue
            mn = r[i_mn].strip()
            m = re.search(r'-(TX|RX)(\d+)$', mn)
            audio = ".A" if ".A-" in mn else ""
            new_mn = f"LAB.DEMO{audio}-{m.group(1)}{m.group(2)}" if m else an.text(mn)
            alt = (f"DEMO {'OUT' if m.group(1) == 'TX' else 'IN'} {m.group(2)}") if m else "DEMO"
            n_id += 1
            body.append([str(n_id), new_mn, alt] + [an.text(x) for x in refs])
        width = max(len(b) for b in body) - 3
        header = ["_ID", "_Mnemonic", "_Alternate Mnemonic 1"] + [f"_Level ID {i + 1}" for i in range(width)]
        if kind == "DST":
            header.append("_TL Group")
        buf = io.StringIO()
        csv.writer(buf, delimiter="\t", lineterminator="\n").writerows([header] + body)
        out[kind] = buf.getvalue()
        print(f"{kind}: {len(body)} rows for {device}")
    return out


def main():
    snap_path, src, dst, device = sys.argv[1:5]
    an = Anon(device)
    snap = snapshot(snap_path, device, an)
    with gzip.open(HERE / "demo-snapshot.json.gz", "wt", encoding="utf-8") as f:
        json.dump(snap, f, separators=(",", ":"))
    for kind, text in lists(src, dst, device, an).items():
        (HERE / f"DEMO-{kind}.tsv").write_text(text, encoding="utf-8")
    print(f"snapshot: {len(snap['objects'])} objects, {len(an.ips)} IP addresses replaced")


if __name__ == "__main__":
    main()
