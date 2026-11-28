#!/bin/bash
# Host tests of the v237 Settings diagnostic's two additions (about237.c).  The test includes the
# source directly, so it runs against the patched tree: replay the sources first with replay.py's
# three stages, or point the includes at a tree that has base/ + settings.patch + about237.patch.
#   test.m : the Ethernet answer (own method replaced, inherited method overridden on the watcher
#            only, a method with another shape left alone) and one hang snapshot
#   tick.m : the main-queue watchdog against a deliberately blocked main thread
set -euo pipefail
cd "$(dirname "$0")"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
cp -Rp ../base "$work/source"
for p in ../settings.patch ../about237.patch; do patch --batch --fuzz=0 -s -p1 -d "$work/source" -i "$(cd .. && pwd)/${p#../}"; done
sed "s#/tmp/claude-501/settings237/replay/source/#$work/source/#g" test.m > "$work/test.m"
sed "s#/tmp/claude-501/settings237/replay/source/#$work/source/#g" tick.m > "$work/tick.m"
cp own.m "$work/own.m"
cd "$work"
xcrun clang -framework Foundation -lobjc -fblocks -O1 -Wno-everything -DOWN_METHOD=1 -I source test.m own.m -o Preferences-host-test
./Preferences-host-test
xcrun clang -framework Foundation -lobjc -fblocks -O1 -Wno-everything -DOWN_METHOD=0 -I source test.m own.m -o Preferences-host-test
./Preferences-host-test
xcrun clang -framework Foundation -lobjc -fblocks -O1 -Wno-everything tick.m -o Preferences-tick-test
./Preferences-tick-test
