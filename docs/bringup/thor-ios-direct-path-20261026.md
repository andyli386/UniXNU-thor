# Thor to iOS SpringBoard: Direct boot mainline plan (2026-10-26)

## Verified reason for delay

The currently hardware-tested firmware is a purpose-built bounded XNU loader, not the full UniXNU UEFI stack. The Thor DSC explicitly excludes generic BDS, disk and storage. The current kernel board definition explicitly calls itself a compile-only baseline and uses an empty archives list; the defconfig disables Platform Expert, CPU/timer, UFS, APFS root/Data integration. The known HW10 L3 checkpoint reaches the pre-pmap guard but not pmap, scheduler, launchd or the original iOS userland.

Reference: Pong is also SM8475 and its public iOS16.6.1 (20G81) port reports SpringBoard and display. It carries the matching DarwinBoot22 loader and related patches. Its board config, archive flags, UFS/APFS and userland work are reusable as engineering references, but Pong physical addresses, ADT/FDT, PMIC, panel (1080x2412), storage PHY and DMA ownership must not be copied directly into Thor (1440x3200).

The Thor diagnostic XNU is the XNU10063/Darwin23 branch, while Pong's iOS16 profile has a different OS/loader ABI. Choose and pin one coherent iOS16/XNU/DarwinBoot/driver/userland profile for a faster first SpringBoard. Do not mix the incompatible loader or prelink archives.

## Six goal-based gates

| Gate | Required observable result |
|---|---|
| F1 Complete UEFI | UniXNU.efi, UniXNUDxe, DarwinBoot, RAM disk, DiskIo/FAT, console, payload entry successfully load on Thor |
| F2 Native VM | pmap_bootstrap completes; arm_vm_init returns with a real early kernel logging channel |
| F3 Kernel core | Platform Expert/ADT, ARM CPU/GIC/timer/IOKit and scheduler work with known platform memory |
| F4 Storage root | Thor NativeUfs / APFS publish and mount OS, SystemOS and the Data root with matching UUID |
| F5 Userland | Matched iOS install input, trust cache, Cryptex, launchd and essential daemons really execute |
| F6 SpringBoard | Original iOS display service and SpringBoard frame are visible on Thor display |

These are acceptance gates, not a prediction of six hardware tests. The strict deadline and effort remain unproven. Work on kernel memory/SoC plumbing, full firmware, and external OS image preparation can proceed in parallel. To first show SpringBoard, do not block by unrelated Wi-Fi, camera, audio or full native GPU acceleration.

## New stage HUD and diagnostics

The isolated HW12 XNU branch changes stage 02 from WHITE to CORAL RGB #FF638A while stage 10 remains WHITE. The 11 completed checkpoint colors must be pairwise distinct. In the isolated HW12 UEFI branch the HUD lists all 00-10 checkpoint names, then displays the 6 full iOS boot goals with explicit PENDING values. This is a static legend drawn before ExitBootServices; actual XNU pass state is only the colored 4x4 grid (not a UEFI self-report). Do not imply that UEFI can update after EBS or that any future iOS goal has passed.

Next trace step: establish a continuous kernel/exception event log with stage IDs, first-failure reason, ESR/ELR/FAR when actually captured, and reset cause; use the high address framebuffer only as a fallback for pre-console stages. Do not invent unavailable registers or use a fake iOS UI screenshot as proof.

## Strict operations

1. Retain current HW10 live evidence and frozen HW11 ready-to-test candidate. Never modify or reuse a consumed boot image.
2. Restore full UniXNU UEFI on a separate feature branch, keeping the specific Thor GOP/FDT/memory handoff verified by prior real hardware runs.
3. Pin the exact iOS build, version and usable Apple restore components with hashes, never commit Apple IPSW files, Data images, keys or device secrets.
4. Build and verify native XNU and needed SoC drivers in a coherent ABI. Perform no uncontrolled post-pmap jump; each milestone requires isolated L2 + visible/logged L3 evidence.
5. Do not run flash/erase/format/switch slots or write userdata without fresh user approval and complete backups.
6. Once platform, root and launchd pass, require original SpringBoard and graphical services to actually present a frame; no simulations or fabricated pass cards.

## Source and evidence

Mac sources: references/pong-ios16-source at ab74f075320fe54eee24d5d150386e5b0f0f427f; repos/UniXNU-thor; current HW11 XNU source in worktrees/xnu/vm-dev legacy pinned branch.
Board fullchain checker: tools/thor-ios-readiness.py, report saved under project reports/thor-ios-fullchain-audit-20261026.json.
Details: https://unixnu.com/porting/uefi-firmware , https://unixnu.com/devices/pong , https://github.com/Chengdong-Yu/UniXNU-pong .
