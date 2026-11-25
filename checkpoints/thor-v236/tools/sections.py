#!/usr/bin/env python3
"""Print the Mach-O sections that contain the given vm addresses.
usage: sections.py <macho> <addr> [<addr>...]"""
import subprocess, sys
path = sys.argv[1]
addrs = [int(a, 16) for a in sys.argv[2:]]
sects = []
cur = {}
for l in subprocess.run(['xcrun', 'otool', '-l', path], capture_output=True, text=True).stdout.splitlines():
    p = l.split()
    if len(p) == 2 and p[0] in ('sectname', 'segname', 'addr', 'size', 'offset', 'flags', 'reserved1', 'reserved2'):
        cur[p[0]] = p[1]
    elif l.startswith('Section') and cur:
        sects.append(cur); cur = {}
    elif len(p) == 2 and p[0] == 'cmd' and cur:
        sects.append(cur); cur = {}
if cur:
    sects.append(cur)
def s(x, n):
    return '%x' % int(x, 16) if x else '0'
for a in addrs:
    hit = [x for x in sects if 'addr' in x and int(x['addr'], 16) <= a < int(x['addr'], 16) + int(x['size'], 16)]
    print('0x%016x' % a)
    for x in hit:
        print('    %-18s %-24s addr=0x%s size=0x%s off=%s flags=%s  (+0x%x)' % (x.get('segname'), x.get('sectname'), s(x['addr'], 0), s(x['size'], 0), x.get('offset'), x.get('flags'), a - int(x['addr'], 16)))
    if not hit:
        print('    (no section)')
