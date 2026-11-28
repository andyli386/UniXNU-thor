#!/bin/bash
# Local paths and the phone's fastboot serial come from the environment: THOR_ROOT, THOR_TOOLS, THOR_SERIAL.
# Flash a compact Thor image to boot_a from this Mac with fastboot (the way v239 was flashed on 2026-11-26).
# Runs with the phone already in FASTBOOT:
#   1. waits until the link has answered for 30 seconds without one gap (it re-enumerates for minutes after
#      the phone enters fastboot), completing an interrupted download in a silent device,
#   2. checks the image's SHA-256 against the record, and the slot, the lock state and the partition size,
#   3. sends and writes the image (every fastboot call under a watchdog), then reboots the phone.
# Nothing else on the phone is written.  The bootloader cannot read a partition back (no `fastboot fetch`).
set -uo pipefail
export PATH=/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin
out=${THOR_ROOT}/thor-out
intake=$out/v241-intake
serial=${THOR_SERIAL}
fb=${THOR_TOOLS}/.tools/pt34/platform-tools/fastboot
probe=$out/session-tools/fbprobe
log=$intake/flash-fastboot.log
image=${1:?compact image}
expect=${2:?expected sha256 of the image}
note() { echo "$(date '+%F %T') $*" | tee -a "$log"; }
limited() { local secs=$1; shift; "$@" & local p=$!; ( sleep "$secs"; kill $p 2>/dev/null ) & local w=$!; wait $p; local r=$?; kill $w 2>/dev/null; wait $w 2>/dev/null; return $r; }
answers() { "$probe" cmd "getvar:product" 4000 2>&1 | grep -q "^read .*OKAYthor"; }
getvar() { limited 10 "$fb" -s "$serial" getvar "$1" 2>&1 | tr -d '\r' | grep -E "^$1:|FAIL|error" | head -1 | tr -s " "; }

note "START $(basename "$image")"
[ -f "$image" ] || { note "END no-such-image"; exit 2; }
got=$(shasum -a 256 "$image" | cut -d' ' -f1)
[ "$got" = "$expect" ] || { note "END image-hash-mismatch got=$got"; exit 2; }
note "IMAGE sha256 $expect $(stat -f %z "$image") bytes"

# ---- 1. a steady link --------------------------------------------------------------------------------------
for ((try = 1; try <= 48; try++)); do
  answers && break
  if "$probe" info 2>/dev/null | grep -q "^device 18d1:d00d"; then
    note "SILENT fastboot (attempt $try): completing the interrupted download"
    "$probe" fill 268435456 2>&1 | grep -v '^device ' | tee -a "$log"
  fi
  sleep 10
done
answers || { note "END fastboot-still-silent"; exit 4; }
stable=0
for ((i = 0; i < 90 && stable < 30; i++)); do
  if "$probe" info 2>/dev/null | grep -q "^device 18d1:d00d" && "$probe" cmd "getvar:product" 3000 2>&1 | grep -q "OKAYthor"; then
    stable=$((stable + 2))
  else
    stable=0
    note "link not steady yet (reset after $i probes)"
  fi
  sleep 2
done
[ "$stable" -ge 30 ] || { note "END link-never-steady"; exit 4; }
note "LINK steady for 30 s"

# ---- 2. what is about to be written -----------------------------------------------------------------------
slot=$(getvar current-slot); [ "$slot" = "current-slot: a" ] || { note "END wrong-slot ($slot)"; exit 5; }
unlocked=$(getvar unlocked); [ "$unlocked" = "unlocked: yes" ] || { note "END not-unlocked ($unlocked)"; exit 5; }
size=$(getvar partition-size:boot_a); [ "$size" = "partition-size:boot_a: 0xC000000" ] || { note "END unexpected-partition-size ($size)"; exit 5; }
note "CHECKS slot a, unlocked, boot_a 0xC000000"

# ---- 3. flash ---------------------------------------------------------------------------------------------
if limited 90 "$fb" -s "$serial" flash boot_a "$image" >> "$log" 2>&1; then
  note "FLASH $(tail -4 "$log" | tr '\n' ' ' | cut -c1-240)"
else
  rc=$?
  note "END flash-failed rc=$rc $(tail -3 "$log" | tr '\n' ' ' | cut -c1-200)"
  answers || { "$probe" fill 268435456 2>&1 | grep -v '^device ' | tee -a "$log"; }
  exit 6
fi

# ---- 4. normal reboot: the phone boots what boot_a now holds -----------------------------------------------
if limited 20 "$fb" -s "$serial" reboot >> "$log" 2>&1; then note "REBOOT requested"; else note "REBOOT command not confirmed (rc=$?): enter fastboot again and run: fastboot reboot"; fi
note "END done"
