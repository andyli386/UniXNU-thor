#!/bin/bash
# Local paths and the phone's fastboot serial come from the environment: THOR_ROOT, THOR_TOOLS, THOR_SERIAL.
# Keep the fastboot session busy with a read-only query every 15 s while a build is prepared:
# on 2026-11-26 the phone left an idle fastboot by itself after 13-25 minutes.  Stops when the phone
# has not answered for 3 minutes or after 50 minutes.
out=${THOR_ROOT}/thor-out
probe=$out/session-tools/fbprobe
log=$out/v241-intake/keepalive.log
deadline=$(( $(date +%s) + 50 * 60 )); missed=0
while [ "$(date +%s)" -lt "$deadline" ]; do
  if "$probe" cmd "getvar:product" 3000 2>&1 | grep -q "OKAYthor"; then missed=0; else missed=$((missed + 1)); fi
  [ "$missed" -ge 12 ] && { echo "$(date '+%F %T') END no answer for 3 minutes" >> "$log"; exit 0; }
  sleep 15
done
echo "$(date '+%F %T') END 50 minutes" >> "$log"
