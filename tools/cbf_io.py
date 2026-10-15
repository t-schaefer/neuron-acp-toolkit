"""
Read/write .cbf files. See ../docs/cbf-format.md.
"""


def read_cbf(path):
    """Read a .cbf file and return its text content (UTF-16LE decoded,
    including the leading \\ufeff from the BOM — that's expected and
    correct, see docs/cbf-format.md)."""
    with open(path, 'rb') as f:
        raw = f.read()
    if raw[:2] != b'\xff\xfe':
        raise ValueError(f'{path}: missing expected UTF-16LE BOM at start')
    return raw.decode('utf-16-le')


def write_cbf(path, text):
    """Write text content back out as a .cbf file. Do NOT prepend a BOM
    manually -- the leading \\ufeff already in `text` (from read_cbf, or
    preserved through your edits) encodes to the BOM bytes on its own."""
    if not text.startswith('﻿'):
        raise ValueError(
            'text does not start with the expected \\ufeff BOM marker -- '
            'did you strip it accidentally? Do not add one manually, fix '
            'the source text instead.'
        )
    raw = text.encode('utf-16-le')
    with open(path, 'wb') as f:
        f.write(raw)
    assert raw[:4] == b'\xff\xfe<\x00', 'unexpected header after encode'
    return len(raw)
