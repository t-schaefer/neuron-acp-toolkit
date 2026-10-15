"""
Per-channel audio analysis and Case A / Case B templates.

A Neuron processing channel n (A1..H4 = 1..32) has a video Main Input and a
Backup Input. Each side is resolved on its own (docs/routing-architecture.md:
"Main and Backup are resolved completely independently"):

    side input   case     embedder side (Input Main / Input Backup) comes from
    Stream x     A        IP In S, S = first ARX of the channel's RX row (DST list),
                          NOT the dropdown number x (label != real stream)
    SDI N        B        a 16ch delay group (8 banks) fed by de-embedder of SDI N
    Pattern      -        inactive side, left alone (stale values are harmless)

So a channel can be A/A, B/B, A/- , B/-, or mixed A/B and B/A (IPG03A ch 7:
SDI 07 / Stream 9). For Case B the same delay group also feeds the channel's IP
output stream M (first ATX of the TX row) on Main AND Backup - the IP-out gap
that bit IPG04A. For pure Case A channels the IP output is reported, not changed.

Delay targets (docs/device-status.md, configurable): 19.5 ms Stream Delay on the
input stream for Case A, 18.0 ms on the 8 banks for Case B.

Everything here is pure data: it reads a model (CbfModel/LiveModel) and the
SRC/DST names and returns findings + a list of proposed changes. It never
writes anything.
"""
from cbf_model import BANKS_PER_GROUP

CASE_A_DELAY = 19.5
CASE_B_DELAY = 18.0
OK, INFO, WARN, FAIL = "ok", "info", "warn", "fail"


def _f(level, text):
    return {"level": level, "text": text}


