#!/bin/bash
# Local paths and the phone's fastboot serial come from the environment: THOR_ROOT, THOR_TOOLS, THOR_SERIAL.
# Wait (48 h) for Thor in FASTBOOT with a steady USB link, then flash v239 to boot_a (flash-boot-a.sh)
# and let it reboot normally.  The phone left fastboot on its own before the first attempt.
set -uo pipefail
export PATH=/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin
out=${THOR_ROOT}/thor-out
intake=$out/v239-intake
serial=${THOR_SERIAL}
fb=${THOR_TOOLS}/.tools/pt34/platform-tools/fastboot
probe=$out/session-tools/fbprobe
log=$intake/auto-flash.log
if [ -f "$intake/auto-flash.pid" ] && kill -0 "$(cat "$intake/auto-flash.pid")" 2>/dev/null; then exit 1; fi
echo $$ > "$intake/auto-flash.pid"
note() { echo "$(date '+%F %T') $*" | tee -a "$log"; }
note "ARMED: waiting for $serial in fastboot with a steady link (48 h), then v239 is flashed to boot_a"
deadline=$(( $(date +%s) + 48 * 3600 ))
stable=0
while [ "$(date +%s)" -lt "$deadline" ]; do
  if "$fb" devices 2>/dev/null | grep -q "^$serial" && "$probe" cmd "getvar:product" 3000 2>&1 | grep -q "OKAYthor"; then
    stable=$((stable + 1))
    [ "$stable" -ge 10 ] && break
  else
    [ "$stable" -gt 0 ] && note "link not steady yet (reset after $stable probes)"
    stable=0
  fi
  sleep 2
done
[ "$stable" -ge 10 ] || { note "END timeout-no-phone"; exit 3; }
note "SEEN, link steady"
exec /bin/bash "$intake/flash-boot-a.sh"
