#!/usr/bin/env python3
"""Replay the kernel's boot-time qsort of the fixed kalloc-type views on the host.
usage: kalloc_sort_sim.py <prelinked-kernel.macho> [prelink.json]
Prints how deep the recursive (smaller-side) calls go, for stack budgeting."""
import struct, sys, json

ZONE = [16, 32, 48, 64, 80, 96, 128, 160, 192, 224, 256, 288, 368, 400, 512, 576, 768, 1024, 1152, 1280, 1664, 2048,
        4096, 6144, 8192, 12288, 16384, 24576, 32768]

def dlut_idx(size):
    # kalloc_type_build_dlut: index of the first zone class >= size (size steps of 16)
    step = (size + 15) // 16 * 16
    i = 0
    while ZONE[i] < step:
        i += 1
    return i

class Macho:
    def __init__(self, path):
        self.data = open(path, 'rb').read()
        d = self.data
        self.segs, self.sects = [], {}
        ncmds = struct.unpack_from('<I', d, 16)[0]
        off = 32
        for _ in range(ncmds):
            cmd, cmdsize = struct.unpack_from('<II', d, off)
            if cmd == 0x19:
                segname = d[off + 8:off + 24].rstrip(b'\0').decode()
                vmaddr, vmsize, fileoff, filesize = struct.unpack_from('<QQQQ', d, off + 24)
                nsects = struct.unpack_from('<I', d, off + 64)[0]
                self.segs.append((segname, vmaddr, vmsize, fileoff, filesize))
                so = off + 72
                for _s in range(nsects):
                    sectname = d[so:so + 16].rstrip(b'\0').decode()
                    addr, size = struct.unpack_from('<QQ', d, so + 32)
                    offset = struct.unpack_from('<I', d, so + 48)[0]
                    self.sects[(segname, sectname)] = (addr, size, offset)
                    so += 80
            off += cmdsize

    def off(self, vmaddr):
        for name, va, vs, fo, fs in self.segs:
            if va <= vmaddr < va + fs:
                return fo + (vmaddr - va)
        raise ValueError('vmaddr 0x%x not file-backed' % vmaddr)

    def u32(self, vmaddr):
        return struct.unpack_from('<I', self.data, self.off(vmaddr))[0]

    def u64(self, vmaddr):
        return struct.unpack_from('<Q', self.data, self.off(vmaddr))[0]

    def cstr(self, vmaddr):
        o = self.off(vmaddr)
        e = self.data.index(b'\0', o)
        return self.data[o:e].decode('latin1')

def views(m, lo, hi):
    out = []
    for a in range(lo, hi, 64):
        flags = m.u32(a + 0x28)
        size = m.u32(a + 0x2c)
        if flags & 0x10 or flags & 0x8:      # KT_VM / KT_DATA_ONLY: never enters kt_buffer
            continue
        idx = dlut_idx(size)
        key = (idx, m.cstr(m.u64(a + 0x20)), m.cstr(m.u64(a + 0x10)), size)
        out.append(key)
    return out

