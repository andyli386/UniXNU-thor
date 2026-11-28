# Integrating the front camera probe (v247)

Staged in `thor-out/v247-camera-staging/`. Nothing here has been built into a kernel or run on the phone. What is
verified: the host test passes (clang, ASan/UBSan, 1.6 s) and `PongCamProbe.h` compiles with the flags of
`build-pong-power-native.py` against the v246 kernel framework headers (`-fsyntax-only`, no object written).

## 1. Files

Copy into `device/drivers/PongPower/` of the new recipe:

| File | Role |
|---|---|
| `PongCamBoard.h` | board constants: addresses, pins, rails, TLMM bit fields |
| `PongCamCc.h` | CAM_CC steps: GDSC, RCG to XO, branch; save and restore of the RCG words (from `ZizhanCamCc.h`) |
| `PongCamCci.h` | CCI reset, timings, register read, the ordered chip-id list (from `ZizhanCci.h`) |
| `PongCamPower.h` | CMD DB lookup, VRM rail votes, bounded rail wait, the power sequence and its roll-back |
| `PongCamInventory.h` | the read-only snapshot and the SPMI channel lookup |
| `PongCamProbe.h` | the kernel probe: windows, pins, observer reads, ops, log lines, worker, gate |
| `hosttest/` | `run.sh` and `test_cam.cpp` |

`hosttest/run.sh` finds `ThorModemCmdDb.h` in the parent directory once integrated; until then it falls back to
`../../v246-unified/device/drivers/PongPower` (override with `PONG_POWER_DIR=`).

## 2. The one call

In `device/drivers/PongPower/PongPlatformResources.cpp`:

```cpp
#include "PongCamProbe.h"            // with the other includes at the top
...
extern "C" void pong_rpmh_init(void) {
    thor_battery_start();
    initLock(&powerLock); initLock(&smmuLeaseLock);
    thor_cam_probe::start();         // THOR CAMERA V247: reads /chosen thor-camera once
}
```

Why there and not in `PongPower.cpp`, where zizhan has it (`zizhan_cam_probe::start()` in
`pong_power_start_optional()`, after the SLPI start and before the `pong_adsp` gate):

- In thor's recipe `PongPower.cpp` is **not compiled**: `build-pong-power-native.py:48` lists only `QcomSmem.c`,
  `QcomGlink.c`, `QcomSmp2p.c`, `ThorBatterySource.cpp`, `PongPlatformResources.cpp`.
- `pong_power_start_optional()` is **not linked**: `ArmDTPlatform.cpp:31-34, 244-246` guards the call with
  `#if XNU_PONG_POWER`, and `build-pong-platform-macos.py` defines only `XNU_PONG_ADRENO_PLATFORM` and
  `XNU_PONG_WIFI`. `nm` of the v246 kernel has `_pong_wifi_start_optional` and no `_pong_power_start_optional`.
  Enabling it would also pull in the Pong audio and USB start paths, which thor does not use.
- `pong_rpmh_init()` is the PongPower entry that runs on every thor boot: its first call is
  `pong_wifi_start_optional()` (`PongWifiPlatform.cpp:724`), made by `ArmDTPlatform::start()` right after the CPU
  nubs, where the Wi-Fi and BT gates read `/chosen` too. That is the same moment zizhan's probe is started.

`start()` itself only reads the property and creates one kernel thread; it is the same `IORegistryEntry::fromPath`
call the `thor-wifi` and `thor-bt-probe` gates make there (no new stack depth; still run the boot-stack budget
script before shipping). If the device-tree plane is not there yet at the first call, the next call reads the gate.

The worker waits for `rootvnode` (at most 120 s, like the BT probe) and then 30 s, so the probe runs after the
Wi-Fi bring-up and the modem probes, which share `powerLock` and the RPMh TCS. An equivalent alternative, if the
call is preferred outside PongPlatformResources.cpp: `thor_cam_probe::start();` next to `thor_bt_probe::start();`
in `ArmDTPlatform.cpp:250`.

