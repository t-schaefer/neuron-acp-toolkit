# ID formulas

All three are **hardware-fixed, read-only numbering schemes** — the device
itself assigns these IDs; nothing patches them. What gets patched is which
ID a given `Input Main` / `Input Backup` *points at*.

```python
def deembed_id(ch, k):
    """The k-th (k=1..16) mono audio channel physically de-embedded from
    SDI channel `ch`'s own SDI input. Fixed by hardware."""
    return 3072 + (ch - 1) * 16 + k

def stream_id(streamN, k):
    """The k-th (k=1..16) mono audio channel de-interleaved from IP input
    stream number `streamN` (1..32 on the input side; some devices expose
    higher numbers too, e.g. IPG03A has input streams up to at least 125 —
    don't assume the input side is capped at 32)."""
    return (streamN - 1) * 16 + k

def bank_own_id(bank_num, slot):
    """A delay bank's own identifier (slot=1 or 2 — each BANK group has 2
    AUDIO CHANNEL sub-groups). This is the value a channel's own EMBEDDER
    wires to once that bank is delaying its audio.
    VERIFIED AGAINST ACTUAL FILE VALUES — do not re-derive by hand, see
    lessons-learned for the hand-derived-formula bug this already caused."""
    return 3584 + 2 * (bank_num - 1) + slot
```

`bank_own_id` is also directly readable from the file itself: every `BANK`
group has an `Identifier` object whose `VALUE` is exactly this number —
**prefer reading it from the file over computing it**, the formula above is
for reference/sanity-checking, not as the primary source of truth.

## The critical gotcha: label number ≠ real stream number

A channel's `Main Input` / `Backup Input` ENUM shows a human label like
`"Stream 6"` or `"SDI 13"`. For **SDI**, the label number *is* the physical
SDI port number and *is* the channel number used in `deembed_id` — no
ambiguity there.

For **Stream N** (IP-sourced), the label number `N` is **just a dropdown
index** — it is *not* guaranteed to equal the real stream number used in
`stream_id()`. The real stream number has to be looked up via the site's
route-master CSVs (destinations/RX for input side, sources/TX for output
side — see `docs/routing-architecture.md`), and **the relationship between
label N and real stream number is device-specific**:

- **IPG04A**: real_stream == N exactly, verified across all 14 IP-sourced
  channels on that device. No shift.
- **IPG03A**: real_stream == (N − 1) × 4 + 1 for most channels (e.g. label
  "Stream 2" → real stream 5), **but not all** — two channels (label 15 →
  real stream 53, label 30 → real stream 113) don't follow that formula and
  were only confirmed correct by checking which stream actually has
  `Stream Delay` already set to 19.5 ms on the live device, and cross-
  referencing the CSV. **Never assume a formula holds for every channel on
  a device — verify each one, or at least spot-check beyond the first few.**

**Bottom line: always derive the real stream number per channel, per
device, from the actual route-master CSV (or, if already wired, from the
live file itself) — never assume a pattern seen on one device, or even on
most channels of one device, applies everywhere.**

## Output-side formula (sender/TX)

For the *output* stream (`OUTPUT > IP > AUDIO > STREAM M`) that actually
transmits a given SDI-sourced channel's audio over IP, the mnemonic
convention observed on IPG04A is:

```
M = (ch - 1) * 4 + 1
```

...where `ch` is the channel number, matching the sender-side CSV mnemonic
`{site}.{device}-TX{ch:02d}`, whose first-of-four `ATX-nn` reference
normally equals `M`. **This formula is a convenience, not a guarantee** —
see lessons-learned for the one real case (IPG04A ch13) where the CSV row
for a given TX mnemonic was anomalous/stale and this formula (confirmed via
a second, fresher CSV source and the device's own `Stream`
Enabled/Disabled flag) was what caught it.
