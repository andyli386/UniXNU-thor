# Building the Thor port

The build system of the port. It needs one directory tree and nothing else:

```
<root>/src/UniXNU-thor      this repository (branch thor/main); thor/ is this directory
<root>/src/UniXNU-pong      the device repository with the driver repositories and the pinned reference sources
<root>/inputs/              decrypted IPSW components, signed libraries, private firmware inputs, tools, the image template
<root>/work/<release>/      working copies: generated, disposable
<root>/images/<release>/    boot images with their kernels and records
```

`thor/env.sh` finds the root from its own location and exports it (`THOR_ROOT`, `THOR_REPO`, `THOR_PONG`,
`THOR_INPUTS`, `THOR_WORK`, `THOR_IMAGES`, `THOR_PY`, `THOR_JOBS`, and the `PONG_*` inputs the device
repository's scripts look for). No file here names a machine or a user.

## One version, three commands

```sh
source thor/env.sh
$THOR_PY -I thor/bootstrap.py thor/versions/v247.json                 # 1. create work/v247-unified
thor/build-kernel.sh $THOR_WORK/v247-unified                          # 2. compile the kernel (about two minutes from scratch)
$THOR_PY -I thor/package.py thor/versions/v247.json --work $THOR_WORK/v247-unified --publish    # 3. package, verify, publish
```

1. `bootstrap.py` creates the working copy from the sources and the inputs; nothing is cloned from an older
   working copy. It makes `device/` (the device repository through links, with a real copy of `scripts/` so that
   build outputs stay out of the source tree), the build-tree skeleton (the device repository's own
   `build-ipados18-pong-macos.py --prepare-only`), the kernel source tree (the pinned Apple XNU checkout, the recorded
   patch series, the version's kernel tree checkpoint — **compared with the recorded tree hash**) and `runtime/`
   (the libraries rebuilt from `thor/libraries/` and compared with the digests in `thor/libraries.json`, the signed
   ones copied from `inputs/signed/`). It writes `recipe.json`.
2. `build-kernel.sh` compiles the kernel with the device repository's build script (`SKIP_ALL_XNU_PATCHES=1`: the tree
   already carries every patch) and prelinks it to `kernel/kernel-ipados18-thor.macho`.
3. `package.py` takes the parent image from `images/`, replaces the kernel, the device tree, the manifest and the
   menu of the template image, trims the volume, verifies the result and writes the image and its record. It needs no
   older packaging script: the format helpers are in `thor/thorimage.py`.

A working copy can be deleted at any time and is back in a few seconds.

## Making the next version

1. Copy `thor/versions/v247.json` to the next number and change `version`, `release`, `title`, `parent` (the previous
   image in `images/`), `parent_sha256`, `chosen` and `manifest` (what the image's manifest says about this version).
2. Add to `kernel_strings`: `new` for messages this kernel prints and its parent did not, `gone` for the parent's
   that are no longer there; move earlier `new` ones into `carried`.
3. `bootstrap.py` with the new file (the `checkpoint` is still the previous one, so the tree starts from it).
4. Change the kernel under `work/<release>/build-tree/.cache/xnu-work/`, drivers under
   `src/UniXNU-pong/drivers/<driver>/`, library sources under `thor/libraries/`. A library that changes gets a new
   name (and a new entry in `thor/libraries.json` with its digest after the first build); `runtime/env.sh`, made by
   `bootstrap.py`, tells the kernel build which library is embedded.
5. `build-kernel.sh`, then `package.py --publish`.
6. Record the sources: the kernel tree as a new checkpoint
   (`patches/xnu-ipados18/checkpoints/thor-vNNN/xnu-after-series.patch`: pristine XNU + the series, then everything
   the tree adds) and the drivers as one patch each against their pinned upstream commit
   (`checkpoints/thor-vNNN/drivers/`), as thor-v247 was recorded; commit.

## What is where

| File | Content |
|---|---|
| `env.sh` | The layout, as environment variables |
| `bootstrap.py` | Creates a working copy from the sources and the inputs |
| `build-kernel.sh` | Compiles the kernel of a working copy and prelinks it |
| `package.py` | Packages, verifies and publishes the boot image |
| `checkpoint.py` | Records the kernel tree and the driver sources of a working copy as the version's checkpoint, verified by round trips |
| `thorimage.py` | The image format without older scripts: Android v4 boot image, the FAT16 volume, trimming, the menu, the Mach-O helpers, the device tree |
| `libraries.json` | Every library the kernel embeds: where it is built from or copied from, and the recorded digest |
| `libraries/<name>/` | Sources, build scripts and host tests of the injected libraries (the latest version of each; older ones are in `checkpoints/`) |
| `versions/vNNN.json` | One version: its parent, title, device-tree properties, manifest changes and the kernel messages packaging checks |
| `thor-gpu.defconfig` | The kernel configuration |
| `abi/BootArgs.h` | The loader's command-line ABI (1024 bytes), read by the menu check |

## What was verified on 2026-11-28

- All three steps ran in a sandbox that denied every access to the old project directories.
- Packaging the members of the v247 image as shipped reproduces that image byte for byte
  (sha256 `4092ce88…`, 95,490,048 bytes).
- `bootstrap.py` reproduces the v247 kernel source tree exactly (tree `a9192b569462…`) and the five rebuilt libraries
  byte for byte.
- The kernel rebuilt from scratch is not byte-identical to the one v247 shipped. The source trees are the same and
  1032 of 1044 compiled objects carry the same code and data. Eleven objects differ (`osfmk/kern/clock.c`,
  `ledger.c`, `debug.c`, the six `kdp` files, `console/video_console.c`): the old build's sanitizer ignore list
  named the tree's location of v202, so it matched nothing and those files were compiled with integer-overflow
  checks the sources mean to exclude; the new build follows the sources and is about 8 KB smaller there. The rest is
  the build time and the source paths embedded in the kernel. That kernel has not run on the phone.
- The record of v247 as shipped names the log capture library's digest under `console_sha256`; the new packaging
  writes the console library's.
- `checkpoint.py` records the rebuilt working copy as the same kernel tree patch (tree `a9192b569462…`) and the same
  eight driver patches as the thor-v247 checkpoint, byte for byte.

## Not here yet

The install and collect scripts for the phone, and the boot-stack budget tool, still live in the old project tree
(per-version intake folders and its tools folder) and in `checkpoints/`. They are records there; port them into this
directory before the first version that is installed from here.
