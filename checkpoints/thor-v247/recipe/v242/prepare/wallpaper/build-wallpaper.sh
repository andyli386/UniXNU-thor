#!/bin/bash
# Build the wallpaper catalog library that the v242 kernel puts into the processes that link
# WallpaperKit (SpringBoard, chronod, PosterBoard, CollectionsPoster).
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p ../../runtime
name=libpong-wallpaper-v242.dylib
output=../../runtime/$name
tmp="$(mktemp -d .link.XXXXXX)"
trap 'rm -rf "$tmp"' EXIT
# The public SDK has no stub for MobileGestalt; stubs/ holds one with the framework's real install
# name (/usr/lib/libMobileGestalt.dylib) that exports _MGCopyAnswer, so the library binds to the real
# framework by that name: an interpose entry must name its target by install name.
xcrun --sdk iphoneos clang -target arm64-apple-ios18.0 -Os -dynamiclib -Wall -Wextra -Werror \
  -Wl,-no_fixup_chains -install_name /pong/$name -F stubs \
  -framework CoreFoundation -framework MobileGestalt -lobjc wallpaper242.c -o "$tmp/$name"
codesign --force --sign - --pagesize 4096 "$tmp/$name"
codesign --verify --strict "$tmp/$name"
test "$(xcrun lipo -archs "$tmp/$name")" = arm64
# Every library this one loads must be named exactly as Apple's own binaries of this system name it.
load=$(otool -L "$tmp/$name" | tail -n +2 | awk '{print $1}' | sort)
while read -r one; do
  case "$one" in
    /pong/*) continue ;;
    /usr/lib/libMobileGestalt.dylib|/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation|/usr/lib/libobjc.A.dylib|/usr/lib/libSystem.B.dylib) ;;
    *) echo "unexpected dependency: $one" >&2; exit 1 ;;
  esac
done <<< "$load"
mv "$tmp/$name" "$output"
shasum -a 256 "$output"
wc -c < "$output"
