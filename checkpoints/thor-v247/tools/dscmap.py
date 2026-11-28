#!/usr/bin/env python3.13
"""Mappings of a dyld shared cache in the sub-cache file format: every mapping of the main file and of every
sub-cache, read from each file's own header (mappingOffset / mappingCount).

   DSC_MAIN=<main cache file> python3 dscmap.py            print the mappings
   import:  mappings(path) -> [(address, size, fileOffset, flags, maxProt, initProt)]"""
import glob, os, struct

def mappings(path):
    out = []
    with open(path, 'rb') as f:
        hdr = f.read(32)
        if hdr[:10] != b'dyld_v1   ':
            return out
        mappingOffset, mappingCount = struct.unpack_from('<II', hdr, 16)
        for i in range(mappingCount):
            f.seek(mappingOffset + i * 64)
            address, size, fileOffset, flags, maxProt, initProt = struct.unpack('<QQQIII', f.read(36))
            out.append((address, size, fileOffset, flags, maxProt, initProt))
    return out

def all_mappings(main):
    """[(start, end, path, fileoff)] sorted; every mapping of the main file and its sub-caches."""
    rows = []
    for path in sorted(glob.glob(main + '*')):
        if path.endswith(('.a2s', '.symbols', '.atlas')):
            continue
        for address, size, fileOffset, flags, maxProt, initProt in mappings(path):
            if size:
                rows.append((address, address + size, path, fileOffset))
    rows.sort()
    return rows

if __name__ == '__main__':
    main = os.environ['DSC_MAIN']
    for address, size, fileOffset, flags, maxProt, initProt in mappings(main):
        print('%#x %#x %#x flags=%#x prot=%o/%o %s' % (address, size, fileOffset, flags, maxProt, initProt, os.path.basename(main)))
    rows = all_mappings(main)
    print('# entries', len(rows), file=__import__('sys').stderr)
    prev = None
    for s, e, p, off in rows:
        if prev and s != prev:
            print('# gap before %#x (previous ends at %#x)' % (s, prev), file=__import__('sys').stderr)
        prev = e
    print('# last end %#x' % prev, file=__import__('sys').stderr)
