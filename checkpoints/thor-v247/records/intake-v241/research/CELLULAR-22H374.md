# Cellular (CommCenter <-> integrated MPSS) on iPadOS 18.7.10 / 22H374 — host-only research

Scope: what Apple's 22H374 userland and kernelcache expect from a baseband, what umi's (HakiOS, iOS 16.5) bridge
publishes, and what that means for Thor (SM8475, integrated MPSS). Read-only: Apple binaries, Thor sources, umi
sources. Nothing was built, flashed or modified. **[INFERENCE]** marks conclusions not directly read.
Paths: `K=/tmp/claude-501/cont/research-a` (scratch), `R=reference-umi/hakios-xnu-kernel-umi-fixes/devices/xiaomi-mi-10`,
`T=v240-unified/device`, `P=v161-rmtfs-staging/pong-baseband` (an earlier Thor-side bridge, see 4.0).

## Summary
1. CommCenter's kernel ABI on 22H374 is **unchanged from 16.5** for everything umi implements: service name `baseband`
   (DT `config` >= 24 bytes, `class`), `AppleBaseband` user client (umi's 27 selectors all present with the same
   meaning; 8 more exist and are already covered by umi's "unsupported" default), `AppleBasebandPCIControl` variant
   (first of three), `IOServiceMatching("AppleBasebandPCIInterface")` + `ABPInterfaceProtocol` name, interface selectors
   0..12 with the same scalar/async counts, `AppleIPAppender` selector 16 = blockPDPTraffic.
2. The **channel id table changed** (18 entries, 1-based: pbl=1 .. boot_logger=0x1000) but ids are a libPCITransport
   internal key; only the *name* crosses to the kernel. umi's 18 published names are still exactly the 18 names used.
3. The iPad7c cellular identity is **ICE18** (`Firmware/ICE18-7.03.02.Release.bbfw`, ChipId 0x5e, Apple "Ice"
   baseband: ebl/psi_ram/SYS_SW.elf), **not Mav17**. 22H374 has no `Mav17` string at all.
4. The userland driver stack is selected by **MobileGestalt, not by the kernel**: `HasBaseband` must be true, then
   `HWModelStr` -> a static ProductType->radio table -> vendor. Vendor 1 loads `libBasebandManager.dylib` (QMI/Mav);
   vendor 2/3 loads `libBasebandManagerICE.dylib` (ARI/ICE). With Thor's iPad identity the vendor is ICE [INFERENCE:
   table entry for iPad14,x not decoded], so CommCenter would speak ARI, not the QMI umi's bridge answers.
5. The activation gate is still there ("Device is not activated! activationState=", "SMS %s - BB not activated",
   `/cc/props/baseband_activated`), the QMI BSP activation code is still there (`applyTlv<bsp::tlv::ActivationState>`
   reads **TLV 0x01** = umi's state 0; `ActivationMode` = umi's TLV 0x15 route, TLV id not re-verified). Some BSP TLV
   ids moved (ICCID 0x42->0x16, IMSI 0x43->0x13, ProvisioningStatus 0x03, ActivationInfo 0x04).
