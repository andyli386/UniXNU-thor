#!/bin/bash
# Host test of the wallpaper library (compiled for this Mac, bound by install name as on the device):
# the interposer answers "iPad" to WallpaperKit's two questions only, and the two getters answer yes.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/MobileGestalt.framework" "$work/WallpaperKit.framework"
cat > "$work/MobileGestalt.framework/MobileGestalt.tbd" <<TBD
--- !tapi-tbd
tbd-version:     4
targets:         [ arm64-macos ]
install-name:    '$work/MobileGestalt.framework/MobileGestalt'
current-version: 1
compatibility-version: 1
exports:
  - targets:         [ arm64-macos ]
    symbols:         [ _MGCopyAnswer ]
...
TBD
xcrun clang -dynamiclib -Os -framework CoreFoundation "$here/mg.c" -o "$work/MobileGestalt.framework/MobileGestalt"
xcrun clang -dynamiclib -Os -F "$work" -framework CoreFoundation -framework MobileGestalt "$here/kit.c" -o "$work/WallpaperKit.framework/WallpaperKit"
lib="$work/libpong-wallpaper.dylib"
xcrun clang -dynamiclib -Os -Wall -Wextra -Werror -F "$work" -framework CoreFoundation -framework MobileGestalt -lobjc "$here/../wallpaper241.c" -o "$lib"
xcrun clang -framework Foundation -F "$work" -framework MobileGestalt "$here/main.m" -o "$work/test"
echo "== with the library"
DYLD_INSERT_LIBRARIES="$lib" "$work/test" "$work/WallpaperKit.framework/WallpaperKit"
echo "== without the library"
"$work/test" "$work/WallpaperKit.framework/WallpaperKit"
