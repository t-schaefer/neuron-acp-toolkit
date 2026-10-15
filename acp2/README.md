# ACP2 live-configuration client — skeleton, not functional

## What this is for

Everything else in this repo patches an offline `.cbf` backup file that the
user then has to manually re-import into the physical Neuron device. ACP2
is EVS's own control protocol — if we can talk it directly, we could read
and write a device's live configuration over the network instead, which
would remove the manual re-import step entirely and let us verify a change
immediately (read the value back) rather than trusting a file we can't
test.

## What's missing before this can work

**I (Claude) do not have the ACP2 protocol specification.** ACP2 is
proprietary — normally distributed by EVS under NDA as part of an SDK to
automation/control-system partners (the kind of thing Pebble Beach, Softel,
etc. would integrate against). I have no reliable, verifiable knowledge of
its actual wire format (framing, opcodes, addressing scheme for a given
GROUP/OBJECT path, authentication if any), and I'm not going to guess at
one and hand you something that looks plausible but silently sends garbage
to a production broadcast chain. That's a worse failure mode than saying
"I don't know" up front.

**To make this real, we need, from you:**
1. The ACP2 protocol document / SDK (even an older version — protocols
   like this rarely break wire compatibility across minor versions).
2. Ideally a way to test against either a real Neuron (even one not in the
   live PLS chain) or an EVS-provided simulator, so we're not debugging a
   from-scratch protocol implementation against production hardware.
3. Confirmation of transport (raw TCP socket on a known port? something
   layered on HTTP? a vendor library you're already permitted to call from
   Python, in which case we may not need to reimplement the wire protocol
   at all).

## What's already here

`client.py` sketches the *shape* a client like this usually takes —
connection lifecycle, a `read(path)` / `write(path, value)` call surface
keyed by the same GROUP/OBJECT path concept already used for `.cbf` files,
and a place to plug in request/response framing once it's known. None of
the actual wire-level code is implemented; every function that would need
protocol knowledge raises `NotImplementedError` with a note on what's
missing, rather than silently doing nothing or faking a response.

## Also worth considering once this exists

- **This cloud session cannot reach the plant network** (10.12.80.x is not
  on this container's network allowlist). A working ACP2 client needs to
  run somewhere with real network access to the Neuron units — your own
  machine via Claude Code locally, or a linked device/bridge. Keep that in
  mind when deciding where this code actually executes.
- Once read access works, the single highest-value feature is probably:
  for every channel already wired via `.cbf`-file patching, read back the
  live `Stream` (Enabled/Disabled) flag and the actual wired IDs, and flag
  any mismatch automatically — i.e. turn the manual verification habits in
  `docs/lessons-learned.md` into something that runs itself against the
  real device instead of against an exported file.
