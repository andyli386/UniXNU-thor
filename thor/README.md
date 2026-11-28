# Building the Thor port

Everything of the port lives in one directory tree and, inside it, in a few git repositories that all keep
upstream's layout, so that a change can be offered upstream as a pull request from the fork:

```
<root>/src/UniXNU-thor      this repository: the Thor device repository (fork of SFZ97/UniXNU-thor). thor/ is this directory
   drivers/<D>/             twelve driver submodules; eight of them on thor/main of their own forks (Thor's change), four at upstream commits
   refer/, apple/           ignored local folders: the pinned reference sources and Apple's components
<root>/src/uefi-pong        the UEFI firmware (fork of Chengdong-Yu/uefi-pong)
<root>/src/UniXNU           the upstream build-system repository (fork of UniXNU/UniXNU)
<root>/src/uefi-publish-20261117/darwinboot   the loader (a worktree on thor/main of the fork of Chengdong-Yu/pong-darwin-boot24)
<root>/inputs/              decrypted IPSW components, signed libraries, private firmware inputs, tools, the image template
<root>/work/<release>/      working copies: generated, disposable
<root>/images/<release>/    boot images with their kernels and records
```

`thor/env.sh` finds the root from its own location and exports it (`THOR_ROOT`, `THOR_REPO`, `THOR_PONG` — the
device tree, which is this repository —, `THOR_INPUTS`, `THOR_WORK`, `THOR_IMAGES`, `THOR_PY`, `THOR_JOBS`, and the
`PONG_*` inputs the device scripts look for). No file here names a machine or a user.

## One branch in every repository: `thor/main`

Each repository has the user's fork as `origin` (`git@github.com:andyli386/<name>.git`) and the upstream as
`upstream`; its local `main` mirrors the upstream. Every fix is committed on `thor/main` in the repository it
belongs to, and pushed to the fork once it is verified. No other branch is made.

| Change | Where it is committed |
|---|---|
| The device tree (scripts, kits, configurations, userland sources), the kernel patch series, the records, this build system | this repository |
| A driver | the driver's submodule, on `thor/main` of its own fork |
| The UEFI platform for the phone | `src/uefi-pong` |
| The loader | `src/uefi-publish-20261117/darwinboot` (the fork of `pong-darwin-boot24`) |
| The build system of the upstream project | `src/UniXNU` |

A pull request to an upstream repository is opened from `thor/main` of its fork. The old `thor/*` topic branches of
2026-11-17 that still exist on the forks are not used.

## One version, three commands

```sh
source thor/env.sh
$THOR_PY -I thor/bootstrap.py thor/versions/v247.json                 # 1. create work/v247-unified
thor/build-kernel.sh $THOR_WORK/v247-unified                          # 2. compile the kernel (two to four minutes from scratch)
$THOR_PY -I thor/package.py thor/versions/v247.json --work $THOR_WORK/v247-unified --publish    # 3. package, verify, publish
```

1. `bootstrap.py` creates the working copy from the sources and the inputs; nothing is cloned from an older working
   copy. It makes `device/` (the device repository through links, with a real copy of `scripts/` so that build
   outputs stay out of the source tree), the build-tree skeleton (the device repository's own
   `build-ipados18-pong-macos.py --prepare-only`), the kernel source tree (the pinned Apple XNU checkout, the recorded
   patch series, the version's kernel tree checkpoint — **compared with the recorded tree hash**) and `runtime/`
   (the libraries rebuilt from `thor/libraries/` and compared with the digests in `thor/libraries.json`, the signed
   ones copied from `inputs/signed/`). It writes `recipe.json`.
2. `build-kernel.sh` compiles the kernel with the device repository's build script (`SKIP_ALL_XNU_PATCHES=1`: the tree
   already carries every patch) and prelinks it, with the storage bindings this build made and the Apple kext closure
   from `apple/` in the device tree.
3. `package.py` takes the parent image from `images/`, replaces the kernel, the device tree, the manifest and the
   menu of the template image, trims the volume, verifies the result and writes the image and its record. It needs no
   older packaging script: the format helpers are in `thor/thorimage.py`.

A working copy can be deleted at any time and is back in a few seconds.

## Making the next version

