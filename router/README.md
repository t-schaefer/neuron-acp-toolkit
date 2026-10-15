# Neuron Audio Router

Graphical replacement for the Neuron "Audio Stream Shuffler" page: route audio
by clicking and dragging instead of typing channel IDs. Works **offline on
`.cbf` exports only**: load an export, route, export a patched copy, re-import
it on the device. It never talks to a Neuron (live access would need ACP2 on
TCP 2072, for which there is no documentation; see `../acp2/README.md`).

## Run

```
python3 server.py            # then open http://localhost:8091/
python3 server.py --host 0.0.0.0 --port 8091   # share on the network
```

Python 3.8+, standard library only. Put `.cbf` exports into `cbf/` or upload
them in the browser (button or drag the file onto the page). Patched files go
to `exports/` as `<name>_routed_<timestamp>.cbf`, with a `.changes.json` log
next to each one. Both folders are git-ignored (device configs).

## Using it

- **Sources** (left): IP In 16ch / 64ch, De-Embedder A1–H4, Delay Banks, MADI In.
  Green = stream enabled. Click a block to see its channels. Click a channel to
  see everywhere it is routed.
- **Destinations** (right): Embedder A1–H4, IP Out 1–144, Delay Banks, MADI Out.
  The slot table shows Main and Backup. Orange = pending change (old value struck
  through), `M≠B` = Main and Backup point at different sources.
- **Write to** Main / Backup / Both decides which field an action edits.
- **Fill**: pick a destination block and a source block, then set start channels
  and count. Or drag a source block onto a destination block.
- **Shuffle**: click a slot, then click source channels. Each click assigns and
  moves the cursor down one slot. You can also drag a single channel onto a slot.
- Undo with the button or Ctrl/Cmd+Z, and revert single rows with ×.
  Unsent changes are kept per file in the browser.
- A purple ring means the block feeds, or uses, the current selection.
- ⚠ on a pending change: the destination stream is disabled or the embedder is off,
  or the source stream is disabled. See `../docs/lessons-learned.md` (IPG04A ch13).

## Safety

- The loaded file is never modified. Every export is a new file.
- Only `Input Main` / `Input Backup` VALUEs are edited (offset splices, reverse order).
- Before writing: every source ID must exist in the file, target objects must be
  writable, Object_Count must still match, and the UTF-16LE header must be intact.
- After writing: the export is parsed again and every change is compared. On a
  mismatch the file is deleted and an error is shown.

This verifies the file, not the routing intent. You still have to check that the
route is the *right* one, e.g. against the route-master CSVs, the way
`../docs/lessons-learned.md` describes.

## Files

- `cbf_model.py`: parses a `.cbf` into sources (`Identifier`) and destinations
  (`Input Main/Backup`) and builds patched text. Ranges are read from the file,
  not assumed (IPG03A/04A: sources 1–4352, 4352 destinations).
- `server.py`: small HTTP server and JSON API (`/api/files`, `/api/model`,
  `/api/upload`, `/api/export`, `/api/download`).
- `dashboard.html`: the UI, in the style of the Fusion dashboard.
