"""
A simulated Neuron speaking AN2 + ACP2 (spec: Axonnet2 v1.1, ACP2 v1.0) on
localhost, for tests that must never touch a real device.

Tree (slot 1, the "card"), a small copy of the real BRIDGE menu:
    IDENTITY (Card Name, Product Version), GENERAL/NMOS (Label)
    INPUT/IP/AUDIO/STREAM 1..8   Stream, Stream Delay, SDP File, AUDIO CHANNELS/AUDIO CHANNEL k/Identifier
    PROCESSING VIDEO/PATH A/CHANNEL 01..04
        Main Input, Backup Input, Input Selection, Audio Embedder Mode,
        DE-EMBEDDER/AUDIO CHANNEL k/Identifier, EMBEDDER/AUDIO CHANNEL k/{Input Current, Input Main, Input Backup}
    PROCESSING AUDIO/AUDIO DELAY/BANK 1..32  AUDIO CHANNEL 1|2/{Identifier, Input Current, Input Main, Input Backup}, Delay
    OUTPUT/IP/AUDIO/STREAM 1..8  Stream, Channels, SDP File, AUDIO CHANNELS/AUDIO CHANNEL k/{Input Current, Input Main, Input Backup}

Channels: A1 Stream 1/Stream 1 (A/A), A2 SDI 02/Stream 2 (B/A, mixed),
          A3 SDI 03/SDI 03 (B/B), A4 Stream 4/Pattern (A/-).
Every set-property request is recorded in `FakeNeuron.set_attempts` and refused.
"""
import socketserver
import struct
import threading

MAGIC = 0xC635
INPUT_OPTIONS = [f"SDI 0{i}" for i in range(1, 5)] + [f"Stream {i}" for i in range(1, 9)] + ["Pattern"]


class Tree:
    def __init__(self):
        self.objs, self._next = {}, 1

    def add(self, parent, label, otype, value=None, access=1, options=None, vtype=6):
        oid = self._next
        self._next += 1
        self.objs[oid] = {"label": label, "type": otype, "value": value, "access": access,
                          "options": options, "vtype": vtype, "children": []}
        if parent:
            self.objs[parent]["children"].append(oid)
        return oid

    def node(self, parent, label):
        return self.add(parent, label, 0)

    def num(self, parent, label, value, rw=False, vtype=6):
        return self.add(parent, label, 3, value, 3 if rw else 1, vtype=vtype)

    def enum(self, parent, label, options, index, rw=True):
        return self.add(parent, label, 2, index, 3 if rw else 1, options=options)

    def string(self, parent, label, value):
        return self.add(parent, label, 5, value)


def build_card_tree(delay_b=16.8, delay_a=19.5):
    t = Tree()
    root = t.node(None, "ROOT_NODE_V2")
    ident = t.node(root, "IDENTITY")
    t.string(ident, "Card Name", "BRIDGE")
    t.string(ident, "Product Version", "7.0.3")
    gen = t.node(root, "GENERAL")
    nmos = t.node(gen, "NMOS")
    t.string(nmos, "Label", "IPGT1")
    inp = t.node(root, "INPUT")
    ip_audio = t.node(t.node(inp, "IP"), "AUDIO")
    for s in range(1, 9):
        st = t.node(ip_audio, f"STREAM {s}")
        t.enum(st, "Stream", ["Disabled", "Enabled"], 1 if s != 8 else 0)
        t.num(st, "Stream Delay", delay_a if s != 4 else 0.0, rw=True, vtype=8)
        t.string(st, "SDP File", f"v=0\r\ns=SRC.IN {s}\r\n")
        chs = t.node(st, "AUDIO CHANNELS")
        for k in range(1, 17):
            t.num(t.node(chs, f"AUDIO CHANNEL {k}"), "Identifier", (s - 1) * 16 + k)
    pv = t.node(root, "PROCESSING VIDEO")
    path = t.node(pv, "PATH A")
    setup = {1: ("Stream 1", "Stream 1"), 2: ("SDI 02", "Stream 2"), 3: ("SDI 03", "SDI 03"), 4: ("Stream 4", "Pattern")}
    for c in range(1, 5):
        ch = t.node(path, f"CHANNEL 0{c}")
        main, backup = setup[c]
        t.enum(ch, "Input Selection", ["Auto", "Main", "Backup"], 1)
        t.enum(ch, "Main Input", INPUT_OPTIONS, INPUT_OPTIONS.index(main))
        t.enum(ch, "Backup Input", INPUT_OPTIONS, INPUT_OPTIONS.index(backup))
        t.enum(ch, "Audio Embedder Mode", ["Off", "Append", "Overwrite", "Blank"], 2)
        de = t.node(ch, "DE-EMBEDDER")
        for k in range(1, 17):
            t.num(t.node(de, f"AUDIO CHANNEL {k}"), "Identifier", 3072 + (c - 1) * 16 + k)
        emb = t.node(ch, "EMBEDDER")
        for k in range(1, 17):
            a = t.node(emb, f"AUDIO CHANNEL {k}")
            src = (c - 1) * 16 + k          # everything starts on IP In c
            t.num(a, "Input Current", src)
            t.num(a, "Input Main", src, rw=True)
            t.num(a, "Input Backup", src, rw=True)
    pa = t.node(t.node(root, "PROCESSING AUDIO"), "AUDIO DELAY")
    for b in range(1, 33):
        bank = t.node(pa, f"BANK {b}")
        for sl in (1, 2):
            a = t.node(bank, f"AUDIO CHANNEL {sl}")
            own = 3584 + 2 * (b - 1) + sl
            t.num(a, "Identifier", own)
            t.num(a, "Input Current", own)
            t.num(a, "Input Main", own, rw=True)
            t.num(a, "Input Backup", own, rw=True)
        t.num(bank, "Delay", delay_b if b <= 8 else 0.0, rw=True, vtype=8)
    out_audio = t.node(t.node(t.node(root, "OUTPUT"), "IP"), "AUDIO")
    for m in range(1, 9):
        st = t.node(out_audio, f"STREAM {m}")
        t.enum(st, "Stream", ["Disabled", "Enabled"], 1)
        t.enum(st, "Channels", ["0", "1", "2", "4", "8", "16"], 5)
        t.string(st, "SDP File", f"v=0\r\ns=ATX-0{m}\r\n")
        chs = t.node(st, "AUDIO CHANNELS")
        for k in range(1, 17):
            a = t.node(chs, f"AUDIO CHANNEL {k}")
            t.num(a, "Input Current", (m - 1) * 16 + k)
            t.num(a, "Input Main", (m - 1) * 16 + k, rw=True)
            t.num(a, "Input Backup", (m - 1) * 16 + k, rw=True)
    return t


