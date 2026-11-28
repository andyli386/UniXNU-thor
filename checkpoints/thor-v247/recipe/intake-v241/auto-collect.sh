#!/bin/bash
# Local paths and the phone's fastboot serial come from the environment: THOR_ROOT, THOR_TOOLS, THOR_SERIAL.
# Capture only.  Waits (48 h) for Thor to appear in FASTBOOT, then runs collect-full.sh:
# RAM-boots TWRP (nothing is flashed), dumps the log directories raw and read-only, returns the
# phone to fastboot and stops.  It only lives while the Claude Code session is open.
set -uo pipefail
out=${THOR_ROOT}/thor-out
intake=$out/v241-intake
fb=${THOR_TOOLS}/.tools/pt34/platform-tools/fastboot
log=$intake/auto-collect.log
if [ -f "$intake/auto-collect.pid" ] && kill -0 "$(cat "$intake/auto-collect.pid")" 2>/dev/null; then exit 1; fi
echo $$ > "$intake/auto-collect.pid"
echo "$(date '+%F %T') ARMED waiting for ${THOR_SERIAL} in fastboot (48 h)" >> "$log"
deadline=$(( $(date +%s) + 48 * 3600 ))
while [ "$(date +%s)" -lt "$deadline" ]; do
  "$fb" devices 2>/dev/null | grep -q "^${THOR_SERIAL}" && break
  sleep 2
done
"$fb" devices 2>/dev/null | grep -q "^${THOR_SERIAL}" || { echo "$(date '+%F %T') END timeout-no-phone" >> "$log"; exit 3; }
echo "$(date '+%F %T') SEEN" >> "$log"
exec /bin/bash "$intake/collect-full.sh" v241
