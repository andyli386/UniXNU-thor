#!/usr/bin/python3.13
"""Kexts prelinked into a Thor kernelcache image, and their symbols.

  kc_prelink.py <kernelcache.macho> info                 -> segments, sections, prelinked kexts
  kc_prelink.py <kernelcache.macho> lookup <addr> [...]  -> kext and symbol for runtime addresses

Runtime addresses equal the link addresses of the image (the kernel proper matches the unstripped
kernel's `nm` addresses).  The kexts' exported symbols come from __PRELINK_INFO,__symtab; local
symbols are not in the image.
"""
import bisect, plistlib, struct, sys

def load(path):
    f = open(path, "rb")
    magic, _cputype, _cpusub, _filetype, ncmds, sizeofcmds, _flags, _res = struct.unpack("<IiiIIIII", f.read(32))
    assert magic == 0xFEEDFACF, hex(magic)
    cmds = f.read(sizeofcmds)
    segs, sects, symtab = [], [], None
    off = 0
    for _ in range(ncmds):
        cmd, size = struct.unpack_from("<II", cmds, off)
        if cmd == 0x19:
            name = cmds[off + 8:off + 24].split(b"\0")[0].decode()
            vmaddr, vmsize, fileoff, filesize, _maxprot, initprot, nsects, _fl = struct.unpack_from("<QQQQiiII", cmds, off + 24)
            segs.append((name, vmaddr, vmsize, fileoff, filesize, initprot))
            so = off + 72
            for _s in range(nsects):
                sname = cmds[so:so + 16].split(b"\0")[0].decode()
                segname = cmds[so + 16:so + 32].split(b"\0")[0].decode()
                addr, ssize, soff = struct.unpack_from("<QQI", cmds, so + 32)
                sects.append((segname, sname, addr, ssize, soff))
                so += 80
        elif cmd == 0x2:
            symoff, nsyms, stroff, strsize = struct.unpack_from("<IIII", cmds, off + 8)
            symtab = (symoff, nsyms, stroff, strsize)
        off += size
    f.seek(0)
    return f, segs, sects, symtab

def section(f, sects, seg, name):
    for s in sects:
        if s[0] == seg and s[1] == name:
            f.seek(s[4])
            return f.read(s[3]), s[2]
    return None, 0

def kexts(f, sects):
    info, _ = section(f, sects, "__PRELINK_INFO", "__info")
    if not info:
        return []
    # the section holds an XML plist (possibly followed by padding); find its extent
    # the section is the bare <dict> ... </dict> of the info dictionary (no plist wrapper)
    start = info.find(b"<dict>")
    end = info.rfind(b"</dict>")
    rows = plistlib.loads(b'<?xml version="1.0" encoding="UTF-8"?><plist version="1.0">' + info[start:end + 7] + b"</plist>")
    return rows.get("_PrelinkInfoDictionary", [])

def symtab(f, sects):
    data, base = section(f, sects, "__PRELINK_INFO", "__symtab")
    out, at, n = [], 0, len(data)
    while at + 4 <= n:
        addr = struct.unpack_from("<I", data, at)[0]
        at += 4
        z = data.find(b"\0", at)
        if z < 0:
            break
        name = data[at:z].decode("latin1")
        at = z + 1
        if addr == 0 and not name:
            out.append((0, "", True))      # end of one kext's list
        else:
            out.append((addr, name, False))
    return out

def main(argv):
    f, segs, sects, _ = load(argv[1])
    if argv[2] == "info":
        for s in segs:
            print("segment %-18s %#x + %#x" % (s[0], s[1], s[2]))
        for k in kexts(f, sects):
            print("%#x + %#x  %s" % (k.get("_PrelinkExecutableLoadAddr", 0), k.get("_PrelinkExecutableSize", 0), k.get("CFBundleIdentifier")))
        return 0
    if argv[2] == "lookup":
        rows = kexts(f, sects)
        syms = symtab(f, sects)
        # the lists are in the kexts' order; the kernel proper's list comes first
        groups, cur = [], []
        for addr, name, end in syms:
            if end:
                groups.append(cur)
                cur = []
            else:
                cur.append((addr, name))
        if cur:
            groups.append(cur)
        print("symbol groups: %d (%s)" % (len(groups), " ".join(str(len(g)) for g in groups)))
        for text in argv[3:]:
            a = int(text, 16)
            owner = [k for k in rows if k.get("_PrelinkExecutableLoadAddr", 0) <= a <
                     k.get("_PrelinkExecutableLoadAddr", 0) + k.get("_PrelinkExecutableSize", 0)]
            seg = [s for s in segs if s[1] <= a < s[1] + s[2]]
            line = "%#x %s" % (a, "%s+%#x" % (seg[0][0], a - seg[0][1]) if seg else "no segment")
            if owner:
                k = owner[0]
                line += "  kext %s+%#x" % (k.get("CFBundleIdentifier"), a - k["_PrelinkExecutableLoadAddr"])
            best = None
            for g in groups:
                if not g:
                    continue
                starts = [x[0] for x in g]
                i = bisect.bisect_right(starts, a) - 1
                if i >= 0 and (best is None or g[i][0] > best[0]):
                    best = g[i]
            if best and a - best[0] < 0x400000:
                line += "  nearest %s+%#x" % (best[1], a - best[0])
            print(line)
        return 0
    return 2

if __name__ == "__main__":
    sys.exit(main(sys.argv))
