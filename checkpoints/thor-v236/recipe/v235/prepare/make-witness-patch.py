"""Record THOR WITNESS V235 as one unified patch of the v234 recipe and prove that it reproduces v235.

  python3.13 -I prepare/make-witness-patch.py

Writes prepare/witness.patch (paths relative to the kernel work tree, plus the UFS driver),
applies it with `patch` to a copy of the v234 files and compares the result with this tree.
Run after clone.sh + build.  The witness header is a new file (the v234 one was renamed); the GPU
trace fix that v234's tree already carried after its build is part of that header.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
base = root.parent / "v234-unified"
work = Path("build-tree/.cache/xnu-work")
files = [
    work / "osfmk/kern/debug.c",
    work / "bsd/kern/kern_exec.c",
    work / "osfmk/kern/thor_witness_v235.h",
    Path("device/drivers/NativeUfs/PongUfsTransport.cpp"),
]

out = []
for rel in files:
    new, old = root / rel, base / rel
    before = old.read_bytes() if old.exists() else b""
    after = new.read_bytes()
    assert before != after, f"{rel}: no change"
    with tempfile.TemporaryDirectory() as tmp:
        a, b = Path(tmp) / "a", Path(tmp) / "b"
        a.write_bytes(before)
        b.write_bytes(after)
        diff = subprocess.run(["diff", "-u", "--label", "a/" + str(rel), "--label", "b/" + str(rel), str(a), str(b)],
                              capture_output=True).stdout
    out.append(diff)
patch = root / "prepare/witness.patch"
patch.write_bytes(b"".join(out))
print(f"{patch.relative_to(root)}: {len(files)} files, {patch.stat().st_size} bytes")

with tempfile.TemporaryDirectory() as tmp:
    stage = Path(tmp)
    for rel in files:
        old = base / rel
        if old.exists():
            (stage / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(old, stage / rel)
    subprocess.run(["patch", "-p1", "-s", "-i", str(patch)], cwd=stage, check=True)
    for rel in files:
        assert (stage / rel).read_bytes() == (root / rel).read_bytes(), f"{rel}: patch does not reproduce v235"
print("applied to the v234 sources the patch reproduces every v235 file byte for byte")