1. Copy `thor/versions/v247.json` to the next number and change `version`, `release`, `title`, `parent` (the previous
   image in `images/`), `parent_sha256`, `chosen` and `manifest` (what the image's manifest says about this version).
   `chosen` adds `/chosen` properties to the parent's device tree; `"chosen_removed": ["name", ...]` takes ones away
   (v248 took `thor-camera`); the manifest's `chosen_selectors` must then name exactly what is left.
2. Add to `kernel_strings`: `new` for messages this kernel prints and its parent did not, `gone` for the parent's
   that are no longer there; move earlier `new` ones into `carried`.
3. `bootstrap.py` with the new file (the `checkpoint` is still the previous one, so the tree starts from it).
4. Change the kernel under `work/<release>/build-tree/.cache/xnu-work/`; drivers in their submodules under
   `src/UniXNU-thor/drivers/`; library sources under `thor/libraries/`. A library that changes gets a new name and an
   entry in `thor/libraries.json` with its digest after the first build; `runtime/env.sh`, made by `bootstrap.py`,
   tells the kernel build which library is embedded.
5. `build-kernel.sh`, then `package.py --publish`.
6. Record the kernel tree: **first set `"checkpoint"` in the version file to the new name (`thor-vNNN`)**, then
   `$THOR_PY -I thor/checkpoint.py thor/versions/vNNN.json --work $THOR_WORK/vNNN-unified` writes
   `patches/xnu-ipados18/checkpoints/<checkpoint>/` (the tree beyond the series, round-trip checked) and the
   commit and branch of every driver submodule; it refuses uncommitted driver changes and never replaces a
   checkpoint that exists (on 2026-11-28 a run with the old name left in the file overwrote the v247 files; they
   were restored from git).
7. Commit on `thor/main` in each repository the change belongs to and push to the forks.

## An OS patch: a change of the system volume travels in the boot image (since v249)

The system volume — the "OS" window of SHNGROOT, a raw APFS image of 0x198000000 bytes — is mounted read-only by the
port, so a change to it cannot be made on the running phone. It is made in a copy of the window on the Mac, and the
4 KiB blocks that differ travel as one file in the boot image's FAT volume; at boot the kernel streams that file
from boot_a and the storage transport applies it before it publishes any window. The first one carries the phone
keyboard resources of v249.

1. The installed window as a base: `inputs/private/os-window-as-installed.img` (the OS window of the userdata image
   of 2026-11-14; the port has not written to the window since — it cannot). Take a working copy with `cp -c`
   (a clone) and attach it read-write: `hdiutil attach -readwrite -nomount -owners on <copy>`, `diskutil mount`.
2. Add what the change needs. For the phone resources of the keyboard:
   `python3.13 -I thor/phone_resources.py copy --donor <OS image of the iPhone restore> --manifest <its BuildManifest.plist>
   --target-root <the mounted copy> --set keyboard --report <json>`. Unmount and detach.
3. `python3.13 -I thor/os_patch.py make --base <as installed> --modified <the copy> --output <patch> --version NNN`
   lists the blocks (one record to a block: 925 for v249); `show <patch> --all` prints them. Test the patch with the
   code the kernel runs: `clang -O1 -o harness thor/tests/os_patch_harness.c -I <kernel tree>/bsd/kern`, then run it
   on another clone of the base: the copy it leaves equals the modified window and a second run writes nothing;
   `S<n>` stops after n writes and the next run continues, `T<n>` tears a write. After packaging,
   `bash thor/tests/loadtest.sh <kernel tree> <binary>` builds the test of the transport's read (a copy of its code
   that the script compares with the driver) and runs the same apply with the patch taken from the built image.
4. In the version file: `"os_patch": {"file": "inputs/private/os-patches/…", "sha256": …, "bytes": …, "records": …,
   "window_bytes": …, "what": …}`. `bootstrap.py` copies the patch into the working copy as `os-patch.bin`;
   `package.py` writes it into the image as `/efi/unixnu/os-patch.bin` (the first member written, so its place is
   the template's), builds once more with the device tree naming its place in boot_a (`/chosen thor-os-patch-offset`
   and `thor-os-patch-bytes`), and checks that it did not move. The manifest and the record name the patch.
5. In the kernel: `bsd/kern/thor_os_patch.h` (included at the end of `kern_exec.c`; the kernel carries the code
   only, never the patch: a 3.8 MB payload did not fit under the prelink base). In `drivers/NativeUfs`:
   `PongUfsTransport.cpp` reads the two properties, asks the firmware LUN and calls the apply before any window is
   published; `RootCommands.h` allows reads of boot_a inside the patch's blocks only; `ModemFirmwareBounds.h` names
   the partition (boot_a: 4 KiB blocks 131334..180485 of LUN 4). `PONG_UFS OS PATCH V249:` and `THOR OS PATCH V249:`
   lines in the boot log say what happened. The kernel does not parse the FAT volume: the file has to be in one
   piece, which `package.py` checks.

Nothing is written on the phone unless every record is, there, exactly the original bytes or already the new ones,
and the first blocks of the window are those of the installed image (or already of the new one); otherwise the
boot goes on unchanged. Every record is read back after it is written. The patch is not atomic: if the phone loses
power in the middle, the next boot finds part of the records new and writes the rest before anything is mounted.
If a record does not match (the window on the phone is not the base the patch was made from), capture the window
read-only through TWRP and make the patch from that: `python3.13 -I thor/os_patch.py apply <patch> --dry-run`
also works directly against a phone in TWRP (the same checks, the same order) for when an image cannot be booted.

