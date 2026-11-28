#!/bin/bash
# Local paths and the phone's fastboot serial come from the environment: THOR_ROOT, THOR_TOOLS, THOR_SERIAL.
# Temporary boot of the v242 image that is also in boot_a (nothing is flashed): the comparison run for the
# partition-table check of the firmware LUN, which passed in every temporary boot recorded so far and failed in
# the four boots from flash with the v239 and v241 kernels (while eight boots from flash of the older v202 B
# image passed).  Run with the phone in FASTBOOT; afterwards arm auto-collect-temp.sh again.
#   1. waits until the link has answered for 30 seconds without one gap,
#   2. checks the image's SHA-256 against the record and the slot,
#   3. sends the image with `fastboot boot` (under a watchdog).  boot_a is not touched.
set -uo pipefail
export PATH=/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin
out=${THOR_ROOT}/thor-out
intake=$out/v242-intake
serial=${THOR_SERIAL}
fb=${THOR_TOOLS}/.tools/pt34/platform-tools/fastboot
probe=$out/session-tools/fbprobe
log=$intake/boot-temp.log
image=${1:-$out/v242-unified/boot-thor-unixnu-v242-unified.img}
expect=${2:-07d615c79a0ef81ea1aa56086c0daf048bd66f165de2ea6af03c38e16579d581}
note() { echo "$(date '+%F %T') $*" | tee -a "$log"; }
limited() { local secs=$1; shift; "$@" & local p=$!; ( sleep "$secs"; kill $p 2>/dev/null ) & local w=$!; wait $p; local r=$?; kill $w 2>/dev/null; wait $w 2>/dev/null; return $r; }
answers() { "$probe" cmd "getvar:product" 4000 2>&1 | grep -q "^read .*OKAYthor"; }
getvar() { limited 10 "$fb" -s "$serial" getvar "$1" 2>&1 | tr -d '\r' | grep -E "^$1:|FAIL|error" | head -1 | tr -s " "; }

note "START temporary boot of $(basename "$image")"
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

# ---- 2. what is about to be booted -------------------------------------------------------------------------
slot=$(getvar current-slot); [ "$slot" = "current-slot: a" ] || { note "END wrong-slot ($slot)"; exit 5; }
note "CHECKS slot a (boot_a keeps the same image)"

# ---- 3. temporary boot -------------------------------------------------------------------------------------
if limited 90 "$fb" -s "$serial" boot "$image" >> "$log" 2>&1; then
  note "BOOT $(tail -3 "$log" | tr '\n' ' ' | tr -s ' ' | cut -c1-200)"
else
  rc=$?
  note "END boot-failed rc=$rc $(tail -3 "$log" | tr '\n' ' ' | cut -c1-200)"
  answers || { "$probe" fill 268435456 2>&1 | grep -v '^device ' | tee -a "$log"; }
  exit 6
fi
for ((i = 0; i < 20; i++)); do "$fb" devices 2>/dev/null | grep -q "^${THOR_SERIAL}" || break; sleep 1; done
note "END the phone left fastboot"
