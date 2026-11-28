#!/bin/bash
# Local paths and the phone's fastboot serial come from the environment: THOR_ROOT, THOR_TOOLS, THOR_SERIAL.
# Collect the v241 boot's evidence (same procedure as v236).  Runs with the phone already in FASTBOOT:
#   1. RAM-boots TWRP (nothing is flashed),
#   2. dumps the log directories raw and read-only (full snapshot: kernel console, app logs,
#      crash reports, unified log text),
#   3. runs the usual history capture on top,
#   4. returns the phone to fastboot and stops.  Nothing is booted afterwards.
#
# 2026-11-25 13:18: the automatic waiter found the phone 4 s after it enumerated and the USB link
# bounced in the middle of the 123 MB upload.  The phone then sat in fastboot reading no commands
# (it was still counting download bytes), and the fastboot tool waits forever on a silent device.
# So here every fastboot call is time-limited, the device is given time to settle, and a silent
# device is brought back by completing its download with empty bytes (session-tools/fbprobe fill).
set -uo pipefail
export PATH=/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin
out=${THOR_ROOT}/thor-out
intake=$out/v241-intake
serial=${THOR_SERIAL}
fb=${THOR_TOOLS}/.tools/pt34/platform-tools/fastboot
ad=/opt/homebrew/bin/adb
probe=$out/session-tools/fbprobe
ram=$out/recovery-ram-trial/thor-twrp-ram-only-candidate.img
log=$intake/collect-full.log
tag=${1:-v241}
note() { echo "$(date '+%F %T') $*" | tee -a "$log"; }

[ "$(shasum -a 256 "$ram" | cut -d' ' -f1)" = 6dc604e4ce75fdae3ab89c6b9c453d51d7253dbdc237c0c41adb4cd06179ccd0 ] || { note "END bad-twrp-image"; exit 2; }
S() { "$ad" -s "$serial" shell -n "$@" 2>/dev/null | tr -d '\r'; }
answers() { "$probe" cmd "getvar:product" 4000 2>&1 | grep -q "^read .*OKAYthor"; }
# The fastboot tool never times out on a silent device: run it under a watchdog.
limited() { local secs=$1; shift; "$@" & local p=$!; ( sleep "$secs"; kill $p 2>/dev/null ) & local w=$!; wait $p; local r=$?; kill $w 2>/dev/null; wait $w 2>/dev/null; return $r; }

# The link can re-enumerate for minutes after the phone enters fastboot (17:51 on 2026-11-25: one
# disconnect every 0.5-3 s for 4.5 minutes).  Wait for an answer instead of giving up after 10 s:
# up to 8 minutes, and a device that is silent gets its interrupted download completed.
for ((try = 1; try <= 48; try++)); do
  answers && break
  "$probe" info 2>/dev/null | grep -q "^device 18d1:d00d" && {
    note "SILENT fastboot (attempt $try): completing the interrupted download"
    "$probe" fill 268435456 2>&1 | grep -v '^device ' | tee -a "$log"
  }
  sleep 10
done
answers || { note "END fastboot-still-silent"; exit 4; }
# 20:35 on 2026-11-25: the waiter answered 4 s after the phone enumerated and three uploads in a
# row broke on the still flapping link; fastboot then sat in the middle of a download until
# fbprobe fill completed it.  Upload only after the device has answered for 30 s without one gap.
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
# ---- 1. TWRP in RAM ------------------------------------------------------------------
booted=""
for ((try = 1; try <= 3; try++)); do
  [ -z "$booted" ] || break
  sleep 4
  if limited 45 "$fb" -s "$serial" boot "$ram" >> "$log" 2>&1; then booted=1; else
    note "TWRP upload failed (attempt $try): $(tail -2 "$log" | tr '\n' ' ' | cut -c1-200)"
    sleep 15
    answers || { "$probe" fill 268435456 2>&1 | grep -v '^device ' | tee -a "$log"; sleep 15; }
  fi
done
[ -n "$booted" ] || { note "END twrp-boot-failed"; exit 5; }
state=""
for ((i = 0; i < 90; i++)); do state=$("$ad" -s "$serial" get-state 2>/dev/null); [ "$state" = recovery ] && break; sleep 1; done
[ "$state" = recovery ] || { note "END recovery-not-enumerated (state=$state)"; exit 6; }
note "RECOVERY $(S 'getprop ro.product.device; cat /proc/sys/kernel/random/boot_id' | tr '\n' ' ')"

# ---- 2. full read-only snapshot ------------------------------------------------------
dest=$out/recovery-$tag-full-$(date -u +%Y%m%dT%H%M%SZ)
echo "$dest" > "$intake/full-dump-dir.txt"
/opt/homebrew/bin/python3.13 -I "$out/v228-intake/full-snapshot.py" "$out/../UniXNU-pong/tools/apfs" dump "$dest" \
  logs mobile/Library/Logs MobileSoftwareUpdate mobile/Library/Preferences tmp db/diagnostics db/uuidtext \
  > "$intake/full-dump.log" 2>&1
note "SNAPSHOT rc=$? $(tail -1 "$intake/full-dump.log") $(du -sh "$dest" | cut -f1) -> $dest"

# ---- 3. history capture --------------------------------------------------------------
stamp=$(date -u +%Y%m%dT%H%M%SZ)
cap=$out/capture-$stamp
mkdir -p "$cap"
( cd "$out" && /bin/bash v227-history-work/capture-history.sh ) > "$cap/logs.txt" 2>&1
note "LOGS rc=$? $(grep -c . "$cap/logs.txt") lines; $(grep -m1 'Evidence directory' "$cap/logs.txt")"

# ---- 4. back to fastboot, stop -------------------------------------------------------
"$ad" -s "$serial" reboot bootloader >/dev/null 2>&1
present=""
for ((i = 0; i < 45; i++)); do "$fb" devices 2>/dev/null | grep -q "^$serial" && present=1; [ -n "$present" ] || sleep 2; [ -z "$present" ] || break; done
[ -n "$present" ] || { note "END collected-but-not-back-in-fastboot"; exit 7; }
sleep 6
answers && note "END ok-collected-back-in-fastboot" || note "END collected-back-in-fastboot-silent (run $0 again to unwedge; it will re-capture)"
