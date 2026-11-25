# HakiOS (umi, SM8250 / Adreno 650) reference notes: GPU, display, hang evidence

Read-only survey of `reference-umi/hakios-xnu-kernel-umi-fixes` (HakiOS 2.3.4 lineage, XNU 8796.121.2, iOS 16.5).
Path shorthands: `D/` = `devices/xiaomi-mi-10/`, `O/` = `D/overlays/xnu/files/`, `U/` = `D/overlays/userland/src/`,
`L/` = `D/loader/loader.c`. **[inferred]** marks my reading, not something the tree states.

## Summary

- Pipeline: backboardd's CoreAnimation -> Metal plug-in (`HakiosMetalCA` + `hakios-metal`) -> `HakiosGPU` -> IOSurface
  -> `MiniIOMobileFramebuffer` -> DPU. With `fb_dpu=4` the GPU draws CA's frame surfaces straight into scanout frames.
- The "100-118 frames/s" figures belong to the **POCO X5 Pro (redwood, Adreno 642L, 120 Hz)**, not the Mi 10
  (`docs/reference/metal.md:3-8`; `CHANGELOG.md:42,47`). The Mi 10 panel measures 59.835 Hz (`D/FLICKER-umi.md:22`);
  best there: 23,974 flips, 23,971 latched, 0 not-in-time (`:44-48`); 90 Hz untested on hardware (`:4`).
- Hang evidence does **not** rely on flash: a resident loader on CPU0 reads the dead kernel's log ring and a
  per-processor "breadcrumb" table from RAM after 50 s of silence and draws them on the panel for 30 s.
- The tree records Thor's symptom class (UI freeze after leaving an app) as a GPU SMMU fault on freed memory
  (`D/METAL-ALL-umi.md:24,31`).
- Task-brief corrections: `umi_cpu_pc.h` is the breadcrumb table, not CPU power management (`umi_cpu_pc.h:2`);
  `SAFARI-umi.md` is a Safari History crash loop, not an app-launch freeze.

## Rendering pipeline

1. **Discovery**: with `metal=1` the IOAccelerator service carries `MetalPluginName`/`MetalPluginClassName`; Metal
   loads `HakiosMetal.bundle` (`userland/src/HakiosMetalCA.m:12-16`). The device subclasses `_MTLDevice` directly
   because `IOGPUMetalDevice` opens the accelerator with a type the kernel refuses (`:23-28`); `<MTLDeviceSPI>`
   (337 methods) is added at runtime (`:29`).
2. **Process**: backboardd is the window server ("CA::WindowServer", `U/inject.c:100`). The CA build shows the device
   to the window server alone; `metal=2` all but backboardd, `metal=3` everyone (`U/HakiosMetal.m:1482-1545`).
3. **CA to Metal**: QuartzCore asks for pipelines by label `com.apple.coreanimation.frag.<code>`. iOS 16.5's own
   `QuartzCore.framework/default.metallib` is copied in by `tools/ca-metallib.py` (`D/CA-METAL-umi.md:23-27`). A
   label the pipelinelib lacks gets the generic uber shader compiled with CA's named function constants (`:36-42`),
   AIR to NIR to ir3 in-process. The older fallback `hm_ca_nearest()` matches sampled type, blend and varyings, then
   fewest inputs changed (`userland/src/HakiosMetalCA.m:1993-2074`).
4. **Kernel**: `HakiosGPU` speaks Linux msm DRM request numbers over wired, SMMU-mapped buffers, plus a buffer over an
   IOSurface (`O/iokit/Kernel/hakios.gpu.cpp:1-24`); `O/bsd/dev/hakios.adreno.c:1-60` is the 22-step bring-up.
5. **Hand-off**: backboardd hands IOSurfaces to IOMobileFramebuffer, so `presentDrawable` counts stay 0 and `fbstat`
   is the real number (`D/CA-METAL-umi.md:136-140`).

## Scanout and frame rate

- **Baseline (CPU)**: scale 1125x2436 to 1080x2340, wait for the command-mode DMA idle window, memcpy 10 MB into the
  UEFI GOP frame, `bzero` the kept surface: 32-42 fps, 10-13.5 ms in the driver (`D/DISPLAY-SCANOUT-umi.md:7-21`).
- **Hardware facts**: DSI0 command mode with INTF_1 autorefresh; the GOP frame at 0x9C000000 lies in a 0x2300000 splash
  carve-out mapped 1:1 for the display SMMU, room for three frames (`:70-74`). apps_smmu CB4 bypasses but is 32-bit
  only: "the carve-out is the only DPU-reachable memory" (`:276-280`). One pipe, VIG0 (QSEED3LITE) (`:200-207`).