No build script changes: `PongPlatformResources.cpp` is compiled with `-I drivers/PongPower` and
`libPongPower.a` is force-loaded (`build-xnu-ipados18.sh:1219-1222`).

## 3. The `/chosen` property

`thor-camera`, a 4-byte little-endian integer, the format of every other thor selector (`thor-wifi`, `thor-bt-probe`,
`thor-pas4-probe`, ... in `unified-verification.json`). Absent or 0 = off: nothing is mapped and no thread is
started. 1 = level 1. 2 = level 2. Anything else logs `SKIP unsupported-mode=` and does nothing.

How thor selectors get into the image. The generator `scripts/make-thor-adt-ipados18.py` (a symlink into
`UniXNU-pong`, so copy it privately before editing it) builds the base `/chosen` (`chosen = [...]`, lines 191-249)
and holds no `thor-*` selector. The recipes inject them when packaging, as in `package-v212.py:18-53`:

```python
nodes = api["parse_adt"](old[device_tree])
assert "thor-camera" not in nodes["/chosen"]
nodes["/chosen"]["thor-camera"] = (1).to_bytes(4, "little")
dt = bytes(api["emit"](nodes, "/"))
assert api["parse_adt"](dt) == nodes
```

`package-v246.py:175-179` carries the parent's ADT on unchanged, so `package-v247.py` must take the `tree`/`dt`
route above, let `adt_member` land in `changed` (the `if dt != old[adt_member]` branch at line 344 already does
that), set `chosen_changed=True`, add `"thor-camera": 1` to `manifest["config_change"]["chosen_selectors"]`, and
add the new kernel strings to `new_kernel_strings`:

```
b"THOR CAMERA V247: RUNNING mode=%u readonly=%u"
b"THOR CAMERA V247: %s snapshot done camcc_read=%u writes=0"
b"THOR CAMERA V247: DONE attended id_rc=%d failed_step=%d left=%x rails_off=%u clocks_off=%d pins_restored=%u"
```

No boot argument changes (the command line stays at 1005 bytes).

Ship level 1 first. It only reads and can go into any image. Level 2 belongs in a dedicated image for the attended
session: the property is baked into the ADT, so a value of 2 powers the front camera at every boot of that image,
about 30 s after the root volume mounts. Go back to 0 or 1 afterwards.

## 4. Kernel lines to expect

Every line starts with `THOR CAMERA V247: `. `%x` are register values, bit 31 = clock off or power off.

At start (both levels, on the boot path):

```
THOR CAMERA V247: RUNNING mode=1 readonly=1          (or mode=2 readonly=0)
```

Level 1, once, about 30 s after the root volume mounts:

```
inventory gcc camera_ahb=%x camera_xo=%x camera_bcr=%x (bit31 = clock off; bcr bit0 = held in reset)
inventory pin=104 cfg=%x func=%u oe=%u pull=%u drive=%umA egpio=%u/%u in=%u out=%u     (then pin=108, 208, 209)
inventory camcc read=%u (0 = camera AHB clock off, CAM_CC not touched)
  camcc gdscr titan=%x bps=%x ife0=%x ife1=%x ife2=%x ipe0=%x sbi=%x sfe0=%x sfe1=%x          (only when read=1)
  camcc mclk4 rcg=%x/%x branch=%x cci1 rcg=%x/%x branch=%x slow_ahb=%x/%x fast_ahb=%x/%x camnoc=%x/%x
  camcc cpas_ahb=%x camnoc_axi=%x core_ahb=%x (bit31 = off)
  titan blocks read=%u (0 = titan_top off or its AHB stopped) cci0=%x cci1=%x csid0=%x csid_lite0=%x ife_lite0=%x
inventory cmd-db ldoj3=%x ldoj5=%x ldoj2=%x (0 = absent)
inventory spmi ldoj3 slave=9 peripheral=42 channel=%d of %u        (then ldoj5 44, ldoj2 41; -1 = no channel)
inventory snapshot done camcc_read=%u writes=0
```

