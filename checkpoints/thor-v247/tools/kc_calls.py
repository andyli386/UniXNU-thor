#!/usr/bin/env python3.13
"""Callers and callees of an address in a prelinked kernel image, plus the function it belongs to.

  kc_calls.py <kernelcache.macho> to <va> [...]      every bl / b that targets the address
  kc_calls.py <kernelcache.macho> from <va> [N]      the next N instructions (default 64), annotated
  kc_calls.py <kernelcache.macho> start <va> [...]   the first instruction of the function around va

Kext code is linked into __PLK_TEXT_EXEC with no symbol table and no function starts, so a function
start is found by scanning back for a frame-setting prologue (a stack allocation or a pre-index
store pair with write-back on sp) that follows the end of the previous function (ret, brk, br, an
unconditional b, padding).
"""
import struct, sys
sys.path.insert(0, __file__.rsplit("/", 1)[0])
import kc_prelink as K

f, segs, sects, _ = K.load(sys.argv[1])
rows = K.kexts(f, sects)


def seg_of(va):
    for s in segs:
        if s[1] <= va < s[1] + s[2]:
            return s


def file_off(va):
    s = seg_of(va)
    if not s or va - s[1] >= s[4]:
        return None
    return s[3] + va - s[1]


def read(va, n):
    o = file_off(va)
    if o is None:
        return b""
    f.seek(o)
    return f.read(n)


def words(va, n):
    b = read(va, 4 * n)
    return struct.unpack("<%dI" % (len(b) // 4), b)


def kext_of(va):
    """The kext whose read-only part contains va; code in __PLK_TEXT_EXEC carries no name here."""
    for k in rows:
        a = k.get("_PrelinkExecutableLoadAddr", 0)
        if a <= va < a + k.get("_PrelinkExecutableSize", 0):
            return "%s+%#x" % (k.get("CFBundleIdentifier"), va - a)
    return None


def describe(va):
    s = seg_of(va)
    line = "%#x %s+%#x" % (va, s[0], va - s[1]) if s else "%#x no segment" % va
    k = kext_of(va)
    return line + ("  " + k if k else "")


def cstring_at(va):
    b = read(va, 200)
    z = b.find(b"\0")
    if z <= 0:
        return None
    b = b[:z]
    if all(32 <= c < 127 or c in (9, 10) for c in b):
        return b.decode()
    return None


def allocates(x):
    """sub sp, sp, #imm (incl. shifted) | stp xA, xB, [sp, #-imm]! (64-bit)."""
    return (x & 0xFF8003FF) == 0xD10003FF or (x & 0xFFC003E0) == 0xA98003E0


def ends_function(x):
    return ((x & 0xFC000000) == 0x14000000            # b
            or (x & 0xFFFFFC1F) == 0xD65F0000         # ret xN
            or x in (0xD65F0BFF, 0xD65F0FFF)          # retaa / retab
            or (x & 0xFFE0001F) == 0xD4200000         # brk
            or x == 0)


def function_start(va):
    back = 0x2000 // 4
    w = words(va - 4 * back, back)
    for i in range(len(w) - 1, 0, -1):
        if allocates(w[i]) and ends_function(w[i - 1]):
            return va - 4 * (len(w) - i)
    return None


def callers(target):
    for s in segs:
        if s[0] not in ("__TEXT_EXEC", "__PLK_TEXT_EXEC"):
            continue
        f.seek(s[3])
        blob = f.read(s[4])
        base = s[1]
        cnt = len(blob) // 4
        arr = struct.unpack("<%dI" % cnt, blob[:4 * cnt])
        for i, w in enumerate(arr):
            if w & 0x7C000000 == 0x14000000:
                imm = w & 0x3FFFFFF
                if imm & (1 << 25):
                    imm -= 1 << 26
                if base + 4 * i + 4 * imm == target:
                    a = base + 4 * i
                    st = function_start(a)
                    print("%s  %s -> %#x   in function %#x (+%#x)" % (
                        describe(a), "bl" if (w >> 26) & 1 else "b ", target,
                        st or 0, a - st if st else 0))


mode = sys.argv[2]
if mode == "to":
    for a in sys.argv[3:]:
        t = int(a, 16)
        print("callers of %#x (%s)" % (t, kext_of(t)))
        callers(t)
elif mode == "from":
    va = int(sys.argv[3], 16)
    n = int(sys.argv[4]) if len(sys.argv) > 4 else 64
    page = {}
    for i, w in enumerate(words(va, n)):
        a = va + 4 * i
        note = ""
        if (w & 0x9F000000) == 0x90000000:                                  # adrp
            rd = w & 31
            imm = ((w >> 5) & 0x7FFFF) << 2 | ((w >> 29) & 3)
            if imm & (1 << 20):
                imm -= 1 << 21
            page[rd] = (a & ~0xFFF) + (imm << 12)
            note = "x%d = %#x" % (rd, page[rd])
        elif w & 0xFF800000 == 0x91000000 and (w >> 5) & 31 in page:        # add xd, xn, #imm12
            imm = ((w >> 10) & 0xFFF) << (12 if w & (1 << 22) else 0)
            t = page[(w >> 5) & 31] + imm
            note = "x%d = %#x" % (w & 31, t)
            s = cstring_at(t)
            if s:
                note += "  %r" % s
        elif w & 0xFC000000 == 0x14000000:                                   # b / bl
            imm = w & 0x3FFFFFF
            if imm & (1 << 25):
                imm -= 1 << 26
            t = a + 4 * imm
            st = function_start(t)
            note = "%s %#x%s" % ("bl" if (w >> 26) & 1 else "b ", t,
                                 "  (function %#x)" % st if st and st != t else "")
        print("%#x: %08x  %s" % (a, w, note))
elif mode == "start":
    for a in sys.argv[3:]:
        va = int(a, 16)
        st = function_start(va)
        print("%#x: function starts at %s" % (
            va, "%#x (%s), %#x bytes in" % (st, kext_of(st), va - st) if st else "not found"))
