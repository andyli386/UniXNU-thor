#!/bin/bash
# Build the Settings diagnostic (libpong-settings-v185.dylib): the v224 base sources plus the three recorded
# patches (v225 to v236, v237, v238).  Every stage is built from source; the result must have the recorded digest.
set -euo pipefail
cd "$(dirname "$0")"
here=$(pwd -P)
name=libpong-settings-v185.dylib
tmp="$(mktemp -d .settings.XXXXXX)"
trap 'rm -rf "$tmp"' EXIT
cp -R base "$tmp/source"
for patch in settings.patch about237.patch about238.patch; do
  patch --batch --fuzz=0 -s -p1 -d "$tmp/source" -i "$here/$patch"
done
/bin/bash "$tmp/source/build-settings-trace.sh" >/dev/null
mkdir -p ../../runtime
mv "$tmp/source/runtime/$name" "../../runtime/$name"
shasum -a 256 "../../runtime/$name"
wc -c < "../../runtime/$name"
