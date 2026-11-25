#!/usr/bin/env python3
"""Depth of the boot-time kalloc-type qsort under alternative view orderings.
usage: kalloc_orderings.py <sim-dir> <kernel.macho> [...]"""
import struct, sys
src = open(sys.argv[1] + '/kalloc_sort_sim.py').read().split("for path in sys.argv[1:]:")[0]
ns = {}
exec(compile(src, 'sim', 'exec'), ns)
Macho, views, Sim = ns['Macho'], ns['views'], ns['Sim']

def kernel_views(m):
    a, s, _ = m.sects[('__DATA_CONST', '__kalloc_type')]
    return views(m, a, a + s)

def kext_headers(m):
    la, ls, _ = m.sects[('__PRELINK_TEXT', '__macho_list')]
    return [m.u64(la + 8 * i) for i in range(ls // 8)]

def kext_views(m, hdrs):
    out = []
    for h in hdrs:
        ncmds = struct.unpack_from('<I', m.data, m.off(h) + 16)[0]
        off = m.off(h) + 32
        for _ in range(ncmds):
            cmd, cmdsize = struct.unpack_from('<II', m.data, off)
            if cmd == 0x19 and m.data[off + 8:off + 24].rstrip(b'\0') == b'__DATA_CONST':
                nsects = struct.unpack_from('<I', m.data, off + 64)[0]
                so = off + 72
                for _s in range(nsects):
                    if m.data[so:so + 16].rstrip(b'\0') == b'__kalloc_type':
                        sa, ss = struct.unpack_from('<QQ', m.data, so + 32)
                        out += views(m, sa, sa + ss)
                    so += 80
            off += cmdsize
    return out

def fileset_order(m):
    ids = []
    ncmds = struct.unpack_from('<I', m.data, 16)[0]
    off = 32
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from('<II', m.data, off)
        if cmd == 0x35:   # LC_FILESET_ENTRY
            vmaddr = struct.unpack_from('<Q', m.data, off + 8)[0]
            ids.append(vmaddr)
        off += cmdsize
    return ids

def depth(items):
    s = Sim(); s.run(list(items))
    fr = s.frames
    chain = []
    for d in range(1, s.maxdepth + 1):
        c = [f for f in fr if f[0] == d]
        chain.append(max(c, key=lambda f: f[1])[1])
    return s.maxdepth, chain, s.calls

for path in sys.argv[2:]:
    m = Macho(path)
    k = kernel_views(m)
    hdrs = kext_headers(m)
    x = kext_views(m, hdrs)
    fs = fileset_order(m)
    print(path.split('/')[-3], 'kernel views', len(k), 'kext headers', len(hdrs), 'kext views', len(x), 'fileset entries', len(fs), flush=True)
    variants = [('kernel only', k), ('kernel + kexts (macho_list order)', k + x),
                ('kexts + kernel', x + k), ('kexts reversed + kernel', x[::-1] + k)]
    for name, items in variants:
        d, chain, calls = depth(items)
        print('   %-36s n=%4d depth=%2d stack=0x%05x (%5d B) calls=%6d chain=%s' % (name, len(items), d, d * 0x600, d * 0x600, calls, chain), flush=True)
