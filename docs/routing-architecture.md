# Audio routing architecture (Bridge / Convert-IP devices)

Every channel (1–32) is one of two cases, decided by its `Main Input` ENUM:

## Case A — IP-sourced (`Main Input = "Stream N"`)

```
Input Stream N  --stream_id(real_N,k)-->  INPUT>IP>AUDIO>STREAM real_N
                                           (Stream Delay = 19.5 ms, applied here)
                                                  |
                                                  v
                              channel's own EMBEDDER (Input Main = stream_id(real_N,k))
                                                  |
                                                  v
                                          physical SDI output
```

Delay is applied **once, on the input stream itself** (`Stream Delay`
object inside `INPUT > IP > AUDIO > STREAM {real_N}`). The channel's
EMBEDDER (which feeds the SDI output) just wires directly to that same
stream's de-interleaved IDs — it picks up the already-delayed audio for
free, no separate bank needed.

**Main and Backup are resolved completely independently** — a channel's
Backup Input can select a *different* real stream than its Main (e.g.
IPG03A ch7: Main = SDI, Backup = a real IP stream). Always parse and patch
both sides separately; never assume Backup mirrors Main.

**`"Pattern"` ENUM value = no real signal selected.** Both for Main and
Backup, independently — if a side shows `Pattern`, skip it silently (don't
invent a wiring edit for it). It's common to see a leftover/stale raw ID
sitting in that side's embedder slot even though it's inactive — harmless,
ignore it unless specifically asked to clean it up.

## Case B — SDI-sourced (`Main Input = "SDI N"`)

Three stages:

```
SDI Input N --(fixed HW de-embedder)--> deembed_id(N,k)   [k=1..16]
                                               |
                                               v
                              Delay bank (8 banks / channel, 16 slots)
                              bank's Input Main AND Input Backup
                                = deembed_id(N,k)  (pulls de-embedded audio in)
                              bank's Delay = 18.0 ms  (applied HERE)
                                               |
                          (bank's own Identifier = the delayed copy)
                                               |
                    +--------------------------+--------------------------+
                    v                                                     v
       channel's own EMBEDDER                              OUTPUT>IP>AUDIO>STREAM M
       (Input Main = bank's own Identifier)                 (Input Main AND Backup
              |                                               = bank's own Identifier)
              v                                                           |
       physical SDI output                                    IP output (ST2110-30)
```

**Both outputs must be wired from the same bank ID.** The bank is the only
place delay is applied; SDI-out and IP-out are two independent downstream
consumers of that one delayed copy and both must be patched. **This is the
gap that bit us on IPG04A**: for a long time only the SDI-out path
(channel's own EMBEDDER) was being wired; the IP-out path
(`OUTPUT>IP>AUDIO>STREAM`) was never touched and stayed at its untouched
default — so audio was present on SDI out but silently missing (or
undelayed, depending on the device's prior state) on IP out. **Always wire
and verify both.**

### Bank allocation — fixed port-based scheme

Current/preferred scheme (superseded an earlier "sequential in channel
order" scheme that wasted no banks but was harder to reason about and
collided awkwardly when a channel was reclassified):

```python
def port_bank_range(sdi_port_number):
    b0 = (sdi_port_number - 1) * 8 + 1
    return b0, b0 + 7   # inclusive
```

SDI port N always uses banks `(N-1)*8+1 .. N*8`, fixed regardless of which
ports are actually active on a given device. This uses the full 256-bank
pool (32 possible SDI ports × 8 banks) with zero collision risk, ever, and
is trivial to recall/audit by hand. When reassigning a channel's banks away
from an old ad-hoc allocation, **reset the old banks to default** (Delay =
`0.000000`, Input Main/Backup = the bank's own Identifier) rather than
leaving them stale-but-wired.

### Finding the real output stream M

Via the sender-side route-master CSV, mnemonic `{site}.{device}-TX{ch:02d}`
— see `docs/id-formulas.md` for the formula/shift caveat and
`docs/lessons-learned.md` for why you must cross-check this, not just trust
the first CSV you load.

## Site mnemonic prefixes

Not every device uses the same prefix — observed so far:
- `DCB.IPGxxA` / `DCB.IPGxxB` for some units (e.g. IPG02, IPG04, IPG08,
  IPG10, IPG12)
- `DCR.IPGxxA` / `DCR.IPGxxB` for others (e.g. IPG01, IPG03, IPG07, IPG09,
  IPG11)

Always check which prefix a given device actually uses in the CSV before
building a lookup key — don't assume `DCB.` universally.
