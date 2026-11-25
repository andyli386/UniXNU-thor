#!/usr/bin/python3.13
"""Physical extents of files in the port's APFS "Data" volume, read raw from the phone in TWRP.
usage: apfs_extents.py <full-snapshot.py> <apfs tool dir> <volume-relative path> [<reference file> ...]
With a reference file (same path dumped earlier), each of its blocks is compared with the raw bytes at
candidate locations: container start + physical block * 4096 (what VNOP_BLOCKMAP + window base means).
Nothing is written: sda36 is only read, through the snapshot tool's transport.
"""
from pathlib import Path
import struct, sys, subprocess, hashlib

full, tools, path = sys.argv[1], sys.argv[2], sys.argv[3]
refs = sys.argv[4:]
source = open(full).read().split("# ---- v228 full read-only snapshot")[0]
sys.argv = [sys.argv[0], tools] + sys.argv[3:]
exec(compile(source, full, "exec"), globals())
before = snap()
size = int(shell('blockdev --getsize64 /dev/block/sda36'))
phys = Physical(size)
try:
    raw = phys.read(0, 4096)
    assert raw[:8] == b'SHNGROOT'
    version, count, total = struct.unpack_from('<IIQ', raw, 8)
    regions = {}
    for i in range(count):
        name, off, length, block, flags = struct.unpack_from('<32sQQII', raw, 24 + 128 * i)
        regions[name.split(b'\0')[0].decode('ascii')] = (off, length)
    print("SHNGROOT regions:", {k: (hex(v[0]), hex(v[1])) for k, v in regions.items()})
    region = regions['Data']
    back = Window(phys, *region)
    container_base = region[0]
    if back.read(512, 512).startswith(b'EFI PART'):
        parts = [p for p in read_gpt(back) if p.type_guid == APFS_PART_TYPE]
        print("GPT inside the Data region:", [(hex(p.offset), hex(p.length)) for p in parts])
        container_base = region[0] + parts[0].offset
    print("container start inside sda36: %#x (block %#x)" % (container_base, container_base // 4096))
    container = data_container(phys, region)
    volume = apfs.pick_volume(container, 'Data')
    hit = volume.lookup(path.lstrip('/'))
    assert hit, path
    ino = volume.inode(hit[0])
    ext = volume.extents(hit[0])
    print("%s: size %d, %d extents" % (path, ino.size, len(ext)))
    for logical, length, physblk in ext[:70]:
        print("  logical %#-8x length %#-8x physical block %#x (first byte %#x)" % (logical, length, physblk, container_base + physblk * 4096))
    for ref in refs:
        content = Path(ref).read_bytes()
        print("reference %s: %d bytes; head %r; mark %r" % (ref, len(content), bytes(content[:64]), bytes(content[256:288])))
        ext1 = [e for e in ext if e[1]]
        total = 0
        ok = 0
        for n, (logical, length, physblk) in enumerate(ext1):
            off = logical
            while off < logical + length and off < len(content):
                take = min(4096, logical + length - off, len(content) - off)
                got = phys.read(container_base + physblk * 4096 + (off - logical), take)
                same = got == content[off:off + take]
                total += 1
                ok += same
                if n == 0 and off == logical:
                    print("  block 0 at %#x (physical block %#x): %s; raw head %r; raw mark %r" % (
                        container_base + physblk * 4096, physblk, "MATCH" if same else "DIFFERS",
                        bytes(got[:64]), bytes(got[256:288])))
                off += take
        print("  blocks compared: %d identical of %d" % (ok, total))
        # other guesses for where block 0 might be found
        want = content[:4096]
        p0 = ext[0][2]
        guesses = {
            "container + block*4096": container_base + p0 * 4096,
            "no window base": p0 * 4096,
            "container + block*512": container_base + p0 * 512,
            "region start + block*4096": region[0] + p0 * 4096,
            "container + block*4096*8": container_base + p0 * 4096 * 8,
        }
        for label, at in guesses.items():
            got = phys.read(at, 4096) if at + 4096 <= size else b''
            print("  guess %-28s at %#x: %s" % (label, at, "MATCH" if got == want else "no (head %r)" % bytes(got[:24])))
        # the same check for the kernel console mirror: its first block, read raw
finally:
    phys.close()
after = snap()
assert before['boot_id'] == after['boot_id']
assert all(before['stats'][node][i] == after['stats'][node][i] for node in nodes for i in write_fields)
print("read-only verified: no writes or discards on sda36/dm-7")
