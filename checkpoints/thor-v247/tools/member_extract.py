#!/opt/homebrew/bin/python3.13
"""Extract FAT members of a Thor boot image into a directory.
usage: member_extract.py <image> <outdir>"""
import os
import runpy, sys
from pathlib import Path
root = Path(os.environ['THOR_ROOT'] + '/thor-out')
previous = runpy.run_path(str(root / 'v212-integrated/package-v212.py'))
decode, sha, k, m, c = (previous[n] for n in ('decode', 'sha', 'k', 'm', 'c'))
old, fw, sig, id_ = decode(Path(sys.argv[1]))
out = Path(sys.argv[2]); out.mkdir(parents=True, exist_ok=True)
for name, data in old.items():
    p = out / name.lstrip('/')
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    print('%8d %s' % (len(data), p))
print('firmware bytes', len(fw), 'signature bytes', len(sig), 'id', id_)
