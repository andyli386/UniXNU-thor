#!/usr/bin/env python3
"""Nearest symbol below each address, in a compact table.
usage: symat.py <macho> <addr> [<addr> ...]
Prints: address  section  offset-from-section-start  nearest-symbol  +distance
"""
import bisect, struct, subprocess, sys

def rows(path):
    out = subprocess.check_output(["xcrun", "nm", "-arch", "arm64", "-n", path], text=True, stderr=subprocess.DEVNULL)
    r = []
    for line in out.splitlines():
        p = line.split()
        if len(p) < 3:
            continue
        try:
            v = int(p[0], 16)
        except ValueError:
            continue
        r.append((v, p[1], " ".join(p[2:])))
    r.sort()
    return r

def segments(path):
    data = open(path, "rb").read(1 << 20)
    n = struct.unpack_from("<I", data, 16)[0]
    off, out = 32, []
    for _ in range(n):
        cmd, size = struct.unpack_from("<II", data, off)
        if cmd == 0x19:
            name = data[off + 8:off + 24].rstrip(b"\0").decode()
            vmaddr, vmsize, fileoff, filesize = struct.unpack_from("<QQQQ", data, off + 24)
            out.append((name, vmaddr, vmsize, fileoff, filesize))
        off += size
    return out

def main():
    path = sys.argv[1]
    r = rows(path)
    addrs = [x[0] for x in r]
    segs = segments(path)
    for a in sys.argv[2:]:
        v = int(a, 16)
        seg = next(("%s+0x%x" % (s[0], v - s[1]) for s in segs if s[1] <= v < s[1] + s[2]), "none")
        i = bisect.bisect_right(addrs, v) - 1
        if i < 0:
            print("0x%012x  %-22s  (no symbol below)" % (v, seg))
            continue
        # skip the three giant payload arrays so that a code address is not reported as "inside a payload"
        j = i
        while j >= 0 and any(k in r[j][2] for k in ("_payload", "Payload")):
            j -= 1
        s = r[j]
        nxt = r[i + 1] if i + 1 < len(r) else None
        end = "  [next %s +0x%x]" % (nxt[2], nxt[0] - v) if nxt else ""
        print("0x%012x  %-22s  %s+0x%x (%s)%s" % (v, seg, s[2], v - s[0], s[1], end))

main()
