# ACP2 client — read-only, working

**Status 2026-10-15:** `client.py` speaks AN2 + ACP2 and reads a live Neuron.
Spec: Axon "Axonnet2 protocol" v1.1 and "ACP2 protocol" v1.0, published in
[by-openclaw/go-acp](https://github.com/by-openclaw/go-acp) (`internal/acp2/assets`).
The client is read-only by construction: it refuses every AN2/ACP2 function
except get version / device info / slot info / get object / get property.
There is no set-property code path.

Verified read-only against IPG03A (10.12.80.20, FW 7.0.3):
- AN2 v1, 2 slots: slot 0 = frame/board (BOARD, PSU, QSFP …), protocols [2,3,4];
  slot 1 = the BRIDGE card (IDENTITY, GENERAL, INPUT, PROCESSING VIDEO/AUDIO,
  OUTPUT …), protocols [2,3]. ACP2 v2. Root object id = 1 (`ROOT_NODE_V2`).
- The slot-1 tree is the same GROUP/OBJECT tree as the `.cbf` export. Sample
  routing values (bank 1/17, embedder A1, IP Out 13) are identical live and in
  the 2026-10-14 export. Enums come back as option text instead of index.
- Object ids are contiguous in tree order: BANK n node = 163242 + (n-1)*17
  (BANK 1 / AUDIO CHANNEL 1 = 163243, its Identifier..Phase = 163244..163249).
- Enum options on the wire are variable-length (u32 index + 0-terminated text,
  padded to 4), not the 72 bytes in the spec — same as go-acp handles it.
- The device sends nothing until a client sends a request (checked by listening
  passively for 45 s). Announces need AN2 "enable protocol events".

Writes live in a separate module, `writer.py` (ACP2 set property on the value
of u32 / float objects only), used by `router/take.py` for Take/Undo.

**First live write, 2026-10-15, IPG03A** (approved by the user, on a path that
was not in use: H4 / channel 32, VRX-32 and ARX-125..128 disabled):
- object 81668 = PROCESSING VIDEO/PATH H/CHANNEL 04/EMBEDDER/AUDIO CHANNEL 1/Input Backup (u32)
- Take: 3569 (De-Emb H4.1) -> 1985 (IP In 125.1); device reply 1985, read-back 1985, 0.07 s
- independent read with the read-only client: Backup 1985, Main and Current unchanged (1985)
- Undo: 1985 -> 3569; reply 3569, read-back 3569, 0.06 s. Device back to its original state.
- set property request = AN2 data frame, ACP2 `0 | mtid | 3 | 8 | obj-id | idx 0 | pid 8, vtype 6, plen 8, u32`;
  the reply carries the new value as a pid-8 property, exactly as the spec describes.

---

# (History) ACP2 skeleton notes from before the spec was found


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

## Observed on IPG03A (10.12.80.20, FW 7.0.3), 2026-10-15, read-only probes

- TCP 2072 open, which is very likely ACP2. Nothing was sent there.
- UDP 2071 (ACP1, per the "AxonNet objects and ACP rev 1.3" PDF + AxonNet SDK
  1.0): one `getObject(Root)` request to slots 0 and 1, **no reply**. The Neuron
  does not answer ACP1. ACP1 could not address the Neuron tree anyway (1-byte
  object IDs, at most 256 per group, against about 39,000 objects).
- HTTP 80 redirects to HTTPS, and HTTPS `/` returns 500 (nginx).
- No ACP2 documentation is available (confirmed by the user). Until that
  changes, `router/` works on `.cbf` files only.

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