`titan blocks read=1` is not expected at a normal boot: titan_top is off then and the CCI/CSID/IFE words are only
read while it is powered. If the CMD DB cannot be mapped, `FAIL cmd-db map` (or `FAIL cmd-db memory`) precedes the
cmd-db line.

Level 2: the same block with the prefix `before`, then

```
ATTENDED front camera: reset gpio108 low; vio ldoj3 1800 mV, vana ldoj5 2800 mV, vdig ldoj2 1104 mV (RPMh); MCLK4 19.2 MHz on gpio104; reset released; CCI1 master 1 on gpio208/209
cmd-db ldoj3=%x ldoj5=%x ldoj2=%x
spmi ldoj3 slave=9 peripheral=42 channel=%d of %u read=%d type=%x/%x status=%x enable=%x mv=%u      (then ldoj5, ldoj2)
reset gpio108 asserted low cfg=%x io=%x
gcc camera ahb already running reg=%x owned=0      (or: gcc camera ahb was=%x now=%x owned=1)
rail ldoj3 1800mV addr=%x channel=%d rpmh_rc=%d type=%x/%x status=%x enable=%x mv=%u settled=%u   (then ldoj5 2800mV, ldoj2 1104mV)
mclk rc=%d stage=%u gdscr=%x rcg=%x/%x branch=%x
mclk pin gpio104 cfg=%x
reset gpio108 released cfg=%x io=%x
cci clocks on gdscr=%x cci1 rcg=%x/%x branch=%x core_ahb=%x cpas_ahb=%x camnoc=%x
cci pads gpio208=%x gpio209=%x
titan hw cci1=%x cci0=%x csid0=%x csid_lite0=%x ife_lite0=%x (reads only)
cci m1 try=%u %s addr=%02x reg=%04x rc=%d id=%02x%02x%02x      (one per attempt, at most eight)
ID ANSWERED %s addr=%02x reg=%04x id=%02x%02x%02x              (or: ID no answer tries=8 rc=%d)
cci clocks off step=%d cci1=%x cpas_ahb=%x camnoc=%x core_ahb=%x slow_ahb=%x/%x camnoc_rcg=%x/%x
reset gpio108 asserted low cfg=%x io=%x
mclk off rc=%d branch=%x rcg=%x/%x gdscr=%x
rail off ldoj2 addr=%x rc=%d      (then ldoj5, ldoj3)
gcc camera ahb off reg=%x owned=0
after rail ldoj2 read=%d status=%x enable=%x mv=%u     (then ldoj5, ldoj3)
after camcc titan_gdscr=%x mclk4=%x cci1=%x cpas_ahb=%x camnoc=%x core_ahb=%x gcc_camera_ahb=%x
power-down failed_step=%d rail=%u id=%d left=%x
DONE attended id_rc=%d failed_step=%d left=%x rails_off=%u clocks_off=%d pins_restored=%u
```

A good run ends `id_rc=0 failed_step=0 left=0 rails_off=3 clocks_off=1 pins_restored=4`. Each RPMh vote also
prints its existing `THOR RPMH V204: ... result=acked` line (twelve of them).

A step that fails stops the power-up and the power-down block follows at once:

- `failed_step` 1 reset assert, 2 camera AHB clock, 3 a rail (`rail=` its index), 4 MCLK (`mclk rc=101..103`,
  the last digit is the stage), 5 reset release, 6 CCI clocks (`FAIL cci clocks step=%d`). The failing line
  itself is printed just before: `FAIL rail ldoj5 2800mV addr=%x rpmh rc=%d`, or `settled=0` when the rail did not
  read back at its voltage within 100 ms.