- **Mechanism**: `fb_dpu=1` snapshots UEFI's DPU state read-only, scales into carve-out frames 1-2, writes `SRCn_ADDR`
  then `CTL_FLUSH`, waits up to 100 ms for the latch, else falls back to the CPU path (`:82-104`): 27/27 latched (`:3`).
  `fb_dpu=3`: VIG0 QSEED3 scales, the CPU only memcpys (1.13 ms) (`:232-256,282-285`). `fb_dpu=4`: `zeroSlot()` binds
  a CA surface via `HakiosGPUSurfaceAlias()`, rewriting its GPU PTEs to a carve-out frame; `SwapEnd` returns at once
  (`:287-316,370-372`).
- **Bottlenecks and fixes**: A55 helpers set the frame time, so 32-row dynamic chunks (`:60-62`); SwapWait returned
  early and CA drew into a pending surface, so `waitShown()` (`:415-418`); submission waited a whole TE, so v16 frees a
  frame once the refresh's transfer is done, 90.8% on time (`:422-433`); multi-layer frames dropped when the copy
  window shut (`:667-719`); the carve-out is Device memory, so `carveCopy()` (`:392-411`); CA's own frame CPU caused
  1737 of 2458 late frames (`D/CA-METAL-umi.md:696-698`).
- Multi-layer DPU composition (VIG0..3 into LM0) is default off behind `/var/db/hakios-display/dpu-compose`,
  host-validated only (`:441-451,517-551`).

## Flicker and stale-frame root causes

- **Steady peripheral flicker**: normal AMOLED pulse modulation, vendor "4 Pulse" (`D/FLICKER-umi.md:13-19`). Large
  brightness steps are idle dim/off, not an ALS loop (`:28-42`). DC dimming is not implemented (`:82-94`).
- **Scanout content**: 454 windows, zero not-in-time; zero copy was disabled once after 16 demotions, a copy
  transition rather than a refresh-rate one (`:43-53`).
- **Slow frames filtered at once** (`xnu/files/iokit/Kernel/MiniIOMobileFramebuffer.cpp:165-176`): `fb_filter=2` draws
  nearest-neighbour while frames move and bilinear after 40 ms, else a 10 fps clock makes every edge jump twice.
  "Nothing drawn again from a surface the GPU has taken back" is `fStatSettleReused` (`:394`).
- **Red wallpaper head, white status-bar band, opaque Weather widget**: run-time pipelines got one fixed variant; fixed
  by `hm_ca_nearest()`, then exact compile (`CHANGELOG.md:24`; `docs/reference/metal.md:176-180`).
- Ranked flicker causes (wrong shader stand-ins, deferred-commit race, stale IOSurface shadows): `D/CA-METAL-umi.md:121-130`.
  Maps: GMEM-to-sysmem dropped the pass prefix (`MAPS-umi.md:9-16`).

## Hang debugging and durable evidence

- **Breadcrumbs** (`O/osfmk/arm64/umi_cpu_pc.h`, version 6): one 192-byte entry per core, written by that core only,
  plain stores, no locks, no MMIO (`:47-51`). Fields: interrupted PC/LR/SP/time/tid (`:335-348`), idle flag around
  `machine_idle()` (`:352-368`), spin-lock caller/lock and waits over 100 us (`:375-403`), GIC INTID in flight
  (`:410-425`), pmap teardown phase (`:463-477`), last 8 process exits (`:485-501`), and a panic record formatted
  before any lock (`:647-703`). A panicking core cleans its cache lines and parks (`:731-748`); the table is plain
  kernel data (`umi_cpu_pc.inc:15-20`).
- **Reader**: the loader runs XNU on CPU7 and stays on CPU0 (`L:925-938`: heartbeat every 10 s, log pointers, `cpu_pc`).
  After `SILENCE_SECONDS` 50, or 2 s once the panic record says PARKED (`L:1183-1184`), `report_hung_kernel()`
  (`L:2443-2582`) prints "THE KERNEL HUNG on core", the panic text and the stuck MMIO access, then
  `log_of_hung_cpu_pc()` (`L:1607`), then holds `HUNG_SCREEN_SECONDS` 30 while petting the watchdog
  (`L:2431,2565-2576`). `hung_scanout_probe()` re-points the DPU pipe at the report frame (`L:4550-4619`).
