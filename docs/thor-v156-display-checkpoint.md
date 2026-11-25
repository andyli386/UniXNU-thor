# Thor v156 display source checkpoint

This checkpoint records seven XNU source files selected from the locally
compiled v156 display implementation. The patch includes retained scanout,
DSI mode and command staging, scaled or native CPU composition, worker
balance, IOSurface GPU handoff, touch timestamps and the switcher collector.
To replay the source, begin with Apple XNU
`43a90889846e00bfb5cf1d255cdc0a701a1e05a4` and the seven device patches in
the public device series at `e9c7f81a38dbca2a1ceba89ad38895d74d8ba5a9`.
Apply `patches/xnu-ipados18/checkpoints/thor-v156-display.patch` after that
series. All seven resulting files were checked against the extracted
implementation, byte for byte.

The v156 UI cue producer is in
`userland/checkpoints/thor-v156-switcher-timing.c`. The matching developer
runtime links that source and calls `thor_switcher_timing_start` from
SpringBoard startup. The separate opt-in trace port on the public framebuffer
has its own patch and runtime integration. Apply the original checkpoint and
the public trace port in separate worktrees.

The matching Thor firmware and DarwinBoot24 reserve the memory lease and
fill the 16-byte `/chosen/thor-native-scanout` ADT property. Prepare a
copy of a local boot ADT with `scripts/prepare-thor-retained-display-adt.py`.
An all-zero placeholder leaves the lease unavailable to XNU. The actual
runtime used the hardware 1440×3200 framebuffer with 5760-byte scan lines,
`arp0=thor`, `fb_hz=120` and `fb_copy=0`. Its boot packer removed the
earlier `fbscale` option. Replay of a subset of XNU files is a source check.
A complete v156 rebuild also needs the other device and OS profile adaptations,
matching GPU stack, firmware, bootloader, platform inputs and the locally
prepared guest runtime.

The L1 WQHD DSI command batches are encoded from the public LineageOS Thor
device-tree revision
[`096f783227ec`](https://github.com/LineageOS/android_kernel_xiaomi_sm8450-devicetrees/blob/096f783227ec4c3c4d195fb7e8a3d2a1ddee0854/qcom/display/display/dsi-panel-l1-38-0c-0a-dsc-cmd.dtsi). The original v156 device capture measured
119.8 Hz at the DPU and recovered 383 switcher frame rows. That run used
the original integrated build. A build of this checkpoint from the public
device baseline still needs separate host and device validation.
