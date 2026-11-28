#!/bin/bash
# Host test of the probe library: compiled in under the names configd, wifid and PosterBoard.
# The bootstrap and the console redirect are compiled out (PROBE_HOST_TEST), so this Mac's dynamic
# store, preferences and console are not touched.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
for name in configd wifid PosterBoard; do
  xcrun clang -DPROBE_HOST_TEST -O1 -Wno-everything -framework Foundation -framework IOKit -I "$here/.." "$here/main.c" -o "$work/$name"
done
echo "== configd: samples, the marker test, no bootstrap"
"$work/configd" x 2 2>&1 | cut -c1-200
echo "== wifid: a fatal signal is reported with its place, then takes its default action"
"$work/wifid" crash 1 2>&1 | tail -3 | cut -c1-200 || true
echo "== PosterBoard: the wallpaper folders"
"$work/PosterBoard" a 5 2>&1 | grep "path \|wallpaper\|exit" | cut -c1-200
