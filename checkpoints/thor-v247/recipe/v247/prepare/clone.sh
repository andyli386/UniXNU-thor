#!/bin/bash
# APFS-clone the v246 unified recipe into v247 (same method as every earlier version).
set -euo pipefail
out=$(cd "$(dirname "$0")/.." && pwd)
base="$(dirname "$out")/v246-unified"
for n in device build-tree runtime; do
  test ! -e "$out/$n"
  cp -cRp "$base/$n" "$out/$n"
done
cp -p "$base/build-kernel.sh" "$base/thor-gpu.defconfig" "$out/"
for p in "$out/build-tree/drivers" "$out/build-tree/patches/xnu"; do
  target=$(readlink "$p")
  [[ "$target" == "$base/"* ]]
  rm "$p"
  ln -s "$out/${target#"$base/"}" "$p"
done
/opt/homebrew/bin/python3.13 "$out/prepare/relocate.py"
test -d "$out/device/drivers/ArmCpu" && test ! -L "$out/device/drivers/ArmCpu"
date > "$out/prepare/ready.ok"
