#!/bin/bash
# Local paths and the phone's fastboot serial come from the environment: THOR_ROOT, THOR_TOOLS, THOR_SERIAL.
# Wait (48 h) until the v236 collector has pulled the v236 logs and the phone is back in FASTBOOT
# with a steady USB link, temp-boot v237 (nothing is flashed), then hand over to the capture-only
# waiter (auto-collect.sh), which pulls the v237 logs the next time the phone shows up in fastboot.
# If the v236 collector is gone without having collected, its collection runs here first, so the
# v236 logs (the first with the freeze fix) are not overwritten by v237's.  This script only lives
# while the Claude Code session is open.
set -uo pipefail
export PATH=/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin
out=${THOR_ROOT}/thor-out
intake=$out/v237-intake
serial=${THOR_SERIAL}
fb=${THOR_TOOLS}/.tools/pt34/platform-tools/fastboot
probe=$out/session-tools/fbprobe
log=$intake/auto-boot.log
if [ -f "$intake/auto-boot.pid" ] && kill -0 "$(cat "$intake/auto-boot.pid")" 2>/dev/null; then exit 1; fi
echo $$ > "$intake/auto-boot.pid"
note() { echo "$(date '+%F %T') $*" | tee -a "$log"; }
collected() { tail -3 "$out/v236-intake/collect-full.log" 2>/dev/null | grep -q "END .*collected-back-in-fastboot"; }
note "ARMED: waiting for the v236 collector, then $serial in fastboot with a steady link (48 h), then v237 is temp-booted"
deadline=$(( $(date +%s) + 48 * 3600 ))
# The modem server's first run on the device is reviewed before this image boots: review.ok lets it
# boot, review.hold stops the waiter for a rebuild.  Without either marker the main session's own
# review (v237-intake/review.md) stands after 20 minutes.
gate=$(( $(date +%s) + 1200 ))
# 1. the v236 collector owns the phone until it is done
old=$out/v236-intake/auto-collect.pid
while [ "$(date +%s)" -lt "$deadline" ]; do
  [ -f "$old" ] && kill -0 "$(cat "$old")" 2>/dev/null || break
  sleep 15
done
# 2. v236's logs first, if nobody collected them
if ! collected; then
  note "the v236 collector is gone without collecting: collecting v236 here first"
  for ((try = 1; try <= 5; try++)); do
    for ((i = 0; i < 21600; i++)); do "$fb" devices 2>/dev/null | grep -q "^$serial" && break; sleep 2; done
    "$fb" devices 2>/dev/null | grep -q "^$serial" || { note "END timeout-no-phone"; exit 3; }
    /bin/bash "$out/v236-intake/collect-full.sh" v236
    collected && break
    note "v236 collection attempt $try did not finish"
    sleep 60
  done
fi
collected && note "v236 logs collected" || note "WARNING v236 logs could not be collected; booting v237 anyway"
while [ ! -f "$intake/review.ok" ] && [ ! -f "$intake/review.hold" ] && [ "$(date +%s)" -lt "$gate" ]; do sleep 15; done
[ -f "$intake/review.hold" ] || { true; }
if [ -f "$intake/review.hold" ]; then note "END review-hold (the image is being rebuilt)"; exit 3; fi
[ -f "$intake/review.ok" ] || note "no review marker within 20 min: booting on the main session's review"
# 3. a steady link, then v237
stable=0
while [ "$(date +%s)" -lt "$deadline" ]; do
  if "$fb" devices 2>/dev/null | grep -q "^$serial" && "$probe" cmd "getvar:product" 3000 2>&1 | grep -q "OKAYthor"; then
    stable=$((stable + 1))
    [ "$stable" -ge 15 ] && break
  else
    [ "$stable" -gt 0 ] && note "link not steady yet (reset after $stable probes)"
    stable=0
  fi
  sleep 2
done
[ "$stable" -ge 15 ] || { note "END timeout-no-phone"; exit 3; }
note "SEEN, link steady for 30 s"
/bin/bash "$intake/boot-v237.sh" >> "$log" 2>&1
rc=$?
[ "$rc" -eq 0 ] || { note "END v237-boot-failed rc=$rc"; exit $((rc + 10)); }
# The phone leaves fastboot while it boots; the waiter must not see that as "phone in fastboot".
for ((i = 0; i < 30; i++)); do "$fb" devices 2>/dev/null | grep -q "^$serial" || break; sleep 1; done
sleep 5
rm -f "$intake/auto-collect.pid"
exec /bin/bash "$intake/auto-collect.sh"
