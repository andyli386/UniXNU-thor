#!/bin/bash
# Local paths and the phone's fastboot serial come from the environment: THOR_ROOT, THOR_TOOLS, THOR_SERIAL.
# Wait (48 h) for Thor to appear in FASTBOOT, then install v244 in one TWRP session: the logs of the session
# that just ended are taken first (read-only), boot_a is written through TWRP and verified, the slot state is
# read in the bootloader and the phone boots v244.  Then the log collector for the v244 boot is armed, so the
# next visit to fastboot takes its logs.  It only lives while the Claude Code session is open.
set -uo pipefail
out=${THOR_ROOT}/thor-out
intake=$out/v244-intake
fb=${THOR_TOOLS}/.tools/pt34/platform-tools/fastboot
image=$out/v244-unified/boot-thor-unixnu-v244-unified.img
sha=03a5c59c6a06dc59f6de7d224cc673dcdb0c31baadcd15f45a42bb7d26f359b6
log=$intake/auto-install.log
echo "$(date '+%F %T') ARMED waiting for ${THOR_SERIAL} in fastboot (48 h)" >> "$log"
deadline=$(( $(date +%s) + 48 * 3600 ))
while [ "$(date +%s)" -lt "$deadline" ]; do
  "$fb" devices 2>/dev/null | grep -q "^${THOR_SERIAL}" && break
  sleep 2
done
"$fb" devices 2>/dev/null | grep -q "^${THOR_SERIAL}" || { echo "$(date '+%F %T') END timeout-no-phone" >> "$log"; exit 3; }
echo "$(date '+%F %T') SEEN: installing v244" >> "$log"
/bin/bash "$intake/install-image.sh" "$image" "$sha" >> "$intake/install.out" 2>&1
rc=$?
echo "$(date '+%F %T') INSTALL rc=$rc $(tail -3 "$intake/install.out" | tr '\n' ' ' | cut -c1-400)" >> "$log"
[ "$rc" = 0 ] || exit 4
# The phone stays enumerated for a moment after the reboot command: the collector must not take that
# for a new visit, so first wait until it has gone (up to 3 minutes), then arm it for the next visit.
gone=""
for ((i = 0; i < 90; i++)); do
  "$fb" devices 2>/dev/null | grep -q "^${THOR_SERIAL}" || { gone=1; break; }
  sleep 2
done
[ -n "$gone" ] || { echo "$(date '+%F %T') END still-in-fastboot-after-the-reboot-command (collector not armed)" >> "$log"; exit 5; }
echo "$(date '+%F %T') the phone left fastboot; arming the log collector" >> "$log"
exec /bin/bash "$intake/auto-collect.sh"
