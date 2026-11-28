#!/usr/bin/env python3
"""Diff the device trees (and list FAT members) of two Thor boot images.
usage: adt_diff.py <image-a> <image-b>"""
import os
import runpy, sys
from pathlib import Path
root = Path(os.environ['THOR_ROOT'] + '/thor-out')
previous = runpy.run_path(str(root / 'v212-integrated/package-v212.py'))
decode, sha, k, m, c, api = (previous[n] for n in ('decode', 'sha', 'k', 'm', 'c', 'api'))

def load(img):
    old, fw, sig, id_ = decode(Path(img))
    return old, fw, sig, id_

a, b = load(sys.argv[1]), load(sys.argv[2])
print('members A:', sorted((k_, len(v)) for k_, v in a[0].items()))
print('members B:', sorted((k_, len(v)) for k_, v in b[0].items()))
print('firmware same:', a[1] == b[1], ' signature same:', a[2] == b[2])
ta, tb = api['parse_adt'](a[0][k + '.adt']), api['parse_adt'](b[0][k + '.adt'])
for key in sorted(set(ta) | set(tb)):
    na, nb = ta.get(key), tb.get(key)
    if na is None:
        print('+ node', key, {p: (v[:40].hex() if isinstance(v, (bytes, bytearray)) else v) for p, v in list(nb.items())[:12]}); continue
    if nb is None:
        print('- node', key); continue
    for p in sorted(set(na) | set(nb)):
        va, vb = na.get(p), nb.get(p)
        if va != vb:
            fa = (va[:64].hex() if isinstance(va, (bytes, bytearray)) else va)
            fb = (vb[:64].hex() if isinstance(vb, (bytes, bytearray)) else vb)
            print('~ %s %s: A=%s B=%s' % (key, p, fa, fb))
print('menu args A:', a[0][c][:600])
print('menu args B:', b[0][c][:600])
