#!/bin/zsh
# Build the thor PID 1 helper.
#   ./build.sh [out-path]
# Same recipe pong uses for userland/init.c: freestanding static arm64, page
# zero at 4 GiB (iOS arm64 ABI) and an ad-hoc signature with a 4 KiB digest
# page, which is what the kernel's code-signing blob parser expects.
set -e
HERE=${0:A:h}
OUT=${1:-$HERE/init}
SDK=$(xcrun --sdk iphoneos --show-sdk-path)

xcrun --sdk iphoneos clang -target arm64-apple-ios16.6.1 -isysroot "$SDK" \
  -Os -static -nostdlib -ffreestanding \
  -fno-stack-protector -fno-stack-check -fno-builtin -Wall -Wextra -Werror \
  -I"$HERE/libmini" -Wl,-e,_start -Wl,-pagezero_size,0x100000000 -Wl,-no_fixup_chains \
  "$HERE/libmini/start.S" "$HERE/libmini/mini.c" "$HERE/init.c" -o "$OUT"

codesign --force --sign - --pagesize 4096 \
  --entitlements "$HERE/init-entitlements.plist" "$OUT"
chmod 755 "$OUT"
ls -l "$OUT"
