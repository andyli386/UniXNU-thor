#!/bin/zsh
# Thor iPadOS 18 boot image: complete pipeline from IPSW to flashable image.
# Run after the IPSW is downloaded and kernel/firmware are built.
set -e

ROOT=/Users/<user>/projects/thor-unixnu1030
PY=/Users/<user>/projects/thor-unixnu/toolchains/python313/bin/python
IPSW=$ROOT/thor-out/ipsw/22H374.ipsw
KERNEL=$ROOT/thor-out/kernel/mach.development.pong
FD=$ROOT/uefi-pong/Build/thorPkg/DEBUG_CLANGPDB/FV/SILICIUM_UEFI.fd
DTB=/tmp/thor-hw.dtb

echo "== 0. Verify inputs =="
test -f "$IPSW" || { echo "IPSW missing: $IPSW"; exit 1; }
test -f "$KERNEL" || { echo "kernel missing: $KERNEL"; exit 1; }
test -f "$FD" || { echo "firmware missing: $FD"; exit 1; }
test -f "$DTB" || { echo "DTB missing: $DTB"; exit 1; }
echo "all inputs present"

echo "== 1. Extract IPSW components =="
cd $ROOT/UniXNU
$PY ./unixnu ipsw -D ../UniXNU-thor-18 --ipsw "$IPSW" fetch 2>&1 | tail -5

echo "== 2. Build payload =="
# The payload includes: kernel + trust cache + Data seed + boot config
$PY ./unixnu build payload \
  -D ../UniXNU-thor-18 \
  --ipsw "$IPSW" \
  --kernel "$KERNEL" \
  --unique-chip-id 0x12345678 \
  2>&1 | tail -5

echo "== 3. Build boot image =="
$PY ./unixnu bootimg \
  -D ../UniXNU-thor-18 \
  --uefi ../uefi-pong \
  --kernel "$KERNEL" \
  --dtb "$DTB" \
  2>&1 | tail -5

echo "== 4. Build userdata =="
$PY ./unixnu build userdata \
  -D ../UniXNU-thor-18 \
  --ipsw "$IPSW" \
  2>&1 | tail -5

echo "== done =="
ls -lh $ROOT/UniXNU/out/thor/ipados-18.7.10-22H374/thor_ipados18/boot/boot.img 2>/dev/null
ls -lh $ROOT/UniXNU/out/thor/ipados-18.7.10-22H374/thor_ipados18/userdata/*.img 2>/dev/null

# Staging notes:
# - kernel is copied to tree/build/xnu/ios-obj/DEVELOPMENT_ARM64_PONG/
# - NativePrelink is copied to tree/kits/
# - IPSW kernelcache needs extraction from the .ipsw to out/ipsw/22H374/kernelcache.decompressed
# - DTB is at /tmp/thor-hw.dtb (also staged at UniXNU-thor-18/build/thor/thor.dtb)