## History that was never pushed

Until 2026-11-28 most of the port existed only on one machine: as uncommitted changes and as commits that reached no
remote. All of it is on `thor/main` of its repository now. The final states are ordinary commits; the older lines
are merged as history through merge commits whose tree is the branch's tree, so the content of each branch is exactly
what was built and verified:
- this repository: the main of a clone of the upstream device repository (2026-10-30 to 11-15), the local
  repository "UniXNU-thor-18" (2026-11-10), the first local repository called UniXNU-thor (2026-10-29 to 11-12) and
  the October repository of the same name (the first-generation bring-up tooling, an incomplete clone);
- the driver repositories: the topic branches of 2026-11-17 and 11-18;
- `src/UniXNU`: the branch thor-ios16-migration (2026-10-29 to 11-07) and a working copy of October and November.
In the copied commits a personal author identity is replaced by the noreply one, and the phone's serial, home paths
and the personal name and e-mail in file contents and messages are replaced by placeholders. The unaltered originals
stay in bundles under `archive/git-history-20261128/` of the project directory; they are not published.

## What is where

| File | Content |
|---|---|
| `env.sh` | The layout, as environment variables |
| `bootstrap.py` | Creates a working copy from the sources and the inputs |
| `build-kernel.sh` | Compiles the kernel of a working copy and prelinks it |
| `package.py` | Packages, verifies and publishes the boot image |
| `checkpoint.py` | Records the kernel tree checkpoint and the driver pins of a version |
| `os_patch.py` | The changed blocks of the system window as one file: make, show, apply (a phone in TWRP, or an image) |
| `phone_resources.py` | Adds the resources of an iPhone restore of the same build to the guest's frameworks (the keyboard layouts) |
| `tests/os_patch_harness.c` | The OS patch code of the kernel, run against an image file on the Mac |
| `tests/boot_image_loader_test.c`, `tests/loadtest.sh` | The transport's read of the patch from boot_a, run against the built image |
| `thorimage.py` | The image format without older scripts: Android v4 boot image, the FAT16 volume, trimming, the menu, the Mach-O helpers, the device tree |
| `libraries.json` | Every library the kernel embeds: where it is built from or copied from, and the recorded digest |
| `libraries/<name>/` | Sources, build scripts and host tests of the injected libraries (the latest version of each; older ones are in `checkpoints/`) |
| `versions/vNNN.json` | One version: its parent, title, device-tree properties, manifest changes and the kernel messages packaging checks |
| `thor-gpu.defconfig` | The kernel configuration |
| `abi/BootArgs.h` | The loader's command-line ABI (1024 bytes), read by the menu check |

## What was verified on 2026-11-28

- Bootstrap, kernel build and packaging ran in a sandbox that denied every access to the old project directories.
- Packaging the members of the v247 image as shipped reproduces that image byte for byte (sha256 `4092ce88…`).
- `bootstrap.py` reproduces the v247 kernel source tree exactly (tree `a9192b569462…`) and the five rebuilt libraries
  byte for byte.
- With this repository as the device tree and the drivers as submodules: the same kernel as with the tree the
  images were built from so far — 1043 of 1044 compiled objects identical (the last is `version.o`, the build time),
  all 53 members of the seven native driver archives identical, the prelinked kernel different only in the version
  time and the working copy's path.
- A kernel rebuilt from scratch is not byte-identical to the one v247 shipped. The source trees are the same and
  1032 of 1044 objects carry the same code and data as the shipped kernel's. Eleven objects differ (`osfmk/kern/clock.c`,
  `ledger.c`, `debug.c`, the six `kdp` files, `console/video_console.c`): the old build's sanitizer ignore list
  named the tree's location of v202, so it matched nothing and those files were compiled with integer-overflow
  checks the sources mean to exclude; the new build follows the sources and is about 8 KB smaller there. The rest is
  the build time and the source paths embedded in the kernel. That kernel has not run on the phone.
- The record of v247 as shipped names the log capture library's digest under `console_sha256`; the new packaging
  writes the console library's.
- v247 was installed from `images/v247/` on 2026-11-28 at 18:31: its first boot panicked about 43.6 s after boot, the
  second reached the system and Wi-Fi joined. v248 is the first version made here (`images/v248-unified/`, sha256
  `bd87ca7c…`, kernel UUID `253D732E-…`): the same kernel sources except `osfmk/kern/thor_witness_v235.h` (the first
  page of a panic names the fault) and a device tree without `/chosen thor-camera`, so the camera inventory of v247
  does not start. It passed every check of `package.py`; it has not run on the phone at the time of writing.

## Not here yet

The install and collect scripts for the phone, and the boot-stack budget tool, are records in `checkpoints/` (copies
from the old tree). Port them into this directory: v248 was installed with copies of the old tree's scripts.
