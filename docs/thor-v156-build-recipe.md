# Thor v156 build recipe

This source checkpoint preserves the build, sparse prelink and retained-ADT packaging steps used for the Thor v156 development image. Prepare a working device tree with the matching source changes before invoking the tools.

- The device patch stack carries the SM8475 scheduler, input, display writeback, switcher, cellular topology and the app installer staging and sandbox policy. The independent CPU, touch, cellular and system-app helper branches capture portions of this stack.
- The signed UI adapter and native installer must match the selected device stack. Check the installer size and SHA-256 expected by the UI adapter and the CodeDirectory hash selected by the scoped kernel policy.
- Use the matching PongAdreno/PongMetal and platform archives, including the real NativeUfs SMMU lease provider. Use the Thor firmware build with the retained v137 display and memory configuration.
- Obtain Apple restore inputs, decompressed 22H374 kernel cache and generated storage bindings locally. Build outputs, local AppOS inventory, partition data, firmware and identity values stay in caller-owned local paths.

## Build and prelink

On macOS, provide Xcode with an iPhoneOS SDK, Python 3.11 or newer and the tool dependencies used by the base Darwin 24 builder. Prepare the Git checkouts from `feeds.toml` in a directory organized by their ref tags. Initialize the selected driver sources. The local APFS JSON supplies `os.uuid`, `data.uuid` and `systemos.uuid`; use the local resolved defconfig for the active GPU configuration.

```sh
python3 -I scripts/build-thor-v156-macos.py \
  --tree "$BUILD_TREE" --references "$REFERENCES" \
  --developer-dir "$XCODE_DEVELOPER_DIR" --artifact-dir "$OUTPUTS" \
  --volume-layout "$VOLUME_LAYOUT" --config-path "$DEFCONFIG" \
  --adreno-firmware "$ADRENO_FIRMWARE" --jobs 8 \
  --thor-ui-runtime "$SIGNED_UI_RUNTIME" --thor-app-installer "$SIGNED_APP_HELPER"

python3 scripts/verify-thor-v156-kernel.py --kernel "$OUTPUTS/mach.development.pong"
python3 -I scripts/prelink-thor-kernel.py --gpu \
  --native "$OUTPUTS/mach.development.pong" --original "$DECOMPRESSED_22H374_KERNELCACHE" \
  --bindings "$LOCAL_22H374_STORAGE_BINDINGS" --output "$OUTPUTS/kernel.macho" \
  --plan-json "$OUTPUTS/prelink-plan.json"
```

The runner stages the signed artifact bytes in a temporary C header in the XNU work directory. It uses the public builder's strict patch replay. The verifier checks the linked Edge scheduler symbols, three sandbox copyinstr paths and the fragmented GPU physical-span and write-combine paths.

## Prepare the ADT and package

Use the v126 low-DMA Thor Android v4 boot template and its retained ADT. Provide the locally built 3 MiB Thor firmware FD, the stock Thor Recovery DTB and the Thor memory and FTS configuration JSON. Preparing the ADT adds the CPU classes, native scanout capability flag and the interrupt masking trace mode. The cellular branch's host tool adds the topology property to a separate output.

```sh
python3 -I scripts/prepare-thor-v156-adt.py --input "$TEMPLATE_ADT" --output "$OUTPUTS/amp.adt"
python3 scripts/prepare-thor-cellular-adt.py --fdt "$THOR_RECOVERY_DTB" \
  --adt "$OUTPUTS/amp.adt" --output "$OUTPUTS/v156.adt"

python3 scripts/pack-thor-boot.py \
  --template "$V126_TEMPLATE" --firmware "$THOR_FD" --kernel "$OUTPUTS/kernel.macho" \
  --volume-layout "$VOLUME_LAYOUT" --memory-config "$MEMORY_CONFIG" \
  --touch-config "$TOUCH_CONFIG" --retained-adt "$OUTPUTS/v156.adt" \
  --boot-arg cpus=8 --boot-arg fb_hz=120 --boot-arg fb_copy=0 \
  --omit-boot-arg fbscale --boot-arg "$V156_EXEC_INJECT_ARG" \
  --software-platform-identity --menu-title 'Thor v156 Switcher and Cellular' \
  --output "$OUTPUTS/boot-thor-v156.img"
```

The local `V156_EXEC_INJECT_ARG` is the template's `pong_exec_inject=...` argument with the location daemon removed. Preserve the template's `memlog=1` logging and its local software platform identity. The packager checks the 1023-byte boot-argument bound, validates its generated development ADT and verifies the retained Thor ADT survives FAT packaging.

The checkpoint has syntax, parser and isolated host checks. A full clean rebuild and a phone boot from the consolidated source branches are separate validation steps.
