"""
Cerebrum route-master lists (sources = SRC, destinations = DST) -> stream names.

Each row is one router source/destination, e.g.

    2564  DCR.IPG03A-TX04  CC1 ...  NMOS2|95|IPG03A|VTX-04  NMOS2|95|IPG03A|DTX-04
                                    NMOS2|95|IPG03A|ATX-13  NMOS2|95|IPG03A|ATX-14 ...

The NMOS references name the device (here `IPG03A`, the same string the
device's .cbf holds in GENERAL > NMOS > Label) and the stream (`ATX-13` =
OUTPUT > IP > AUDIO > STREAM 13, `ARX-13` = INPUT > IP > AUDIO > STREAM 13).

How many audio streams a row lists and how they are numbered differs from
device to device (IPG03A: channel n -> streams (n-1)*4+1..+4; IPG03B/04B:
channel n -> stream n; some rows list 1 or 3 refs). Nothing here assumes a
formula: every name comes from the row that actually references that stream.
See docs/id-formulas.md and docs/lessons-learned.md for why.

Accepted file formats: .csv (semicolon), .tsv/.txt (tab), .rtf as saved by
TextEdit (tab-separated lines ending in a backslash), and .xlsx (first sheet,
as exported from Cerebrum / Excel - read with the standard library only).
"""
import csv
import io
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

CHANNEL_ROW_RE = re.compile(r'^(?P<site>[A-Z]+)\.(?P<dev>[^.]+?)-(?P<dir>TX|RX)(?P<n>\d+)$')  # not the ".A-" audio-only rows
REF_RE = re.compile(r'^NMOS\d*\|[^|]*\|(?P<dev>[^|]+)\|(?P<kind>[AVD][TR]X)-(?P<num>\d+)\s*$')


def normalize_device(name):
    """'DCB.IPG 10A' / 'IPG10A' / 'DCR.IPG07A' / 'IPG7A' -> 'IPG10A' / 'IPG7A'."""
    s = re.sub(r'^(DC[RB])\.', '', (name or '').strip().upper())
    s = re.sub(r'[^A-Z0-9]', '', s)
    return re.sub(r'(?<=[A-Z])0+(?=\d)', '', s)


def _rtf_to_text(raw):
    """Minimal RTF reader for TextEdit-exported plain tables: drop the header
    groups and control words, keep the text, one line per `\\` line break."""
    out, i, depth, skip_depth = [], 0, 0, None
    n = len(raw)
    while i < n:
        c = raw[i]
        if c == '{':
            depth += 1
            if skip_depth is None and raw.startswith(('{\\fonttbl', '{\\colortbl', '{\\*'), i):
                skip_depth = depth
            i += 1
        elif c == '}':
            if skip_depth == depth:
                skip_depth = None
            depth -= 1
            i += 1
        elif c == '\\':
            nxt = raw[i + 1] if i + 1 < n else ''
            if nxt in '\\{}':
                if skip_depth is None:
                    out.append(nxt)
                i += 2
            elif nxt == '\n' or nxt == '\r':
                if skip_depth is None:
                    out.append('\n')
                i += 2
            elif nxt == "'":
                if skip_depth is None:
                    out.append(bytes([int(raw[i + 2:i + 4], 16)]).decode('cp1252', 'replace'))
                i += 4
            else:
                m = re.match(r'\\([a-zA-Z]+)(-?\d+)? ?', raw[i:])
                if m:
                    if m.group(1) in ('par', 'line') and skip_depth is None:
                        out.append('\n')
                    elif m.group(1) == 'tab' and skip_depth is None:
                        out.append('\t')
                    i += m.end()
                else:
                    i += 2
        elif c in '\r\n':
            i += 1
        else:
            if skip_depth is None:
                out.append(c)
            i += 1
    return ''.join(out)


