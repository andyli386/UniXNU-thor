#!/bin/bash
# Build the kernel of a working copy and the prelinked kernel the boot image carries.
#   thor/build-kernel.sh work/<release>
# The working copy comes from thor/bootstrap.py.  The kernel is compiled from its kernel source tree with the
# device repository's own build script; the injected libraries of runtime/ are embedded as the build goes.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
out=${1:?the working copy, for example work/v247-unified}
out=$(cd "$out" && pwd -P)
test -d "$out/build-tree/.cache/xnu-work/.git" || { echo "not a working copy made by bootstrap.py: $out" >&2; exit 2; }
test -s "$out/runtime/final-inputs.json"
# apple/ in the device tree is the device repository's own place for fetched Apple components (ignored by git);
# the UFS driver's build and the prelink step read the decompressed kernelcache of the 22H374 restore from it.
closure="$THOR_PONG/apple/ipados18-22H374/components/kernelcache.release.ipad7c.decompressed/kernelcache.release.ipad7c.decompressed"
test -s "$closure" || { echo "apple/ is missing in the device tree: the decompressed kernelcache of the 22H374 restore" >&2; exit 2; }
export PATH=/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin
source "$out/runtime/env.sh"
tree="$out/build-tree"
artifact="$out/kernel"
mkdir -p "$artifact"
for f in "$out/runtime/"*.dylib; do codesign --verify --strict "$f"; done
export PONG_ADRENO_FIRMWARE=$THOR_INPUTS/private/gpu/thor-adreno730
export THOR_APP_INSTALLER=$THOR_INPUTS/private/thor-apps-install
export PONG_WIFI_BUILD="$tree/build/pong-ath11k"
export SDKROOT=$(xcrun --sdk iphoneos --show-sdk-path)
export SKIP_ALL_XNU_PATCHES=1            # the source tree already carries every patch (bootstrap.py checked its hash)
unset THOR_PERSONA_LAUNCHD THOR_PERSONA_LAUNCHD_SOURCE
started=$(date +%s)
"$THOR_PY" -I "$out/device/scripts/build-ipados18-pong-macos.py" --jobs "$THOR_JOBS" --skip-image-verification \
  --tree "$tree" --volume-layout "$THOR_INPUTS/private/volume-layout.json" --config-path "$out/thor-gpu.defconfig" \
  --artifact-dir "$artifact" --thor-ui-runtime "$out/runtime/libpong-ui.dylib" --thor-ui-runtime-tag "$(cat "$out/runtime/ui-tag.txt")"
cp -p "$tree/build/xnu/ios-obj/DEVELOPMENT_ARM64_PONG/mach.development.pong.unstripped" "$artifact/"
# The storage bindings are the ones this build just made; the Apple kext closure is an input.
"$THOR_PY" -I "$THOR_PONG/scripts/prelink-thor-kernel.py" --gpu --native "$artifact/mach.development.pong" \
  --bindings "$out/device/mac-build/native-ufs/bindings.json" \
  --original "$closure" \
  --output "$artifact/kernel-ipados18-thor.macho" --plan-json "$artifact/prelink-thor.json"
xcrun dwarfdump --uuid "$artifact/kernel-ipados18-thor.macho"
shasum -a 256 "$artifact/kernel-ipados18-thor.macho"
echo "kernel built in $(( $(date +%s) - started )) s: $artifact"
