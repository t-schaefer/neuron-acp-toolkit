"""
ACP2 WRITER - the only code in this repo that can change a Neuron.

Separate from client.py on purpose: the read-only client stays read-only, and
anything that writes has to import this module explicitly.

It can do exactly one thing: ACP2 `set property` (func 3) on the VALUE (pid 8)
of one object, then return what the device answered. Only number objects of
type u32 (6) and float (8) are accepted - that covers `Input Main`,
`Input Backup` (u32) and `Delay` / `Stream Delay` (float). Everything else is
refused before anything is sent.

Spec: ACP2 protocol v1.0 §4.3.4 (set property): header type=0, mtid, func=3,
pid=8, obj-id u32, idx u32, then the property header pid=8 | vtype | plen=8 |
value. The device replies with the property as it now is.
"""
import struct

from client import (ACP2_REQUEST, AN2_DATA, PROTO_ACP2, Acp2Client, Acp2Error,
                    parse_properties)

ACP2_SET_PROPERTY = 3
P_VALUE = 8
WRITABLE_TYPES = {6: ">I", 8: ">f"}   # number_type -> struct format (u32, float)


class Acp2Writer(Acp2Client):
    def set_value(self, slot, obj_id, value, number_type):
        """Set the value of one object. Returns the value the device reports back."""
        fmt = WRITABLE_TYPES.get(number_type)
        if fmt is None:
            raise Acp2Error(f"refusing to write number type {number_type} (only u32 and float)")
        if number_type == 6:
            value = int(value)
            if not 0 <= value <= 0xFFFFFFFF:
                raise Acp2Error(f"value {value} out of u32 range")
        else:
            value = float(value)
        self._acp2_mtid = self._acp2_mtid % 255 + 1
        prop = struct.pack(">BBH", P_VALUE, number_type, 8) + struct.pack(fmt, value)
        msg = bytes([ACP2_REQUEST, self._acp2_mtid, ACP2_SET_PROPERTY, P_VALUE]) + struct.pack(">II", obj_id, 0) + prop
        self._send_frame(PROTO_ACP2, slot, 0, AN2_DATA, msg)
        while True:
            proto, rslot, _, mtype, payload = self._recv_frame()
            if proto == 0 and mtype == 3:
                raise Acp2Error(f"AN2 error status {payload[0] if payload else '?'} on slot {slot}")
            if proto != PROTO_ACP2 or rslot != slot or len(payload) < 4:
                continue
            atype, mtid, func_or_stat, _ = payload[:4]
            if atype == 2 or mtid != self._acp2_mtid:
                continue                      # announce / unrelated
            if atype == 3:
                from client import ACP2_ERRORS
                raise Acp2Error(f"ACP2 {ACP2_ERRORS.get(func_or_stat, func_or_stat)} on set (obj {obj_id})")
            return parse_properties(payload[12:]).get("value")
