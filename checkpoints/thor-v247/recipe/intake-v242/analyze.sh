#!/bin/bash
# Local paths and the phone's fastboot serial come from the environment: THOR_ROOT, THOR_TOOLS, THOR_SERIAL.
# analyze.sh: split the last boot's kernel console out of the mirror file and list what matters for v242.
#   analyze.sh <dump directory (recovery-v242-full-…)> [previous mirror] [output]
# Without arguments it uses the newest recovery-v242-* snapshot and the v241 mirror as the previous copy.
set -uo pipefail
out=${THOR_ROOT}/thor-out
d=${1:-$(ls -d "$out"/recovery-v242-full-* 2>/dev/null | tail -1)}
prev=${2:-$out/recovery-v241-full-20261127T022441Z/Data/PongConsole-kernel-v235.txt}
f=${3:-$out/v242-intake/logs/console-v242-kernel.txt}
mkdir -p "$(dirname "$f")"
[ -n "$d" ] || { echo "no v242 snapshot"; exit 2; }
/opt/homebrew/bin/python3.13 -I "$out/v242-intake/split.py" "$d/Data/PongConsole-kernel-v235.txt" "$prev" "$f"
cp "$d/Data/PongConsole.txt" "$(dirname "$f")/console-v242-runningboardd.txt" 2>/dev/null
section() { echo; echo "=== $1"; }
section "boot type: the first timestamps (10-11 s = from flash, later = temporary)"
 sed -n '1,4p' "$f" | cut -c1-90
section "version / slot / probe"
 grep -n "THOR WITNESS V242\|THOR WALLPAPER V242: staged\|THOR PROBE V241\|THOR PROBE V241: stage failed" "$f" | cut -c1-230 | head -12
section "wallpaper library: how often each process was armed (once per start)"
 grep -o "THOR WALLPAPER V242: [A-Za-z.]* catalog library armed" "$f" | sort | uniq -c
section "starts and deaths of the processes that carry it"
 for p in SpringBoard chronod PosterBoard CollectionsPoster; do
   printf '%-18s armed=%s corpse=%s crashed=%s exited=%s\n' $p "$(grep -c "THOR WALLPAPER V242: $p catalog library armed" "$f")" \
     "$(grep -c "$p\[[0-9]*\] Corpse" "$f")" "$(grep -c "crashed: $p\$" "$f")" "$(grep -c "PONG EXIT $p " "$f")"
 done
 grep -n "crashed: SpringBoard\|crashed: chronod\|Corpse failure\|SpringBoard\[[0-9]*\] Corpse\|chronod\[[0-9]*\] Corpse" "$f" | cut -c1-200 | head -12
section "wallpaper lines of the probe"
 grep -n "Pong Probe: wallpaper v242" "$f" | sed -E 's/^([0-9]+):\[ *([0-9.]+)\]: PONG PROBE ([A-Za-z.]*)\[[0-9]+\]: Pong Probe: /\1 \2 \3 /' | cut -c1-300 | head -60
section "PosterBoard"
 echo "probe lines: $(grep -c 'PONG PROBE PosterBoard' "$f")"
 grep -n "PONG PROBE PosterBoard.*\(launched\|exit\|signal\|sample 60s\|sample 1s\)" "$f" | cut -c1-300 | head -14
section "CollectionsPoster / chronod probes"
 grep -n "PONG PROBE CollectionsPoster\|PONG PROBE chronod" "$f" | cut -c1-260 | head -14
section "firmware LUN (LUN 4)"
 grep -n "THOR MODEM FW V242\|THOR MODEM FW V163\|THOR MODEM FW V174\|THOR MODEM FW V186\|THOR MODEM FW V182\|THOR MODEM FW V164\|THOR WLAN V242\|THOR WLAN V22[0-9]\|THOR ADSP FW\|PongWifi: firmware\|WiFi initialization\|THOR RMTFS V162: state=\(dma\|read-only\)\|THOR UFS NV" "$f" | cut -c1-330 | head -70
section "battery / adsp chain"
 grep -n "THOR BATTERY\|pmic-glink" "$f" | cut -c1-200 | head -20
section "configd probe"
 grep -c "PONG PROBE configd" "$f"
 grep -n "PONG PROBE configd" "$f" | sed -E 's/PONG PROBE configd\[[0-9]+\]: Pong Probe: //' | cut -c1-260 | head -16
section "Settings"
 grep -c "Pong Settings: About" "$f"
 grep -n "PONG EXIT Preferences\|name=Preferences" "$f" | cut -c1-160 | head -6
section "crash words"
 grep -c "panic(cpu\|KERNEL PANIC LOG\|FRAMES STOPPED" "$f"
section "last lines"
 tail -2 "$f" | cut -c1-160
if [ "${4:-}" = detail ]; then
 section "every THOR WLAN / PongWifi line up to the radio's stop"
 grep -n "THOR WLAN\|PongWifi" "$f" | cut -c1-220 | sed -n '1,80p'
 section "all probe lines of SpringBoard (first 40)"
 grep -n "PONG PROBE SpringBoard" "$f" | cut -c1-260 | head -40
 section "exits of wallpaper-related processes"
 grep -n "PONG EXIT" "$f" | grep -E "SpringBoard|chronod|PosterBoard|CollectionsPoster|Preferences" | cut -c1-200 | head -30
fi
