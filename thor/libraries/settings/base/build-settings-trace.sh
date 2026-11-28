#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p runtime
output=runtime/libpong-settings-v185.dylib
tmp="$(mktemp -d runtime/.link.XXXXXX)"
trap 'rm -rf "$tmp"' EXIT
xcrun --sdk iphoneos clang -target arm64-apple-ios18.0 -Os -dynamiclib -Wall -Wextra -Werror \
  -Wl,-no_fixup_chains -install_name /pong/libpong-settings-v185.dylib \
  -lobjc -framework UIKit -framework Foundation -fblocks settings_trace18.c navigation_completion185.m general_prefetch223.m window_cpu223.m -o "$tmp/libpong-settings-v185.dylib"
codesign --force --sign - --pagesize 4096 "$tmp/libpong-settings-v185.dylib"
codesign --verify --strict "$tmp/libpong-settings-v185.dylib"
xcrun dyld_info -validate_only "$tmp/libpong-settings-v185.dylib"
test "$(xcrun lipo -archs "$tmp/libpong-settings-v185.dylib")" = arm64
mv "$tmp/libpong-settings-v185.dylib" "$output"
shasum -a 256 "$output"
