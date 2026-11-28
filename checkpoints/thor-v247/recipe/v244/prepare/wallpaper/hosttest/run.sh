#!/bin/bash
# Host test of the wallpaper library (compiled for this Mac).
#   A stand-in for WallpaperKit (WallpaperKit.framework/WallpaperKit, loaded by the test like the real
#   framework is loaded) asks a stand-in for MobileGestalt and lower-cases the answer.
#   The library is compiled from the same source as on the device.
# Prints two lines:
#   with the library:    this program's own call=iphone | from WallpaperKit: device name=ipad, another string=ipados_17 | ignore screen class=1 ignore product types=1
#   without the library: this program's own call=iphone | from WallpaperKit: device name=iphone, another string=ipados_17 | ignore screen class=0 ignore product types=0
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/MobileGestalt.framework" "$work/WallpaperKit.framework"
xcrun clang -dynamiclib -Os -install_name /usr/lib/libMobileGestalt.dylib -framework CoreFoundation "$here/mg.c" -o "$work/MobileGestalt.framework/libMobileGestalt.dylib"
xcrun clang -dynamiclib -Os -framework Foundation "$work/MobileGestalt.framework/libMobileGestalt.dylib" "$here/kit.m" -o "$work/WallpaperKit.framework/WallpaperKit"
lib="$work/libpong-wallpaper.dylib"
xcrun clang -dynamiclib -Os -Wall -Wextra -Werror -framework Foundation -lobjc "$here/../wallpaper243.m" -o "$lib"
xcrun clang -framework Foundation -framework CoreFoundation "$work/MobileGestalt.framework/libMobileGestalt.dylib" "$here/main.m" -o "$work/test"
# The stand-in for MobileGestalt is offered under the real library's leaf name; without this
# line the Mac's own /usr/lib/libMobileGestalt.dylib answers ("Mac").
export DYLD_LIBRARY_PATH="$work/MobileGestalt.framework"
echo "== with the library"; export PONG_WALLPAPER_STDERR=1
DYLD_INSERT_LIBRARIES="$lib" "$work/test" "$work/WallpaperKit.framework/WallpaperKit"
echo "== without the library"
"$work/test" "$work/WallpaperKit.framework/WallpaperKit"
