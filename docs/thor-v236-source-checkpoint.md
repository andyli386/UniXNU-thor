# Thor v236 source checkpoint

This checkpoint is the state of the Thor port on 2026-11-25: everything that had not been
committed anywhere since the last commits of 2026-11-18 (the v185 to v187 branches; the previous
source checkpoint is [v176](thor-v176-source-checkpoint.md)), collected on the single branch
`thor/main` of this fork together with the thirteen 2026-11-17 topic branches. The port targets
iPadOS 18.7.10 / 22H374 (Darwin 24, Apple XNU 43a90889846e00bfb5cf1d255cdc0a701a1e05a4) on the
Xiaomi 12S Ultra (`thor`, SM8475, Adreno 730).

It records sources and porting records only. Images, kernels, firmware, signed libraries, logs and
anything that names the device or the machine stay local.

## What is where

| Part | Path | Content |
| --- | --- | --- |
| Kernel patch series | `patches/xnu-ipados18/series` and `*.patch` | The 39-entry series the v236 image was built from: seven base patches and 32 Thor patches from v108 to v236 |
| Kernel tree | `patches/xnu-ipados18/checkpoints/thor-v236/` | `xnu-after-series.patch` (35 files, 7 new) and `manifest.json`: everything between "pristine XNU + the series" and the v236 build tree |
| Drivers | `checkpoints/thor-v236/drivers/` | One patch per driver that differs from the commit its upstream repository pins (8 of 12), and a manifest with each pinned commit |
| Device repository | `checkpoints/thor-v236/pong-device/`, `checkpoints/thor-v236/unixnu-thor-18/` | The uncommitted changes of the Pong SM8475 device working tree and of the Thor 18 device repository |
| Analysis records | `checkpoints/thor-v236/records/` | Per-version analyses v228 to v236, the boot-panic study of v230, the upstream GPU assessments, the HakiOS umi reference notes, the Rust crash assessment |
| Tools | `checkpoints/thor-v236/tools/` | Read-only evidence tools: APFS readers for the phone's storage regions, panic record and hang report decoders, kernel symbolication and disassembly helpers, the boot-stack budget, the full log snapshot, the stackshot viewer; sources of `fbprobe` and `usbreset` |
| Recipe and intake scripts | `checkpoints/thor-v236/recipe/` | How each version's recipe is cloned, built and packaged (v235, v236), and the boot, collection and waiter scripts |

The five 2026-11-17 topic patches (`patches/xnu-ipados18/zzzzzzzzzz-*.patch`) were merged from their
branches and stay in the directory as records. They are not part of the recorded series; the v236
build does not apply them.

## Rebuilding the kernel tree

1. Check out Apple XNU 43a90889846e00bfb5cf1d255cdc0a701a1e05a4 into an empty tree.
2. Apply `patches/xnu-ipados18/series` in order with `patch --batch --fuzz=3 --forward -p1`.
   All 39 patches apply to the pristine tree.
3. `git apply --index patches/xnu-ipados18/checkpoints/thor-v236/xnu-after-series.patch`.

The result is tree `87b77ec85073` of the checkpoint manifest, 5531 files: the v236 build tree
without three generated headers that stay local (below) and without the backup and reject files the
patch tool leaves behind.

## Rebuilding a driver

Export the commit the manifest names for the driver from its upstream repository and apply the
driver's patch with `git apply --index`. The result equals the v236 recipe's source file for file.
`PongWifi.patch` keeps the upstream `README.md` and `AGENTS.md`, which the recipe no longer carries.

## Scripts

Scripts under `recipe/` and `tools/` read three variables from the environment instead of naming a
machine or a device:

| Variable | Meaning |
| --- | --- |
| `THOR_ROOT` | The directory that holds `UniXNU-pong/` and `thor-out/` |
| `THOR_TOOLS` | The directory that holds `.tools/pt34/platform-tools/fastboot` |
| `THOR_SERIAL` | The phone's fastboot serial number |

`tools/full-snapshot.py` needs the APFS reader (`UniXNU-pong/tools/apfs`) as its first argument, as
before.

## What stays local

- Boot images and their kernels, the embedded graphics libraries and their generated payload
  headers (`thor_console_v172_payload.h`, `thor_ui_runtime_payload.h`,
  `thor_metal_pool_payload.h`): signed binaries and local paths.
- Firmware, restore inputs and every other build product.
- Logs, panic records and photographs: they name the device (ECID, serial, boot session).

## Where the port stood

- v231 to v235 boot to the desktop and every run ended in the "画面定格". The v235 panic record,
  the first one ever stored, names the cause: `NativeAdrenoUserClient::dispatch()` kept a static
  pointer to the clock of its last call, and with fence-wait tracing on that clock is an object on
  the calling thread's kernel stack; the timing hook then jumped to whatever that stack held next.
- v236 changes that one function and keeps a copy of the clock's two words instead. It was built
  and verified on the host only; the device result was pending when this checkpoint was made.
- Open: Settings → General → About hangs on a synchronous XPC call that is never answered; each
  backboardd submission costs about 4.3 ms of CPU cache maintenance over every imported buffer in
  the client's window; Wi-Fi association, Bluetooth past probing, audio, calls and cellular data.

## Verification done for this checkpoint (host only)

- The series replays on the pristine tree; the kernel tree patch round-trips to the recorded tree
  hash and file list.
- Each driver patch applies to a fresh export of its pinned commit and reproduces the recipe source.
- Every shell script parses and every Python file compiles after the path rewriting; all added files
  are UTF-8 text.
- `porting.toml` parses and `scripts/porting-record.py check` passes for the whole branch.
- No added file contains a home directory path, the device serial or key material.

Nothing in this checkpoint was built into an image or run on the device; it is a record of sources.
