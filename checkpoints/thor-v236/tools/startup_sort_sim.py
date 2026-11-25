#!/usr/bin/env python3
"""Depth of the boot-time qsort of the __init_entry_set startup table (kernel_startup_bootstrap).
usage: startup_sort_sim.py <sim-dir> <kernel.macho.unstripped> [...]"""
import struct, sys
src = open(sys.argv[1] + '/kalloc_sort_sim.py').read().split("for path in sys.argv[1:]:")[0]
ns = {}
exec(compile(src, 'sim', 'exec'), ns)
Macho = ns['Macho']
LOCKDOWN = 0xffffffff
for path in sys.argv[2:]:
    m = Macho(path)
    key = next(k for k in m.sects if k[1] == '__init_entry_set')
    a, size, off = m.sects[key]
    rows = []
    for i in range(size // 16):
        sub, rank, fn, arg = struct.unpack_from('<IIQQ', m.data, off + i * 16)
        rows.append((LOCKDOWN if sub == LOCKDOWN else sub, rank))
    class S(ns['Sim']):
        def c(self, x, y):
            self.calls += 1
            if x[0] == y[0]:
                return 0 if x[1] == y[1] else (-1 if x[1] < y[1] else 1)
            # LOCKDOWN (~0u) sorts last in C unsigned compare
            return -1 if x[0] < y[0] else 1
    s = S(); s.run(list(rows))
    chain = []
    for d in range(1, s.maxdepth + 1):
        c = [f for f in s.frames if f[0] == d]
        chain.append(max(c, key=lambda f: f[1])[1])
    print('%s startup entries %d: qsort depth %d = 0x%x bytes, chain %s' % (path.split('/')[-3], len(rows), s.maxdepth, s.maxdepth * 0x600, chain))
