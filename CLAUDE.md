# Neuron IPG Toolkit — Handover

Working context for EVS Neuron IPG Gateway (Bridge / Convert-IP) backup-file
patching and, eventually, live configuration over ACP2 — Premier League
Studios (PLS), London, Qvest project (UK.Qvest-PLS).

**Read this file first, then the `docs/` files relevant to the task at hand.**
Don't start re-deriving ID formulas or re-discovering the CSV staleness trap —
it's all written down. If something here conflicts with what you observe in
a real file, trust the file and update these docs.

## What this project is

Each Neuron IPG channel needs audio-delay compensation so that audio stays
in sync with video after the IP/SDI conversion:
- **19.5 ms** for IP-sourced channels (Main/Backup Input = "Stream N")
- **18.0 ms** for SDI-sourced channels (Main/Backup Input = "SDI N")
  — this was **16.8 ms** earlier in the project; some devices still have the
  old value live on them (see `docs/device-status.md`). Always check which
  value is currently asked for before patching.

The work happens by editing `.cbf` backup files (UTF-16LE, GROUP/OBJECT XML-
like tree) offline, then the user re-imports them into the physical device.
See `docs/cbf-format.md` for the file format and `docs/id-formulas.md` /
`docs/routing-architecture.md` for what actually needs wiring and why.

**Read `docs/lessons-learned.md` before trusting any lookup CSV or claiming
something is "fully verified."** We shipped a real, undetected bug once
(ch13 on IPG04A routed to the wrong, disabled output stream) because a CSV
row was stale and we didn't cross-check it. The user caught it in the field.
Don't repeat that class of mistake.

## Directory layout

- `docs/cbf-format.md` — file format, encoding, structural integrity check
- `docs/id-formulas.md` — the ID formulas (de-embedder, stream, bank) and the
  critical warning that the "shift" between a channel's label and its real
  stream number is **device-specific** and must be verified per device, never
  assumed
- `docs/routing-architecture.md` — the full signal path for both channel
  types, the bank-allocation scheme, and the OUTPUT>IP>AUDIO gap that affects
  every device until explicitly fixed
- `docs/lessons-learned.md` — mistakes made, how they were caught, and the
  verification habits that came out of them
- `docs/device-status.md` — per-device status: what's done, what's pending,
  device-specific quirks (site mnemonic prefix, label/stream shift, firmware
  version)
- `tools/` — reusable Python: `cbf_io.py` (read/write), `build_engine.py`
  (tree navigation + the ID formulas as code), `csv_lookup.py` (building
  fresh TX/RX lookups from the route-master CSVs — never trust a pickled/
  cached lookup across sessions, see lessons-learned)
- `acp2/` — skeleton for a live-configuration client over EVS's ACP2 protocol,
  meant to eventually replace the "patch file → manual re-import" workflow.
  Not functional yet — needs the EVS ACP2 SDK/protocol spec, which this
  session doesn't have. See `acp2/README.md`.

## Working method (so far)

1. Get a **live** `.cbf` export directly from the device (not an old combined
   backup) whenever possible — see `docs/lessons-learned.md` on why.
2. Parse with `tools/build_engine.py` (`find_group`, `get_all_subgroups`,
   `parse_input_obj`), never regex the whole file blind.
3. Build an `edits` list of `(start, end, new_value)` tuples and apply them
   **in reverse offset order** so earlier offsets stay valid.
4. After every patch: check `Object_Count` against the actual
   `<OBJECT NAME=` count in the `<DEVICE>` block (see `docs/cbf-format.md`).
5. Before declaring anything "verified" or "done": check the *output* side
   too (OUTPUT>IP>AUDIO>STREAM), check the stream's own `Stream`
   (Enabled/Disabled) flag, and cross-check any CSV-derived number against
   at least one independent source (another device's equivalent row, the
   device's own already-live wiring, or a fresh CSV upload). See
   `docs/lessons-learned.md` — this is not optional, it's how a real bug was
   found last time.

## Open work

See `docs/device-status.md` for the live list. As of 2026-10-15:
- **IPG04A**: done, delivered (`IPG4A.cbf`), includes port-based bank scheme,
  Ch32 backup fix, full IP-output routing for all 20 SDI channels, Ch13
  corrected to Stream 49.
- **IPG03A**: compared against original backup, not yet patched. Needs: bump
  184 banks from 16.8 → 18.0 ms, and build the OUTPUT>IP>AUDIO routing
  (same gap as IPG04A had) — the input side (Stream Delay 19.5 ms, SDI→bank
  wiring) was already done manually on the live device and looks correct.
- All other IPG units (01A/B, 02A/B, 04B, 07–12 A/B): not yet started this
  round.
