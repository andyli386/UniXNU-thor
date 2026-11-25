#!/usr/bin/python3.13
"""Read-only look at another SHNGROOT region of the port (OS / SystemOS / Data) from the phone in TWRP.

  apfs_region.py <full-snapshot.py> <apfs tool dir> <region> vols
  apfs_region.py <full-snapshot.py> <apfs tool dir> <region> ls   <volume> <path>
  apfs_region.py <full-snapshot.py> <apfs tool dir> <region> stat  <volume> <path>
  apfs_region.py <full-snapshot.py> <apfs tool dir> <region> extents <volume> <path>
  apfs_region.py <full-snapshot.py> <apfs tool dir> <region> cat <volume> <path> <local file> [<offset> <length>]

Uses the snapshot tool's transport (raw reads of sda36 over adb, verified read-only).  Nothing is written.
"""
import struct, sys
from pathlib import Path

args = sys.argv[:]
full, tools, region_name, cmd = args[1:5]
source = open(full).read().split("# ---- v228 full read-only snapshot")[0]
sys.argv = [args[0], tools]            # the tool's header reads its tool directory from argv[1]
exec(compile(source, full, "exec"), globals())
sys.argv = args
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
    region = regions[region_name]
    print("%s region: %#x + %#x" % (region_name, region[0], region[1]))
    container = data_container(phys, region)
    if cmd == 'vols':
        for v in container.volumes():
            print("volume %d %r role=%s sealed=%s files=%s dirs=%s" % (v.index, v.volname, v.role, v.sealed, v.num_files, v.num_directories))
    else:
        want = sys.argv[5]
        volume = apfs.pick_volume(container, want)
        path = sys.argv[6].strip('/')
        hit = volume.lookup(path)
        assert hit, path
        ino = volume.inode(hit[0])
        if cmd == 'ls':
            if ino.is_dir:
                for name, fid, kind in sorted(volume.dir_entries(hit[0]), key=lambda e: e[0]):
                    if name in ('.', '..'):
                        continue
                    child = volume.inode(fid)
                    print("%s %12d %s" % ('d' if child.is_dir else '-', child.size if child.is_reg else 0, name))
            else:
                print("regular file: %d bytes" % ino.size)
        elif cmd == 'stat':
            print("size %d  dir=%s" % (ino.size if ino.is_reg else 0, ino.is_dir))
        elif cmd == 'extents':
            for logical, length, physblk in volume.extents(hit[0]):
                print("  logical %#-10x length %#-10x physical block %#x" % (logical, length, physblk))
        elif cmd == 'cat':
            offset = int(sys.argv[8], 0) if len(sys.argv) > 8 else 0
            length = int(sys.argv[9], 0) if len(sys.argv) > 9 else ino.size - offset
            # plain extents only: read just the requested range, straight from the region
            assert ino.is_reg and not ino.decmpfs, "not a plain uncompressed regular file"
            back = Window(phys, *region)
            container_base = region[0]
            if back.read(512, 512).startswith(b'EFI PART'):
                parts = [q for q in read_gpt(back) if q.type_guid == APFS_PART_TYPE]
                container_base = region[0] + parts[0].offset
            block_size = container.c.block_size if hasattr(container, 'c') else container.block_size
            assert offset <= offset + length <= ino.size
            with open(sys.argv[7], 'wb') as out:
                pos = offset
                for logical, ext_length, physblk in volume.extents(hit[0]):
                    if logical + ext_length <= pos or pos >= offset + length:
                        continue
                    if logical > pos:
                        raise AssertionError("hole at %#x" % pos)
                    take = min(ext_length - (pos - logical), offset + length - pos)
                    got = phys.read(container_base + physblk * block_size + (pos - logical), take)
                    assert len(got) == take
                    out.write(got)
                    pos += take
                assert pos == offset + length, (pos, offset + length)
            print("wrote %d bytes of %s from offset %#x (container at %#x, block %d)" % (length, path, offset, container_base, block_size))
finally:
    phys.close()
after = snap()
assert before['boot_id'] == after['boot_id']
assert all(before['stats'][node][i] == after['stats'][node][i] for node in nodes for i in write_fields)
print("read-only verified: no writes or discards on sda36/dm-7")