class ChannelAnalysis:
    def __init__(self, model, names, n, delay_a=CASE_A_DELAY, delay_b=CASE_B_DELAY, bank_group=None):
        self.m, self.names, self.n = model, names or {}, n
        self.delay_a, self.delay_b = delay_a, delay_b
        self.ch = next(c for c in model.channels() if c["n"] == n)
        self.letter = self.ch["letter"]
        self.findings, self.changes = [], []
        self.forced_group = bank_group
        self._letters = {c["n"]: c["letter"] for c in model.channels()}
        self.sides = {}
        self._analyse()

    # ---- lookups ----
    def _row(self, direction):
        return (self.names.get(direction) or {}).get(self.n) or (self.names.get(direction) or {}).get(str(self.n))

    def _src_id(self, kind, block, ch):
        for sid, s in self.m.sources.items():
            if s["kind"] == kind and s["block"] == block and s["ch"] == ch:
                return sid
        return None

    def _block_ids(self, kind, block, count=16):
        ids = sorted((s["ch"], sid) for sid, s in self.m.sources.items() if s["kind"] == kind and s["block"] == block)
        return [sid for _, sid in ids][:count]

    def _dst(self, key):
        return self.m.destinations.get(key)

    def _feeding(self, keys, field):
        """Which source block feeds these destination slots: (kind, block) or 'mixed'/None."""
        blocks = set()
        for k in keys:
            d = self._dst(k)
            s = self.m.sources.get(d[field]) if d else None
            blocks.add((s["kind"], s["block"]) if s else None)
        return blocks.pop() if len(blocks) == 1 else ("mixed" if blocks else None)

    def _bank_keys(self, group):
        out = []
        for ch in range(1, 17):
            bank = (group - 1) * BANKS_PER_GROUP + (ch + 1) // 2
            out.append(f"bank:{bank}:{2 - ch % 2}")
        return out

    def _pattern_ok(self, direction, ref):
        """Does this row follow the pattern most rows of this device follow?"""
        rows = self.names.get(direction) or {}
        firsts = [(int(k), r["refs"][0]) for k, r in rows.items() if r.get("refs")]
        if len(firsts) < 4:
            return None, ""
        x4 = sum(1 for k, f in firsts if f == (k - 1) * 4 + 1) / len(firsts)
        x1 = sum(1 for k, f in firsts if f == k) / len(firsts)
        if x4 >= 0.8:
            return ref == (self.n - 1) * 4 + 1, "(n-1)*4+1"
        if x1 >= 0.8:
            return ref == self.n, "n"
        return None, "no dominant pattern"

    # ---- analysis ----
    def _analyse(self):
        c = self.ch
        emb_keys = [f"emb:{self.letter}:{k}" for k in range(1, 17)]
        rx, tx = self._row("rx"), self._row("tx")
        self.rx_stream = rx["refs"][0] if rx and rx.get("refs") else None
        self.tx_stream = tx["refs"][0] if tx and tx.get("refs") else None
        self.rx_row, self.tx_row = rx, tx
        sdi_groups = {}

        for field, kind, num in (("main", c["main_kind"], c["main_num"]), ("backup", c["backup_kind"], c["backup_num"])):
            side = {"field": field, "label": c[field], "kind": kind, "case": None, "source": None,
                    "current": self._feeding(emb_keys, field)}
            if kind == "Stream":
                side["case"] = "A"
                S = self.rx_stream
                if S is None:
                    self.findings.append(_f(WARN, f"{field}: no RX row for channel {self.n} in the DST list - "
                                                  f"real input stream unknown (label 'Stream {num}' is only a dropdown index)"))
                else:
                    side["source"] = ("ipin", S)
                    side["stream"] = S
            elif kind == "SDI":
                side["case"] = "B"
                side["sdi"] = num
                deemb_letter = self._letters.get(num)
                side["deemb"] = deemb_letter
                cur = side["current"]
                if self.forced_group:
                    g = self.forced_group
                elif isinstance(cur, tuple) and cur[0] == "bank":
                    g = cur[1]                 # keep the group the device already uses
                else:
                    g = sdi_groups.get(num) or num   # fixed port scheme: SDI N -> group N
                sdi_groups[num] = g
                side["group"] = g
                side["source"] = ("bank", g)
            else:
                side["case"] = None
            self.sides[field] = side

        # ---- list rows ----
        for direction, row, kind in (("rx", rx, "ARX"), ("tx", tx, "ATX")):
            if not row:
                self.findings.append(_f(WARN, f"no {direction.upper()} row for channel {self.n} in the "
                                              f"{'DST' if direction == 'rx' else 'SRC'} list"))
                continue
            refs = row["refs"]
            cons = refs == list(range(refs[0], refs[0] + len(refs)))
            self.findings.append(_f(OK if cons else WARN,
                                    f"{row['mnemonic']} \"{row['alt']}\": {kind} {refs}"
                                    + ("" if cons else " - NOT consecutive, check this row (lessons-learned: IPG04A ch13)")))
            ok, rule = self._pattern_ok(direction, refs[0])
            if ok is False:
                self.findings.append(_f(WARN, f"{row['mnemonic']}: first {kind} {refs[0]} breaks this device's "
                                              f"pattern {rule} - verify before routing"))

        # ---- per side ----
        for field, side in self.sides.items():
            if side["case"] is None:
                self.findings.append(_f(INFO, f"{field}: '{side['label']}' - inactive side, not routed "
                                              f"(embedder {field} currently {self._fmt(side['current'])})"))
                continue
            if side["case"] == "A" and side.get("stream"):
                self._check_stream_in(field, side)
                want = self._block_ids("ipin", side["stream"])
                self._plan_emb(field, want, f"Case A: IP In {side['stream']}")
            if side["case"] == "B":
                self._check_case_b(field, side)

        # ---- IP output of this channel ----
        self._check_ip_out()

    def _fmt(self, cur):
        if cur is None:
            return "nothing"
        if cur == "mixed":
            return "mixed sources"
        kind, block = cur
        return {"ipin": "IP In", "deemb": "De-Emb", "bank": "Delay group", "madiin": "MADI In"}.get(kind, kind) + f" {block}"

    def _check_stream_in(self, field, side):
        S = side["stream"]
        sid = self._src_id("ipin", S, 1)
        s = self.m.sources.get(sid)
        if not s:
            self.findings.append(_f(FAIL, f"{field}: IP In {S} does not exist on this device"))
            return
        name = s.get("name") or ""
        self.findings.append(_f(OK if s["enabled"] else FAIL,
                                f"{field}: IP In {S} {'enabled' if s['enabled'] else 'DISABLED'}"
                                + (f", receiving \"{name}\"" if name else "")))
        d = s.get("stream_delay")
        if d is not None:
            good = abs(d - self.delay_a) < 1e-3
            self.findings.append(_f(OK if good else WARN, f"{field}: IP In {S} Stream Delay {d:.1f} ms"
                                    + ("" if good else f" (Case A target {self.delay_a} ms)")))
            if not good:
                self.changes.append({"key": f"streamdelay:{S}", "field": "delay", "value": self.delay_a,
                                     "why": f"Case A delay on IP In {S}"})

    def _check_case_b(self, field, side):
        N, g, L = side["sdi"], side["group"], side.get("deemb")
        if not L:
            self.findings.append(_f(FAIL, f"{field}: SDI {N} has no de-embedder on this device"))
            return
        deemb = self._block_ids("deemb", L)
        bank_keys = self._bank_keys(g)
        if any(self._dst(k) is None for k in bank_keys):
            self.findings.append(_f(FAIL, f"{field}: delay group {g} does not exist"))
            return
        # who else uses this group?
        users = []
        for c in self.m.channels():
            if c["n"] == self.n:
                continue
            for f in ("main", "backup"):
                cur = self._feeding([f"emb:{c['letter']}:{k}" for k in range(1, 17)], f)
                if cur == ("bank", g) and c[f + "_kind"] == "SDI" and c[f + "_num"] != N:
                    users.append(f"{c['letter']} {f}")
        if users:
            self.findings.append(_f(FAIL, f"{field}: delay group {g} is already used by {', '.join(users)} - pick another group"))
        cur_in = self._feeding(bank_keys, "main")
        self.findings.append(_f(OK if cur_in == ("deemb", L) else WARN,
                                f"{field}: delay group {g} (banks {(g-1)*8+1}-{g*8}) fed by {self._fmt(cur_in)}"
                                + ("" if cur_in == ("deemb", L) else f", Case B needs De-Emb {L} (SDI {N})")))
        for k, sid in zip(bank_keys, deemb):
            for f in ("main", "backup"):
                self._want(k, f, sid, f"Case B: SDI {N} -> delay group {g}")
        delays = sorted({round(self.m.sources[self._dst(k)["own_id"]]["delay"] or 0, 3)
                         for k in bank_keys if self._dst(k).get("own_id") in self.m.sources})
        good = delays == [self.delay_b]
        self.findings.append(_f(OK if good else WARN, f"{field}: delay group {g} delay {', '.join(f'{d:.1f}' for d in delays)} ms"
                                + ("" if good else f" (Case B target {self.delay_b} ms)")))
        if not good:
            for b in range((g - 1) * 8 + 1, g * 8 + 1):
                self.changes.append({"key": f"bankdelay:{b}", "field": "delay", "value": self.delay_b,
                                     "why": f"Case B delay on bank {b}"})
        group_ids = [self._dst(k)["own_id"] for k in bank_keys]
        self._plan_emb(field, group_ids, f"Case B: delay group {g}")
        side["group_ids"] = group_ids

    def _plan_emb(self, field, want_ids, why):
        keys = [f"emb:{self.letter}:{k}" for k in range(1, 17)]
        wrong = sum(1 for k, sid in zip(keys, want_ids) if self._dst(k)[field] != sid)
        self.findings.append(_f(OK if not wrong else WARN,
                                f"{field}: Embedder {self.letter} {field} "
                                + ("matches " + why if not wrong else f"differs from {why} on {wrong}/16 slots "
                                   f"(now {self._fmt(self._feeding(keys, field))})")))
        for k, sid in zip(keys, want_ids):
            self._want(k, field, sid, why)

    def _check_ip_out(self):
        M = self.tx_stream
        if M is None:
            return
        keys = [f"ipout:{M}:{k}" for k in range(1, 17)]
        d = self._dst(keys[0])
        if not d:
            self.findings.append(_f(FAIL, f"IP Out {M} (TX row) does not exist on this device"))
            return
        self.findings.append(_f(OK if d["enabled"] else FAIL, f"IP Out {M} {'enabled' if d['enabled'] else 'DISABLED'}"
                                f", {d.get('channels') or '?'} ch configured, SDP \"{d.get('name') or ''}\""))
        b_sides = [s for s in self.sides.values() if s["case"] == "B" and s.get("group_ids")]
        main_cur, back_cur = self._feeding(keys, "main"), self._feeding(keys, "backup")
        if not b_sides:
            self.findings.append(_f(INFO, f"IP Out {M}: main from {self._fmt(main_cur)}, backup from {self._fmt(back_cur)}"
                                          " - no SDI side, IP output left as it is"))
            return
        # IP out carries the delayed SDI copy: main from the main SDI group (else backup's), backup likewise
        by_field = {s["field"]: s for s in b_sides}
        src_main = (by_field.get("main") or by_field.get("backup"))
        src_back = (by_field.get("backup") or by_field.get("main"))
        for f, side in (("main", src_main), ("backup", src_back)):
            want = side["group_ids"]
            wrong = sum(1 for k, sid in zip(keys, want) if self._dst(k)[f] != sid)
            self.findings.append(_f(OK if not wrong else WARN,
                                    f"IP Out {M} {f}: " + (f"matches delay group {side['group']}" if not wrong else
                                    f"from {self._fmt(self._feeding(keys, f))}, Case B needs delay group {side['group']} "
                                    f"({wrong}/16 slots differ)")))
            for k, sid in zip(keys, want):
                self._want(k, f, sid, f"Case B: delay group {side['group']} -> IP Out {M}")
        if len(b_sides) == 2 and b_sides[0]["group"] != b_sides[1]["group"]:
            self.findings.append(_f(INFO, "Main and Backup are SDI from different ports - IP Out main/backup follow them"))

    def _want(self, key, field, sid, why):
        d = self._dst(key)
        if d is None or sid is None or d[field] == sid:
            return
        self.changes.append({"key": key, "field": field, "value": sid, "why": why, "old": d[field]})

    # ---- result ----
    @property
    def summary_case(self):
        a = self.sides["main"]["case"] or "-"
        b = self.sides["backup"]["case"] or "-"
        return f"{a}/{b}"

    def as_dict(self):
        seen, changes = set(), []
        for c in self.changes:            # B/B on one SDI port proposes the same bank delay twice
            if (c["key"], c["field"]) not in seen:
                seen.add((c["key"], c["field"]))
                changes.append(c)
        self.changes = changes
        worst = max((["ok", "info", "warn", "fail"].index(f["level"]) for f in self.findings), default=0)
        return {"n": self.n, "letter": self.letter, "main": self.ch["main"], "backup": self.ch["backup"],
                "case": self.summary_case, "rx_stream": self.rx_stream, "tx_stream": self.tx_stream,
                "rx_row": self.rx_row, "tx_row": self.tx_row,
                "sides": {f: {k: v for k, v in s.items() if k != "group_ids"} for f, s in self.sides.items()},
                "status": ["ok", "info", "warn", "fail"][worst],
                "findings": self.findings, "changes": self.changes}


def analyse_channel(model, names, n, **kw):
    return ChannelAnalysis(model, names, n, **kw).as_dict()
