#!/bin/bash
# Build the wallpaper catalog library that the v241 kernel puts into the processes that load
# WallpaperKit (PosterBoard, the poster extensions of the stock collections, the wallpaper settings intent).
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p ../../runtime
name=libpong-wallpaper-v241.dylib
output=../../runtime/$name
tmp="$(mktemp -d .link.XXXXXX)"
trap 'rm -rf "$tmp"' EXIT
# The public SDK has no stub for MobileGestalt; stubs/ holds one that names _MGCopyAnswer, so the
# library binds to the real framework by its install name (an interpose entry must name its target).
xcrun --sdk iphoneos clang -target arm64-apple-ios18.0 -Os -dynamiclib -Wall -Wextra -Werror \
  -Wl,-no_fixup_chains -install_name /pong/$name -F stubs \
  -framework CoreFoundation -framework MobileGestalt -lobjc wallpaper241.c -o "$tmp/$name"
codesign --force --sign - --pagesize 4096 "$tmp/$name"
codesign --verify --strict "$tmp/$name"
test "$(xcrun lipo -archs "$tmp/$name")" = arm64
mv "$tmp/$name" "$output"
shasum -a 256 "$output"
wc -c < "$output"