- **Watchdogs**: a Haven SMC watchdog is petted by CPU0 only while the heartbeat advances
  (`D/LOADER-PROD-umi.md:37-43`). `gpu_recover=1` is default on umi (`hakios.adreno.c:4324-4326`); an SMMU fault
  during a fence wait triggers `hakios_gpu_recover()` (`:3885`): reset the core, drop in-flight submissions, keep a
  512-entry mapping ring (`D/METAL-ALL-umi.md:162-182`). Recovery counts only if fps and `fbstat` keep growing (`:228`).
- **Escape without forced reboot**: five fingers held 1.5 s call `loader_return()` (`O/bsd/dev/hakios.input.c:62-83`).
- **SpringBoard stall sampler** (`U/inject_sb.c`, needs `touch_lat=1`): a watcher thread walks the main thread's frame
  pointers via `thread_get_state` (`:156`) at 25 ms then every 50 ms; a turn over 100 ms prints one
  `tl: A turn_ms=... top=NAME:COUNT,... last=...` line with `dladdr` names (`:80-93,296-345`). Lines reach the kernel
  ring through `hakios_tl()` (`hakios.input.c:30-45`).
- **Method**: the host pulls `oem log` snapshots every N s (`XL_FOLLOW`, `tools/metal-run.sh:108`) and dedupes them
  because the ring rolls (`D/WALLPAPER-umi.md:17-25`); panels are photographed and cited by log line number, and each
  defect gets a host regression test before a fix is claimed (`MAPS-umi.md:5-20`).

## Logging durability notes

- **Kernel ring**: 256 KiB head + 1 MiB ring in RAM (`xnu/files/osfmk/arm64/loader_return.c:37-38`); the middle is
  dropped and os_log floods it (`docs/ru/notes.md:283-288`). Writers spin `LOADER_LOG_SPINS` 100000, then write
  unlocked and say so (`D/xnu-device.patch:283-311`).
- **Loader**: "The UMI bring-up keeps logs in RAM/USB; no raw partition writes" (`L:356`); other platforms write a
  `logdump` partition with flush and read-back (`L:389-405`).
- **Kernel-to-HFS**: `usb_file_thread` writes `/private/var/db/hakios-usb-<epoch>-{0,1}.bin`: `copy_log()`, FNV-1a
  hash, header before and after the data, `vn_rdwr(... IO_SYNC)`, `VNOP_FSYNC(MNT_WAIT)`
  (`D/drivers/umi/hakios.usb.c:1312-1398`), every 60 s in production, 2 s in debug (`:1396`), with a final snapshot
  from `reboot_kernel` (`:1406-1414`). About a minute can be lost (`D/LOADER-PROD-umi.md:59`).
- **logd**: `var_store` does not keep `log`, `logs`, `db/diagnostics`, `db/uuidtext` between boots
  (`userland/src/var_store.c:70-79`); init creates markers because "logd takes a log directory without this marker for
  one left by an older system" (`userland/src/init.c:970-977`). **[inferred]** A missing or stale marker is a plausible
  reason for logd to stop persisting; the tree never describes logd stopping. No `F_FULLFSYNC` use found
  (`userland/libmini/mini.c:261` is a plain syscall).

## CPU frequency and scheduler

- `cpufreq=1`: EPSS (`qcom-cpufreq-hw`) at 0x18591000, three domains (cores 0-3, 4-6, 7), one register write each
  (`O/bsd/dev/hakios.cpufreq.c:1-12,62-68,93-100`).
- Governor: 20 ms tick (`:339`); busy >= 70% jumps to the cap, else the row for 60% occupancy; two-sample downward
  hysteresis; idle domains to row 0; thermal and screen caps always win (`:277-307`).
- `hakios_cpufreq_boost(ms)` is capped at 8 ms and raises the caller's domain only unless
  `/private/var/db/umi-thermal-global-boost` exists (`:71-77,246-275`). Callers: touch 40 ms (`umi_touch.c:88`),
  present 24 ms (`MiniIOMobileFramebuffer.cpp:2043,2496`), GPU submit 32 ms (`hakios.gpu.cpp:1276`).
- `sched.patch`: `amp=1` sets `small_cores_apart = 2` (`D/sched.patch:7-10`). `idle=1` wraps WFI for measurement
  only, never CPU_SUSPEND (`O/osfmk/arm64/umi_idle.h:1-3,42-58`).
- Screen off: `umi_pm_cpu_cap_khz()` ceiling, domain 0 exempt (`D/SLEEP-DVFS-INTERFACE.md:3-10`); GPU votes 305 MHz
  over HFI (`umi_gpu_screenoff.h:4-15`).
