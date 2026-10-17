"""
Listen to ACP2 announces (events) of a Neuron - READ-ONLY.

Sends exactly one AN2 request, "enable protocol events" for ACP2 (AN2 func 3,
proto list [2]). That is a per-connection subscription, not a device setting:
it only tells the device to send ACP2 announces to this TCP connection. After
that the script only receives. No ACP2 request is ever sent.

    python3 acp2/listen_events.py 10.12.80.20 60            # all announces, 60 s
    python3 acp2/listen_events.py 10.12.80.20 60 167852     # only these object ids

Prints: time, slot, object id, property id, decoded value.
"""
import socket
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from client import AN2_MAGIC, parse_properties   # noqa: E402

AN2_ENABLE_EVENTS = 3


def main():
    host, secs = sys.argv[1], float(sys.argv[2])
    only = {int(x) for x in sys.argv[3:]}
    s = socket.create_connection((host, 2072), timeout=5)
    s.settimeout(1.0)
    payload = bytes([AN2_ENABLE_EVENTS, 1, 2])            # func, num protos, [ACP2]
    s.sendall(struct.pack(">HBBBBH", AN2_MAGIC, 0, 0, 1, 0, len(payload)) + payload)
    buf, t0, n_all, n_shown = b"", time.time(), 0, 0
    while time.time() - t0 < secs:
        try:
            chunk = s.recv(65536)
        except socket.timeout:
            continue
        if not chunk:
            print("connection closed by device")
            break
        buf += chunk
        while len(buf) >= 8:
            magic, proto, slot, mtid, mtype, dlen = struct.unpack(">HBBBBH", buf[:8])
            if len(buf) < 8 + dlen:
                break
            body, buf = buf[8:8 + dlen], buf[8 + dlen:]
            if proto == 0:
                print(f"{time.time() - t0:7.2f}s AN2 type {mtype} slot {slot} payload {body.hex()}", flush=True)
                continue
            if proto != 2 or len(body) < 12 or body[0] != 2:      # ACP2 announce = type 2
                continue
            n_all += 1
            pid = body[3]
            oid, idx = struct.unpack(">II", body[4:12])
            if only and oid not in only:
                continue
            n_shown += 1
            props = parse_properties(body[12:])
            val = props.get("value", props)
            print(f"{time.time() - t0:7.2f}s slot {slot} obj {oid} idx {idx} pid {pid}: {val}", flush=True)
    s.close()
    print(f"done: {n_all} ACP2 announces received, {n_shown} shown")


if __name__ == "__main__":
    main()
