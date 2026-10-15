"""
Build TX (sources/ATX) and RX (destinations/ARX) mnemonic -> real stream
number lookups from the site's route-master CSVs.

See ../docs/lessons-learned.md: build these fresh from the most recent CSV
upload every time you start work on a new device. Do not cache/pickle a
lookup across sessions without re-validating a sample against the live
device file -- we have direct proof a CSV row can be stale while every
other row in the same file looks fine.

File format (semicolon-delimited, utf-8-sig):
  _ID; _Mnemonic; _Alternate Mnemonic 1..6,8; _TL Inhibit; (_TL Group for
  destinations); _Level ID 1..15

For a TX (sources) row: Level ID 1 = VTX (video), Level ID 2 = DTX
(ancillary/data), Level ID 3-6 = ATX references (normally 4 consecutive
values -- see docs/id-formulas.md and docs/lessons-learned.md for the one
confirmed case where a row broke that pattern).

For an RX (destinations) row: similarly VRX / DRX / ARX.
"""
import csv
import re

ATX_RE = re.compile(r'ATX-(\d+)')
ARX_RE = re.compile(r'ARX-(\d+)')


def _first_ref(row, pattern):
    """Return the first ATX-nn / ARX-nn integer found anywhere in the row's
    Level ID columns, or None."""
    for cell in row:
        m = pattern.search(cell)
        if m:
            return int(m.group(1))
    return None


def _all_refs(row, pattern):
    """Return every ATX-nn / ARX-nn integer found in the row, in column
    order. Use this (not _first_ref) when you need to check whether the
    row's references form a clean consecutive run -- see lessons-learned."""
    return [int(m.group(1)) for cell in row for m in pattern.finditer(cell)]


def load_sources_csv(path):
    """Returns {mnemonic: first_ATX_number} for every row with a mnemonic
    and at least one ATX reference. mnemonic is column 2 (_Mnemonic), e.g.
    'DCB.IPG04A-TX13'."""
    out = {}
    with open(path, encoding='utf-8-sig', newline='') as f:
        for row in csv.reader(f, delimiter=';'):
            if len(row) < 2 or not row[1]:
                continue
            mnemonic = row[1]
            ref = _first_ref(row, ATX_RE)
            if ref is not None:
                out[mnemonic] = ref
    return out


def load_destinations_csv(path):
    """Returns {mnemonic: first_ARX_number} for every row with a mnemonic
    and at least one ARX reference. mnemonic is column 2 (_Mnemonic), e.g.
    'DCB.IPG04A-RX13'."""
    out = {}
    with open(path, encoding='utf-8-sig', newline='') as f:
        for row in csv.reader(f, delimiter=';'):
            if len(row) < 2 or not row[1]:
                continue
            mnemonic = row[1]
            ref = _first_ref(row, ARX_RE)
            if ref is not None:
                out[mnemonic] = ref
    return out


def check_consecutive(path, mnemonic, kind='ATX'):
    """Sanity-check helper: for a given mnemonic, print/return whether its
    Level-ID references form a clean consecutive run of 4. Use this before
    trusting a lookup for anything that will go live -- see
    docs/lessons-learned.md (this is exactly the check that caught the
    IPG04A ch13 bug)."""
    pattern = ATX_RE if kind == 'ATX' else ARX_RE
    with open(path, encoding='utf-8-sig', newline='') as f:
        for row in csv.reader(f, delimiter=';'):
            if len(row) >= 2 and row[1] == mnemonic:
                refs = _all_refs(row, pattern)
                consecutive = refs == list(range(refs[0], refs[0] + len(refs))) if refs else None
                return {'mnemonic': mnemonic, 'refs': refs, 'consecutive': consecutive, 'row': row}
    return None


if __name__ == '__main__':
    import sys
    if len(sys.argv) != 3:
        print('usage: python csv_lookup.py <sources_or_destinations.csv> <mnemonic>')
        sys.exit(1)
    path, mnemonic = sys.argv[1], sys.argv[2]
    kind = 'ATX' if '-TX' in mnemonic else 'ARX'
    result = check_consecutive(path, mnemonic, kind)
    print(result)
