#!/usr/bin/env python3
"""Nearest-symbol lookup for kernel addresses.
usage: sym.py <macho> <addr> [<addr> ...]   (addr may be 0x-prefixed hex)
Reads `xcrun nm -n` output from a cache file next to nothing (in $TMPDIR) keyed by mtime."""
import os, subprocess, sys

def load(path):
    cache = os.path.join(os.environ.get('TMPDIR', '/tmp'), 'symcache-%s-%d.txt' % (os.path.basename(path), int(os.path.getmtime(path))))
    if not os.path.exists(cache):
        out = subprocess.run(['xcrun', 'nm', '-n', '-arch', 'arm64', path], capture_output=True, text=True).stdout
        rows = []
        for line in out.splitlines():
            p = line.split()
            if len(p) < 3:
                continue
            try:
                v = int(p[0], 16)
            except ValueError:
                continue
            rows.append((v, p[1], ' '.join(p[2:])))
        rows.sort()
        with open(cache, 'w') as f:
            for r in rows:
                f.write('%x %s %s\n' % r)
    rows = []
    for line in open(cache):
        v, t, name = line.rstrip('\n').split(' ', 2)
        rows.append((int(v, 16), t, name))
    return rows

def main():
    path = sys.argv[1]
    rows = load(path)
    # segments
    segs = []
    for l in subprocess.run(['xcrun', 'otool', '-l', path], capture_output=True, text=True).stdout.splitlines():
        p = l.split()
        if len(p) == 2 and p[0] == 'segname':
            cur = p[1]
        elif len(p) == 2 and p[0] == 'vmaddr' and cur is not None:
            addr = int(p[1], 16)
        elif len(p) == 2 and p[0] == 'vmsize' and cur is not None:
            segs.append((addr, addr + int(p[1], 16), cur))
    addrs = [int(a, 16) for a in sys.argv[2:]]
    lo, hi = min(addrs), max(addrs)
    for a in addrs:
        seg = [s[2] for s in segs if s[0] <= a < s[1]]
        below = [r for r in rows if r[0] <= a]
        above = [r for r in rows if r[0] > a]
        print('0x%016x  seg=%s' % (a, ','.join(seg) or 'NONE'))
        for r in below[-3:]:
            nxt = above[0][0] if above else 0
            print('   <= %016x %s +%-7d %s' % (r[0], r[1], a - r[0], r[2]))
        for r in above[:2]:
            print('    > %016x %s (in %d) %s' % (r[0], r[1], r[0] - a, r[2]))

main()
