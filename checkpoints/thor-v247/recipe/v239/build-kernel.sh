#!/bin/bash
set -euo pipefail
export PATH=/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin
out=$(cd "$(dirname "$0")" && pwd -P)
project=$(cd "$out/../.." && pwd -P)
tree="$out/build-tree"
mode=${1:?choose precompile or final}
if [[ "$mode" == precompile ]]; then
    artifact="$out/precompile/kernel"
elif [[ "$mode" == final ]]; then
    artifact="$out/kernel"
    test -s "$out/runtime/final-inputs.json"
else
    exit 2
fi
[[ "$(readlink "$tree/drivers")" == "$out/device/drivers" ]]
mkdir -p "$artifact"
/usr/bin/python3 - "$out" <<'CHECK'
from pathlib import Path
import sys
out = Path(sys.argv[1])
for file in (out/'runtime/thor_metal_pool_payload.h', out/'build-tree/.cache/xnu-work/bsd/kern/thor_metal_pool_payload.h'):
    header = file.read_text().replace(str(out.parent/'v201-desktop-freeze/runtime'), str(out/'runtime'))
    if header != file.read_text():
        file.write_text(header)
    for name in ('PongMetalBridge.dylib','libvulkan_freedreno.dylib'):
        assert str(out/'runtime'/name) in header
CHECK
for f in "$out/runtime/"*.dylib; do codesign --verify --strict "$f"; done
export PONG_ADRENO_FIRMWARE="$project/pong-work/private-inputs/thor-adreno730"
export THOR_APP_INSTALLER="$out/../v151-balanced-rows/runtime/thor-apps-install"
export THOR_SETTINGS_RUNTIME="$out/runtime/libpong-settings-v185.dylib"
export THOR_PROBE_RUNTIME="$out/runtime/libpong-probe-v239.dylib"
export SKIP_ALL_XNU_PATCHES=1
unset THOR_PERSONA_LAUNCHD THOR_PERSONA_LAUNCHD_SOURCE
py=/opt/homebrew/bin/python3.13
"$py" -I "$out/device/scripts/build-ipados18-pong-macos.py" --jobs 9 --skip-image-verification --tree "$tree" --volume-layout "$out/../volume-layout.json" --config-path "$out/thor-gpu.defconfig" --artifact-dir "$artifact" --thor-ui-runtime "$out/runtime/libpong-ui.dylib" --thor-ui-runtime-tag "$(cat "$out/runtime/ui-tag.txt")"
if [[ "$mode" == final ]]; then
    "$py" -I "$project/UniXNU-pong/scripts/prelink-thor-kernel.py" --gpu --native "$artifact/mach.development.pong" --output "$artifact/kernel-ipados18-thor.macho" --plan-json "$artifact/prelink-thor.json"
fi
