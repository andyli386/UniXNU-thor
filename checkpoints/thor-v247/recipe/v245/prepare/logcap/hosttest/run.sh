#!/bin/bash
# Host test of the log capture library.  Prints, for the names SpringBoard and Freeform, the lines
# the library passed on (here into a file, on the phone into the kernel's diagnostic channel) and the
# exit status of each run.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
work=$(mktemp -d)
# Every run under a watchdog (20 s): a crashed run must not hang the test.
limited() { local secs=$1; shift; "$@" & local p=$!; ( sleep "$secs"; kill $p 2>/dev/null ) & local w=$!; wait $p; local r=$?; kill $w 2>/dev/null; wait $w 2>/dev/null; return $r; }
trap 'rm -rf "$work"' EXIT
xcrun clang -dynamiclib -Os -Wall -Wextra -Werror "$here/../logcap244.c" -o "$work/libpong-logcap.dylib"
for name in SpringBoard Freeform Other; do
  xcrun clang -Os "$here/main.c" -o "$work/$name"
  out=$work/$name.txt
  PONG_LOGCAP_TEST_FILE="$out" DYLD_INSERT_LIBRARIES="$work/libpong-logcap.dylib" limited 20 "$work/$name" 2>"$work/$name.stderr" || status=$?
  echo "== $name: exit status ${status:-0}; left on stderr: $(wc -c < "$work/$name.stderr" | tr -d ' ') bytes"
  sed 's/^/   /' "$out" 2>/dev/null || echo "   (nothing captured)"
  status=0
done
