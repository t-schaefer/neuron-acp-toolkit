"""
Skeleton for a live ACP2 client talking to an EVS Neuron device. NOT
FUNCTIONAL -- see README.md for exactly what's missing and why nothing here
guesses at the actual wire protocol.

The intent is to give this the same mental model as the .cbf file tools:
address a setting by its GROUP/OBJECT path (e.g.
"PROCESSING AUDIO/AUDIO DELAY/BANK 41/Delay") and read or write its value,
so that scripts written against tools/build_engine.py's offline logic can
eventually be pointed at a live device instead of a file with minimal
changes.
"""
from __future__ import annotations

import socket
from dataclasses import dataclass


class Acp2NotImplemented(NotImplementedError):
    """Raised by anything that needs real protocol knowledge we don't have
    yet. See README.md -- this is intentional, not a bug to work around."""


@dataclass
class Acp2Connection:
    host: str
    port: int  # unknown -- placeholder; confirm from EVS SDK/docs
    timeout: float = 5.0

    _sock: socket.socket | None = None

    def connect(self) -> None:
        """Open the transport connection. Socket-level connect is fine to
        implement now (no protocol knowledge needed) -- what's missing is
        everything that happens after this: handshake/auth (if any), and
        the actual request framing used below."""
        self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)

    def close(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    def __enter__(self) -> "Acp2Connection":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- everything below needs the actual ACP2 spec ---

    def read(self, group_path: str, object_name: str):
        """Read a single OBJECT's current value from the live device.

        group_path: '/'-separated path of GROUP names from the device root,
                    e.g. 'PROCESSING AUDIO/AUDIO DELAY/BANK 41'
        object_name: the OBJECT's NAME, e.g. 'Delay'

        Needs: the actual request frame format, how a GROUP/OBJECT path (or
        whatever addressing scheme ACP2 actually uses -- it may not be a
        path at all, e.g. could be numeric object IDs) gets encoded, and
        how a response frame is parsed back into a value + type.
        """
        raise Acp2NotImplemented(
            "ACP2 wire protocol unknown -- see acp2/README.md. "
            f"Would have read {group_path!r} / {object_name!r}."
        )

    def write(self, group_path: str, object_name: str, value) -> None:
        """Write a single OBJECT's value on the live device.

        Same addressing caveat as read(). Also unknown: whether writes are
        fire-and-forget or ack'd, whether there's a commit/apply step
        separate from individual writes, and whether the device enforces
        the same write-protection semantics the .cbf file's WRITABLE="0"
        flags imply (some OBJECTs in the file are read-only status/telemetry
        -- see docs/cbf-format.md -- a live write attempt to one of those
        should presumably be rejected by the device, but we don't know what
        that rejection looks like on the wire).
        """
        raise Acp2NotImplemented(
            "ACP2 wire protocol unknown -- see acp2/README.md. "
            f"Would have written {group_path!r} / {object_name!r} = {value!r}."
        )

    def read_many(self, paths: list[tuple[str, str]]):
        """Batch read -- almost certainly wanted once this works, so a
        verification pass (per docs/lessons-learned.md: check every
        channel's SDI-out vs IP-out wiring, check every Stream's
        Enabled/Disabled flag) doesn't need one round-trip per OBJECT.
        Whether ACP2 actually supports batching, or this just has to loop
        read() under the hood, depends on the spec."""
        raise Acp2NotImplemented("see read() -- same missing protocol knowledge")


def verify_channel_live(conn: Acp2Connection, channel_banks: list[int], out_stream: int) -> dict:
    """Sketch of the single highest-value check once read() works: for a
    Case B (SDI-sourced) channel already patched via .cbf file, confirm live
    that every bank's Delay is the expected value, that the channel's own
    EMBEDDER and the OUTPUT>IP>AUDIO>STREAM target agree, and that the
    output stream's `Stream` flag is Enabled -- i.e. run the exact
    verification habits from docs/lessons-learned.md against the real
    device instead of against an exported file.

    Not implemented -- placeholder showing the intended shape once read()
    exists.
    """
    raise Acp2NotImplemented("depends on Acp2Connection.read() / read_many()")
