#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p runtime
output=runtime/libpong-about-v179.dylib
tmp="$(mktemp -d runtime/.link.XXXXXX)"
trap 'rm -rf "$tmp"' EXIT
xcrun --sdk iphoneos clang -target arm64-apple-ios18.0 -Os -dynamiclib -Wall -Wextra -Werror \
  -Wl,-no_fixup_chains -Wl,-no_uuid -install_name /pong/libpong-about-v179.dylib \
  -lobjc about_trace18.c -o "$tmp/libpong-about-v179.dylib"
codesign --force --sign - --pagesize 4096 "$tmp/libpong-about-v179.dylib"
codesign --verify --strict "$tmp/libpong-about-v179.dylib"
test "$(xcrun lipo -archs "$tmp/libpong-about-v179.dylib")" = arm64
mv "$tmp/libpong-about-v179.dylib" "$output"
shasum -a 256 "$output"
