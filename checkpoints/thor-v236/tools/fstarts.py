#!/usr/bin/env python3
"""Function boundaries from LC_FUNCTION_STARTS (data-in-code entries included) of a Mach-O kernel.
usage: fstarts.py <macho> <addr> ...        -> for each address: the function it is in (start, size) and
                                                nearest exported/local nm symbol at or below the start
"""
import bisect, struct, subprocess, sys

def uleb(buf, i):
    v = s = 0
    while True:
        b = buf[i]; i += 1
        v |= (b & 0x7f) << s
        if not b & 0x80:
            return v, i
        s += 7

def main():
    path = sys.argv[1]
    data = open(path, "rb").read()
    n = struct.unpack_from("<I", data, 16)[0]
    off = 32
    text = 0
    starts = []
    for _ in range(n):
        cmd, size = struct.unpack_from("<II", data, off)
        if cmd == 0x19 and data[off + 8:off + 24].rstrip(b"\0") == b"__TEXT":
            text = struct.unpack_from("<Q", data, off + 24)[0]
        if cmd == 0x26:
            dataoff, dsize = struct.unpack_from("<II", data, off + 8)
            i = dataoff
            v = 0
            end = i + dsize
            while i < end and data[i]:
                d, i = uleb(data, i)
                v += d
                starts.append(text + v)
        off += size
    starts.sort()
    nm = subprocess.check_output(["xcrun", "nm", "-arch", "arm64", "-n", path], text=True, stderr=subprocess.DEVNULL)
    syms = {}
    for l in nm.splitlines():
        p = l.split()
        if len(p) == 3:
            try:
                syms.setdefault(int(p[0], 16), []).append(p[1] + " " + p[2])
            except ValueError:
                pass
    symaddrs = sorted(syms)
    for a in sys.argv[2:]:
        v = int(a, 16)
        i = bisect.bisect_right(starts, v) - 1
        if i < 0:
            print("0x%x: not in a function" % v); continue
        st = starts[i]
        en = starts[i + 1] if i + 1 < len(starts) else st
        j = bisect.bisect_right(symaddrs, st) - 1
        name = syms[symaddrs[j]][0] if j >= 0 else "?"
        exact = " (exact)" if symaddrs and symaddrs[j] == st else " (nearest symbol is %#x below the start)" % (st - symaddrs[j])
        print("0x%012x: function %#x..%#x (%#x bytes), offset %#x; %s%s" % (v, st, en, en - st, v - st, name, exact))

main()
