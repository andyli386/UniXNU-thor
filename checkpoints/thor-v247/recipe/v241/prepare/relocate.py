"""Rehome XNU generated dependency inputs in an APFS clone of the unified v240 tree."""
import collections
import json
import os
from pathlib import Path

root = Path(__file__).resolve().parents[1]
old = root.parent / "v240-unified"
tree = root / "build-tree"
obj = tree / "build/xnu/ios-obj"
old_b = os.fsencode(old)
new_b = os.fsencode(root)
assert (tree / "drivers").resolve() == root / "device/drivers"
patches = tree / "patches/xnu"
updated = root / "device/patches/xnu-ipados18"
assert patches.is_symlink() and patches.resolve() in {old / "device/patches/xnu-ipados18", updated}
if patches.resolve() != updated:
    patches.unlink()
    patches.symlink_to(updated)
counts = collections.Counter()
for p in obj.rglob("*"):
    if p.is_symlink() or not p.is_file():
        continue
    if p.name not in {"Makefile", ".CFLAGS", ".CXXFLAGS", ".ASFLAGS"} and p.suffix not in {
        ".d", ".cpd", ".filelist", ".mk", ".inc", ".rsp", ".h", ".json",
    }:
        continue
    data = p.read_bytes()
    if old_b in data:
        st = p.stat()
        p.write_bytes(data.replace(old_b, new_b))
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
        counts[p.suffix or p.name] += 1
for group in ("bsd", "iokit"):
    m = obj / "DEVELOPMENT_ARM64_PONG" / group / "DEVELOPMENT/Makefile"
    assert old_b not in m.read_bytes() and new_b in m.read_bytes(), m
for path in (root / "runtime/thor_metal_pool_payload.h", tree / ".cache/xnu-work/bsd/kern/thor_metal_pool_payload.h"):
    data = path.read_bytes()
    if old_b in data:
        path.write_bytes(data.replace(old_b, new_b))
    assert old_b not in path.read_bytes() and new_b in path.read_bytes()
print(json.dumps(counts, indent=2))
