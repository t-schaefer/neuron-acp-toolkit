# Lessons learned — read before trusting a lookup or claiming "verified"

## The big one: ch13 on IPG04A was routed to the wrong, disabled stream

What happened: the sender-side CSV (SOURCES.csv) row for
`DCB.IPG04A-TX13` listed four `ATX-nn` references as `129, 50, 51, 52` —
**not a consecutive run of 4**, unlike every other TX row on that device
(which all show clean consecutive quads, e.g. TX07 → 25,26,27,28). Taking
"the first listed" (the established convention — only the first of the
four sub-stream references is the one that's actually used) gave Stream
129. The fix was built and delivered against Stream 129.

The user reported, from the real device, that ch13 audio still wasn't
coming out over IP. Direct inspection of Stream 129 inside the live file
showed:
- `Stream` object = `Disabled` (every genuinely active output stream
  checked had this = `Enabled`)
- a 64-channel-wide `AUDIO CHANNELS` group (all other normal streams: 16)
- an `SDP File` with `s=ATX-129` and a label suggesting a
  "HITOMI|GENERATOR" reference/sync signal, not program audio

Meanwhile `(ch-1)*4+1` for ch13 gives 49 — which **was already correct**:
Stream 49 was `Enabled`, had the standard 16-channel layout, and — most
tellingly — its first 4 audio-channel slots already pointed at the exact
bank IDs (41/42) the user had *manually, partially* wired by hand earlier
in the project, confirming 49 was always the real target. A second,
independently-uploaded copy of the sender CSV confirmed `ATX-49,50,51,52`
cleanly (consecutive, no anomaly) for the same TX13 row — the first CSV we
had loaded was stale/bad for that one row.

**Takeaways, as hard rules for this project going forward:**

1. **A CSV row that breaks the pattern every other row follows is a signal,
   not noise.** Don't silently take "the first value" and move on — stop
   and investigate why this one row looks different.
2. **Check the target's own `Stream` Enabled/Disabled flag before wiring
   anything into it.** A disabled output stream is a near-certain sign of
   a wrong target, independent of any CSV.
3. **Cross-check a CSV-derived number against a second source whenever
   the stakes are "the user will test this on a live broadcast chain."**
   Good second sources, in order of reliability: (a) does the live device
   already have *something* wired there that matches a different,
   independently-plausible target (e.g. the user's own earlier manual
   wiring)? (b) does a freshly-pulled copy of the same CSV agree? (c) does
   the formula that holds for every other channel on this device agree?
4. **"Fully verified" must mean the output side too**, not just that the
   edits applied cleanly and `Object_Count` matched. Structural integrity
   (`Object_Count`) only proves the file wasn't corrupted by the edit — it
   says nothing about whether the edit was the *right* edit.
5. When the user pushes back with a one-line skeptical question ("is that
   really true?" / "it's still not working") — that is real-world signal,
   not impatience to be smoothed over. Re-open the investigation from raw
   data, don't just re-assert the earlier conclusion more confidently.

## Hand-derived formulas: verify against the actual file, always

Early in the project, `bank_own_id` was guessed as `3583 + 2*bank_num +
slot`. It was off by one bank's worth of offset (predicted 3602 for
bank9/slot1, actual file value was 3601). The fix: read the `Identifier`
value directly out of the file for a few sample banks and derive/confirm
the formula from that, rather than deriving it purely from the spec text
or from a different device. **Treat every formula in `id-formulas.md` as a
checked hypothesis for the devices it was checked on — re-verify on a new
device family before relying on it blindly.**

## Combined backups vs. live exports

A live export pulled directly from the device may contain *only* the
relevant `<DEVICE DEVICE="BRIDGE">` block — no `SHPRM1` wrapper, unlike an
older combined backup. Don't assume the wrapper tag is present; search for
`DEVICE="BRIDGE"` directly (see `docs/cbf-format.md`).

**Prefer a live export over an old backup whenever the user can get one.**
On IPG03A, comparing an old backup against a fresh live export surfaced a
large amount of configuration drift: the user had already manually
corrected several embedder-wiring bugs, already applied Stream Delay
(19.5 ms, correctly) to all IP-sourced channels, and already wired all 23
SDI-sourced channels through 8-bank groups with Delay = 16.8 ms (the old,
pre-correction value) directly on the device's own control UI — completely
outside this file-patching workflow. If we had patched the stale backup
directly, we would have **overwritten or silently diverged from** real,
already-correct, already-deployed configuration. **Always diff a fresh live
export against whatever baseline you're about to patch before building
anything, even if nobody asked for a comparison** — the device may have
moved since the last known-good file.

## A patch script that only touched the first bank of each group

When told "18ms for SDI", an early build script updated only the *first*
bank in each channel's 8-bank group (`bank_starts` dict, one value per
channel) instead of the full `range(b0, b0+8)`. Caught by an independent
`awk` aggregation over all bank delay values showing a mix of old and new
values within what should have been one uniform group. **When patching N
repeated sub-elements per unit, explicitly iterate all N — don't rely on a
single "starting" index and an implicit loop that may not actually cover
the full range.**

## Verification habits worth keeping permanently

- After every patch: `Object_Count` check (see `docs/cbf-format.md`).
- After every patch touching Case B channels: assert SDI-out wiring ==
  IP-out wiring == IP-out backup wiring, for every patched channel, and
  print the result rather than asserting silently — a printed per-channel
  table is what let the user (and us) actually see the ch13 anomaly stand
  out against 19 clean rows.
- Before declaring a device "done": check the `Stream` Enabled/Disabled
  flag for every output stream newly wired into.
- Keep CSV lookups built fresh per-session from whatever file was most
  recently supplied — don't reuse a pickled lookup across devices/sessions
  without re-validating at least a sample against the live file, given we
  now have direct proof that a stale CSV snapshot can exist and look
  perfectly plausible until checked.
