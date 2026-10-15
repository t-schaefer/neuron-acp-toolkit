"""
READ-ONLY ACP2 client for EVS/Axon Neuron, over AN2/TCP 2072.

Wire format from the Axon specs "Axonnet2 protocol" v1.1 (2018-05-28) and
"ACP2 protocol" v1.0 (2018-08-01), as published in github.com/by-openclaw/go-acp
(internal/acp2/assets). Summary in acp2/README.md.

    AN2 header (8 bytes, big-endian): magic u16 0xC635 | proto u8 | slot u8 |
                                      mtid u8 | type u8 | dlen u16 | payload
    ACP2 message (inside an AN2 data frame, proto=2, type=4):
                                      type u8 | mtid u8 | func u8 | pid u8 |
                                      obj-id u32 | idx u32 | property headers

SAFETY: this client can only READ. `_send_an2` refuses AN2 functions other than
get version / get device info / get slot info, and `_acp2_request` refuses ACP2
functions other than get version / get object / get property. There is no
set-property code path at all. Writing will be a separate, deliberate step.
"""
from __future__ import annotations

import socket
import struct
import time
from dataclasses import dataclass, field

AN2_MAGIC = 0xC635
AN2_PORT = 2072
# AN2 protos / types / functions
PROTO_AN2, PROTO_ACP1, PROTO_ACP2 = 0, 1, 2
AN2_REQUEST, AN2_REPLY, AN2_EVENT, AN2_ERROR, AN2_DATA = 0, 1, 2, 3, 4
AN2_GET_VERSION, AN2_GET_DEVICE_INFO, AN2_GET_SLOT_INFO = 0, 1, 2
AN2_READ_FUNCS = {AN2_GET_VERSION, AN2_GET_DEVICE_INFO, AN2_GET_SLOT_INFO}
SLOT_STATUS = {0: "empty", 1: "powerup", 2: "present", 3: "error", 4: "removed", 5: "boot"}
# ACP2 types / functions
ACP2_REQUEST, ACP2_REPLY, ACP2_ANNOUNCE, ACP2_ERROR = 0, 1, 2, 3
ACP2_GET_VERSION, ACP2_GET_OBJECT, ACP2_GET_PROPERTY = 0, 1, 2   # 3 = set property: NOT allowed here
ACP2_READ_FUNCS = {ACP2_GET_VERSION, ACP2_GET_OBJECT, ACP2_GET_PROPERTY}
ACP2_ERRORS = {0: "protocol error", 1: "invalid obj-id", 2: "invalid idx", 3: "invalid pid",
               4: "no access", 5: "invalid value"}
OBJECT_TYPES = {0: "node", 1: "preset", 2: "enum", 3: "number", 4: "ipv4", 5: "string"}
# property ids
P_OBJECT_TYPE, P_LABEL, P_ACCESS, P_EVENT_DELAY, P_NUMBER_TYPE, P_STRING_MAX = 1, 2, 3, 4, 5, 6
P_PRESET_DEPTH, P_VALUE, P_DEFAULT, P_MIN, P_MAX, P_STEP, P_UNIT = 7, 8, 9, 10, 11, 12, 13
P_CHILDREN, P_OPTIONS, P_EVENT_TAG, P_EVENT_PRIO, P_EVENT_STATE, P_EVENT_MSG, P_PRESET_PARENT = \
    14, 15, 16, 17, 18, 19, 20
PROPERTY_NAMES = {1: "object_type", 2: "label", 3: "access", 4: "event_delay", 5: "number_type",
                  6: "string_max_length", 7: "preset_depth", 8: "value", 9: "default", 10: "min",
                  11: "max", 12: "step", 13: "unit", 14: "children", 15: "options",
                  16: "event_tag", 17: "event_prio", 18: "event_state", 19: "event_messages",
                  20: "preset_parent"}
VALUE_PIDS = {P_VALUE, P_DEFAULT, P_MIN, P_MAX, P_STEP}


class Acp2Error(Exception):
    pass


@dataclass
class Acp2Object:
    obj_id: int
    props: dict = field(default_factory=dict)   # name -> value (lists for preset-depth values)

    @property
    def type(self):
        return OBJECT_TYPES.get(self.props.get("object_type"), self.props.get("object_type"))

    @property
    def label(self):
        return self.props.get("label", "")

    @property
    def children(self):
        return self.props.get("children", [])

    @property
    def value(self):
        v = self.props.get("value")
        if self.props.get("object_type") == 2 and isinstance(v, int):  # enum -> option text
            return (self.props.get("options") or {}).get(v, v)
        return v

    @property
    def writable(self):
        return bool((self.props.get("access") or 0) & 2)


