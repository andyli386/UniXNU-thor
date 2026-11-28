#!/bin/bash
# Build the passive probe library that the v239 kernel injects into PosterBoard, wifid, airportd and configd.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p ../../runtime
output=../../runtime/libpong-probe-v239.dylib
tmp="$(mktemp -d .link.XXXXXX)"
trap 'rm -rf "$tmp"' EXIT
xcrun --sdk iphoneos clang -target arm64-apple-ios18.0 -Os -dynamiclib -Wall -Wextra -Werror \
  -Wl,-no_fixup_chains -install_name /pong/libpong-probe-v239.dylib -framework CoreFoundation -framework IOKit \
  probe239.c -o "$tmp/libpong-probe-v239.dylib"
codesign --force --sign - --pagesize 4096 "$tmp/libpong-probe-v239.dylib"
codesign --verify --strict "$tmp/libpong-probe-v239.dylib"
xcrun dyld_info -validate_only "$tmp/libpong-probe-v239.dylib"
test "$(xcrun lipo -archs "$tmp/libpong-probe-v239.dylib")" = arm64
mv "$tmp/libpong-probe-v239.dylib" "$output"
shasum -a 256 "$output"
wc -c < "$output"
