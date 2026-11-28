#!/bin/bash
# Host test of the wallpaper library (compiled for this Mac).  It is linked exactly as on the device:
# the stub carries MobileGestalt's real install name (/usr/lib/libMobileGestalt.dylib), and the stand-in
# for MobileGestalt is offered under that leaf name through DYLD_LIBRARY_PATH.  The interposer answers
# "iPad" to WallpaperKit's two questions only, and the two getters answer yes afterwards.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/MobileGestalt.framework" "$work/WallpaperKit.framework" "$work/MobileGestalt.tbd.framework"
# The device stub targets iOS; the Mac needs the same stub (same install name, same symbol) for macOS.
sed 's/arm64-ios, arm64e-ios/arm64-macos/' "$here/../stubs/MobileGestalt.framework/MobileGestalt.tbd" > "$work/MobileGestalt.tbd.framework/MobileGestalt.tbd"
xcrun clang -dynamiclib -Os -install_name /usr/lib/libMobileGestalt.dylib -framework CoreFoundation "$here/mg.c" -o "$work/MobileGestalt.framework/libMobileGestalt.dylib"
xcrun clang -dynamiclib -Os -framework CoreFoundation "$work/MobileGestalt.tbd.framework/MobileGestalt.tbd" "$here/kit.c" -o "$work/WallpaperKit.framework/WallpaperKit"
lib="$work/libpong-wallpaper.dylib"
xcrun clang -dynamiclib -Os -Wall -Wextra -Werror -framework CoreFoundation "$work/MobileGestalt.tbd.framework/MobileGestalt.tbd" -lobjc "$here/../wallpaper242.c" -o "$lib"
xcrun clang -framework Foundation -framework CoreFoundation "$work/MobileGestalt.tbd.framework/MobileGestalt.tbd" "$here/main.m" -o "$work/test"
export DYLD_LIBRARY_PATH="$work/MobileGestalt.framework"
echo "== with the library"
DYLD_INSERT_LIBRARIES="$lib" "$work/test" "$work/WallpaperKit.framework/WallpaperKit"
echo "== without the library"
"$work/test" "$work/WallpaperKit.framework/WallpaperKit"
