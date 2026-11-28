#!/bin/bash
# Local paths and the phone's fastboot serial come from the environment: THOR_ROOT, THOR_TOOLS, THOR_SERIAL.
# Temp-boot v237 from fastboot (nothing is flashed), then arm the capture-only waiter.
set -uo pipefail
out=${THOR_ROOT}/thor-out
intake=$out/v237-intake
image=$out/v237-unified/boot-thor-unixnu-v237-unified.img
fb=${THOR_TOOLS}/.tools/pt34/platform-tools/fastboot
probe=$out/session-tools/fbprobe
log=$intake/boot.log
[ "$(shasum -a 256 "$image" | cut -d' ' -f1)" = ee4a590a7b59a7ee8bdba821f19555d6c3514a54eb5481a0f37774a722f26f50 ] || { echo "bad image" | tee -a "$log"; exit 2; }
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
  echo "$(date '+%F %T') BOOT v237 $(tr '\n' ' ' < "$intake/boot-fastboot.txt" | cut -c1-160)" | tee -a "$log"
else
  echo "$(date '+%F %T') END v237-boot-failed: $(tr '\n' ' ' < "$intake/boot-fastboot.txt" | cut -c1-200)" | tee -a "$log"; exit 5
fi
# The phone leaves fastboot while it boots: arm the waiter once it is gone.
for ((i = 0; i < 20; i++)); do "$fb" devices 2>/dev/null | grep -q "^${THOR_SERIAL}" || break; sleep 1; done
echo armed
