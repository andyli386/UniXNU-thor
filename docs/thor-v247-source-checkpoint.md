# Thor v247 source checkpoint

This checkpoint is the state of the Thor port on 2026-11-28: everything that changed since the
[v236 checkpoint](thor-v236-source-checkpoint.md) of 2026-11-25 — the eleven versions v237 to v247 — collected on
the single branch `thor/main`. The port targets iPadOS 18.7.10 / 22H374 (Darwin 24, Apple XNU
43a90889846e00bfb5cf1d255cdc0a701a1e05a4) on the Xiaomi 12S Ultra (`thor`, SM8475, Adreno 730).

It records sources and porting records only. Images, kernels, firmware, signed libraries, logs and anything that
names the device, the machine or the office network stay local.

## What is where

| Part | Path | Content |
| --- | --- | --- |
| Kernel patch series | `patches/xnu-ipados18/series` and `*.patch` | Unchanged since v236: the v247 recipe holds the same 39 entries and byte-identical patches |
| Kernel tree | `patches/xnu-ipados18/checkpoints/thor-v247/` | `xnu-after-series.patch` (36 files) and `manifest.json`: everything between "pristine XNU + the series" and the v247 build tree; the manifest also lists the five files that differ from the v236 tree |
| Drivers | `checkpoints/thor-v247/drivers/` | One patch per driver that differs from the commit its upstream repository pins (8 of 12), and a manifest with each pinned commit and the files that changed since v236 |
| Current recipe | `checkpoints/thor-v247/recipe/v247/` | `package-v247.py`, `build-kernel.sh`, `thor-gpu.defconfig` and `prepare/`: the sources, build scripts and host tests of everything injected into userland — the log capture library (v247), the display library (v246), the probe and the Wi-Fi daemon's library (v244), the Settings diagnostic. `device/scripts/`: the four device build scripts the recipe changed beyond the device repository, the Wi-Fi station source generator among them. `camera-staging/`: the host test of the camera inventory |
| Earlier recipes | `checkpoints/thor-v247/recipe/v237` … `v246` | Each version's packaging and build scripts, and only those `prepare/` files that no newer recipe holds byte-identical (the earlier library sources and per-version patches) |
| Packaging chain | `checkpoints/thor-v247/recipe/packaging-chain/` | The eighteen older packaging scripts that `package-v247.py` loads with `runpy`, from v152 to v246 |
| Intake | `checkpoints/thor-v247/recipe/intake-v237` … `v247` | The install, collect and waiter scripts of each version and its source patches (`apply.py`, `apply2.py`, `guards.py`, `mkpackage*.py`) |
| Records | `checkpoints/thor-v247/records/` | The analyses v237 to v247 with the research on cellular, the Wi-Fi daemon, calls and the camera, and the verification records of the eleven images |
| Embedded library sources | `checkpoints/thor-v247/sources/` | Sources of embedded libraries that no recipe held: the console capture library every image since v232 embeds (`console-v205.c` of v232 and its staging script, with the earlier versions v171 to v205), the UI adapter of the signed UI runtime library (`ui_compat18-v158.c`, its build script, the bridge sources of v158) and the baseband service of the upstream SM8475 port |
| Tools | `checkpoints/thor-v247/tools/` | The 28 evidence tools new or changed since v236: readers for Apple's binaries and the dyld cache, the OS volume reader, GPT comparison and marking, stackshot and kernelcache helpers |

The console and UI sources were missing from every checkpoint before this one: they lived only in intake and
experiment folders (`v232-intake/console/`, `v158-metal-staging/`) while the recipe embeds their signed builds.

The Pong SM8475 device repository and the Thor 18 device repository are unchanged since v236: the same HEADs and
the same modified and new files, compared by content. `checkpoints/thor-v236/pong-device/` and
`checkpoints/thor-v236/unixnu-thor-18/` still describe them.

## What changed in the sources since v236

Kernel tree, five files of a9192b569462 against 87b77ec85073:

- `bsd/kern/kern_exec.c` (+469/−1): the staging and injection of the probe, the log capture and display libraries
  and the UI adapter in PosterBoard; the Wi-Fi start gate.
- `bsd/kern/kern_exit.c` (+75/−4): the probe's console lines under a budget of their own; the gate signal from
  configd; the graphics bridge's lines from Freeform and kbd; since v247 one line (`PONG DEATH …`) for every
  process that ends by a core-generating signal.
- `bsd/kern/kern_proc.c` (+16/−1), `iokit/Kernel/MiniIOSurface.cpp` (+69): since v237, protection classes A and B
  open for every process (the data volume is not content protected) and user-client selector 21 (set-ownership)
  is answered; both were refused in every boot before and PosterBoard, which draws the wallpaper, was hit by both.
- `osfmk/kern/thor_witness_v235.h` (+16): the witness lines v237 to v247.

Drivers, three patches of eight (`AdrenoMetal`, `ArmCpu`, `ArmDTPlatform`, `PongAdreno`, `PongMetal` are byte-identical to v236; `DarwinBoot24`, `NativeRamBlock`, `PongBootCapture`, `PongUsb` equal their pinned commits):

- `PongPower`: the read-only camera inventory of v247 (six new headers, started from `pong_rpmh_init`, switched by
  the device-tree property `thor-camera`); a free mark outside an SMEM partition is refused, the modem's SMEM host
  id is corrected and its trial held to 24 s (v237); the battery is the power source wifid waits for (v240).