def collect(path):
    m = Macho(path)
    a, s, _ = m.sects[('__DATA_CONST', '__kalloc_type')]
    items = views(m, a, a + s)
    nk, nkexts = len(items), 0
    if ('__PRELINK_TEXT', '__macho_list') in m.sects:
        la, ls, _ = m.sects[('__PRELINK_TEXT', '__macho_list')]
        for i in range(ls // 8):
            h = m.u64(la + 8 * i)
            nkexts += 1
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
                            items += views(m, sa, sa + ss)
                        so += 80
                off += cmdsize
    return items, nk, nkexts

def cmpf(a, b):
    if a[0] == b[0]:
        if a[1] != b[1]:
            return -1 if a[1] < b[1] else 1
        if a[2] != b[2]:
            return -1 if a[2] < b[2] else 1
        return 0
    return -1 if a[3] < b[3] else (1 if a[3] > b[3] else 0)

class Sim:
    """Exact port of bsd/kern/qsort.c (Bentley & McIlroy), tracking recursion depth."""
    def __init__(self):
        self.depth = 0; self.maxdepth = 0; self.calls = 0; self.frames = []   # frames: (depth, n) at entry
    def run(self, a):
        self.depth += 1
        self.maxdepth = max(self.maxdepth, self.depth)
        self.frames.append((self.depth, len(a)))
        n, es = len(a), 1
        while True:
            swap_cnt = 0
            if n < 7:
                for pm in range(1, n):
                    pl = pm
                    while pl > 0 and self.c(a[pl - 1], a[pl]) > 0:
                        a[pl - 1], a[pl] = a[pl], a[pl - 1]
                        pl -= 1
                self.depth -= 1
                return
            pm = n // 2
            if n > 7:
                pl, pn = 0, n - 1
                if n > 40:
                    d = n // 8
                    pl = self.med3(a, pl, pl + d, pl + 2 * d)
                    pm = self.med3(a, pm - d, pm, pm + d)
                    pn = self.med3(a, pn - 2 * d, pn - d, pn)
                pm = self.med3(a, pl, pm, pn)
            a[0], a[pm] = a[pm], a[0]
            pa = pb = 1
            pc = pd = n - 1
            while True:
                while pb <= pc and (r := self.c(a[pb], a[0])) <= 0:
                    if r == 0:
                        swap_cnt = 1
                        a[pa], a[pb] = a[pb], a[pa]
                        pa += 1
                    pb += 1
                while pb <= pc and (r := self.c(a[pc], a[0])) >= 0:
                    if r == 0:
                        swap_cnt = 1
                        a[pc], a[pd] = a[pd], a[pc]
                        pd -= 1
                    pc -= 1
                if pb > pc:
                    break
                a[pb], a[pc] = a[pc], a[pb]
                swap_cnt = 1
                pb += 1
                pc -= 1
            if swap_cnt == 0:
                for pm in range(1, n):
                    pl = pm
                    while pl > 0 and self.c(a[pl - 1], a[pl]) > 0:
                        a[pl - 1], a[pl] = a[pl], a[pl - 1]
                        pl -= 1
                self.depth -= 1
                return
            pn = n
            r = min(pa, pb - pa)
            a[0:r], a[pb - r:pb] = a[pb - r:pb], a[0:r]
            r = min(pd - pc, pn - pd - 1)
            a[pb:pb + r], a[pn - r:pn] = a[pn - r:pn], a[pb:pb + r]
            r = pb - pa
            if r > 1:
                self.run(a[:r])
            r = pd - pc
            if r > 1:
                a = a[pn - r:]
                n = r
                continue
            self.depth -= 1
            return
    def c(self, x, y):
        self.calls += 1
        return cmpf(x, y)
    def med3(self, a, i, j, k):
        # med3(a, b, c): cmp(a,b) < 0 ? (cmp(b,c) < 0 ? b : (cmp(a,c) < 0 ? c : a))
        #                        : (cmp(b,c) > 0 ? b : (cmp(a,c) < 0 ? a : c))
        if self.c(a[i], a[j]) < 0:
            return j if self.c(a[j], a[k]) < 0 else (k if self.c(a[i], a[k]) < 0 else i)
        return j if self.c(a[j], a[k]) > 0 else (i if self.c(a[i], a[k]) < 0 else k)

for path in sys.argv[1:]:
    items, nk, nx = collect(path)
    s = Sim()
    s.run(list(items))
    deepest = max(s.frames)[0]
    chain = [f for f in s.frames if f[0] >= deepest - 1][:6]
    sizes = {}
    for d, n in s.frames:
        sizes.setdefault(d, []).append(n)
    print('%s' % path.split('/')[-3])
    print('  fixed views sorted: %d (kernel %d + %d prelinked kexts), comparator calls %d' % (len(items), nk, nx, s.calls))
    print('  max recursion depth: %d qsort frames x 0x600 = 0x%x bytes of stack' % (s.maxdepth, s.maxdepth * 0x600))
    for d in sorted(sizes):
        print('    depth %2d: %3d calls, sub-array sizes min %d max %d' % (d, len(sizes[d]), min(sizes[d]), max(sizes[d])))
