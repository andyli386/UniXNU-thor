#!/bin/bash
# Local paths and the phone's fastboot serial come from the environment: THOR_ROOT, THOR_TOOLS, THOR_SERIAL.
# Read-only look for the phone's own Wi-Fi address.  Runs with the phone in FASTBOOT: RAM-boots TWRP (nothing is
# flashed), reads a few device-tree / property / interface files, returns the phone to fastboot and stops.
# Every command is a read; nothing is written to the phone.
set -uo pipefail
export PATH=/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin
out=${THOR_ROOT}/thor-out
intake=$out/v245-intake
serial=${THOR_SERIAL}
fb=${THOR_TOOLS}/.tools/pt34/platform-tools/fastboot
ad=/opt/homebrew/bin/adb
probe=$out/session-tools/fbprobe
ram=$out/recovery-ram-trial/thor-twrp-ram-only-candidate.img
log=$intake/mac-probe.log
result=$intake/mac-probe.out
note() { echo "$(date '+%F %T') $*" | tee -a "$log"; }

[ "$(shasum -a 256 "$ram" | cut -d' ' -f1)" = 6dc604e4ce75fdae3ab89c6b9c453d51d7253dbdc237c0c41adb4cd06179ccd0 ] || { note "END bad-twrp-image"; exit 2; }
S() { "$ad" -s "$serial" shell -n "$@" 2>/dev/null | tr -d '\r'; }
answers() { "$probe" cmd "getvar:product" 4000 2>&1 | grep -q "^read .*OKAYthor"; }
limited() { local secs=$1; shift; "$@" & local p=$!; ( sleep "$secs"; kill $p 2>/dev/null ) & local w=$!; wait $p; local r=$?; kill $w 2>/dev/null; wait $w 2>/dev/null; return $r; }

for ((try = 1; try <= 18; try++)); do
  answers && break
  sleep 10
done
answers || { note "END fastboot-not-answering"; exit 4; }
stable=0
for ((i = 0; i < 90 && stable < 30; i++)); do
  if "$probe" info 2>/dev/null | grep -q "^device 18d1:d00d" && "$probe" cmd "getvar:product" 3000 2>&1 | grep -q "OKAYthor"; then
    stable=$((stable + 2))
  else
    stable=0
  fi
  sleep 2
done
[ "$stable" -ge 30 ] || { note "END link-never-steady"; exit 4; }
note "LINK steady for 30 s"
sleep 4
limited 45 "$fb" -s "$serial" boot "$ram" >> "$log" 2>&1 || { note "END twrp-boot-failed"; exit 5; }
state=""
for ((i = 0; i < 90; i++)); do state=$("$ad" -s "$serial" get-state 2>/dev/null); [ "$state" = recovery ] && break; sleep 1; done
[ "$state" = recovery ] || { note "END recovery-not-enumerated (state=$state)"; exit 6; }
device=$(S getprop ro.product.device)
[ "$device" = thor ] || { note "END wrong-device ($device)"; S reboot bootloader; exit 7; }
note "RECOVERY thor: reading"
{
  echo "## interfaces"
  S 'for n in /sys/class/net/*/address; do echo "$n $(cat $n 2>/dev/null)"; done'
  echo "## device-tree nodes and properties with mac, wlan or wifi in their name"
  S 'find /sys/firmware/devicetree/base -maxdepth 5 \( -iname "*mac*" -o -iname "*wlan*" -o -iname "*wifi*" \) 2>/dev/null | head -60'
  echo "## property names in the flattened tree that mention mac, wlan or wifi"
  S 'grep -a -o -i "[A-Za-z0-9_,-]*\(mac\|wlan\|wifi\)[A-Za-z0-9_,-]*" /sys/firmware/fdt 2>/dev/null | sort -u | head -60'
  echo "## boot arguments that mention mac, wlan or wifi"
  S 'tr " " "\n" < /proc/device-tree/chosen/bootargs 2>/dev/null | grep -i "mac\|wlan\|wifi" | head -20'
  echo "## properties that mention mac, wlan or wifi"
  S 'getprop 2>/dev/null | grep -i "mac\|wlan\|wifi" | head -30'
  echo "## partitions"
  S 'ls -l /dev/block/bootdevice/by-name 2>/dev/null | awk "{print \$8, \$10}" | head -80'
} > "$result" 2>&1
# Read-only scan of the small data partitions for a stored Wi-Fi address (size and a few matching lines).
{
  echo "## small partitions: size, then lines with a Wi-Fi address"
  S 'for p in /dev/block/bootdevice/by-name/*; do n=$(basename $p); case "$n" in *devinfo*|*persist*|*misc*|*mdb*|*cbdd*|*msm*|*secdata*|*devcfg*|*wifi*|*wlan*|*bluetooth*|*bt*|*fsg*|*fsc*|*modemst1*|*modemst2*|*dip*|*limits*) s=$(blockdev --getsize64 $p 2>/dev/null); echo "$n $s";; esac; done'
  S 'for p in /dev/block/bootdevice/by-name/persist /dev/block/bootdevice/by-name/devinfo /dev/block/bootdevice/by-name/misc; do [ -e "$p" ] || continue; s=$(blockdev --getsize64 $p); [ "$s" -le 33554432 ] || { echo "-- $p too large to scan ($s bytes)"; continue; }; echo "-- $p ($s bytes)"; if command -v strings >/dev/null 2>&1; then strings -n 6 "$p" | grep -i "wlan_mac\|wifi_mac\|macaddr\|mac_addr\|wlan\.mac\|wifi\.mac" | head -20; else grep -a -o -i "[A-Za-z0-9_=.:-]*\(wlan_mac\|wifi_mac\|macaddr\|mac_addr\|wlan\.mac\|wifi\.mac\)[A-Za-z0-9_=.:-]*" "$p" | sort -u | head -20; fi; done'
} >> "$result" 2>&1
note "READ done: $(wc -l < "$result" | tr -d ' ') lines in $result"
S reboot bootloader
sleep 20
for ((i = 0; i < 60; i++)); do answers && break; sleep 2; done
answers && note "END back in fastboot" || note "END phone did not answer in fastboot within 2 minutes"
