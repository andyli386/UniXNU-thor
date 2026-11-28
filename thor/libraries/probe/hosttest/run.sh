#!/bin/bash
# Host test of the probe library: compiled in under the names configd and PosterBoard.
# The bootstrap is compiled out (PROBE_HOST_TEST), so this Mac's dynamic store and preferences
# are not touched.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
for name in configd PosterBoard; do
  xcrun clang -DPROBE_HOST_TEST -O1 -Wno-everything -framework Foundation -framework IOKit -I "$here/.." "$here/main.c" -o "$work/$name"
done
echo "== configd: samples, the marker test, no bootstrap"
"$work/configd" x 2 2>&1 | cut -c1-200
echo "== PosterBoard: the wallpaper folders"
"$work/PosterBoard" a 5 2>&1 | grep "path \|wallpaper\|exit" | cut -c1-200
