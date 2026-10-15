"""
Probe the EVS Cerebrum XML API (CRBM-XML-CI, WebSocket) - READ-ONLY.

Standard library only. Opens one WebSocket to the Cerebrum server, optionally
logs in, sends the messages you give it and prints every reply for a few
seconds. Write messages are refused: only root elements in READ_ONLY_ROOTS are
sent, so ACTION (the XML-CI write command, see docs/cerebrum-write-path.md)
can never leave this script.

    # no login: shows reachability, server banner and the NACK error codes
    python3 cerebrum/probe_xml_api.py 10.12.81.19:40007

    # login: credentials from the environment only, never on the command line
    CEREBRUM_USER=... CEREBRUM_PASS=... python3 cerebrum/probe_xml_api.py 10.12.81.19:40007 --login

    # login, then send read-only messages from a file (one XML element per line)
    CEREBRUM_USER=... CEREBRUM_PASS=... python3 cerebrum/probe_xml_api.py 10.12.81.19:40007 --login msgs.xml 20

What is known (2026-08-30 probe, test server 10.12.81.19:40007):
- HTTP GET answers "426 Upgrade Required", Server: WebSocket++/0.8.2
- every message needs an MTID attribute on the root element, else
  <NACK MTID="0" ERROR="MTID_ERROR" ERROR_CODE="1"/>; bad XML -> INVALID_XML (3)
- before a login every command gets <NACK ... ERROR="NOT_LOGGED_IN" ERROR_CODE="6"/>
- the server's own elements are UPPER CASE; the earlier probe only tried
  <Login>/<Hello>/... in mixed case, which may be why no login was found.
The element and attribute names below (LOGIN USERNAME PASSWORD, POLL,
SUBSCRIBE, OBTAIN, DEVICE_CHANGE ...) are a working assumption until the
CRBM-XML-CI documentation is at hand.
"""
import base64
import os
import re
import socket
import struct
import sys
import time
from xml.sax.saxutils import quoteattr

READ_ONLY_ROOTS = {"LOGIN", "LOGOUT", "POLL", "OBTAIN", "SUBSCRIBE", "UNSUBSCRIBE"}


class WebSocket:
    """Minimal RFC 6455 client: text frames, ping/pong, close. ws:// only."""

    def __init__(self, host, port, timeout=5.0):
        self.s = socket.create_connection((host, port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET / HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
               f"Sec-WebSocket-Version: 13\r\n\r\n")
        self.s.sendall(req.encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.s.recv(4096)
            if not chunk:
                raise ConnectionError("closed during handshake")
            head += chunk
        head, self.buf = head.split(b"\r\n\r\n", 1)
        self.handshake = head.decode("latin1")
        if " 101 " not in self.handshake.splitlines()[0]:
            raise ConnectionError("no WebSocket upgrade:\n" + self.handshake)

    def _send_frame(self, opcode, data):
        mask = os.urandom(4)
        n = len(data)
        if n < 126:
            hdr = struct.pack(">BB", 0x80 | opcode, 0x80 | n)
        elif n < 65536:
            hdr = struct.pack(">BBH", 0x80 | opcode, 0x80 | 126, n)
        else:
            hdr = struct.pack(">BBQ", 0x80 | opcode, 0x80 | 127, n)
        self.s.sendall(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def send_text(self, text):
        self._send_frame(0x1, text.encode("utf-8"))

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.s.recv(65536)
            if not chunk:
                raise ConnectionError("connection closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def recv_text(self):
        """Next complete text message, or None after a close frame."""
        parts = []
        while True:
            b0, b1 = self._read(2)
            opcode, n = b0 & 0x0F, b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            mask = self._read(4) if b1 & 0x80 else None
            data = self._read(n)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if opcode == 0x9:                       # ping -> pong
                self._send_frame(0xA, data)
                continue
            if opcode == 0xA:
                continue
            if opcode == 0x8:
                return None
            parts.append(data)
            if b0 & 0x80:
                return b"".join(parts).decode("utf-8", "replace")

    def close(self):
        try:
            self._send_frame(0x8, b"")
        finally:
            self.s.close()


def root_name(xml):
    m = re.match(r"\s*(?:<\?xml[^>]*\?>\s*)?<([A-Za-z_][\w.-]*)", xml)
    return m.group(1) if m else None


def drain(ws, secs):
    t_end = time.time() + secs
    while time.time() < t_end:
        ws.s.settimeout(max(0.1, t_end - time.time()))
        try:
            msg = ws.recv_text()
        except socket.timeout:
            break
        if msg is None:
            print("<- (close)")
            return False
        print("<-", msg)
    return True


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(2)
    host, port = args.pop(0).rsplit(":", 1)
    login = "--login" in args
    args = [a for a in args if a != "--login"]
    msg_file = args[0] if args else None
    listen = float(args[1]) if len(args) > 1 else 3.0

    msgs = []
    if msg_file:
        for line in open(msg_file, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            name = root_name(line)
            if name not in READ_ONLY_ROOTS:
                sys.exit(f"refused (not read-only): <{name}> - allowed: {sorted(READ_ONLY_ROOTS)}")
            msgs.append(line)

    ws = WebSocket(host, int(port))
    print(ws.handshake.splitlines()[0], "|",
          next((l for l in ws.handshake.splitlines() if l.lower().startswith("server:")), ""))
    mtid = 1
    if login:
        user, pw = os.environ.get("CEREBRUM_USER"), os.environ.get("CEREBRUM_PASS")
        if not user or pw is None:
            sys.exit("--login needs CEREBRUM_USER and CEREBRUM_PASS in the environment")
        ws.send_text(f"<LOGIN MTID=\"{mtid}\" USERNAME={quoteattr(user)} PASSWORD={quoteattr(pw)}/>")
        print(f"-> <LOGIN MTID=\"{mtid}\" USERNAME=... PASSWORD=.../>")
        mtid += 1
        if not drain(ws, 2.0):
            return
    if not msgs:                                   # default: one harmless read request
        msgs = [f"<POLL MTID=\"{mtid}\"/>"]
    for m in msgs:
        print("->", m)
        ws.send_text(m)
        if not drain(ws, 1.0):
            return
    drain(ws, listen)
    ws.close()


if __name__ == "__main__":
    main()