- `left` is not zero only when a switch-off failed: 0x01 CCI bus, 0x02 reset pin, 0x04 MCLK, 0x08 AHB clock,
  0x10/0x20/0x40 rail 0/1/2. Such a run left something on and needs a reboot.

Three exits happen before anything is switched on and say so: `FAIL map (attended)`,
`SKIP cmd-db lacks ldoj3/ldoj5/ldoj2`, `SKIP no SPMI observer channel for PM8010J LDOs mask=%x`. The last one is
the conservative default (`CAM_REQUIRE_RAIL_READBACK 1` in `PongCamProbe.h`): without a physical read-back of every
rail the probe does not power the camera. The level-1 `spmi ... channel=` lines tell beforehand whether the
channels exist. With the constant at 0 the probe continues on the RPMh acknowledgements alone, as stock Android
does.

## 5. What to collect

- Before any power-off, the usual log snapshot (`collect-full.sh`), or the kernel console mirror
  (`PongConsole-kernel-v235.txt`, which is not truncated at boot: split the new run from the old tail with the
  intake `split.py`). Then `grep "THOR CAMERA V247"` and `grep "THOR RPMH V204"`.
- `THOR CAMERA V247: ID ANSWERED ... id=XXXXXX` gives the part; the sensor's full power-up table and register
  list then come from Android's `/vendor/lib64/camera/com.qti.sensormodule.thor_*`.
- Read-only samples from stock Android or TWRP before the attended run would remove the two remaining guesses:
  `ls /vendor/lib64/camera/ /vendor/etc/camera/` (the module names carry the part); the regulator summary rows
  `pm8010j_l2`, `pm8010j_l3`, `pm8010j_l5` (which LDO is which, and the unit in `regulator_summary`);
  `/sys/kernel/debug/pinctrl/*/pinmux-pins` for gpio104, 108, 208, 209, or the TLMM words at 0xf1068000,
  0xf106c000, 0xf10d0000, 0xf10d1000 (function bits [5:2]; 1 expected, and bit 11 set on 208/209).

## 6. Risks, each checked against the tree

- **CAM_CC shares MMCX with the running display.** CAM_CC (`DTS:815-827`, `vdd_mm-supply` = `mmcx.lvl`) sits in the
  domain the display keeps up. The probe never votes MMCX or MXA, never enables a camera PLL, and uses only the
  19.2 MHz XO. A read of CAM_CC assumes MMCX is powered: run it only in the unified image (`pong_display=1`), not in
  a display-less boot. Zizhan ran the same steps under its display.
- **Camera reset.** `gcc ... camera_bcr=` is GCC_CAMERA_BCR (0x36000, `GCC_CAMERA_BCR` in gcc-sm8450.c). With bit 0
  set the boot chain holds the whole camera block in reset and nothing in the probe will answer (every CCI attempt
  reports `rc=2`, a timeout). The probe reports it and never clears it.
- **titan_top GDSC parent.** The GDSCs' `parent-supply` is the MXC voter level (`DTS:1009`, `<0x2b>`); no thor
  driver votes `mxc.lvl` (Wi-Fi votes `mx.lvl`, `cx.lvl`, `xo.lvl`). If the GDSC never reports PWR_ON, the MCLK step
  times out at stage 1 and everything stays off: `mclk rc=2 stage=1`. The remedy for a later version is an
  `mxc.lvl` vote like the Wi-Fi parents loop (`PongWifiPlatform.cpp:183-189`); it is not in this probe because it
  touches a shared rail.
- **CCI1 and its pads.** No thor driver uses CCI1. The pads GPIO208/209 are an eGPIO pair shared with a remote
  processor (the suspend state hands them back with `qcom,remote`, `DTS:4698-4712`); the probe claims them
  (bit 12) for well under a second and writes the saved word back afterwards. Stock Android does the same each time
  the front camera opens. GPIO104 and GPIO108 are used only by `cam-sensor1`/`eeprom1` (`DTS:5491-5550`); the Wi-Fi
  driver uses 80, 81, 82, 94, 95, 96, 204 and the BT probe 76-79, 81, 82, 204.
