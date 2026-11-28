#!/usr/bin/env python3.13
"""Mach-O images (UUID, size, path) below directories of an APFS volume in a local image or in a region of the phone.

  apfs_macho_uuids.py <apfs tool dir> <image> <volume> <dir> [<dir> ...] [--uuid PREFIX] [--dump <dir>]
With --uuid only images whose UUID starts with PREFIX are listed (and dumped to <dir> when --dump is given).
"""
import struct, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import apfs
tools, image, volume, dirs = sys.argv[1], sys.argv[2], sys.argv[3], []
want, dump = None, None
args = sys.argv[4:]
i = 0
while i < len(args):
    if args[i] == "--uuid": want = args[i + 1].lower(); i += 2
    elif args[i] == "--dump": dump = Path(args[i + 1]); i += 2
    else: dirs.append(args[i]); i += 1
c = apfs.Container(image)
v = apfs.pick_volume(c, volume)

def uuids(data):
    out = []
    magic = struct.unpack_from("<I", data)[0]
    if magic == 0xCAFEBABE or magic == 0xBEBAFECA:
        n = struct.unpack_from(">I", data, 4)[0]
        for k in range(n):
            ctype, csub, off, size, align = struct.unpack_from(">IIIII", data, 8 + 20 * k)
            out += uuids(data[off:off + size])
    elif magic == 0xFEEDFACF:
        ncmds = struct.unpack_from("<I", data, 16)[0]
        off = 32
        for _ in range(ncmds):
            cmd, size = struct.unpack_from("<II", data, off)
            if cmd == 0x1B:
                out.append(data[off + 8:off + 24].hex())
            off += size
    return out

found = 0
def walk(oid, rel, depth=0):
    global found
    if depth > 12: return
    for name, fid, kind in v.dir_entries(oid):
        if name in (".", ".."): continue
        child = v.inode(fid)
        if child is None: continue
        path = rel + "/" + name
        if child.is_dir:
            walk(fid, path, depth + 1)
        elif child.is_reg and 64 <= child.size < 120 * 1024 * 1024:
            data = child.contents()
            if data[:4] in (b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"):
                us = uuids(data[:min(len(data), 262144)])
                if us and not (want and not any(u.lower().startswith(want) for u in us)):
                    found += 1
                    print("%s %9d %s" % (",".join(u.lower() for u in us), child.size, path))
                    if dump:
                        target = dump / (path.strip("/").replace("/", "__"))
                        dump.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(data)

for d in dirs:
    hit = v.lookup(d.lstrip("/"))
    if hit: walk(hit[0], "")
    else: print("no such directory:", d)
print("images listed: %d" % found)
