#!/bin/bash
# Local paths and the phone's fastboot serial come from the environment: THOR_ROOT, THOR_TOOLS, THOR_SERIAL.
# Wait (48 h) for Thor in FASTBOOT with a steady USB link, temp-boot v236 (nothing is flashed), then
# hand over to the capture-only waiter (auto-collect.sh), which pulls the logs the next time the
# phone shows up in fastboot.  It only lives while the Claude Code session is open.
set -uo pipefail
export PATH=/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin
out=${THOR_ROOT}/thor-out
intake=$out/v236-intake
serial=${THOR_SERIAL}
fb=${THOR_TOOLS}/.tools/pt34/platform-tools/fastboot
probe=$out/session-tools/fbprobe
log=$intake/auto-boot.log
if [ -f "$intake/auto-boot.pid" ] && kill -0 "$(cat "$intake/auto-boot.pid")" 2>/dev/null; then exit 1; fi
echo $$ > "$intake/auto-boot.pid"
note() { echo "$(date '+%F %T') $*" | tee -a "$log"; }
note "ARMED: waiting for $serial in fastboot with a steady link (48 h), then v236 is temp-booted"
deadline=$(( $(date +%s) + 48 * 3600 ))
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
/bin/bash "$intake/boot-v236.sh" >> "$log" 2>&1
rc=$?
[ "$rc" -eq 0 ] || { note "END v236-boot-failed rc=$rc"; exit $((rc + 10)); }
# The phone leaves fastboot while it boots; the waiter must not see that as "phone in fastboot".
for ((i = 0; i < 30; i++)); do "$fb" devices 2>/dev/null | grep -q "^$serial" || break; sleep 1; done
sleep 5
rm -f "$intake/auto-collect.pid"
exec /bin/bash "$intake/auto-collect.sh"
