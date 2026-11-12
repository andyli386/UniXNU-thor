#!/bin/zsh
# Build the 22H374 software-UI compatibility library for iPadOS 18.7.10.
# Output: libpong-ui.dylib (arm64, iOS, ad-hoc signed) destined for
# /private/var/pong/libpong-ui.dylib on the Data volume.
set -e
cd "$(dirname "$0")"
SDK=$(xcrun --sdk iphoneos --show-sdk-path)
xcrun -sdk iphoneos clang -arch arm64 -isysroot "$SDK" \
  -miphoneos-version-min=18.0 -O2 -Wall -dynamiclib \
  -install_name /private/var/pong/libpong-ui.dylib \
  ui_compat.c touch.c icons_cpu.c \
  -framework CoreFoundation -framework CoreGraphics -lobjc \
  -o libpong-ui.dylib
codesign -s - libpong-ui.dylib
file libpong-ui.dylib
otool -L libpong-ui.dylib
shasum -a 256 libpong-ui.dylib
