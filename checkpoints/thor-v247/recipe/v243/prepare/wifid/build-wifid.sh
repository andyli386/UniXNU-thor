#!/bin/bash
# Build the small library that the v241 kernel puts into wifid: it waits until kern.pong_wifi_gate is 1
# and keeps the first two minutes of the daemon's own log.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p ../../runtime
name=libpong-wifid-v241.dylib
output=../../runtime/$name
tmp="$(mktemp -d .link.XXXXXX)"
trap 'rm -rf "$tmp"' EXIT
xcrun --sdk iphoneos clang -target arm64-apple-ios18.0 -Os -dynamiclib -Wall -Wextra -Werror \
  -Wl,-no_fixup_chains -install_name /pong/$name wifid241.c -o "$tmp/$name"
codesign --force --sign - --pagesize 4096 "$tmp/$name"
codesign --verify --strict "$tmp/$name"
test "$(xcrun lipo -archs "$tmp/$name")" = arm64
mv "$tmp/$name" "$output"
shasum -a 256 "$output"
wc -c < "$output"
