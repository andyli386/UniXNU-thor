#!/bin/bash
# Local paths and the phone's fastboot serial come from the environment: THOR_ROOT, THOR_TOOLS, THOR_SERIAL.
# Wait (48 h) for Thor to appear in FASTBOOT.  When it appears, start the keep-alive (a read-only query
# every 15 s, so the phone does not leave fastboot by itself: a normal boot that follows would use one
# of the unverified slot's retries) and exit, which notifies the session.  Stop the keep-alive before any
# upload (kill "$(cat keepalive.pid)").  It only lives while the Claude Code session is open.
set -uo pipefail
out=${THOR_ROOT}/thor-out
intake=$out/v243-intake
fb=${THOR_TOOLS}/.tools/pt34/platform-tools/fastboot
log=$intake/fastboot-waiter.log
if [ -f "$intake/fastboot-waiter.pid" ] && kill -0 "$(cat "$intake/fastboot-waiter.pid")" 2>/dev/null; then exit 1; fi
echo $$ > "$intake/fastboot-waiter.pid"
echo "$(date '+%F %T') ARMED waiting for ${THOR_SERIAL} in fastboot (48 h)" >> "$log"
deadline=$(( $(date +%s) + 48 * 3600 ))
while [ "$(date +%s)" -lt "$deadline" ]; do
  "$fb" devices 2>/dev/null | grep -q "^${THOR_SERIAL}" && break
  sleep 2
done
"$fb" devices 2>/dev/null | grep -q "^${THOR_SERIAL}" || { echo "$(date '+%F %T') END timeout-no-phone" >> "$log"; exit 3; }
echo "$(date '+%F %T') SEEN; keep-alive started" >> "$log"
nohup /bin/bash "$intake/keepalive.sh" >/dev/null 2>&1 &
echo $! > "$intake/keepalive.pid"
