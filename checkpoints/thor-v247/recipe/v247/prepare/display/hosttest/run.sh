#!/bin/bash
# Host test of the display library: under the name backboardd (hooked) and Other (untouched).
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
xcrun clang -dynamiclib -fobjc-arc -Os -Wall -Wextra -Werror -DPONG_DISPLAY_HOSTTEST "$here/../display246.c" -o "$work/libpong-display.dylib"
for name in backboardd Other; do
  xcrun clang -fobjc-arc -framework Foundation -framework QuartzCore -Wall -Wextra -Werror "$here/main246.m" -o "$work/$name"
  status=0
  PONG_DISPLAY_TEST_FILE="$work/$name.txt" DYLD_INSERT_LIBRARIES="$work/libpong-display.dylib" "$work/$name" >"$work/$name.out" 2>"$work/$name.err" || status=$?
  echo "== $name: exit status $status; stderr $(wc -c < "$work/$name.err" | tr -d ' ') bytes"
  sed 's/^/   /' "$work/$name.out"
  sed 's/^/   /' "$work/$name.txt" 2>/dev/null || true
done
