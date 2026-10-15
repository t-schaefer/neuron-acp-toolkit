# .cbf file format

## Encoding

- **UTF-16LE**, with a single BOM (`\xff\xfe`) at the very start of the file.
- When Python decodes the raw bytes with `.decode('utf-16-le')`, the BOM
  shows up as a literal `﻿` character at the start of the resulting
  string — it is **part of the text**, not something Python strips for you.
- **Writing back**: `content.encode('utf-16-le')` is correct and sufficient.
  Do **not** manually prepend a BOM byte sequence — the `﻿` already in
  the string encodes to the BOM bytes on its own. Prepending one yourself
  produces a double-BOM file that may not re-import cleanly.
- Verify after writing: `raw[:4] == b'\xff\xfe<\x00'` (the `<` of
  `<SYSTEM_DEVICE_BACKUP>` or `<DEVICE`, little-endian UTF-16).

## Structure

Root element: `<SYSTEM_DEVICE_BACKUP>` wrapping one or more
`<DEVICE IP="..." DEVICE="SHPRM1|BRIDGE|CONVERT IP" VERSION="...">` blocks.

- A **combined backup** (exported from the management system / taken some
  time ago) typically contains multiple `<DEVICE>` blocks, e.g. an `SHPRM1`
  wrapper device plus the actual `BRIDGE` device.
- A **live export straight from the device itself** may contain **only** the
  relevant `<DEVICE DEVICE="BRIDGE" ...>` block, no `SHPRM1` wrapper. Don't
  assume the wrapper is there — locate the `BRIDGE` (or `CONVERT IP`) device
  block by searching for `DEVICE="BRIDGE"` and walking outward to the
  enclosing `<DEVICE ...>` tag, rather than assuming a fixed structure.
- Inside a `<DEVICE>` block: a tree of `<GROUP NAME="...">...</GROUP>` and
  `<OBJECT NAME="..." VALUE="..." .../>` elements, arbitrarily nested.
- `VERSION="..."` on the `<DEVICE>` tag is the device firmware version
  (e.g. `7.0.2` vs `7.0.3`). A firmware bump between an old backup and a
  fresh live export does **not** necessarily change the object tree
  structure or count — verified this empirically on IPG03A (7.0.2 → 7.0.3,
  identical `Object_Count` and identical object-name sequence). Don't assume
  a version bump invalidates positional/structural assumptions, but don't
  assume it's safe either — check `Object_Count` and the object-name
  sequence before trusting a positional diff across two files.

## Top-level groups inside a Bridge device

- `INPUT` — `SDI`, `IP`, `MADI` sub-groups (physical/network inputs)
- `PROCESSING VIDEO` — `PATH A`..`PATH H`, each containing `CHANNEL 01`..
  `CHANNEL 04` (so 8 paths × 4 channels = 32 channels total, numbered
  sequentially 1–32 in path/channel order). Each channel has (among others)
  an `EMBEDDER` sub-group with 16 `AUDIO CHANNEL` slots.
- `PROCESSING AUDIO` — `AUDIO DELAY` containing 256 `BANK` groups, each with
  2 `AUDIO CHANNEL` sub-groups (so 512 "bank slots" total, 16 slots per
  8-bank group — see `docs/routing-architecture.md`).
- `OUTPUT` — **easy to miss, and the single biggest source of a real bug so
  far (see lessons-learned)**: three branches —
  - `SDI` — 32 `CHANNEL` groups (physical SDI outputs)
  - `IP` — `VIDEO`, `AUDIO`, `ANCILLARY DATA`. The `AUDIO` branch has up to
    144 `STREAM` groups (`STREAM 1`..`STREAM 144`), each with its own
    `AUDIO CHANNELS` group (normally 16 channel slots; **streams 129–144
    observed with 64 slots instead of 16** on IPG04A — not fully explained,
    handle generically by accepting either 16 or 64 and only ever touching
    the first 16), plus `Channels`, `Take`, `Stream` (Disabled/Enabled —
    **check this before wiring anything into a stream**), `Media Type`,
    `SDP File` (contains an `s=` line with a human-readable stream label —
    useful for sanity-checking what a stream actually is), `LEG 1`/`LEG 2`
    (redundant network legs with their own destination IP/port).
  - `MADI`

## Structural integrity check

Every `<DEVICE>` block has a `Cerebrum_Device_Info` group containing an
`Object_Count` object (e.g. `VALUE="39131"`). This must exactly equal the
number of `<OBJECT NAME=` occurrences within that device's block:

```python
import re
m = re.search(r'Object_Count" VALUE="(\d+)"', block)
stated = int(m.group(1))
actual = len(re.findall(r'<OBJECT NAME=', block))
assert stated == actual
```

Run this after every patch, before declaring a file done. It won't catch a
*wrong* edit, but it reliably catches a *structurally broken* one (an edit
that accidentally deleted/duplicated an element).

## Editing approach

Never edit the string in place as you go — collect `(start, end, new_value)`
tuples for every change, then apply them **sorted by descending start
offset** so each splice doesn't invalidate the offsets of edits not yet
applied:

```python
edits.sort(key=lambda e: -e[0])
new_text = text
for start, end, newval in edits:
    new_text = new_text[:start] + newval + new_text[end:]
```

See `tools/build_engine.py` for the tree-navigation helpers that locate
`(start, end)` ranges for a given `<OBJECT NAME="...">`'s `VALUE="..."`.
