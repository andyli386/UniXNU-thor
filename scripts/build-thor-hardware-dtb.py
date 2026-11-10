#!/usr/bin/env python3
"""Compile the thor DTS into the DTB the firmware and boot image carry."""
import subprocess, sys, shutil, os
from pathlib import Path

ROOT = Path("/Users/<user>/projects/thor-unixnu1030")

def main():
    dts = ROOT / "UniXNU-thor-18" / "dts" / "thor.dts"
    soc = ROOT / "UniXNU-sm8475-common" / "dts"
    out = Path(os.environ.get("THOR_DTB_OUT", str(ROOT / "thor-out" / "thor.dtb")))
    out.parent.mkdir(parents=True, exist_ok=True)

    if not dts.is_file():
        sys.exit(f"missing DTS: {dts}")
    if not soc.is_dir():
        sys.exit(f"missing SoC include dir: {soc}")

    dtc = shutil.which("dtc") or "/opt/homebrew/bin/dtc"
    cmd = [dtc, "-q", "-I", "dts", "-O", "dtb",
           "-i", str(soc), "-i", str(dts.parent),
           "-o", str(out), str(dts)]
    print(" ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(result.stderr)
    print(f"built {out} ({out.stat().st_size} bytes)")

if __name__ == "__main__":
    main()
