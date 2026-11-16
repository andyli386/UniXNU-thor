#!/usr/bin/env python3
"""Build the Thor system-app helper with a local 22H374 inventory."""
import argparse
from pathlib import Path
import os
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "userland/thor-apps"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True, type=Path,
                        help="path to the local C initializer list for system bundles")
    parser.add_argument("--output", required=True, type=Path,
                        help="new path for the signed arm64 executable")
    args = parser.parse_args()

    if not args.inventory.is_file():
        parser.error(f"missing app inventory: {args.inventory}")
    if args.output.exists() or args.output.is_symlink():
        parser.error(f"already exists: {args.output}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="thor-apps-", dir=args.output.parent) as work:
        stage = Path(work)
        shutil.copyfile(args.inventory, stage / "app-inventory.inc")
        executable = stage / "thor-apps-install"
        subprocess.run(["xcrun", "--sdk", "iphoneos", "clang",
                        "-target", "arm64-apple-ios18.0",
                        "-fobjc-arc", "-fblocks", "-Os", "-framework", "Foundation",
                        "-I", str(stage), str(SOURCES / "thor-apps-install.m"),
                        "-o", str(executable)], check=True)
        subprocess.run(["codesign", "--force", "--sign", "-",
                        "--entitlements", str(SOURCES / "thor-apps-install.entitlements.plist"),
                        str(executable)], check=True)
        subprocess.run(["codesign", "--verify", "--strict", str(executable)], check=True)
        os.link(executable, args.output)


if __name__ == "__main__":
    main()
