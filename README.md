# UniXNU thor bring-up

Device: Xiaomi 12S Ultra (thor / SM8475).

## Direction

The primary implementation and source versions come from UniXNU/UniXNU.
UniXNU-pong and uefi-pong are references for related SM8475 hardware.
This device repository does not override the main project's feeds or XNU patch set.

## Current scope

`thor_compile_defconfig` describes a host-only ARM64 / 4 KiB compilation baseline.
It enables no hardware drivers or optional runtime providers.
The main project's existing BCM2837 configuration supplies its 4 KiB build layout.
A successful build is not evidence that this profile can boot on thor.

The Mac has completed the main-project source kernel build and its object/kernel audits.
The exact source pins, local guard fix, logs and artifact are recorded in `source-lock.json`.

## Configuration

Source the project environment, then use the core build driver's `defconfig` and
`status` commands with this directory explicitly passed as `-D`.
The selected configuration is stored outside this repository under the project build directory.

The core CLI's `build kernel` also executes prelink. This repository does not yet
supply `[kernel.prelink]`, an IPSW selection, an ADT, a firmware mapping or a payload.
The validated host baseline was built through the main project's
`scripts/build-xnu-4k-baseline.sh`; no prelink was executed.
The new device profile has been parsed and selected; a device-tree assembly and
profile-driven kernel rebuild have not yet been performed.

Do not add `patches/xnu/` for a single thor patch: the module resolver replaces
the entire same-name core patch set. Generic fixes belong in the core repository.

## Next hardware milestones

1. Validate UEFI timer events and load the core UniXNU EFI application from firmware volume.
2. Establish an owned continuous memory window using thor's real memory map and reservations.
3. Build a thor ADT and the core platform/provider dependency set for one boot CPU.
4. Verify an identifiable XNU entry or early console checkpoint before adding other drivers.

The existing smoke01 UEFI evidence remains separate from XNU runtime evidence.
