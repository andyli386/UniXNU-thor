# Thor v176 source checkpoint

This checkpoint records the exact Thor XNU development overlays used in the
v176 Switcher Guard image for iPadOS 18.7.10 / 22H374. The preceding
[v156 build recipe](thor-v156-build-recipe.md) documents the private build inputs.
The checkpoint manifest records the ordered overlays, expected source hashes
and revisions of the matching driver commits. Driver revisions currently
refer to local Git branches named thor/v176-source-checkpoint in their
respective repositories.

Use an isolated Apple XNU 43a90889846e00bfb5cf1d255cdc0a701a1e05a4 source
tree prepared with the exact v156 device adaptations. The first expected
SHA-256 for each updated source is in the manifest. Run the overlay replay
after the baseline device patches and before compilation. A checkout of
Apple XNU by itself is an incomplete baseline. The separately preserved v156
display checkpoint covers a subset of that baseline.

    python3 scripts/replay-thor-v176-xnu.py "$XNU_WORK" --check
    python3 scripts/replay-thor-v176-xnu.py "$XNU_WORK"

The tool stages the complete replay in a scratch directory, checks each
intermediate hash and applies the verified delta to the target worktree.
Use --reverse to recover pre-v157 sources from an exact v176 tree. The
checkpoint lives outside the standard device patch series because it
depends on the prepared Thor baseline. v164–v169 experimental display
overlays are absent from the deployed v176 stack.

For the separate runningboardd console collector, build and ad-hoc sign its
payload with Xcode and the iPhoneOS SDK. iPhoneOS SDK 27.0 and linker 27037.1
reproduced the deployed library and C header by SHA-256.

    python3 scripts/build-thor-v172-console.py "$LOCAL_OUTPUTS" --expect-shipped
    install -m 0644 "$LOCAL_OUTPUTS/thor_console_v172_payload.h" \
      "$XNU_WORK/bsd/kern/thor_console_v172_payload.h"

The console injection also needs the matching signed v158 Thor UI adapter
and its loader configuration. The checkpoint keeps the exact staged UI
source, app launch header and both timing sources in
[userland/checkpoints/thor-v158-ui](../userland/checkpoints/thor-v158-ui).
Its [recipe](../userland/checkpoints/thor-v158-ui/recipe.json) records the
compiler arguments, signing procedure and shipped library identity. Supply
the exact shared userland inputs through COMMON_USERLAND and the historical
v119 touch source, v134 settings watchdog stack source and v98 Metal smoke
object through LOCAL_ARCHIVES. An independent rebuild of this UI adapter
could not be verified with the available copies. Keep restore inputs,
generated storage bindings, signed libraries, firmware and other local
build products in local paths.

For Turnip, prepare the pinned Mesa source with the existing Pong Darwin24
parity patch, then apply the separate checkpoint
patches/mesa/checkpoints/thor-v162-turnip-bo-retirement.patch. The shipped
Turnip used the rebuilt kernel-interface object within the existing bridge.
The manifest records the deployed dylib hash as provenance.

Host verification replays all checkpoint overlays against the actual v176
sources in an isolated tree and verifies the locally rebuilt console. The
original device accepted the integrated v176 image for temporary boot.
These checks cover the checkpoint source and console integrity. A fresh
end-to-end image build needs the remaining matching local device inputs.