def build_board_tree():
    t = Tree()
    root = t.node(None, "ROOT_NODE_V2")
    t.node(root, "BOARD")
    t.node(root, "PSU")
    return t


def _prop(pid, data, body=b""):
    plen = 4 + len(body)
    return struct.pack(">BBH", pid, data, plen) + body + b"\0" * ((4 - plen % 4) % 4)


def _value_prop(pid, o):
    v = o["value"]
    if o["type"] == 2:
        return _prop(pid, 9, struct.pack(">I", v))
    if o["type"] == 5:
        return _prop(pid, 11, v.encode() + b"\0")
    if o["vtype"] == 8:
        return _prop(pid, 8, struct.pack(">f", v))
    return _prop(pid, o["vtype"], struct.pack(">I", v))


def encode_object(o):
    out = _prop(1, o["type"]) + _prop(2, 0, o["label"].encode() + b"\0") + _prop(3, o["access"])
    if o["type"] == 0:
        return out + _prop(14, 0, struct.pack(f">{len(o['children'])}I", *o["children"]))
    if o["type"] == 3:
        out += _prop(5, o["vtype"])
    out += _value_prop(8, o)
    if o["type"] == 2:
        recs = b""
        for i, text in enumerate(o["options"]):
            r = struct.pack(">I", i) + text.encode() + b"\0"
            recs += r + b"\0" * ((4 - len(r) % 4) % 4)
        out += _prop(15, len(o["options"]), recs)
    return out


class FakeNeuron:
    def __init__(self, **kw):
        self.slots = {0: build_board_tree(), 1: build_card_tree(**kw)}
        self.set_attempts = []
        self.requests = 0
        fake = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                buf = b""
                while True:
                    chunk = self.request.recv(65536)
                    if not chunk:
                        return
                    buf += chunk
                    while len(buf) >= 8:
                        magic, proto, slot, mtid, mtype, dlen = struct.unpack(">HBBBBH", buf[:8])
                        if len(buf) < 8 + dlen:
                            break
                        payload, buf = buf[8:8 + dlen], buf[8 + dlen:]
                        fake.requests += 1
                        self.request.sendall(fake.reply(proto, slot, mtid, mtype, payload))

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self.server = Server(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    @staticmethod
    def frame(proto, slot, mtid, mtype, payload):
        return struct.pack(">HBBBBH", MAGIC, proto, slot, mtid, mtype, len(payload)) + payload

    def reply(self, proto, slot, mtid, mtype, payload):
        if proto == 0:                                   # AN2 internal
            func = payload[0]
            if func == 0:
                return self.frame(0, slot, mtid, 1, bytes([0]) + struct.pack(">H", 1))
            if func == 1:
                return self.frame(0, slot, mtid, 1, bytes([1, len(self.slots)]))
            if func == 2:
                if slot not in self.slots:
                    return self.frame(0, slot, mtid, 3, bytes([1]))
                return self.frame(0, slot, mtid, 1, bytes([2, 2, 2, 2, 3]))
            if func == 3:
                return self.frame(0, slot, mtid, 1, bytes([3]))
            return self.frame(0, slot, mtid, 3, bytes([0]))
        # ACP2 data
        atype, amtid, func, pid = payload[:4]
        err = lambda stat: self.frame(2, slot, 0, 4, bytes([3, amtid, stat, 0]))
        tree = self.slots.get(slot)
        if tree is None:
            return self.frame(0, slot, 0, 3, bytes([1]))
        if func == 0:
            return self.frame(2, slot, 0, 4, bytes([1, amtid, 0, 2]))
        obj_id, idx = struct.unpack(">II", payload[4:12])
        o = tree.objs.get(obj_id)
        if func == 3:
            self.set_attempts.append((slot, obj_id, pid, payload[12:]))
            return err(4)
        if o is None:
            return err(1)
        head = bytes([1, amtid, func, pid]) + struct.pack(">II", obj_id, idx)
        if func == 1:
            return self.frame(2, slot, 0, 4, head + encode_object(o))
        if func == 2:
            if pid != 8 or o["type"] == 0:
                return err(3)
            return self.frame(2, slot, 0, 4, head + _value_prop(8, o))
        return err(0)
