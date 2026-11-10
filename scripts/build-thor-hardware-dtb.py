#!/usr/bin/env python3
"""Compile the thor DTS into the DTB the firmware and boot image carry."""
import subprocess, sys, shutil
from pathlib import Path

def main():
    here = Path(__file__).resolve().parent
    dts = here.parent / "dts" / "thor.dts"
    out = here.parent / "build" / "thor" / "thor.dtb"
    out.parent.mkdir(parents=True, exist_ok=True)

    soc = here.parent.parent / "UniXNU-sm8475-common" / "dts"
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