_XNS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _xlsx_rows(data):
    """First worksheet of an .xlsx as lists of strings (shared + inline strings, numbers)."""
    z = zipfile.ZipFile(io.BytesIO(data))
    names = set(z.namelist())
    sheet = "xl/worksheets/sheet1.xml" if "xl/worksheets/sheet1.xml" in names else \
        sorted(n for n in names if n.startswith("xl/worksheets/sheet"))[0]
    for part in ("xl/sharedStrings.xml", sheet):
        if part in names and b"<!DOCTYPE" in z.open(part).read(4096):
            raise ValueError("unexpected DOCTYPE in the .xlsx - not a normal Excel file")
    shared = []
    if "xl/sharedStrings.xml" in names:
        shared = ["".join(t.text or "" for t in si.iter(_XNS + "t"))
                  for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(_XNS + "si")]
    rows = []
    for _, el in ET.iterparse(z.open(sheet)):
        if el.tag != _XNS + "row":
            continue
        cells = {}
        for c in el.findall(_XNS + "c"):
            ref = re.match(r"[A-Z]+", c.get("r") or "")
            if not ref:
                continue
            ci = 0
            for ch in ref.group(0):
                ci = ci * 26 + ord(ch) - 64
            v, t = c.find(_XNS + "v"), c.get("t")
            if t == "s" and v is not None:
                val = shared[int(v.text)]
            elif t == "inlineStr":
                val = "".join(x.text or "" for x in c.iter(_XNS + "t"))
            else:
                val = v.text if v is not None else ""
            cells[ci - 1] = val or ""
        if cells:
            rows.append([cells.get(i, "") for i in range(max(cells) + 1)])
        el.clear()
    return rows


def read_rows(path):
    path = Path(path)
    data = path.read_bytes()
    if data[:2] == b"PK":                                   # .xlsx (a zip)
        return [r for r in _xlsx_rows(data) if any(c.strip() for c in r)]
    if data.startswith(b'{\\rtf'):
        text = _rtf_to_text(data.decode('latin-1'))
    else:
        for enc in ('utf-8-sig', 'cp1252'):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
    first = text.split('\n', 1)[0]
    delim = '\t' if first.count('\t') >= first.count(';') else ';'
    return [r for r in csv.reader(io.StringIO(text), delimiter=delim) if r]


def parse_list(path):
    """Returns {"kind": "SRC"|"DST", "name": file name, "devices": {norm_dev: {
    "label": raw device label, "ATX"/"ARX": {stream: {...}}}}}"""
    rows = read_rows(path)
    header = [h.strip() for h in rows[0]]
    if '_Mnemonic' not in header:
        raise ValueError(f"{Path(path).name}: no _Mnemonic column - not a Cerebrum route list")
    i_id, i_mn = header.index('_ID'), header.index('_Mnemonic')
    i_alt = [i for i, h in enumerate(header) if h.startswith('_Alternate Mnemonic')]
    i_lvl = [i for i, h in enumerate(header) if h.startswith('_Level ID')]
    devices, kinds = {}, set()
    for r in rows[1:]:
        mnemonic = r[i_mn].strip() if i_mn < len(r) else ''
        if not mnemonic:
            continue
        alts = [r[i].strip() for i in i_alt if i < len(r) and r[i].strip()]
        row_m = CHANNEL_ROW_RE.match(mnemonic)
        row_refs = []
        audio_pos = 0
        for i in i_lvl:
            m = REF_RE.match(r[i].strip()) if i < len(r) else None
            if not m or m.group('kind')[0] != 'A':
                continue
            audio_pos += 1
            kinds.add(m.group('kind'))
            dev = devices.setdefault(normalize_device(m.group('dev')),
                                     {"label": m.group('dev'), "site": "", "ATX": {}, "ARX": {}, "TX": {}, "RX": {}})
            num = int(m.group('num'))
            row_refs.append((dev, num))
            entry = {"row": r[i_id].strip(), "mnemonic": mnemonic, "alt": alts[0] if alts else "",
                     "pos": audio_pos}
            # the same stream referenced twice (seen on IPG10B RX) is kept as a list
            dev[m.group('kind')].setdefault(num, []).append(entry)
        if row_m and row_refs:
            # one Neuron channel row: DCR.IPG03A-TX04 -> channel 4, its audio streams in order
            dev = row_refs[0][0]
            dev["site"] = dev["site"] or row_m.group('site')
            dev[row_m.group('dir')][int(row_m.group('n'))] = {
                "row": r[i_id].strip(), "mnemonic": mnemonic, "alt": alts[0] if alts else "",
                "refs": [num for _, num in row_refs]}
    kind = "SRC" if "ATX" in kinds and "ARX" not in kinds else "DST" if "ARX" in kinds and "ATX" not in kinds else \
        ("DST" if '_TL Group' in header else "SRC")
    return {"kind": kind, "name": Path(path).name, "devices": devices}