def _decode_value(vtype, raw):
    if vtype in (0, 1, 2):
        return struct.unpack(">i", raw[:4])[0]
    if vtype in (4, 5, 6, 9):
        return struct.unpack(">I", raw[:4])[0]
    if vtype == 3:
        return struct.unpack(">q", raw[:8])[0]
    if vtype == 7:
        return struct.unpack(">Q", raw[:8])[0]
    if vtype == 8:
        return round(struct.unpack(">f", raw[:4])[0], 6)
    if vtype == 10:
        return ".".join(str(b) for b in raw[:4])
    if vtype == 11:
        return raw.split(b"\0", 1)[0].decode("utf-8", "replace")
    return raw.hex()


def parse_properties(buf):
    """Property headers (pid u8 | data u8 | plen u16 | value), each padded to 4 bytes."""
    props, pos = {}, 0
    while pos + 4 <= len(buf):
        pid, data, plen = buf[pos], buf[pos + 1], struct.unpack(">H", buf[pos + 2:pos + 4])[0]
        if plen < 4:
            raise Acp2Error(f"bad property length {plen} for pid {pid}")
        body = buf[pos + 4:pos + plen]
        name = PROPERTY_NAMES.get(pid, f"pid{pid}")
        if pid in (P_OBJECT_TYPE, P_ACCESS, P_NUMBER_TYPE, P_EVENT_PRIO, P_EVENT_STATE):
            val = data
        elif pid in (P_LABEL, P_UNIT):
            val = body.split(b"\0", 1)[0].decode("utf-8", "replace")
        elif pid == P_EVENT_DELAY or pid == P_PRESET_PARENT:
            val = struct.unpack(">I", body[:4])[0]
        elif pid in (P_STRING_MAX, P_EVENT_TAG):
            val = struct.unpack(">H", body[:2])[0]
        elif pid in (P_PRESET_DEPTH, P_CHILDREN):
            val = list(struct.unpack(f">{len(body) // 4}I", body[:len(body) // 4 * 4]))
        elif pid == P_OPTIONS:
            # Real Neuron firmware: variable-length records, each u32 index + 0-terminated
            # text, padded to 4 bytes (the spec's fixed 72 bytes is not what devices send;
            # same handling as go-acp's PropertyOptionsMap). Kept as {index: text}.
            val, o = {}, 0
            while o + 4 <= len(body):
                oi = struct.unpack(">I", body[o:o + 4])[0]
                end = body.find(b"\0", o + 4)
                end = len(body) if end < 0 else end
                val[oi] = body[o + 4:end].decode("utf-8", "replace")
                o = end + 1
                o += (4 - (o % 4)) % 4
        elif pid == P_EVENT_MSG:
            val = [s.decode("utf-8", "replace") for s in body.split(b"\0")[:2]]
        elif pid in VALUE_PIDS:
            val = _decode_value(data, body)
            props.setdefault(f"{name}_vtype", data)
        else:
            val = body.hex()
        if pid in VALUE_PIDS and name in props:      # preset depth: one entry per idx
            prev = props[name]
            props[name] = (prev if isinstance(prev, list) else [prev]) + [val]
        else:
            props[name] = val
        pos += plen + (4 - plen % 4) % 4
    return props


class Acp2Client:
    def __init__(self, host, port=None, timeout=5.0, pace=0.0):
        # port=None -> module AN2_PORT (tests point that at a simulated device)
        self.host, self.port, self.timeout, self.pace = host, port or AN2_PORT, timeout, pace
        self.sock = None
        self._an2_mtid = 0
        self._acp2_mtid = 0
        self._buf = b""
        self.requests = 0

    # ---- connection ----
    def connect(self):
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.sock.settimeout(self.timeout)
        return self

    def close(self):
        if self.sock:
            self.sock.close()
            self.sock = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, *exc):
        self.close()

    # ---- framing ----
    def _send_frame(self, proto, slot, mtid, mtype, payload):
        frame = struct.pack(">HBBBBH", AN2_MAGIC, proto, slot, mtid, mtype, len(payload)) + payload
        if self.pace:
            time.sleep(self.pace)
        self.sock.sendall(frame)
        self.requests += 1

    def _recv_frame(self):
        while True:
            if len(self._buf) >= 8:
                magic, proto, slot, mtid, mtype, dlen = struct.unpack(">HBBBBH", self._buf[:8])
                if magic != AN2_MAGIC:
                    raise Acp2Error(f"bad AN2 magic 0x{magic:04x}")
                if len(self._buf) >= 8 + dlen:
                    payload, self._buf = self._buf[8:8 + dlen], self._buf[8 + dlen:]
                    return proto, slot, mtid, mtype, payload
            chunk = self.sock.recv(65536)
            if not chunk:
                raise Acp2Error("connection closed by device")
            self._buf += chunk

    # ---- AN2 (read-only functions) ----
    def _send_an2(self, func, slot=0):
        if func not in AN2_READ_FUNCS:
            raise Acp2Error(f"AN2 function {func} is not allowed in the read-only client")
        self._an2_mtid = self._an2_mtid % 255 + 1
        self._send_frame(PROTO_AN2, slot, self._an2_mtid, AN2_REQUEST, bytes([func]))
        while True:
            proto, rslot, mtid, mtype, payload = self._recv_frame()
            if proto != PROTO_AN2 or mtid != self._an2_mtid:
                continue        # unrelated event/data
            if mtype == AN2_ERROR:
                raise Acp2Error(f"AN2 error status {payload[0] if payload else '?'} for func {func} slot {slot}")
            return payload

    def an2_version(self):
        p = self._send_an2(AN2_GET_VERSION)
        return struct.unpack(">H", p[1:3])[0]

    def an2_slot_count(self):
        return self._send_an2(AN2_GET_DEVICE_INFO)[1]

    def an2_slot_info(self, slot):
        p = self._send_an2(AN2_GET_SLOT_INFO, slot)
        stat, num = p[1], p[2]
        return {"slot": slot, "status": SLOT_STATUS.get(stat, stat), "protocols": list(p[3:3 + num])}

    # ---- ACP2 (read-only functions) ----
    def _acp2_request(self, slot, func, pid=0, obj_id=None, idx=0):
        if func not in ACP2_READ_FUNCS:
            raise Acp2Error(f"ACP2 function {func} is not allowed in the read-only client")
        self._acp2_mtid = self._acp2_mtid % 255 + 1
        msg = bytes([ACP2_REQUEST, self._acp2_mtid, func, pid])
        if obj_id is not None:
            msg += struct.pack(">II", obj_id, idx)
        self._send_frame(PROTO_ACP2, slot, 0, AN2_DATA, msg)
        while True:
            proto, rslot, _, mtype, payload = self._recv_frame()
            if proto == PROTO_AN2 and mtype == AN2_ERROR:
                raise Acp2Error(f"AN2 error status {payload[0] if payload else '?'} on slot {slot}")
            if proto != PROTO_ACP2 or rslot != slot or len(payload) < 4:
                continue
            atype, mtid, func_or_stat, pid_or_ver = payload[:4]
            if atype == ACP2_ANNOUNCE or mtid != self._acp2_mtid:
                continue
            if atype == ACP2_ERROR:
                raise Acp2Error(f"ACP2 {ACP2_ERRORS.get(func_or_stat, func_or_stat)} "
                                f"(obj {obj_id}, pid {pid}, slot {slot})")
            return pid_or_ver, payload[4:]

    def acp2_version(self, slot):
        ver, _ = self._acp2_request(slot, ACP2_GET_VERSION)
        return ver

    def get_object(self, slot, obj_id):
        _, body = self._acp2_request(slot, ACP2_GET_OBJECT, 0, obj_id, 0)
        rid, _ = struct.unpack(">II", body[:8])
        return Acp2Object(rid, parse_properties(body[8:]))

    def get_property(self, slot, obj_id, pid, idx=0):
        _, body = self._acp2_request(slot, ACP2_GET_PROPERTY, pid, obj_id, idx)
        return parse_properties(body[8:]).get(PROPERTY_NAMES.get(pid, f"pid{pid}"))

    def walk(self, slot, root=1, max_objects=None, on_object=None):
        """Depth-first walk from `root`. Returns {obj_id: (path tuple, Acp2Object)}."""
        out, stack = {}, [(root, ())]
        while stack:
            oid, parent_path = stack.pop()
            if oid in out:
                continue
            obj = self.get_object(slot, oid)
            path = parent_path + (obj.label,) if oid != root else ()
            out[oid] = (path, obj)
            if on_object:
                on_object(oid, path, obj)
            if max_objects and len(out) >= max_objects:
                break
            for child in reversed(obj.children):
                stack.append((child, path))
        return out
