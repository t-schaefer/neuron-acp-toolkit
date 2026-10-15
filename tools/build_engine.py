"""
Tree-navigation helpers for .cbf (GROUP/OBJECT XML-like) files, plus the
hardware ID formulas. See ../docs/cbf-format.md and ../docs/id-formulas.md
for what these mean and the caveats around them (especially: the
label/stream shift is device-specific, do not assume these formulas transfer
unchecked to a new device family).
"""
import re


def find_group(text, name, start=0):
    """Find a <GROUP NAME="name"> ... matching </GROUP>, searching from `start`.
    Returns (body_start, body_end, tag_start, tag_end) offsets into text, or
    raises if not found / unbalanced."""
    pat = re.compile(r'<GROUP NAME="' + re.escape(name) + r'"[^>]*>')
    m = pat.search(text, start)
    if not m:
        raise ValueError(f'GROUP "{name}" not found from offset {start}')
    depth = 1
    pos = m.end()
    open_re = re.compile(r'<GROUP\b')
    close_re = re.compile(r'</GROUP>')
    while depth > 0:
        o = open_re.search(text, pos)
        c = close_re.search(text, pos)
        if c is None:
            raise ValueError("unbalanced groups")
        if o is not None and o.start() < c.start():
            depth += 1
            pos = o.end()
        else:
            depth -= 1
            pos = c.end()
    body_start = m.end()
    body_end = pos - len('</GROUP>')
    return (body_start, body_end, m.start(), pos)


def get_all_subgroups(text, body_start, body_end):
    """Return [(name, b_start, b_end, tag_start, tag_end), ...] for every
    immediate child GROUP within [body_start, body_end)."""
    sub = text[body_start:body_end]
    results = []
    pos = 0
    tag_pat = re.compile(r'<GROUP NAME="([^"]*)"[^>]*>')
    open_re = re.compile(r'<GROUP\b')
    close_re = re.compile(r'</GROUP>')
    while True:
        m = tag_pat.search(sub, pos)
        if not m:
            break
        name = m.group(1)
        depth = 1
        p = m.end()
        while depth > 0:
            o = open_re.search(sub, p)
            c = close_re.search(sub, p)
            if c is None:
                raise ValueError("unbalanced")
            if o is not None and o.start() < c.start():
                depth += 1
                p = o.end()
            else:
                depth -= 1
                p = c.end()
        b_start = m.end()
        b_end = p - len('</GROUP>')
        results.append((name, body_start + b_start, body_start + b_end,
                         body_start + m.start(), body_start + p))
        pos = p
    return results


def get_obj(text, bs, be, name):
    """Find a single <OBJECT NAME="name" .../> within [bs,be). Returns
    (start, end, raw_tag_text) or None."""
    sub = text[bs:be]
    m = re.search(r'<OBJECT NAME="' + re.escape(name) + r'"[^>]*/?>', sub)
    if not m:
        return None
    return (bs + m.start(), bs + m.end(), sub[m.start():m.end()])


def obj_value(obj_tuple):
    """Extract the VALUE="..." string from a get_obj() result."""
    m = re.search(r'VALUE="([^"]*)"', obj_tuple[2])
    return m.group(1)


def make_set_value(edits_list):
    """Returns a set_value(obj_tuple, newval) closure that appends a
    (start, end, newval) edit (for the VALUE="..." span only) to edits_list."""
    def set_value(obj_tuple, newval):
        ostart, oend, otext = obj_tuple
        m = re.search(r'VALUE="([^"]*)"', otext)
        vstart = ostart + m.start(1)
        vend = ostart + m.end(1)
        edits_list.append((vstart, vend, newval))
    return set_value


def apply_edits(text, edits):
    """Apply a list of (start, end, newval) edits to text, in any order —
    sorts descending by start internally so offsets stay valid."""
    edits = sorted(edits, key=lambda e: -e[0])
    for start, end, newval in edits:
        text = text[:start] + newval + text[end:]
    return text


def check_object_count(device_block_text):
    """Returns (stated, actual, ok:bool) for the Object_Count structural
    integrity check. Run this after every patch."""
    m = re.search(r'Object_Count" VALUE="(\d+)"', device_block_text)
    stated = int(m.group(1))
    actual = len(re.findall(r'<OBJECT NAME=', device_block_text))
    return stated, actual, stated == actual


def find_device_block(text, device_type):
    """Locate the <DEVICE ... DEVICE="device_type" ...> ... </DEVICE> block
    (e.g. device_type='BRIDGE'). Works whether or not an SHPRM1 wrapper
    device is present elsewhere in the file (see docs/cbf-format.md)."""
    pos = text.find(f'DEVICE="{device_type}"')
    if pos == -1:
        raise ValueError(f'no DEVICE="{device_type}" found')
    s = text.rfind('<DEVICE', 0, pos + 1)
    e = text.find('</DEVICE>', pos) + len('</DEVICE>')
    return s, e


# --- hardware ID formulas (see docs/id-formulas.md for caveats) ---

def deembed_id(ch, k):
    """k-th (1..16) mono audio channel de-embedded from SDI channel ch's
    own SDI input. Fixed by hardware."""
    return 3072 + (ch - 1) * 16 + k


def stream_id(streamN, k):
    """k-th (1..16) mono audio channel de-interleaved from IP input stream
    streamN. streamN here is the REAL stream number — see
    docs/id-formulas.md on how to derive it, it is NOT always equal to the
    channel's Main/Backup Input dropdown label number."""
    return (streamN - 1) * 16 + k


def bank_own_id(bank_num, slot):
    """A delay bank's own Identifier (slot=1 or 2). Prefer reading this
    directly from the file's <OBJECT NAME="Identifier"> where possible;
    this formula is for reference/sanity-checking only."""
    return 3584 + 2 * (bank_num - 1) + slot


def port_bank_range(sdi_port_number):
    """Fixed port-based bank allocation: SDI port N always uses banks
    (N-1)*8+1 .. N*8 (inclusive), regardless of which ports are active."""
    b0 = (sdi_port_number - 1) * 8 + 1
    return b0, b0 + 7


def parse_input_obj(obj_tag_text):
    """Given a Main/Backup Input OBJECT tag's raw text, return
    (value:int, label:str, kind, num):
      kind in {'SDI', 'Stream', 'Pattern', 'OTHER'}
      num = the port/stream number parsed out of the label (None for
            Pattern/OTHER)
    """
    mval = re.search(r'VALUE="(-?\d+)"', obj_tag_text)
    menum = re.search(r'ENUMS="([^"]*)"', obj_tag_text)
    value = int(mval.group(1))
    enums = menum.group(1).split(',')
    label = enums[value]
    if label.startswith('SDI '):
        return value, label, 'SDI', int(label.split('SDI ')[1])
    elif label.startswith('Stream '):
        return value, label, 'Stream', int(label.split('Stream ')[1])
    elif label == 'Pattern':
        return value, label, 'Pattern', None
    else:
        return value, label, 'OTHER', None


def all_channels(text, pv_bs, pv_be):
    """Walk PROCESSING VIDEO > PATH * > CHANNEL * in order and return
    {channel_number: (body_start, body_end)} for channels 1..32."""
    paths = get_all_subgroups(text, pv_bs, pv_be)
    ch_num = 0
    ch_info = {}
    for pname, pbs, pbe, *_ in paths:
        channels = get_all_subgroups(text, pbs, pbe)
        for cname, cbs, cbe, *_ in channels:
            ch_num += 1
            ch_info[ch_num] = (cbs, cbe)
    return ch_info