- Measured: the DDR RPMh vote (4096) gives 12.4 -> 14.8 GB/s and "frames over 16.7 ms 28% instead of 34-36%"
  (`:753-771`). `cpuload=MS` logs per-core busy % (`:592-596`); `heat-policy:` every 10 s (`:403`).

## Battery

Neither glink nor RPMh. The loader on CPU0 calls Qualcomm's UEFI ChargerEx and QcomCharger protocols
(`get_charging_action` returns SOC, mV, temperature) (`L:3843-3852,2641-2707`; `D/loader/efi.h:140-167,303-304`) and
publishes `struct loader_battery` (magic "RDWBATT!") with a sequence counter (`L:1113-1160`). XNU `battery_poll()`
takes a seqlock snapshot (`O/bsd/dev/hakios.battery.c:77-119`), exported as `kern.hakios.battery_*` sysctls (`:57-75`).
CHARGING is derived (`plugged && soc < 100`); SOC falls back to a voltage estimate (`L:2699-2704`).
`umi_charge_diag=1` adds read-only PM8150B SMB5 samples (`umi_charger_diag.h:1-15`); `umi_rpmh.c` only votes rails
(`umi_rpmh.c:1-4,17-23`).

## Device-specific vs generic

**SM8250/Mi 10 specific**: chip ID 0x06050002 and Mesa's A650 row (GMEM 1,179,648 B, 96x16 tiles, 32 VSC pipes)
(`hakios-metal/include/hakios-metal/a6xx.hpp:29-40`; `METAL-umi.md:12-16`); "a GPU of another chip ID is refused by
name" (`CHANGELOG.md:35`); `a650_zap.mdt`, `a650_sqe.fw` (`hakios.adreno.c:38,48`); MDP 0xAE01000, INTF_1 0xAE6B800,
DPU 6.0 (`DISPLAY-SCANOUT-umi.md:75-80`; `L:4601-4603`); carve-out 0x9C000000/0x2300000; EPSS, RPMh 0x18220000,
cmd_db 0x80860000; Samsung J2 DSC panel 1080x2340 at ~59.8 Hz; PM8150B.

**Generic, transferable**: the breadcrumb design and loader-side reader; the lock-tolerant log ring; the two-slot HFS
snapshot; `hakios_tl`; the SpringBoard stall sampler; `fbstat`; the EPSS governor shape; zero-copy aliasing
**[inferred]**, provided Thor's DPU can reach the memory (a stage-1 SMMU window was needed on SM8550,
`DISPLAY-SCANOUT-umi.md:121`).

**XNU-8796 / iOS 16.5 specifics likely to differ under xnu-11417 / iPadOS 18.7**: IOMobileFramebuffer selectors and
swap layout "worked out from the user-space side of iOS 16.5" (`MiniIOMobileFramebuffer.cpp:9,150-153,3741`);
IOAcceleratorES `MetalPluginName`; `_MTLDevice`/`IOGPUMetalDevice`/`<MTLDeviceSPI>` layout for Metal 306.6.4
(`HakiosMetalCA.m:10-55`); QuartzCore label grammar and `default.metallib` contents (`:1993-2002`);
`CADisableMinimumFrameDurationOnPhone` (`FLICKER-umi.md:234-240`); unslid 20F66 SpringBoard addresses
(`D/STATUSBAR-umi.md:10-45`). **[inferred]** Adreno 730 is a7xx: its register XML, GMEM size, tile alignment and
firmware names differ from the a6xx tables hakios-metal checks against Mesa.

## Dead ends

- `fb_dpu=2` fetch test: no contiguous RAM below 4 GiB in XNU, skipped (`DISPLAY-SCANOUT-umi.md:278`).
- GMEM draws hung at submission 43 on A650; sysmem is the default and the cause is unproven (`CA-METAL-umi.md:87-105`).
- 90 Hz panel switch: two device runs failed in command transport (`FLICKER-umi.md:168-183`); `fb_hz=` only paces
  software (`:129-131`).
- Wallpaper: frozen to one still picture for the home-button rate; a moving wallpaper "is not solved"
  (`docs/reference/metal.md:165-167`).
- The loader heartbeat cannot detect "UI/GPU deadlock but heartbeat still normal" (`LOADER-PROD-umi.md:43`); the
  SpringBoard sampler is the tool for that.
- Not found: any battery-glink readout, `F_FULLFSYNC`, a reserved-RAM crash ring, or a note that logd stops.
