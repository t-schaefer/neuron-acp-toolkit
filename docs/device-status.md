# Device status

Target values (current, as of 2026-10-15): **19.5 ms** IP-sourced channels,
**18.0 ms** SDI-sourced channels (this was 16.8 ms earlier in the project —
watch for devices that still carry the old value, see below).

| Device | Site prefix | Firmware (last seen) | Label/stream shift | Status |
|---|---|---|---|---|
| IPG04A | `DCB.` | — | none (real_stream == label N) | **Done, delivered.** Port-based bank scheme, Ch32 backup-wiring fix, full OUTPUT>IP>AUDIO routing for all 20 SDI channels, Ch13 corrected to Stream 49 (not 129 — see lessons-learned). |
| IPG04B | `DCB.` | — | not yet characterized | Touched earlier in the project (`ipg04b_patched.txt` exists) — status before this round's port-scheme/output-routing work not reconfirmed. Re-audit before trusting. |
| IPG03A | `DCR.` | 7.0.2 (old backup) → 7.0.3 (live, 2026-10-14) | `(N-1)*4+1` for most channels; **2 exceptions** (label 15→stream 53, label 30→stream 113) that don't follow the formula — confirmed correct via live `Stream Delay` already set | **Compared, not yet patched.** Live device already has: Stream Delay 19.5ms on all 10 real IP streams (correct, no action needed); all 23 SDI channels wired through 8-bank groups with Delay = **16.8ms** (needs bump to 18.0). OUTPUT>IP>AUDIO>STREAM wiring is **untouched**, byte-identical to the old backup — same gap class as IPG04A had, needs building from scratch. |
| IPG10B | — | — | not yet characterized here (earlier project note: "no 4x shift" was said to NOT apply the way IPG04A does — re-verify per id-formulas.md's warning rather than trusting a half-remembered note) | Touched earlier (`ipg10b.txt`, `ipg10b_patched.txt` exist) — re-audit before trusting. |
| IPG01A/B, IPG02A/B, IPG07–12 A/B (remaining) | mixed `DCR.`/`DCB.` — see routing-architecture.md | — | unknown | Not started this round. |

## Immediate next actions

1. **IPG03A**: bump all 184 banks (23 channels × 8) from `16.799999` to
   `18.000000`; build OUTPUT>IP>AUDIO>STREAM routing for all 23 SDI
   channels (mirror the IPG04A script, but verify every TX lookup against
   a fresh CSV + the `Stream` Enabled flag per lessons-learned, don't just
   trust the formula for any channel you haven't individually checked).
2. Re-audit IPG04B and IPG10B against a **fresh live export** (not the old
   `*_patched.txt` files sitting in scratch) before assuming prior work on
   them is complete or correct under the current (18.0ms, not 16.8ms)
   target.
3. Characterize the remaining untouched devices one at a time, same method:
   pull a live export, diff against whatever baseline exists, map the
   label/stream shift per `id-formulas.md`, then patch.

## Route-master CSV files in use

Two canonical files, both semicolon-delimited CSV, `utf-8-sig`:
- **Sources / TX** (sender side, `ATX-nn` references) — most recent upload:
  user-provided file titled "der neuron ip sender" (2026-10-14).
- **Destinations / RX** (receiver side, `ARX-nn` references) — most recent
  upload: user-provided file titled "streams receivers pro jeweiligen
  neuron empfänger kanal" (2026-10-14).

**Treat any older cached/pickled lookup built from a previous CSV upload as
suspect** (see lessons-learned) — rebuild from the freshest CSV the user
has supplied whenever starting work on a new device, and keep using
`tools/csv_lookup.py` rather than a stale pickle file.
