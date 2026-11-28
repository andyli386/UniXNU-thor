#!/bin/bash
# Build the wallpaper catalog library that the v243 kernel puts into the processes that load
# WallpaperKit (SpringBoard, CollectionsPoster).  It needs nothing but Foundation and the Objective-C
# runtime: the MobileGestalt interception of v241 and v242 is gone (it could not take effect).
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p ../../runtime
name=libpong-wallpaper-v243.dylib
output=../../runtime/$name
tmp="$(mktemp -d .link.XXXXXX)"
trap 'rm -rf "$tmp"' EXIT
xcrun --sdk iphoneos clang -target arm64-apple-ios18.0 -Os -dynamiclib -Wall -Wextra -Werror \
  -Wl,-no_fixup_chains -install_name /pong/$name \
  -framework Foundation -lobjc wallpaper243.m -o "$tmp/$name"
codesign --force --sign - --pagesize 4096 "$tmp/$name"
codesign --verify --strict "$tmp/$name"
test "$(xcrun lipo -archs "$tmp/$name")" = arm64
# Every library this one loads must be named exactly as Apple's own binaries of this system name it.
load=$(otool -L "$tmp/$name" | tail -n +2 | awk '{print $1}' | sort)
while read -r one; do
  case "$one" in
    /pong/*) continue ;;
    /System/Library/Frameworks/Foundation.framework/Foundation|/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation|/usr/lib/libobjc.A.dylib|/usr/lib/libSystem.B.dylib) ;;
    *) echo "unexpected dependency: $one" >&2; exit 1 ;;
  esac
done <<< "$load"
mv "$tmp/$name" "$output"
shasum -a 256 "$output"
wc -c < "$output"
