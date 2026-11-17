#!/usr/bin/env python3
"""Replay the checked Thor v176 XNU overlays on a prepared source tree."""

import argparse
import difflib
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile


CHECKPOINT = Path(__file__).resolve().parents[1] / "patches/xnu-ipados18/checkpoints/thor-v176"


def verify(root, hashes):
    for name, expected in hashes.items():
        path = root / name
        if path.resolve() != path or not path.is_file():
            raise SystemExit(f"Expected a regular source file: {path}")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise SystemExit(f"Incorrect SHA-256: {path}")


def apply(root, patch, reverse=False):
    args = ["git", "-C", str(root), "apply", "--whitespace=nowarn"]
    if reverse:
        args.append("--reverse")
    subprocess.run([*args, "--check", str(patch)], check=True)
    subprocess.run([*args, str(patch)], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tree", type=Path, help="prepared Apple XNU work tree")
    parser.add_argument("--reverse", action="store_true", help="restore the verified pre-v157 sources")
    parser.add_argument("--check", action="store_true", help="verify the entire replay without updating the tree")
    options = parser.parse_args()
    tree = options.tree.resolve(strict=True)
    steps = json.loads((CHECKPOINT / "manifest.json").read_text())["steps"]
    incoming, outgoing = ("after", "before") if options.reverse else ("before", "after")
    if options.reverse:
        steps.reverse()
    initial = {}
    final = {}
    for step in steps:
        for name, digest in step[incoming].items():
            initial.setdefault(name, digest)
        final.update(step[outgoing])
    verify(tree, initial)

    with tempfile.TemporaryDirectory(prefix="thor-v176-xnu-") as directory:
        temp = Path(directory).resolve()
        shadow = temp / "shadow"
        shadow.mkdir()
        for name in initial:
            target = shadow / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(tree / name, target)
        for step in steps:
            folder = CHECKPOINT / f"v{step['version']}"
            verify(folder, step["patches"])
            verify(shadow, step[incoming])
            patches = list(step["patches"])
            if options.reverse:
                patches.reverse()
            for patch in patches:
                apply(shadow, folder / patch, reverse=options.reverse)
            verify(shadow, step[outgoing])

        if not options.check:
            # Apply the checked delta once. A failed later overlay must not
            # leave part of the caller's source tree already modified.
            delta = temp / "delta.patch"
            with delta.open("wb") as output:
                for name in initial:
                    before = (tree / name).read_bytes().splitlines(keepends=True)
                    after = (shadow / name).read_bytes().splitlines(keepends=True)
                    output.writelines(difflib.diff_bytes(
                        difflib.unified_diff, before, after,
                        f"a/{name}".encode(), f"b/{name}".encode(),
                    ))
            verify(tree, initial)
            if delta.stat().st_size:
                apply(tree, delta)
            verify(tree, final)

    direction = "reverse" if options.reverse else "forward"
    print(f"Verified {direction} replay of {len(steps)} overlay stages across {len(final)} sources.")


if __name__ == "__main__":
    main()
