#!/bin/bash
# Local paths and the phone's fastboot serial come from the environment: THOR_ROOT, THOR_TOOLS, THOR_SERIAL.
set -euo pipefail
export PATH=/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin
src=$(cd "$(dirname "$0")" && pwd)
adb=/opt/homebrew/bin/adb
test "$("$adb" -s ${THOR_SERIAL} get-state)" = recovery
test "$("$adb" -s ${THOR_SERIAL} shell getprop ro.product.device | tr -d '\r')" = thor
out=$(mktemp -d "$src/../recovery-v227-history-$(date -u +%Y%m%dT%H%M%SZ).XXXXXX")
echo "Evidence directory: $out"
python3.13 -IB "$src/prepare.py" "$out"
cd "$out"
THOR_APP_DIAGNOSTICS=1 /bin/bash capture-readonly.sh
python3.13 -IB "$src/report.py" .
