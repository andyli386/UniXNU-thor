#!/bin/bash
# Build the log capture library that the v244 kernel puts into SpringBoard, PosterBoard,
# CollectionsPoster, PowerUIAgent and Freeform.  It needs nothing but libSystem.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p ../../runtime
name=libpong-logcap-v244.dylib
output=../../runtime/$name
tmp="$(mktemp -d .link.XXXXXX)"
trap 'rm -rf "$tmp"' EXIT
xcrun --sdk iphoneos clang -target arm64-apple-ios18.0 -Os -dynamiclib -Wall -Wextra -Werror \
  -Wl,-no_fixup_chains -install_name /pong/$name \
  logcap244.c -o "$tmp/$name"
codesign --force --sign - --pagesize 4096 "$tmp/$name"
codesign --verify --strict "$tmp/$name"
test "$(xcrun lipo -archs "$tmp/$name")" = arm64
# Every library this one loads must be named exactly as Apple's own binaries of this system name it.
load=$(otool -L "$tmp/$name" | tail -n +2 | awk '{print $1}' | sort)
while read -r one; do
  case "$one" in
    /pong/*) continue ;;
    /usr/lib/libSystem.B.dylib) ;;
    *) echo "unexpected dependency: $one" >&2; exit 1 ;;
  esac
done <<< "$load"
mv "$tmp/$name" "$output"
shasum -a 256 "$output"
wc -c < "$output"
