#!/bin/bash
# Local paths and the phone's fastboot serial come from the environment: THOR_ROOT, THOR_TOOLS, THOR_SERIAL.
# Write the v239 image to boot_a, the way the v202 B image was written on 2026-11-20 and 11-23:
#   1. the compact image padded with zeros to the partition size (192 MiB),
#   2. TWRP RAM-booted from fastboot (nothing else is written),
#   3. the partition's size and its hash before the write (the content that is there must be the
#      v202 B image that was written last; otherwise it is saved here first),
#   4. dd of the padded image to /dev/block/bootdevice/by-name/boot_a, sync,
#   5. the partition's hash read back and compared with the image,
#   6. a normal reboot, which then starts v239 from flash.
set -uo pipefail
export PATH=/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin
out=${THOR_ROOT}/thor-out
intake=$out/v239-intake
serial=${THOR_SERIAL}
fb=${THOR_TOOLS}/.tools/pt34/platform-tools/fastboot
ad=/opt/homebrew/bin/adb
probe=$out/session-tools/fbprobe
ram=$out/recovery-ram-trial/thor-twrp-ram-only-candidate.img
image=$out/v239-unified/boot-thor-unixnu-v239-unified.img
padded=$intake/boot_a-v239-compact-padded.img
part=/dev/block/bootdevice/by-name/boot_a
size=201326592
known_before=632c462986eac5c3cc9f1583b013f19c373f278c15664e5f2203f981e0bb8279
log=$intake/flash-boot-a.log
note() { echo "$(date '+%F %T') $*" | tee -a "$log"; }
S() { "$ad" -s "$serial" shell -n "$@" 2>/dev/null | tr -d '\r'; }
answers() { "$probe" cmd "getvar:product" 4000 2>&1 | grep -q "^read .*OKAYthor"; }
limited() { local secs=$1; shift; "$@" & local p=$!; ( sleep "$secs"; kill $p 2>/dev/null ) & local w=$!; wait $p; local r=$?; kill $w 2>/dev/null; wait $w 2>/dev/null; return $r; }

[ -f "$log" ] && { note "a flash log already exists: refusing to run twice without looking"; exit 9; }
[ "$(shasum -a 256 "$image" | cut -d' ' -f1)" = 87d8deff766f0657661fae7652a9a5e015587d1df5e2fb93f5c4f3eab14f8c8e ] || { note "END the image is not the verified v239"; exit 2; }
[ "$(shasum -a 256 "$ram" | cut -d' ' -f1)" = 6dc604e4ce75fdae3ab89c6b9c453d51d7253dbdc237c0c41adb4cd06179ccd0 ] || { note "END bad-twrp-image"; exit 2; }
if [ ! -f "$padded" ] || [ "$(stat -f %z "$padded")" != "$size" ]; then
  /opt/homebrew/bin/python3.13 -I - "$image" "$padded" "$size" <<'PY' || { note "END padding failed"; exit 2; }
import sys
image, padded, size = sys.argv[1], sys.argv[2], int(sys.argv[3])
data = open(image, 'rb').read()
with open(padded, 'xb') as out:
    out.write(data)
    out.write(bytes(size - len(data)))
PY
fi
padded_sha=$(shasum -a 256 "$padded" | cut -d' ' -f1)
note "START image v239 compact padded: $size bytes, sha256 $padded_sha"

# ---- 1. fastboot with a steady link ------------------------------------------------
for ((try = 1; try <= 48; try++)); do answers && break; sleep 10; done
answers || { note "END fastboot-not-answering"; exit 4; }
stable=0
for ((i = 0; i < 90 && stable < 30; i++)); do
  if "$probe" info 2>/dev/null | grep -q "^device 18d1:d00d" && answers; then stable=$((stable + 2)); else stable=0; fi
  sleep 2
done
[ "$stable" -ge 30 ] || { note "END link-never-steady"; exit 4; }
note "LINK steady for 30 s"
# ---- 2. TWRP in RAM ----------------------------------------------------------------
booted=""
for ((try = 1; try <= 3; try++)); do
  [ -z "$booted" ] || break
  sleep 4
  if limited 45 "$fb" -s "$serial" boot "$ram" >> "$log" 2>&1; then booted=1; else note "TWRP upload failed (attempt $try)"; sleep 15; fi
done
[ -n "$booted" ] || { note "END twrp-boot-failed"; exit 5; }
state=""
for ((i = 0; i < 90; i++)); do state=$("$ad" -s "$serial" get-state 2>/dev/null); [ "$state" = recovery ] && break; sleep 1; done
[ "$state" = recovery ] || { note "END recovery-not-enumerated (state=$state)"; exit 6; }
note "RECOVERY $(S 'getprop ro.product.device' | head -1)"
# ---- 3. the partition as it is -------------------------------------------------------
geometry=$(S "blockdev --getsize64 $part; ls -l $part")
note "partition $geometry"
echo "$geometry" | grep -q "^$size" || { note "END partition size is not $size"; exit 7; }
before=$(S "sha256sum $part" | cut -d' ' -f1)
note "before: sha256 $before"
if [ "$before" != "$known_before" ]; then
  note "the partition does not hold the image written last: saving it as boot_a-before-v239.img"
  "$ad" -s "$serial" exec-out "dd if=$part bs=4194304 2>/dev/null" > "$intake/boot_a-before-v239.img"
  [ "$(stat -f %z "$intake/boot_a-before-v239.img")" = "$size" ] || { note "END could not save the partition"; exit 7; }
  [ "$(shasum -a 256 "$intake/boot_a-before-v239.img" | cut -d' ' -f1)" = "$before" ] || { note "END the saved partition does not match"; exit 7; }
  note "saved: boot_a-before-v239.img"
fi
# ---- 4. the write ---------------------------------------------------------------------
"$ad" -s "$serial" push "$padded" /tmp/boot_a-v239.img >> "$log" 2>&1 || { note "END push failed"; exit 8; }
pushed=$(S "sha256sum /tmp/boot_a-v239.img" | cut -d' ' -f1)
[ "$pushed" = "$padded_sha" ] || { note "END the copy on the phone differs ($pushed)"; exit 8; }
note "WRITE dd to $part"
S "dd if=/tmp/boot_a-v239.img of=$part bs=1048576 && sync" | tee -a "$log"
# ---- 5. read back ---------------------------------------------------------------------
after=$(S "sha256sum $part" | cut -d' ' -f1)
note "after: sha256 $after"
if [ "$after" != "$padded_sha" ]; then
  note "END readback differs: boot_a does not hold the v239 image"
  exit 10
fi
note "READBACK matches"
S "rm -f /tmp/boot_a-v239.img"
# ---- 6. start it ----------------------------------------------------------------------
"$ad" -s "$serial" reboot >/dev/null 2>&1
note "FLASHED v239 is in boot_a; a normal reboot was requested"
