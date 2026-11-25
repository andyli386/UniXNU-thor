#!/bin/bash
# Local paths and the phone's fastboot serial come from the environment: THOR_ROOT, THOR_TOOLS, THOR_SERIAL.
# Temp-boot v236 from fastboot (nothing is flashed), then arm the capture-only waiter.
set -uo pipefail
out=${THOR_ROOT}/thor-out
intake=$out/v236-intake
image=$out/v236-unified/boot-thor-unixnu-v236-unified.img
fb=${THOR_TOOLS}/.tools/pt34/platform-tools/fastboot
probe=$out/session-tools/fbprobe
log=$intake/boot.log
[ "$(shasum -a 256 "$image" | cut -d' ' -f1)" = c3049c63145f01780f1dbd6774bd056b1cb65137b67cfe7cc09976a9cf02b347 ] || { echo "bad image" | tee -a "$log"; exit 2; }
sleep 3
for ((try = 1; try <= 3; try++)); do
  if "$probe" cmd "getvar:product" 4000 2>&1 | grep -q "^read .*OKAYthor"; then break; fi
  echo "$(date '+%F %T') SILENT fastboot, completing the interrupted download" | tee -a "$log"
  "$probe" fill 268435456 2>&1 | grep -v '^device ' | tee -a "$log"
  sleep 2
done
"$probe" cmd "getvar:product" 4000 2>&1 | grep -q "^read .*OKAYthor" || { echo "$(date '+%F %T') END fastboot-still-silent" | tee -a "$log"; exit 4; }
echo "$(date '+%F %T') SEEN $("$probe" cmd "getvar:current-slot" 4000 2>&1 | sed -n 's/^read .*OKAY//p') $(S="$("$probe" cmd "getvar:battery-voltage" 4000 2>&1 | sed -n 's/^read .*OKAY/battery-voltage /p')"; echo "$S")" | tee -a "$log"
if "$fb" -s ${THOR_SERIAL} boot "$image" > "$intake/boot-fastboot.txt" 2>&1; then
  echo "$(date '+%F %T') BOOT v236 $(tr '\n' ' ' < "$intake/boot-fastboot.txt" | cut -c1-160)" | tee -a "$log"
else
  echo "$(date '+%F %T') END v236-boot-failed: $(tr '\n' ' ' < "$intake/boot-fastboot.txt" | cut -c1-200)" | tee -a "$log"; exit 5
fi
# The phone leaves fastboot while it boots: arm the waiter once it is gone.
for ((i = 0; i < 20; i++)); do "$fb" devices 2>/dev/null | grep -q "^${THOR_SERIAL}" || break; sleep 1; done
echo armed