def stream_names(lists, nmos_label):
    """Names for one device: {"ipout": {stream: [entries]}, "ipin": {...},
    "device": matched label or None, "available": [all device labels]}"""
    key = normalize_device(nmos_label)
    out = {"ipout": {}, "ipin": {}, "tx": {}, "rx": {}, "device": None, "site": "",
           "lists": [l["name"] for l in lists],
           "available": sorted({d["label"] for l in lists for d in l["devices"].values()})}
    for l in lists:
        dev = l["devices"].get(key)
        if not dev:
            continue
        out["device"] = dev["label"]
        out["site"] = out["site"] or dev.get("site", "")
        out["tx"].update(dev["TX"])
        out["rx"].update(dev["RX"])
        for num, entries in dev["ATX"].items():
            out["ipout"].setdefault(num, []).extend(entries)
        for num, entries in dev["ARX"].items():
            out["ipin"].setdefault(num, []).extend(entries)
    return out


def device_index(lists):
    """All devices named in the lists: {norm_key: {"label", "site", "tx", "rx"}}."""
    out = {}
    for l in lists:
        for key, d in l["devices"].items():
            e = out.setdefault(key, {"label": d["label"], "site": "", "tx": 0, "rx": 0})
            e["site"] = e["site"] or d.get("site", "")
            e["tx"] = max(e["tx"], len(d.get("TX", {})))
            e["rx"] = max(e["rx"], len(d.get("RX", {})))
    return out


ANY_REF_RE = re.compile(r'^NMOS\d*\|[^|]*\|[^|]+\|[AVD][TR]X-\d+\s*$')
MNEMONIC_RE = re.compile(r'^[A-Z]+\.\S')


def normalize_paste(text, kind=None):
    """Text copied out of the Cerebrum route master (grid copy = tab separated,
    or a ';' export) -> (tsv text in the list format parse_list reads, info).

    With the header row (_ID, _Mnemonic, ...) the text is kept as it is.
    Without it, each row is taken apart by content: the first number is the
    _ID, the first "SITE.xxx" cell the _Mnemonic, the next plain cell the
    alternate mnemonic and every NMOS|..|DEV|ATX-n cell a level, in order.
    kind "SRC"/"DST" (or None = detect) only labels header-less pastes."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    lines = [l for l in text.split("\n") if l.strip()]
    if not lines:
        raise ValueError("nothing pasted")
    delim = "\t" if sum(l.count("\t") for l in lines[:20]) >= sum(l.count(";") for l in lines[:20]) else ";"
    rows = [r for r in csv.reader(io.StringIO("\n".join(lines)), delimiter=delim) if r]
    if any(h.strip() == "_Mnemonic" for h in rows[0]):
        out_rows, with_header = rows, True
    else:
        with_header, body, maxrefs, skipped = False, [], 0, 0
        for r in rows:
            cells = [c.strip() for c in r]
            refs = [c for c in cells if ANY_REF_RE.match(c)]
            mi = next((i for i, c in enumerate(cells) if MNEMONIC_RE.match(c) and not ANY_REF_RE.match(c)), None)
            if mi is None or not refs:
                skipped += 1
                continue
            rid = next((c for c in cells[:mi] if c.isdigit()), "")
            alt = next((c for c in cells[mi + 1:] if c and not ANY_REF_RE.match(c) and not c.isdigit()), "")
            body.append([rid, cells[mi], alt] + refs)
            maxrefs = max(maxrefs, len(refs))
        if not body:
            raise ValueError("no route-master rows found: expected rows with a mnemonic like DCR.IPG03A-TX04 "
                             "and NMOS|..|IPG03A|ATX-13 level cells")
        header = ["_ID", "_Mnemonic", "_Alternate Mnemonic 1"] + [f"_Level ID {i + 1}" for i in range(maxrefs)]
        if kind == "DST":
            header.append("_TL Group")          # parse_list's fallback hint for a DST list
        out_rows = [header] + body
    buf = io.StringIO()
    csv.writer(buf, delimiter="\t", lineterminator="\n").writerows(out_rows)
    return buf.getvalue(), {"rows": len(out_rows) - 1, "with_header": with_header,
                            "skipped": 0 if with_header else skipped}
