#!/usr/bin/env python3
"""Dump the __init_entry_set startup table of a kernel Mach-O.
usage: startup_entries.py <macho> [subsystem-filter]"""
import struct, subprocess, sys
path = sys.argv[1]
want = sys.argv[2] if len(sys.argv) > 2 else None
SUBS = ['NONE', 'TUNABLES', 'TIMEOUTS', 'LOCKS', 'KPRINTF', 'PMAP_STEAL', 'KMEM', 'ZALLOC', 'KTRACE', 'PERCPU', 'EVENT',
        'CODESIGNING', 'OSLOG', 'MACH_IPC', 'THREAD_CALL', 'SYSCTL', 'EXCLAVES', 'EARLY_BOOT']
syms = []
for line in subprocess.run(['xcrun', 'nm', '-n', '-arch', 'arm64', path], capture_output=True, text=True).stdout.splitlines():
    p = line.split()
    if len(p) >= 3:
        try:
            syms.append((int(p[0], 16), ' '.join(p[2:])))
        except ValueError:
            pass
syms.sort()
def name(v):
    import bisect
    i = bisect.bisect_right([s[0] for s in syms], v) - 1
    if i < 0:
        return '?0x%x' % v
    a, n = syms[i]
    return n if a == v else '%s+0x%x' % (n, v - a)
sects = {}
cur = {}
for l in subprocess.run(['xcrun', 'otool', '-l', path], capture_output=True, text=True).stdout.splitlines():
    p = l.split()
    if len(p) == 2 and p[0] in ('sectname', 'segname', 'addr', 'size', 'offset'):
        cur[p[0]] = p[1]
    elif l.startswith('Section') and cur:
        sects[(cur.get('segname'), cur.get('sectname'))] = cur; cur = {}
    elif len(p) == 2 and p[0] == 'cmd' and cur:
        sects[(cur.get('segname'), cur.get('sectname'))] = cur; cur = {}
if cur:
    sects[(cur.get('segname'), cur.get('sectname'))] = cur
data = open(path, 'rb').read()
total = 0
for (seg, sect), s in sects.items():
    if sect != '__init_entry_set' or int(s['size'], 16) == 0:
        continue
    addr, size, off = int(s['addr'], 16), int(s['size'], 16), int(s['offset'], 16)
    esz = 16  # struct startup_entry { u32 subsystem; u32 rank; func; arg } (startup_rank_t is uint32_t)
    n = size // esz
    rows = []
    for i in range(n):
        sub, rank, fn, arg = (struct.unpack_from('<IxQQ', data, off + i * esz) if esz == 24 else struct.unpack_from('<IIQQ', data, off + i * esz))
        rows.append((sub, rank, fn, arg))
    # the table is unsorted in the file: sort like the kernel does
    rows.sort(key=lambda r: (r[0], r[1]))
    print(';; %s,%s at 0x%x size 0x%x (%d entries, esz %d)' % (seg, sect, addr, size, n, esz))
    for sub, rank, fn, arg in rows:
        sname = SUBS[sub] if sub < len(SUBS) else ('%u' % sub)
        if want and sname != want:
            continue
        total += 1
        print('%-11s rank=%-3u fn=%s arg=%s' % (sname, rank, name(fn), name(arg) if arg else '0'))
print(';; printed %d entries' % total)
