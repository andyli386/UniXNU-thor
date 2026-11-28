#!/usr/bin/env python3.13
"""The APFS reader with checksum verification turned off (for the system cryptex image, whose volume
metadata blocks carry no valid checksum in their object header).

  apfs_noverify.py <apfs tool dir> -- <apfs.py arguments>          e.g. ... -- ls IMAGE PATH -v VOLUME
  apfs_noverify.py <apfs tool dir> --volume-oids IMAGE VOLUME     the object ids of a volume's files
"""
import struct, sys
tools = sys.argv[1]
sys.path.insert(0, tools)
import apfs
apfs.checksum_ok = lambda blk: True
if sys.argv[2] == "--":
    sys.exit(apfs.main(["apfs.py"] + sys.argv[3:]))
if sys.argv[2] == "--volume-oids":
    image, volume = sys.argv[3], sys.argv[4]
    c = apfs.Container(image)
    v = apfs.pick_volume(c, volume)
    kinds = {}
    for k, val in v.tree.iter_all():
        kind = struct.unpack_from("<I", k, 8)[0]
        kinds[kind] = kinds.get(kind, 0) + 1
    print("file-system tree record types:", kinds)
    sys.exit(0)
