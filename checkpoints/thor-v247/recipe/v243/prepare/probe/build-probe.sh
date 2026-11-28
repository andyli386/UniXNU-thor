#!/bin/bash
# Build the probe library that the v241 kernel injects into PosterBoard and configd (configd publishes the
# two dynamic-store values and says when wifid may run).
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p ../../runtime
output=../../runtime/libpong-probe-v241.dylib
tmp="$(mktemp -d .link.XXXXXX)"
trap 'rm -rf "$tmp"' EXIT
xcrun --sdk iphoneos clang -target arm64-apple-ios18.0 -Os -dynamiclib -Wall -Wextra -Werror \
  -Wl,-no_fixup_chains -install_name /pong/libpong-probe-v241.dylib -framework CoreFoundation -framework IOKit \
  probe241.c -o "$tmp/libpong-probe-v241.dylib"
codesign --force --sign - --pagesize 4096 "$tmp/libpong-probe-v241.dylib"
codesign --verify --strict "$tmp/libpong-probe-v241.dylib"
xcrun dyld_info -validate_only "$tmp/libpong-probe-v241.dylib"
test "$(xcrun lipo -archs "$tmp/libpong-probe-v241.dylib")" = arm64
mv "$tmp/libpong-probe-v241.dylib" "$output"
shasum -a 256 "$output"
wc -c < "$output"