6. mobileactivationd still has `-[MobileActivationDaemon getActivationStateWithCompletionBlock:]` (umi's hook point)
   and a built-in "Hactivation ... short circuiting activation state to Activated".
7. Thor shows the **same** `MessagesBlastDoorService` corpse loop as umi; its sandbox stand-in returns 0 for
   `sandbox_check` (request 2) without writing the result, which is exactly umi's root cause.
8. Thor's v240 tree has **no bridge, no IPA/GSI data path, no `pdp_ip`**; the modem is only a 24 s one-shot trial
   (`ThorMssServe.inc`). A complete SM8475/IPA5.1 bridge exists in `v161-rmtfs-staging/pong-baseband` (5.7k lines,
   written against 22G100, with device evidence of registration, HTTP over cellular and IMS registration).
9. First three steps: persistent modem boot + host services; re-base the v161 bridge onto v240 (its ABI matches 22H374);
   decide the identity (QMI vendor via MobileGestalt) before touching activation.
10. Calls: not feasible without a Qualcomm-IMS-to-Apple-IMS bridge and a vocoder/I2S audio model the SoC does not
    have. SMS: feasible once the sandbox fix and the bridge exist; banner/sound were never solved on umi either.

## 1. What CommCenter needs from the kernel on 18.7.10 (verified)

CommCenter is on the OS volume: `/System/Library/Frameworks/CoreTelephony.framework/Support/CommCenter`
(29,957,952 B). It links `AppleBasebandManager`, `AppleBasebandServices`, `libCommCenterBase`, `libCommCenterCommandDrivers`,
`libCommCenterMCommandDrivers`, `libIPTelephony`, `libTelephonyCapabilities`, `libTelephonyBasebandDynamic` (otool -L).
The IOKit call sites live in `/usr/lib/libTelephonyBasebandDynamic.dylib` (control) and `/usr/lib/libIOABP.dylib` +
`/usr/lib/libPCITransport.dylib` (channels), all in the dyld cache.

| Item | 22H374 evidence | umi publishes | Same? |
|---|---|---|---|
| Launch gate | launchd job `com.apple.CommCenter` keeps `_LimitLoadToDeviceTree = baseband`; `com.apple.CommCenter-noncellular` has none | keeps it (`CC:53-58`) | yes |
| Control service | `IOServiceMatching("AppleBaseband")` then `IOServiceNameMatching("baseband")` (`__TelephonyBasebandControllerInitContext_block_invoke.6`, `...LoadConfig_block_invoke`); `IOServiceOpen(type 0)`; `bootstage` property read (`IsBootStageDownABP`) | IOService `AppleBaseband` named `baseband` with DT props (`BB:994-1047`) | yes |
| `config` property | `LoadConfigOnce`: CFData, `CFDataGetLength >= 0x18`, copies 24 bytes (`0x21160f5e0 cmp x0,#0x17`) | 40-byte `config` (`CC:456-461`) | yes |
| `class` property | read by lockdownd ("no class property of IODeviceTree:/baseband", `CC:456-461`) | `class = 3` | yes |
| `supports-cpms-via-spmi` | new in this build: 4-byte CFData read from `baseband` by `_supportsKernelCPMS` (libTelephonyCapabilities `0x250861b88`) | not published | **missing (optional)** |
| Control user client | `_TelephonyBasebandControllerCallMethod` -> `IOConnectCallScalarMethod(conn, sel, in, nin, out, nout)`. 33 distinct selectors at the 22H374 call sites: 0 ResetModem, 1 PowerOn(1 in), 2 Mux(1), 3 GetReset(1 out), 6 Powercycle, 8 VBUS(1), 0xb GetPMUExtOn, **0xf SelfIdentify (struct 0x40)**, 0x10 GetDiagReady, 0x11 GetFSEraseDone, 0x12 GetResetReq, 0x13 CoredumpGPIO(1), 0x14 APTimeSync, 0x16/0x17 Set/GetManagerState, 0x19 ChargerDesense(1), 0x1a/0x1b Get/SetBasebandState, 0x1c GetProperty(1 in,1 out), 0x1d SetProperty(2), 0x1e ThermalData(struct 0x30), 0x1f DebugCommand(struct 0x30), 0x20 ClearFreqList(1), 0x21/0x22 Add/RemoveFreqListExt, 0x23 ResetSPMI(1), 0x24/0x25 SPMI raw write/read(3), 0x29 SPMIGetEvents, 0x2a GetCoredumpReady, 0x2b CrashModemWithSPMI, 0x2c CrashModemWithSPMIWithReason(struct 0x40), 0x2d GNSS trap/wake/Reg0Write(2), 0x2e PowerDebugUnblock | `coachz_bb_control.h:23-91` lists 27; every number and meaning matches; the 8 extra (0x1e,0x1f,0x21,0x22,0x2b,0x2c,0x2d,0x2e) fall into umi's "unsupported" default | **yes** |
| Control variant | `__block_invoke.sVariantMap` (3 entries, tried in order with `IOServiceMatching`): `AppleBasebandPCIControl` (0x211611eae), `AppleConvergedIPCICEBBControl` (0x211611ec6), `AppleConvergedIPCLedaBBControl` (0x211611ee4) | `AppleBasebandPCIControl` | yes (first choice) |
| Channel discovery | `IOABPClass::start_nl`: `IOServiceMatching("AppleBasebandPCIInterface")`, property `ABPInterfaceProtocol` = CFString name, `kIOFirstMatchNotification`; `open_nl`: `IOServiceOpen(type 0)`, `IOServiceAddInterestNotification(IOGeneralInterest)` | 18 services with `ABPInterfaceProtocol` (`coachz_bb_channels.h:20-51`) | yes |
| Channel names | libPCITransport `__DATA_CONST,__const` 0x251a4c058, 18 x {name ptr, id}: pbl, sahara, diag, refs, qmux1, qmux2, rom, boot, ari, trace, at, dpl, mediactl, vcom, gnss, qdss, virtual_timesync, boot_logger | same 18 names | yes |
| Channel ids | 1-based: pbl=1 sahara=2 diag=3 refs=4 qmux1=5 qmux2=6 rom=7 boot=8 ari=9 trace=10 at=11 dpl=12 mediactl=13 vcom=14 gnss=15 qdss=17 virtual_timesync=20 boot_logger=0x1000 | 0-based (pbl=0, qdss=15, vts=17) | **changed, but internal to libPCITransport** (no evidence the id reaches the kernel; the name does) |
| Interface selectors | `IOABPClass` call sites (`iocalls.py` on the extracted dylib): open=0(1 in), close=1, write=2(2 in, async 2), read=3(2 in, async 2), sendImage=4(3 in, async 3), readRegister=5(1 in), abortChannel=6(1), startChannel=7(1), timeSync=8(3, async 3), mapMemory=9(3, async 3), unmapMemory=10(1, async 3), registerTimeEvent=11(1), deregisterTimeEvent=12(1) | `cbh_interface_methods[]`: identical | **yes, all 13** |
| IPAppender | `ApplePDPHelperInterface::init` matches `AppleIPAppender`; `blockPDPTraffic` = selector 0x10 with 2 scalars, `enable` = 3 (`0x21aed343c`, `0x21aed2d50`); entitlement `com.apple.driver.AppleIPAppender.user-access` (kext string) | `AppleIPAppender` selector 16 (`CC`, `BB:1443-1518`) | yes |
| Kernelcache kexts (ipad7c) | `com.apple.driver.AppleBasebandD101` (IOPlatformDevice `baseband,d101`, category `AppleBaseband`, UC `AppleBasebandUserClient`, reads `config`, `backpowering-pin-count`, `function-backpowering_pin`, `baseband-auto-on`; string "cellular baseband supported"), `AppleBasebandPCI` (IOPCIDevice `baseband-pcie`), `AppleBasebandPCIICEControl`, `AppleBasebandPCIICEPDP` (class `AppleBasebandPCIICEPDPManager`, constants keyed ice18/ice17/ibis18/ice16, `pdp_ip0..7`), `AppleIPAppender` (IOResources). **No `AppleBaseband` kext, no Mav/TDM kexts** in this kernelcache (228 kexts listed). | n/a (Apple side) | n/a |
| Thor's kernelcache | `v240-unified/kernel/kernel-ipados18-thor.macho` prelinks 26 kexts, none baseband | | Apple's baseband kexts are stripped, so the bridge must supply every service above |

Sandbox note (from umi, not re-verified on 22H374): user-client class names must match CommCenter's sandbox
(`com.apple.driver.AppleBasebandPCIControl.user-access` and `AppleBasebandUserClient` strings are in CoreFoundation on this build).

## 2. Baseband identity

- BuildManifest (22H374): `j171ap` (Wi-Fi) has **no** BasebandFirmware; `j172ap` (cellular) has
  `Firmware/ICE18-7.03.02.Release.bbfw`. The zip holds `ebl.bin, psi_ram.bin, restorepsi.bin, SYS_SW.elf, LTEFW.elf,
  TDSFW.elf, 2GFW/3GFW/CDMA2KFW/RFFW/RF... elf, bbcfg.bin`; `Info.plist` ChipId `0x5e`. This is Apple's
  in-house "Ice" family, not Qualcomm Mav and not Samsung.
- 22H374 dyld cache knows these firmware families (`libTelephonyCapabilities.dylib`): IBIS18, ICE18, ICE19, Mav13,
  Mav16, Mav20, Mav21, Mav22, Mav23, Mav24, Mav30 (`/usr/local/standalone/firmware/Baseband/<name>`). **`Mav17` does
  not occur anywhere in the cache** (ipsw dyld str). Command-driver families: Mav7/10/13/16/20/30 (M) and ICE (K).
- The kernelcache selects PDP constants by chip (`ice18` is the fallback in `AppleBasebandPCIICEPDP`; `ice19` in PCI/ICEControl).
- **Vendor selection is userland and static**: `abm::loadLibrary` (`AppleBasebandManager 0x1c0d9d5e0`) calls
  `capabilities::radio::vendor()` -> `TelephonyRadiosGetRadioVendor` -> `__TelephonyRadiosDetermineRadio`
  (libTelephonyUtilDynamic `0x190f1f2ac`, a 3.4k-line inlined map). The hardware model comes from
  `___TelephonyCapabilitiesGetHardwareModel_block_invoke`: `MGCopyAnswer("IsVirtualDevice")`; real device ->
  `MGCopyAnswer("HasBaseband")` (**false -> "Unable to determine whether device has baseband"**); true ->
  `MGCopyAnswer("HWModelStr")` -> `TelephonyUtilParseHardwareModel` (prefix, major, minor). Virtual devices use
  `DeviceName` ("iPad"/"Apple Watch"). Vendor 1 -> `/usr/lib/libBasebandManager.dylib` (QMI: "MavInterface",
  "MavRouter", QMux); vendor 2 or 3 -> `/usr/lib/libBasebandManagerICE.dylib` (table at 0x1ecbb7310).
  Command drivers follow: `libCommCenterMCommandDrivers` (QMI, `QMIMav13ActivationCommandDriver`, "BSP QMI client")
  vs `libCommCenterKCommandDrivers` (`IBIActivationCommandDriver`, ARI SDK).
- **Consequence for Thor [INFERENCE]**: with the iPad Pro M2 identity (iPad14,3/14,4, board J17x) the vendor
  resolves to ICE and CommCenter would use ARI/`libBasebandManagerICE`, which umi's QMUX bridge cannot answer. To use
  the QMI stack the port must present a **Mav ProductType in MobileGestalt** (`HWModelStr`/`ProductType` of an
  iPhone/iPad with a Qualcomm Mav20+ radio) **and `HasBaseband = true`**. umi's note that `HasBaseband` "can stay
  false" is wrong for 22H374: false means no radio at all. The `iPad14,x -> vendor` table entry itself was not decoded.
- Does it matter for the bridge? Yes, decisively: the bridge implements QMI over QMUX; it is useless if CommCenter
  loads the ICE stack. It does **not** matter which Mav number is used for the firmware path (umi's Mav17 decoy),
  because 22H374 CommCenter never names Mav17; the bbfw staging path must be a family 22H374 knows (e.g. `Mav20`).

## 3. Activation and carrier-bundle gates on 18.7.10

- Device activation: CommCenter strings "Device is not activated! activationState= %@, queryError= %@",
  "Device is not activated, waiting for ticket to be set by mobile activation", "Device is not activated. Waiting for
  activation ticket to make the baseband go online", "SMS %s - BB not activated", "Baseband is not activated, delaying
  the card provisioned state notification", `/cc/props/baseband_activated`, `/cc/props/is_activation_ticket_updated`,
  `/cc/props/is_postponement_ticket_available`. Gate present.
- Baseband activation (QMI stack, `libCommCenterMCommandDrivers`): "Activation register request failed",
  "Activation request succeeded. Waiting for indication", "Activation state is not ready (%s) waiting for another
  indication" (`0x219a12ce8`, after `applyTlv<bsp::tlv::ActivationState>`, state compared to 3/2), "Missing activation
  info TLV!", "Settings mode %d and activation %d in the TLV", "Asking baseband for postponement state".
  TLV type ids read from `applyTlv<bsp::tlv::X>` -> `findTlvValue(id)`: **ActivationState = 0x01**, ProvisioningStatus
  = 0x03, ActivationInfo = 0x04, RootManifestHash = 0x06, Imsi = 0x13, Iccid = 0x16, ActivationType = 0x18.
  `ActivationMode` (umi's TLV 0x15 = kDisabled) and the other TLVs use a second template form; **their ids were not
  re-read** — umi's ICCID/IMSI ids 0x42/0x43 **do not match** this build.
- So umi's no-patch route (TLV 0x01 = 0 activated, TLV 0x15 = 1 disabled) is still structurally present: the state
  is read from 0x01 as on 16.5, and `kDisabledByActivation`/`fActivationEnabled` strings still exist
  (`libCommCenterBase`, `libCommCenterCommandDrivers`). Whether TLV 0x15 still carries the mode is INFERENCE.
- mobileactivationd (22H374): `-[MobileActivationDaemon getActivationStateWithCompletionBlock:]` (umi's hook target),
  `copy_postponement_info`, `deliver_baseband_ticket`, `updateBasebandTicket:baaCertData:...`, states `Activated`,
  `FactoryActivated`, `Unactivated`; and "Hactivation is enabled, short circuiting activation state to Activated" (a
  built-in switch; the enabling condition was not traced).
- Carrier bundle: CommCenter still has `ActivationCarrierBundleInterface`, `kCTPostponementInfoICCID/IMSI`;
  the 0xa000/0xa001 file push is handled by `QMIBasebandSettingsDriver` in the M drivers ("QMI BSP Send file for
  segment number: %d out of %d"). Unchanged in kind.

## 4. Transport-independent parts of umi's bridge vs Thor's tree

4.0. Two Thor-side bridges exist outside v240:
- `upstream-refresh-20261123/components/QcomBaseband` (Trogdor-ipadOS, SC7180, iOS 17): same headers as umi's
  (`coachz_bb_channels.h` byte-identical by comment) plus IPA v4.2/GSI, eSIM.
- **`v161-rmtfs-staging/pong-baseband`** (commit 817a285, 2026-11-11): "SM8475 / X65 board backend for native
  iPhone17,2 iOS18.6.2 (22G100)": `PongBasebandService.cpp` (1139 l), `PongModemIpa.h`/`PongModemIpaDma.h`
  (IPA5.1/GSI3.0, NothingOSS tables), `PongModemQrtr.cpp`, `PongModemRmtfs.h` (attributed to tqftpserv), `PongModemWan.h`,
  `PongModemSim.h`, `PongModemIms.h`, `PongImsDcm.h`, `PongModemPdc.h`, `PongNasMultiplexer.h`, `PongPdpInterfaces.h`.
  `PongBaseband22G100.h` documents the native selectors 0..12 and `CBH_ASYNC_REFERENCE_WORDS` = 3, i.e. the same
  ABI verified in section 1. README: device evidence record287 (cellular-only HTTP 200), record297 (IMS registration,
  voice/SMS service availability); calls "still end immediately". **None of this is in v240** (`services112` doc:
  "Pong 的蜂窝基带传输尚未接入"). [INFERENCE: it was written for the iPad identity and so against the QMI/M stack.]

| umi part | Thor v240 has | Missing |
|---|---|---|
| Persistent modem boot (PIL, SMP2P ready) | `ThorPas4Kernel.inc`: PAS image 4 auth+reset, READY wait, 24 s trial, then SMP2P stop + PAS shutdown + AOSS "load off" (`pasRunProbe`, `pasWorker:444-474`) | a *keep-up* path: skip the stop when `thor-rmtfs-serve`; today `observeReady()` runs `ThorServe::run` and "the caller continues to the unchanged stop path" (`ThorPas4Kernel.inc:247-250`) |
| GLINK/IPCRTR + QRTR transport | `QcomGlink.c`, `ThorMssServe.inc` (`linkStart`, `sendNow`, HELLO, NEW_SERVER logging) | packet routing to a bridge (`dst_port` demux), a poll loop owned by the bridge, bounded queues (kMaxReply 384 today) |
| Host services: SERVREG locator, rmtfs | `pdmHandle` (GET_DOMAIN_LIST 0x21 only), `rfsHandle` (OPEN/CLOSE/RW_IOVEC/ALLOC_BUFF/GET_DEV_ERROR) with RAM shadow (`ThorMssServe.inc:152-343`) | persistent shadow with real EFS semantics (writes must survive for carrier/PDC settings; today they vanish at teardown), rmtfs sync/GET_DEV_ERROR realism |
| TFTP (`/readonly/firmware/image/*`), QMI/host services the modem opens | none in v240 | TFTP server over QRTR 4096 (umi `mhi.inc:845-942`); decide whether SM8475 MPSS needs it (the stock `modem_a` has `image/*`) |
| CTL local answers (GetClientId 0x22, Release 0x23, Sync 0x27, GetVersionInfo 0x21) | none | port `coachz_bb_qmux.h:134-169` |
| Client pooling (96 slots, port 0x4100+slot, never DEL_CLIENT) | none | port as is |
| Pending-request table (ATCS_TIMEOUT loop fix) | none | port as is |
| DMS mode 4/5 -> 1 rewrite; WDS observation; QOS/WDS endpoint insertion | none | port (endpoint values {3,4} are X55-specific; SM8475 IPA uses DPM/endpoints from `PongModemIpa.h`) |
| Activation indication (TLV 0x01/0x15/...) and carrier file push (0xa000/0xa001) | `PongModemPdc.h` in v161 only | re-derive TLV ids for 22H374 (section 3) |
| `pdp_ip0..3` ifnets (IFNET_FAMILY_CELLULAR, IFT_CELLULAR, plumbers) | none in v240 (`PongPdpInterfaces.h` in v161) | port; xnu-11417 KPIs still declared (prior notes) |
| IP_HW0 / QMAP data path | none in v240. v161 has IPA5.1/GSI3.0 (`PongModemIpaDma.h`, 377 l). `configs/pong-memory-sm8475.json` has only the `ipa_fw_region@8b900000`/`ipa_gsi_region@8b910000` carveouts. | the whole IPA/GSI driver (or MHI if the stock MPSS exposes an IP_HW channel — unknown, see 6) |
| Bridge itself (`AppleBaseband*`, `AppleIPAppender` services) | none in v240. v161 `PongBasebandService.cpp`. | re-base onto v240's PongPower/PongPlatformResources |
| Activation stand-in | none (mobileactivationd is unmodified) | umi `inject.c:1244-1299` equivalent |
| Sandbox `sandbox_check` truthfulness | **absent**: `kern_minimac.c` stand-in returns 0 for request 2 and writes nothing (`0001-pong-darwin24.patch:2918-2931`) | umi's table fix (`SANDBOX-umi.md:45-80`) |

## 5. Voice calls and SMS

Calls on 22H374: CommCenter's `AudioToolboxCallAudioService` + `CallAudioDriverDelegate` drive an **I2S/PCM vocoder
model between AP and baseband**: strings `kAudioI2SInterface`, `kAudioSetI2SFormat`, `kAudioEnablePCMChannel`,
`kAudioQueryAudioVocoderInfo`, "Vocoder info: type: %d, sample rate: %d I2S Clock Rate", "IMS Incoming Indication: AUDIO
STREAM TOKENS" (EVS/AMR-WB negotiated by IMS). On Apple's ICE/Mav the modem runs the vocoder and the AP moves PCM over
I2S. On SM8475 the modem's voice codec talks to the **ADSP** (ALAC/AVS over GLINK/APR), and the AP has no I2S link to
the modem. umi's result stands: IMS bearer up, no SIP registration, no RTP endpoint, `NO_NETWORK_FOUND` for CS.

Feasibility, frank: **not reachable in the near term.** Prerequisites in order:
1. Data path and registration stable (section 4).
2. Choose who owns IMS: Apple's SIP stack (needs a media path = RTP over QMAP + vocoder in AP software, plus the
   private WDS 0x5561 "DAD complete" indication umi synthesises) or the modem's IMS (needs an Apple-IMS <-> QMI-IMSA/IMSS
   translation for SIP MESSAGE, registration state and call control — does not exist in any tree read).
3. Audio: a call-audio backend for the AP (v161's `PongImsDcm.h`/`PongModemIms.h` only *observe* IMS state; "calls
   still end immediately"). Requires the ADSP voice path (not in v240) and a CoreAudio call unit.
4. Then CommCenter call control (`callservicesd`, `PhoneIntentHandler`, in-call UI). MobilePhone.app is arm64e-only
   and not installed (`services112`).

SMS: WMS (0x05) requests/indications forwarded byte-for-byte was enough on umi for MT storage. On Thor:
- Same `MessagesBlastDoorService` loop confirmed: `console-v238-kernel.txt` shows "Corpse allowed 1 of 5 ... Corpse
  failure, too many 6" every ~10 s (770-1109 s) and "Security policy loaded: Seatbelt sandbox policy (stand-in)".
  Root cause as on umi: BlastDoor.swift asserts `sandbox_check(getpid(), NULL, 0) == 1`; Thor's stand-in leaves the
  result block zero. **Fix = umi's kernel-only change** (record processes that carry a `seatbelt-profiles`
  entitlement at exec, answer request 2 with 1 for them, request 0x41 accordingly). Independent of the baseband; can
  be done first.
- MO SMS was never verified on umi; banner/sound never worked (Notification store). Expect the same open items.
- IMS SMS (over the IMS bearer): not found in any tree.

## 6. What cannot be determined statically (needs the phone)
- Which host services SM8475 MPSS (the phone's own `modem_a`) requires before it stays ONLINE: does it need TFTP,
  `pd-map`/locator entries beyond Thor's 7-domain table, an EFS2 modem, `rmtfs` sync before LPM exit? (Thor logs show
  45 services with rmtfs vs 2 without — the rest is unknown.)
- Whether the stock SM8475 MPSS exposes an **IP_HW0/QMAP channel** (MHI-style) or only **IPA** (the Linux
  `ipa_v3` path). v161 assumes IPA5.1/GSI3.0 with NothingOSS tables; its DMA completion and endpoint ownership were
  "mandatory" and only partially proven (record287 had HTTP 200 — but on which MPSS build and with which carrier?).
- The `iPad14,x -> TelephonyRadio/vendor` table entry (which vendor CommCenter computes for Thor's identity), and
  what the M-stack does when `HWModelStr` names an iPhone with a Mav radio but `/product` says iPad.
- Whether TLV 0x15 (ActivationMode) is still the policy switch and whether `kDisabled` alone lets the Internet
  context start on 22H374 without a ticket (umi's "asynchronous boot sequence" caveat).
- Which BSP message ids the 22H374 M-stack sends (0xb00c register, 0xb001 chip info, 0xa000/0xa001, 0xe00d) — the
  strings exist, the ids are in code and were not read.
- IPA/GSI register values for SM8475 (v161's `src`/`dst` resource tables and clock votes): real DMA needs the device.
- Whether `MessagesBlastDoorService`'s only failure is the sandbox check (an .ips from the phone would show the
  SIGTRAP at `BlastDoor/Sandbox.swift`).
- Which `HWModelStr`/`ProductType` MobileGestalt actually returns on Thor today (the /var cache may say iPad).
- Carrier bundle behaviour for a Qualcomm modem on an iPad identity (APN/IMS settings come from the bundle keyed by
  ICCID/IMSI; whether `CarrierSettings` on 22H374 has bundles for the Thor SIM's MCC/MNC).

## Porting checklist (dependency order)

| # | Step | Size | Risk | Notes |
|---|---|---|---|---|
| 1 | Sandbox stand-in: `sandbox_check` truth table (umi `SANDBOX-umi.md`) | S | low | unblocks MessagesBlastDoorService and other BlastDoor services; test by the corpse loop disappearing |
| 2 | Persistent modem: keep MPSS up after READY (`thor-rmtfs-serve` path must skip SMP2P stop / PAS shutdown / AOSS off), run GLINK poll in a long-lived thread, EFS shadow persistent | M | med | verify by `DMS GetOperatingMode` = ONLINE and 45+ QRTR servers, no fatal after 10 min |
| 3 | Host services completeness: locator table vs modem requests, TFTP 4096, rmtfs semantics (GET_DEV_ERROR, sync) | M | med | driven by the modem's requests seen in step 2's log |
| 4 | Decide identity: keep iPad (-> ICE stack, no bridge possible) **or** present a Mav ProductType + `HasBaseband=true` via MobileGestalt | S | **high** | gate for everything below; needs the iPad14,x table entry decoded or tried on the device |
| 5 | Re-base `v161-rmtfs-staging/pong-baseband` onto v240 (`PongBasebandService.cpp`, QMUX/QRTR, CTL, pooling, pending table, `pdp_ip0..3`) | L | med | its 22G100 ABI = 22H374 ABI (section 1); selectors 0..12, control 0..45, IPAppender 16 |
| 6 | Publish `/baseband` node (`config` 40 B, `class=3`, `supports-cpms-via-spmi` 4 B optional) and allow the `com.apple.CommCenter` job | S | low | without it launchd never starts CommCenter |
| 7 | Activation: BSP 0xb00c indication with 22H374 TLV ids (0x01 state, 0x03/0x04, 0x16 ICCID, 0x13 IMSI, ActivationMode), mobileactivationd `getActivationState` hook | M | med-high | TLV 0x15 role unverified |
| 8 | Carrier file push 0xa000/0xa001 ack (TLV 0x40 next segment) + `SetConfig` 0xe00d accept | S | low | else reset every ~12 s |
| 9 | Data path: IPA5.1/GSI3.0 (`PongModemIpa*.h`) or MHI IP_HW0; WDA raw-IP/QMAP; WDS bind/start/stop observation | L | high | SM8475 IPA facts only on the device |
| 10 | SMS: WMS forwarding (already inside the QMI bridge) + step 1 | S | low | banner/sound open |
| 11 | Calls | XL | very high | see section 5; not planned |

## Open questions that need device logs
- `THOR SERVE`: after READY, the full list of QRTR NEW_SERVER services and every `locator request name=...` /
  `unknown msg=` line once the modem is left up for minutes.
- Whether the modem issues `rmtfs` writes that matter (EFS2 sync) and `GET_DEV_ERROR`.
- With step 6 only: does CommCenter start, which user client opens first (`pbl`/`sahara` via BBUpdater?), and the
  os_log line "MavInterface is %p" (M stack) vs an ICE one — this tells the vendor actually chosen.
- `mg_test HasBaseband`, `HWModelStr`, `ProductType` from the running device.
- A `MessagesBlastDoorService` .ips from Thor's crash reports.
- Any `CommCenter` crash on first contact with the bridge (call shapes differ per section 1?).

## Tools / commands used
- `python3 -I v240-unified/device/scripts/decompress-ipados18-kernelcache.py --im4p ipsw-components/components/kernelcache.release.ipad7c --output K/kernelcache.release.ipad7c.macho`; `lipo -thin arm64` -> `K/kc.macho`.
- `K/kexts.py` (written for this report: tolerant parser for Apple's interned-attribute prelink plist): `list`, `classes <bundle>`, `dump <bundle>`, `save kc-prelink.json`. `session-tools/kc_prelink.py` fails on this plist (`<integer ID=.. size=64>0x..`).
- `ipsw dyld str <DSC> "<text>"`; `ipsw dyld extract --force --stubs --cache /tmp/claude-501/cont/dsc.a2s -o K/dylibs <DSC> <path>`; `ipsw dyld disass --cache ... --image <path> <DSC> > K/dis-<name>.txt`; `ipsw dyld disass --cache ... --symbol <mangled> <DSC>`; `ipsw dyld dump <DSC> <addr> --bytes`.
- `K/iocalls.py <dylib> <name-substr>` (written): resolves `__stubs` via the indirect symbol table and prints selector/count immediates before each `IOConnectCall*`. `K/callers.py <disasm> <regex>` (written): per-function call sites with immediates.
- `/tmp/claude-501/cont/calls.py`, `xref.py` (macho.py) for on-disk binaries; `xcrun nm -n ... | c++filt`; `strings`.
- `osimg.data()/node()/listing()`, `plistlib` on `System/Library/xpc/launchd.plist`.
- Extracted (delete when done, ~50 MB each): `K/dylibs/{libIOABP,libPCITransport,libTelephonyBasebandDynamic,libCommCenterBase,libCommCenterCommandDrivers,libCommCenterMCommandDrivers,libQMIParserDynamic,libBasebandManager,AppleBasebandManager,ApplePDPHelper,libTelephonyCapabilities}.dylib`; `bin/CommCenter`, `bin/CommCenterRootHelper`, `bin/CommCenterMobileHelper`.