- `PongWifi`: the unattended association fixture fed from a private file (v237); the kernel's wifid watchdog is told
  of each request (v240); the station's own address (v245). The association request, the frame dumps and the retries
  on 5 GHz and as a plain 802.11g station (v244 to v246) are in the station source generator
  `recipe/v247/device/scripts/build-pong-ath11k.py`.
- `NativeUfs`: a record of what the partition-table check of the modem firmware LUN failed on (v242, v243).

## Rebuilding the kernel tree

1. Check out Apple XNU 43a90889846e00bfb5cf1d255cdc0a701a1e05a4 into an empty tree.
2. Apply `patches/xnu-ipados18/series` in order with `patch --batch --fuzz=3 --forward -p1`. Eight of the 39 patches
   end with exit status 1: they carry hunks that no longer apply after the patches before them, and the build uses
   the tree as it stands. Delete the `.orig` and `.rej` files the tool leaves behind.
3. `git apply --index patches/xnu-ipados18/checkpoints/thor-v247/xnu-after-series.patch`.

The result is tree `a9192b5694626aef7777003c055dfa7b98d8a76a` of the checkpoint manifest, 5531 files: the v247 build tree without three generated
headers that stay local (below) and without the backup and reject files of the patch tool.

## Rebuilding a driver

Export the commit the manifest names for the driver from its upstream repository and apply the driver's patch with
`git apply --index`. The result equals the v247 recipe's source file for file. `PongWifi.patch` keeps the upstream
`README.md` and `AGENTS.md`, which the recipe no longer carries.

## Scripts

Scripts under `recipe/` and `tools/` read three variables from the environment instead of naming a machine or a
device:

| Variable | Meaning |
| --- | --- |
| `THOR_ROOT` | The directory that holds `UniXNU-pong/` and `thor-out/` |
| `THOR_TOOLS` | The directory that holds `.tools/pt34/platform-tools/fastboot` |
| `THOR_SERIAL` | The phone's fastboot serial number |

The packaging scripts of the chain expect the recipe directories of v152 to v247 side by side under
`$THOR_ROOT/thor-out/`, as they were when the images were packaged.

## What stays local

- Boot images and their kernels, the embedded libraries and their generated payload headers
  (`thor_console_v172_payload.h`, `thor_ui_runtime_payload.h`, `thor_metal_pool_payload.h`): signed binaries and
  local paths.
- Firmware, restore inputs, the private Wi-Fi test file and every other build product; the compiled host tests and
  helpers (`fbprobe`, `usbreset` are recorded as C sources in the v236 checkpoint).
- Logs, snapshots, partition-table dumps, panic records and photographs: they name the device (ECID, serial, boot
  session).

## Where the port stood

- v236 ran 7 h 58 min on the phone without the "画面定格"; the freeze was a kernel panic caused by a dangling clock
  pointer in `NativeAdrenoUserClient::dispatch()`.
- v237 to v239: Settings → General → About hung on `SCNetworkInterfaceCopyAll()`, which waits for a marker that
  configd never publishes; configd publishes it from v239 on.
- v239 was the first version flashed to `boot_a`. From flash the battery number and the Wi-Fi firmware were lost
  from then on: the partition-table check of the modem firmware LUN failed when booting from flash, and with it the
  modem, Wi-Fi and the battery dropped out two seconds into the boot. v241 did not reach the desktop at all (the
  wallpaper catalog named a MobileGestalt identity wrongly). v242 recorded which step failed; v243 lets the primary
  table decide and was the first version to boot from flash with the battery and the Wi-Fi firmware published.
- v244 ran 1 h 41 min and shows the status-bar battery number.
- v245 reached the desktop (6 minutes): the wallpaper is black because PosterBoard cannot create its data store on
  the unencrypted Data volume; the UI adapter that carries upstream's fix is injected since v245 and PosterBoard then
  aborts three times (the reason is to be captured); the access point answers the association request with status 48
  on 2.4 GHz; the Messages app hangs the desktop while its helper dies every ten seconds; there is no keyboard,
  because the port carries an iPhone identity on an iPad restore.
- v246 was built and never installed (the phone left fastboot). v247 carries everything of v246 and adds a line for
  every death by signal, crash capture in the Messages helper and a read-only camera inventory; it was built on
  2026-11-28 and has not been installed yet.
- Research written for v241 and v246: calls have no call-control code anywhere and the voice path never reaches the
  AP on this SoC; a working camera is possible and the inventory of v247 is its first step.

## Verification done for this checkpoint (host only)

- The series replays to the recorded tree `b8bbd733b4b8`; on that replay the thor-v236 patch round-trips to its
  recorded tree `87b77ec85073` and the thor-v247 patch round-trips to `a9192b569462`; its file list equals that of
  the build tree without what stays local.
- Each driver patch applies to a fresh export of its pinned commit, reproduces the recorded tree and equals the
  recipe source file for file.
- The device repositories were compared with thor-v236 by content: HEAD, the tracked patch and the 27 new files.
- Every shell script parses and every Python file compiles after the rewriting; all added files are UTF-8 text.
- `porting.toml` parses and `scripts/porting-record.py check` passes for the whole branch.
- No added file contains a home directory path, the device serial, the name of the office network or key material.

Nothing in this checkpoint was built into an image or run on the device for the checkpoint itself; it is a record
of sources. 390 files are indexed in `checkpoints/thor-v247/manifest.json`.
