#!/bin/bash
# Local paths and the phone's fastboot serial come from the environment: THOR_ROOT, THOR_TOOLS, THOR_SERIAL.
# analyze.sh: split the last boot's kernel console out of the mirror file and list what matters.
#   analyze.sh <dump directory (recovery-v239-full-…)> [detail]
set -uo pipefail
out=${THOR_ROOT}/thor-out
d=${1:?dump directory}
mkdir -p "$out/v239-intake/logs"
/opt/homebrew/bin/python3.13 -I - "$d" "$out" <<'PY'
import sys
from pathlib import Path
d, out = Path(sys.argv[1]), Path(sys.argv[2])
b = (d / 'Data/PongConsole-kernel-v235.txt').read_bytes()                 # after the v239 run
c = open(out / 'recovery-v238-full-20261126T135305Z/Data/PongConsole-kernel-v235.txt', 'rb').read()   # after v238's
i = min(len(b), len(c)) - 1
while i >= 0 and b[i] == c[i]:
    i -= 1
nl = b.rfind(b'\n', 0, i + 1)
part = b[:nl + 1]
(out / 'v239-intake/logs/console-v239-kernel.txt').write_bytes(part)
lines = part.decode('utf-8', 'replace').split('\n')
print('v239 console: %d bytes, %d lines; first %s; last %s' % (len(part), len(lines), lines[0][:44], lines[-2][:44] if len(lines) > 1 else ''))
# the userland mirror of the kernel console written by runningboardd (starts earlier in boot)
try:
    (out / 'v239-intake/logs/console-v239-runningboardd.txt').write_bytes((d / 'Data/PongConsole.txt').read_bytes())
except OSError as e:
    print('PongConsole.txt not copied:', e)
PY
f=$out/v239-intake/logs/console-v239-kernel.txt
section() { echo; echo "=== $1"; }
section "version / slot / probe"
 grep -n "THOR WITNESS V239\|THOR WITNESS V235: panic record slot\|THOR PROBE V239\|THOR SETTINGS V180: staged" "$f" | cut -c1-200 | head -12
section "battery / adsp chain"
 grep -n "THOR BATTERY\|pmic-glink\|THOR ADSP\|adsp_apps\|THOR MODEM POWER V175" "$f" | cut -c1-200 | head -30
section "configd bootstrap and samples"
 grep -n "PONG PROBE configd" "$f" | sed -E 's/PONG PROBE configd\[[0-9]+\]: Pong Probe: //' | cut -c1-330 | head -60
section "wifid"
 grep -n "PONG PROBE wifid\|THOR PROBE V239: wifid" "$f" | sed -E 's/PONG PROBE wifid\[[0-9]+\]: Pong Probe: //' | cut -c1-330 | head -70
section "airportd"
 grep -c "PONG PROBE airportd" "$f"
section "driver requests from userland (wifi-net)"
 grep -o "wifi-net: [a-z-]* type [0-9]* \(get\|set\)[^,]*\|wifi-net: power[^,]*\|wifi-net: interface request [^ ]*\|wifi-net: the network stack was opened[^)]*)" "$f" | sort | uniq -c | sort -rn | head -20
section "station / join"
 grep -n "wifi: scan\|wifi: connecting\|wifi: connected\|wifi: associated\|wifi: network\|wifi: join\|wifi-net: join\|wifi-net: link\|wifi-net: dhcp\|V237 association fixture\|V237 .*network" "$f" | cut -c1-220 | head -20
section "Settings"
 grep -n "Pong Settings: About v238 hang=\|PONG EXIT Preferences\|name=Preferences" "$f" | cut -c1-160 | head -12
section "PosterBoard"
 grep -c "PONG PROBE PosterBoard" "$f"; grep -n "PONG PROBE PosterBoard.*\(sample 60s\|exit\|signal\)" "$f" | cut -c1-260 | head -6
section "crash words"
 grep -c "panic(cpu\|KERNEL PANIC LOG\|FRAMES STOPPED" "$f"
section "fairplayd restarts"; grep -c "PONG EXIT fairplayd.A2" "$f"
section "modem trial"
 grep -n "THOR SERVE V237\|THOR SERVE V228: final\|THOR SERVE V228: glink start\|THOR SERVE V228: qrtr\|THOR SERVE V228: rmtfs\|THOR SERVE V228: announced" "$f" | cut -c1-200 | head -14
if [ "${2:-}" = detail ]; then
 section "frame windows"; grep "frames in 5" "$f" | grep -v "(0 a second)\|(1 a second)\|(2 a second)" | cut -c1-200 | tail -12
 section "gpu cache"; grep -n "\[gpu cache\] submits=\|\[gpu host sync\]" "$f" | tail -3 | cut -c1-220
 section "KeyStore / minisandbox refusals"; grep -o "MiniAppleKeyStore: [A-Za-z0-9_.-]*\[[0-9]*\] selector 0x[0-9a-f]* unsupported\|minisandbox: [a-z]* [0-9]* .*refused.*" "$f" | sed -E 's/\[[0-9]+\]//' | sort | uniq -c | sort -rn | head -12
fi