- **PMIC resources.** `ldoj2`, `ldoj3`, `ldoj5` are referenced only by `cam-sensor1` and `eeprom1`
  (`DTS:24918-24951`). The Wi-Fi rails are `smpe2`, `smpb11`, `smpb10`, `ldoc10`, `ldob5`; the modem's `cx`, `mss`,
  `xo`, `mx`; the ADSP's `lcx`, `lmx`. None overlap.
- **Shared RPMh path.** `pong_rpmh_vote()` takes `powerLock` and one DRV2 active TCS. A vote that times out leaves
  `ThorActiveTcs::retained` set and every later RPMh vote of that boot is refused
  (`ThorActiveTcs.h:31, 71, 125`, existing behaviour of the helper). The probe adds twelve votes about 30 s after
  the root volume mounts, when the AOP has already answered the Wi-Fi votes. It is the main way this probe could
  affect the rest of the system.
- **SPMI observer.** The read path of the Wi-Fi driver's `railReady` (`PongWifiPlatform.cpp:114-124`): the
  observer at 0xc440000 is a read-only passthrough and does not touch the command channels the PMIC buttons driver
  or the PMIC arbiter use.
- **Rate changes.** Four root generators (MCLK4, slow AHB, CAMNOC AXI, CCI1) are set to XO and their five words
  are written back and latched again at the end (asserted in the host test). Branch and GDSC registers only get
  bit 0 changed and get it changed back. The camera AHB clock in GCC is disabled again only if the probe enabled it.
- **Power-off state.** The three LDOs are switched off with ENABLE = 0 even if one was already on before the run
  (the `spmi ... enable=` lines of the pre-flight show the earlier state).
- **Panic and hangs.** The probe cannot panic by itself: every wait is bounded, the only allocation (the CMD DB
  copy) is checked, and a failed map exits before any write. The two read gates (camera AHB clock, titan_top
  powered and its two AHB branches running) keep the probe from reading a powered-off block.
- **Boot path.** `start()` adds one `/chosen` lookup at the point where the Wi-Fi and BT gates do theirs; the
  kernel thread is created only when the property is set.

## 7. Which Thor helper does what

| Hardware action | Helper | Where |
|---|---|---|
| Rail votes | `pong_rpmh_vote()` (`ThorActiveTcs` on DRV2 under `powerLock`) | `PongPlatformResources.cpp:173-184, 1235-1249` |
| CMD DB names | `thorCmdDirectory()`, `thorCmdLe32()` | `ThorModemCmdDb.h:31-34, 70-93` |
| CMD DB copy | read-only map, `IOMallocData` copy, as the modem preflight | `PongPlatformResources.cpp:390-400` |
| Rail read-back | the Wi-Fi driver's `pmicRead` word format (observer at 0xc440000) | `PongWifiPlatform.cpp:114-124, 125-152` |
| GPIO | the Wi-Fi `pin()` word layout and order (output, mode, output) | `PongWifiPlatform.cpp:76-87` |
| Register window | `IODeviceMemory::withRange` + `kIOMapReadOnly` (zizhan `Win`, BT `Window`) | `ZizhanCamProbe.h:18-31`, `ThorBtProbe.h:26-47` |
| Gate | `/chosen` property, as `thor-wifi` and `thor-bt-probe` | `PongPlatformResources.cpp:640-650`, `ThorBtProbe.h:627-636` |
| Thread | `kernel_thread_start`, `rootvnode` wait, as the BT probe | `ThorBtProbe.h:577-585, 646-650` |
| Result record | `PongProbeRecord(8, "thor-camera", ...)` (a no-op shim on this board) | `../PongProbe.h` |
