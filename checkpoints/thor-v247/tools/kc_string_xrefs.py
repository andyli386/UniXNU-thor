#!/usr/bin/env python3.13
"""Find a string in a prelinked kernel image and the code that references it.

  kc_string_xrefs.py <kernelcache.macho> <text> [--context N]
Prints the string's address, every adrp+add / adrp+ldr literal pair in the executable sections that
forms that address, and the function (LC_FUNCTION_STARTS when the address is in the kernel, otherwise
the nearest kext and the function start found by scanning backwards for a typical prologue).
"""
import bisect, struct, sys, re
from pathlib import Path
data = Path(sys.argv[1]).read_bytes()
needle = sys.argv[2].encode()
ctx = int(sys.argv[4]) if len(sys.argv) > 4 else 24
magic, _ct, _cs, ft, ncmds, sizeofcmds, _fl, _r = struct.unpack_from("<IiiIIIII", data)
segs, sects, fstarts = [], [], []
off = 32
for _ in range(ncmds):
    cmd, size = struct.unpack_from("<II", data, off)
    if cmd == 0x19:
        name = data[off + 8:off + 24].split(b"\0")[0].decode()
        vmaddr, vmsize, fileoff, filesize = struct.unpack_from("<QQQQ", data, off + 24)
        segs.append((name, vmaddr, vmsize, fileoff, filesize))
        so = off + 72
        for _s in range(struct.unpack_from("<I", data, off + 64)[0]):
            sname = data[so:so + 16].split(b"\0")[0].decode()
            addr, ssize, soff = struct.unpack_from("<QQI", data, so + 32)
            flags = struct.unpack_from("<I", data, so + 64)[0]
            sects.append((name, sname, addr, ssize, soff, flags))
            so += 80
    elif cmd == 0x26:
        fstarts_off, fstarts_size = struct.unpack_from("<II", data, off + 8)
        blob = data[fstarts_off:fstarts_off + fstarts_size]
        v = s = 0
        for b in blob:
            v |= (b & 0x7F) << s
            if b & 0x80:
                s += 7
            else:
                fstarts.append(v); v = s = 0
    off += size
fstarts.sort()
def off2va(o):
    for n, va, sz, fo, fs in segs:
        if fo <= o < fo + fs: return va + o - fo
def va2off(v):
    for n, va, sz, fo, fs in segs:
        if va <= v < va + sz and v - va < fs: return fo + v - va
def owner(v):
    for n, va, sz, fo, fs in segs:
        if va <= v < va + sz: return n
i = data.find(needle)
while i >= 0:
    va = off2va(i)
    print("string at file %#x = va %#x (%s)" % (i, va, owner(va) or "?"))
    page = va & ~0xFFF
    hits = []
    for n, va0, sz, fo, fs in segs:
        if n not in ("__TEXT_EXEC", "__PLK_TEXT_EXEC", "__TEXT", "__PRELINK_TEXT"): continue
        # adrp, little endian: immhi[2:0] 00000, immhi, immhi, then 1 immlo 10000 in the top byte
        ADRP = rb"[\x00\x20\x40\x60\x80\xa0\xc0\xe0][\s\S][\s\S][\x90\xb0\xd0\xf0]"
        for m in re.finditer(ADRP, data[fo:fo + fs - 4]):
            o = fo + m.start()
            if (o - fo) % 4: continue
            w = struct.unpack_from("<I", data, o)[0]
            rd = w & 31
            immlo = (w >> 29) & 3; immhi = (w >> 5) & 0x7FFFF
            imm = (immhi << 2) | immlo
            if imm & (1 << 20): imm -= 1 << 21
            if ((va0 + o - fo) & ~0xFFF) + (imm << 12) != page: continue
            va_ins = va0 + o - fo
            # look at the next 6 instructions for an add/ldr with this base register
            for k in range(1, 7):
                w2 = struct.unpack_from("<I", data, o + 4 * k)[0]
                if w2 & 0xFF800000 == 0x91000000 and (w2 >> 5) & 31 == rd:       # add xd, xn, #imm
                    if ((w2 >> 10) & 0xFFF) << (12 if w2 & (1 << 22) else 0) == va & 0xFFF:
                        hits.append(va_ins)
                elif w2 & 0xFFC00000 == 0xF9400000 and (w2 >> 5) & 31 == rd:     # ldr xt, [xn, #imm]
                    if ((w2 >> 10) & 0xFFF) * 8 == va & 0xFFF:
                        hits.append(va_ins)
    for h in sorted(set(hits)):
        j = bisect.bisect_right(fstarts, h) - 1
        print("  referenced at va %#x (%s), %d bytes into the function starting %#x" % (h, owner(h), h - fstarts[j] if j >= 0 else -1, fstarts[j] if j >= 0 else 0))
    i = data.find(needle, i + 1)
