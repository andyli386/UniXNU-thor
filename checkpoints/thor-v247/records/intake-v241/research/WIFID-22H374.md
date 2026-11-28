# wifid (22H374) — why it runs but never answers; evidence from the 22H374 binaries

Scope: host-only research. Sources: `/usr/sbin/wifid` 22H374 (on-disk Mach-O, stripped; addresses are
unslid `0x1000xxxxx`), SystemConfiguration and IO80211 from the 22H374 dyld cache (unslid), configd 22H374,
the driver source `PongWifiNet.cpp`, upstream notes 93–111 and `wifi-bootstrap.c`. Nothing below was run on
the phone. "INFERENCE" marks what I did not read directly; "not determined" marks what I could not settle.

## Summary

1. wifid has ONE serial queue that matters: `com.apple.wifid.managerQueue` (made in `main` 0x10008eb84). Every
   CoreWiFi XPC request, the legacy MobileWiFi MIG server, the IOKit device notifications, the SCDynamicStore
   callbacks and the SCPreferences callbacks are all serviced on that queue (evidence in A1).
2. The XPC listeners are resumed very early (`-[WiFiXPCManager activate]` at 0x10006c3ec, inside
   `WiFiManagerScheduleWithQueue` 0x10006c330), BEFORE any device exists and before the legacy
   `bootstrap_check_in` (0x10009f8a8, called from 0x10006c418). Clients therefore connect and send; the
   requests are queued on the manager queue and only answered once that queue gets to run.
3. The device-arrival path (`__WiFiManagerDeviceAddedCallback` 0x10014a724) ends by running
   `WiFiDeviceManagerOpen` (0x10016916c) on the manager queue, and that calls `SCNetworkInterfaceCopyAll`
   (0x1001691e8; again at 0x10014b0d8). In 22H374 `SCNetworkInterfaceCopyAll` first waits for
   `Plugin:InterfaceNamer` to hold `*QUIET*`, `*COMPLETE*` or `*TIMEOUT*`, using `SCDynamicStoreNotifyWait`,
   whose `waitForMachMessage` (0x18d52bf4c) has NO timeout. Until that returns, the manager queue is blocked
   and nothing answers — XPC or MIG.
4. The arrival callback requires the BSD name to be present in `Interfaces` of the GLOBAL dynamic-store key
   `State:/Network/Interface` (in 22H374 `SCDynamicStoreKeyCreateNetworkInterface` builds
   `<domain>/Network/Interface` and ignores its third argument). When absent it logs
   "%@ does not exist in dynamic store" (0x10014b174) and RETURNS; it does not retry by itself.
   Retries come from the two triggers registered in `WiFiManagerScheduleWithQueue`: the IOKit
   "IOServiceMatched" notification for `{IOPropertyMatch:{IO80211InterfaceRole:"Infrastructure"}}` and the
   dynamic-store notification for `State:/Network/Interface` (initial sync at 0x10006c844).
5. Consequence for v239: publishing the marker before en0 is listed is harmless for wifid (it returns and is
   re-triggered when the key changes), but the marker must exist (or change later) BEFORE the first
   `SCNetworkInterfaceCopyAll`, and configd must be able to answer RPCs at that moment. The most likely v239
   failure is a manager queue stuck in that wait (or in an SC RPC to a configd that is itself blocked), not a
   missing device list. See A4 for the alternatives and the log lines that decide between them.
6. Starting wifid only after configd has published the marker, listed en0 and created the service is
   consistent with the binary and is the safest order. Two caveats: (a) with the stock plist the XPC
   MachServices are visible to lookups as soon as the job is loaded, so synchronous CoreWiFi callers may stall
   while wifid is held (use `HideUntilCheckIn` on them as upstream did for the legacy port); (b) the legacy
   `com.apple.wifi.manager` check-in fails unless the job declares it, so `com.apple.wifi.manager.available`
   is never posted (0x10014afac–0x10014afe4).

## A1. Server check-in — what must succeed, on which queue

`main` (0x10008eae4): `_set_user_dir_suffix("com.apple.wifi.manager")`, creates `com.apple.wifid.managerQueue`
(0x10008eb80, stored 0x1002756e8), a dispatch group, watchdog endpoint `com.apple.wifid.watchdog`,
`com.apple.wifid.serialQueue`, `com.apple.wifi.network-sync`, then `dispatch_async(managerQueue, block
0x10008ee68)` (0x10008ed48). The block reaches `-[WiFiManagerLifeCycle startOnQueue:group:]` (0x10008e718):

- `WiFiManagerCreate` wrapper 0x100067274: `MGCopyAnswer("BuildVersion")`, `SCPreferencesCreate` for
  `com.apple.wifi.plist`, `com.apple.wifi-networks.plist` (with `ProtectionClass`="C"),
  `com.apple.wifi-private-mac-networks.plist`, `com.apple.radios.plist` (0x1000676e0–0x1000677e8; preference
  domain "com.apple.wifi.manager"). If it returns NULL: "%s: Error: WiFiManagerCreate failed" and nothing else
  runs (0x10008e7c8).
- `start` 0x100155628: `handleSignal:onQueue:` for signals 2, 15, 3; `WiFiManagerOpen` 0x10006c228 (sets
  manager+0x10, applies a callback over the device set manager+0x70, empty at boot); `WiFiManagerEnable`
  0x10007144c → `sub_1000234c8(manager,1)` (applies `__WiFiManagerDeviceManagerApplier` 0x100006b90 over the
  same empty set); `WiFiManagerScheduleWithQueue` 0x10006c330; `notify_post("com.apple.wifivelocity.trigger")`.

`WiFiManagerScheduleWithQueue(manager, queue, group)` (0x10006c330), all synchronous on the manager queue:
1. stores queue at manager+0x20, group at +0x28; `sub_100051758(queue)`.
2. `[[WiFiXPCManager sharedXPCManager] setWiFiManager:]`, `activate` (0x10006c3ec) → `-[WiFiXPCManager activate]`
   0x1000f1b2c: for each supported request type `-[CWFXPCManager setTargetQueue:requestType:interfaceName:]`
   (0x1000f1bcc), then `-[CWFXPCManager resume]` (0x1000f1bfc). The `CWFXPCManager` was created in
   `-[WiFiXPCManager init]` 0x1000f1900 with service types {1,2,3,4,6,7} (array 0x10025c8a0) = the five
   `com.apple.private.corewifi.*-xpc` endpoints plus `com.apple.wifi-xpc` (INFERENCE: mapping of numbers to
   names lives in CoreWiFi, not read).
3. `[manager+0x468 activate]` (0x10006c3f4; object not identified), known-networks cache 0x10006a208 ("device
   not yet unlocked. Not fetching network list."), 0x10006a5f8.
4. **legacy MIG check-in** `sub_10009f860(manager+0x58, queue)` at 0x10006c418 → `bootstrap_check_in(
   bootstrap_port, "com.apple.wifi.manager", &port)` at 0x10009f8a8; result byte stored at manager+0x60
   (0x10006c41c). Served on the same manager queue.
5. `MKBUserUnlockedSinceBoot`, `MKBGetDeviceLockState` (0x10006c484, 0x10006c5d0), notify registrations.
6. `SCDynamicStoreCreate("WiFiManager")` (0x10006c800, stored manager+0x50), `SCDynamicStoreSetDispatchQueue(
   store, managerQueue)` (0x10006c81c), `__WiFiManagerRegisterDynamicStore` 0x10014a608 (notification key
   `SCDynamicStoreKeyCreateNetworkInterface(NULL, State, …)` 0x10014a634 → "State:/Network/Interface"),
   **initial sync** `sub_10014a41c(store, NULL, manager)` (0x10006c844, logs "Callback reason=Initialization").
7. `IONotificationPortCreate` (manager+0x3c8) and `IOServiceAddMatchingNotification(port, "IOServiceMatched",
   {IOPropertyMatch:{IO80211InterfaceRole:"Infrastructure"}} (literal 0x10025c610), handler 0x10006ea58,
   manager, &manager+0x3d0)` (0x10006c894) followed by the **immediate** `sub_10006ea58(manager, iterator)`
   (0x10006c8a8). Later `IONotificationPortSetDispatchQueue(port, managerQueue)` (0x10006cc44). Then
   notifications for "AppleWLANDriver" matched/terminated, "AppleSunriseWLAN", "IOPMPowerSource",
   "IOUSBDevice", "IOPort", `IORegisterForSystemPower` (0x10006c8b4–0x10006cbec).
8. `SCPreferencesSetDispatchQueue` ×4 (0x10006ccc4–0x10006ccf4), `BTSessionAttachWithQueue(…,
   "com.apple.wifi.manager", …)` (0x10006cd28), CoreTelephony handlers, analytics, NDD manager.

Which queue serves the XPC endpoints: `-[WiFiXPCManager XPCManager:XPCConnection:receivedXPCRequest:]`
(0x10001976c) computes `q = [self __queueForRequest:]` and `dispatch_async(q, block)` (0x100019804).
`-[WiFiXPCManager __queueForRequest:]` (0x10001bb20): only request types 0x39/0x3a with
`info[CWFXPCForceNoCacheKey]` go to `self->_20` (`com.apple.wifid.current-network`, created 0x1000f1974);
everything else goes to `sub_10001a378(manager)` = `manager->_20` = the manager queue (0x10001bb68 →
0x10001a378: `ldr x0,[x0,#0x20]`). So every client request waits for the manager queue.

Synchronous waits on that queue that can block everything: the `SCNetworkInterfaceCopyAll` wait described in
A2/A4; every SC RPC (`SCDynamicStoreCopyValue`, `SCDynamicStoreSetNotificationKeys`, the `AddNotificationPort`
inside `SCDynamicStoreNotifyWait`), which hang if configd does not answer; `IOServiceGetMatchingService`
retry loop 32×`usleep(10000)` (0x10014a894–0x10014a8bc, bounded 0.32 s); `Apple80211GetPowerState` retries
with `usleep(60000)` on 0x3d/0x10 (0x100056214–0x10005622c, loop bound not read); keybag calls at 6.
`IOKitWaitQuiet` is NOT called by wifid (no import). `dispatch_sync` is imported but its callers are
non-startup code (IDSSyncEngine, hotspot, locale) per the xref.

## A2. Device discovery

Trigger A, IOKit: handler 0x10006ea58 iterates `IOIteratorNext`, reads property `IOInterfaceName`
(0x10006ea94; NULL → 0x10014ff00, not read), and calls 0x10014fed0(name, manager): `ioctl(SIOCGIFMEDIA
0xc0206938)` on the BSD name, requires `(ifm_current & 0xe0) == 0x80` i.e. `IFM_IEEE80211` (0x100082b04–
0x100082b74; our driver answers `IFM_IEEE80211|IFM_AUTO`), then `__WiFiManagerDeviceAddedCallback`
0x10014a724(manager, name). Requires the interface service to carry `IOInterfaceName` (driver sets "en0") and
`IO80211InterfaceRole` = "Infrastructure" (driver sets it).

Trigger B, dynamic store: callback 0x10014a41c does `SCDynamicStoreCopyValue(store, "State:/Network/Interface")`
(0x10014a4a8), takes `Interfaces` (kSCDynamicStorePropNetInterfaces, 0x10014a4d0), keeps names passing the same
SIOCGIFMEDIA test (0x10014a52c), logs "Discovered %ld airport Interfaces", and for each name not already a
device (0x10014a6a0) logs "Now attempting to add AirPort Interface with BSDName %@" and calls 0x10014a724.

`__WiFiManagerDeviceAddedCallback(name, manager)` (0x10014a724):
- `manager+0x50` store NULL → "%@ does not exist in dynamic store" (0x10014b160–0x10014b178) and return.
- `key = SCDynamicStoreKeyCreateNetworkInterface(kCFAllocatorDefault, kSCDynamicStoreDomainState, …)`
  (0x10014a7a8) → `SCDynamicStoreCopyValue(managerStore, key)` (via 0x100089bb0) → must be a dictionary →
  `Interfaces` must be an array → `CFArrayContainsValue(Interfaces, name)` (0x10014a818). Otherwise w19=0 →
  0x10014b160: log "%@ does not exist in dynamic store" and RETURN. No retry is registered here and it does
  not block; the retry is whatever re-fires trigger A or B.
- Builds `{IOPropertyMatch:{IOInterfaceName:name}}` and `IOServiceGetMatchingService` in a loop
  32×(usleep 10 ms) (0x10014a894–0x10014a8bc); failure → "No Matching Service Found for interface %@".
- Reads `IO80211InterfaceRole` of that service; not "Infrastructure" → "%@ is a virtual IO80211 interface"
  and return (0x10014a8f8–0x10014aa1c).
- `[[WiFiUserInteractionMonitor sharedInstance] startMonitoringInterface:name]` (0x10014a930);
  `sub_100053a54(allocator, service, managerQueue)` creates the device object (0x10014a940).
- If `manager+0x440` (SCPreferences) and `sub_100133478`: `__WiFiManagerUpdateDeviceSystemConfiguration`
  (0x10014a964): `SCNetworkInterfaceCreateWithIONetworkInterfaceObject(service)` (0x10014a974),
  `SCNetworkSetCopyAll(prefs)` (0x10014a984), and for every set not containing the interface
  `SCNetworkSetEstablishDefaultInterfaceConfiguration(set, interface)` (0x10014a9d4). All on the manager
  queue; these are in-memory edits through SCPreferences with a dispatch queue (no `SCPreferencesLock`
  import exists in wifid).
- `CFSetAddValue(manager+0x68, device)`, `sub_1000a1b6c` → `__WiFiManagerDeviceManagerInit` with ~60
  callback registrations (0x10014aa98–0x10014af50), "Device manager created. %s automatic association state".
- `CFSetApplyFunction(manager+0x70, __WiFiManagerDeviceManagerApplier 0x100006b90)` (0x10014b0c0) →
  `WiFiDeviceManagerOpen` 0x10016916c per device → **`SCNetworkInterfaceCopyAll`** (0x1001691e8; the
  matching SCNetworkInterface is stored at devMgr+0x1590, NULL is tolerated) → then `sub_100071610` =
  `SCNetworkInterfaceCopyAll` + `SCNetworkInterfaceGetBSDName/GetInterfaceType/GetLocalizedDisplayName`
  (0x10014b0d8). `notify_post("com.apple.wifi.manager.available")` only if `manager+0x60 == 1` i.e. the legacy
  check-in succeeded (0x10014afac–0x10014afe4). Final log "Detected %ld wifi device(s)" (0x10014b10c).

Why the global key: SystemConfiguration 22H374 `_SCDynamicStoreKeyCreateNetworkInterface` (0x18d525978,
symbol confirmed with `ipsw dyld a2s`) formats `"%@/%@/%@"` with (domain, kSCCompNetwork 0x1e78f3140,
kSCCompInterface 0x1e78f3160) and never reads x2; none of its callers in wifid or configd set x2 (e.g.
configd `copy_interfaces` 0x100013a90, `update_interface` 0x100013bd4). So `State:/Network/Interface` is the
key, as upstream found. (`macOS 26.6.2` on the host shows the same shape: a global `State:/Network/Interface`
with `Interfaces`, no per-interface base keys.)

First Apple80211 requests once the device exists (IO80211 22H374, `Apple80211BindToInterfaceWithService`
0x1e3c7eb4c): `IOServiceOpen(service, task, type 0)` (0x1e3c7ec4c; failure → IOCTL fallback), selector 0
with `SIOCGIFMEDIA`-style envelope `SIOCGA80211`, type **43** (DriverVersion) with a 256-byte output
(0x1e3c7ecdc; failure → bind fails), selector **1** (0x1e3c7ed78) and selector **7** (0x1e3c7eda0; 1 byte
each), `Apple80211RawGet` type **12** (CardCaps) with **19** bytes (0x1e3c7edf8; failure → bind fails). The
service is found by `__Apple80211FindService` = `IOServiceGetMatchingService({IOPropertyMatch:{IOInterfaceName:
ifname}})` (0x1e3c7ea64); property `IO80211VirtualInterfaceRole` is optional ("AirLink" → compatibility mode).
Then `WiFiDeviceOpen` 0x100056140: `Apple80211GetPowerState` (type **19**), `Apple80211CopyValue` type
**106** (RSSI_MIN/MAX/THRESHOLD, 0x100056288), type **12** again (0x100056390), then
`Apple80211EventMonitoringInit2` (0x1000568d8). Must-succeed set for the device to be created and the
manager to report it: 43 (256 B), 12 (19 B), 19; everything later (scans, associates) happens per client
request. The v238 console shows configd already issued 43/12/1 at 737.08 s, so the tunnel works for configd.

## A3. Power state — partly determined

- Preferences are SCPreferences files in domain "com.apple.wifi.manager": `com.apple.wifi.plist` (holds
  `AllowEnable`, handled at 0x100070a9c–0x100070fd4 in `WiFiManagerEnable`), `com.apple.radios.plist`
  (`AirplaneMode`/`KeepWiFiPoweredAirplaneMode`, 0x1000695d8–0x100069790, `__WiFiManagerSetAirplaneMode…`
  0x100087b3c), `com.apple.wifi-networks.plist`, `com.apple.wifi-private-mac-networks.plist`. wifid contains
  no "/private/var/wireless" or `_wireless` string (not used by 22H374 wifid); the files live where configd
  keeps named SCPreferences (`/Library/Preferences/SystemConfiguration/`, INFERENCE from open-source configd).
- Power decision is in `__WiFiManagerDeviceManagerApplier` 0x100006b90: `MGGetBoolAnswer("3kmXfug8VcxLI5yEmsqQKw")`
  (0x100006c28), `MKBUserUnlockedSinceBoot` ("device hasn't been unlocked since boot. Overriding power state
  to OFF." 0x100006c6c), then "Device power = %s and prefences power = %s. Setting device power to %s
  (unlockedSinceBoot=%d)" (0x100006cd4) → `sub_10016b590` sets it. Whether an explicit power-on request is
  needed on a data volume that never had Wi-Fi: not determined (depends on the stored `AllowEnable`/power
  preference default; upstream's bootstrap sends one and upstream saw "MobileWiFi … 发现1个设备" only after it).
- `WiFiManagerEnable` at start (step A1) already applies the applier with the enable flag; it is a no-op
  until a device exists.

## A4. Failure modes matching the symptoms

1. Manager queue stuck in `SCNetworkInterfaceCopyAll` (0x1001691e8 / 0x10014b0d8):
   `__SCNetworkInterfaceCopyAllWithPreferences` → `__wait_for_IOKit_to_quiesce` (block 0x18d5251e4, "Waiting
   for IOKit to quiesce (or timeout)") loops: `SCDynamicStoreCopyValue("Plugin:InterfaceNamer")`, if the dict
   lacks `*QUIET*`/`*COMPLETE*`/`*TIMEOUT*` → `SCDynamicStoreNotifyWait` (0x18d52be34) = RPC
   `___SCDynamicStoreAddNotificationPort` + `waitForMachMessage` (0x18d52bf4c: `mach_msg(MACH_RCV_MSG …)` with
   timeout word 0, i.e. none) until the key changes. So: marker present before the call → passes; marker
   published later → wakes on the change; never published → blocked forever; configd unable to answer the
   RPC → blocked forever. A marker written between the `CopyValue` check and the `AddNotificationPort` RPC is
   a lost wake-up (INFERENCE from the code order). Because the queue is shared, this is exactly "endpoints
   checked in, clients unanswered, `wifi_manager_open` unanswered".
2. Marker published before en0 is listed (v239's order): wifid's trigger A fires at 0x10006c8a8 (if the
   IOKit object already exists) → "%@ does not exist in dynamic store" → returns. Recovery depends on
   trigger B receiving a change of `State:/Network/Interface` AFTER wifid registered its keys (step A1-6) —
   a key written before registration is still seen by the initial sync at 0x10006c844, so both orders work
   provided the key is actually written with `Interfaces` containing "en0". Not a permanent loss.
3. SCNetworkService / preferences.plist entry for en0: wifid itself creates what it needs
   (`__WiFiManagerUpdateDeviceSystemConfiguration`, A2) and tolerates a missing SCNetworkInterface in
   `WiFiDeviceManagerOpen`. A missing service therefore does not stop wifid; it stops configd/IPMonitor from
   publishing `State:/Network/Interface/en0/IPv4` etc. (INFERENCE). The bootstrap creating the service is
   still useful for IPMonitor, but if it does so with `SCPreferencesLock` on configd's own main thread it
   deadlocks configd (lock server = configd) and then every SC RPC from wifid blocks — mode 1 again.
4. Legacy port: stock plist does not declare `com.apple.wifi.manager` → `bootstrap_check_in` fails fast
   (manager+0x60=0), MobileWiFi clients fail fast as in v238. Only after adding it (upstream) can a
   `wifi_manager_open` block — upstream's 105/106/107 stackshots (`WiFiManagerClientCreate` on SpringBoard's
   main thread) are this mode with the port declared and wifid stuck in mode 1.
5. Blank Wi-Fi pane / stale Settings: Settings' Wi-Fi pane talks to wifid through CoreWiFi XPC
   (`com.apple.private.corewifi-xpc`); Preferences imports `WFWiFiStateMonitor` (WiFiKit) and
   `MobileGestalt_get_wifiCapability`; whether WiFiKit's calls are synchronous was not determined here.
   With the queue blocked, any synchronous request leaves the pane empty and the Settings main thread parked.
6. Status-bar battery percentage: no Wi-Fi import in `SpringBoard`, `PowerUIAgent` or `PosterBoard`
   (import lists from chained fixups; SpringBoard's imports are in the dyld cache frameworks it links, so
   this is weak evidence). `configd` imports `CWFInterface`. A synchronous Wi-Fi call on the battery path
   was NOT found; not determined. Alternative (INFERENCE): the bootstrap holding configd's main thread for
   5 s delays configd's own publishers (IPMonitor, power/kv notifications), which SpringBoard/PowerUI
   consume for the battery display; v238 had no such hold.

## A5. Recipe for this port (ordered)

All before or at the moment wifid's manager queue reaches `SCNetworkInterfaceCopyAll`; later is also fine for
steps 1–2 because of the re-trigger, but step 3 must precede step 4.

1. Driver: keep `IOInterfaceName`=en0, `IO80211InterfaceRole`=Infrastructure, `IOBuiltin`, SIOCGIFMEDIA =
   IFM_IEEE80211 (already true). IOKit object published before or after configd — both handled.
2. configd: `State:/Network/Interface` = dict with `Interfaces` containing "en0" (normally done by
   configd's KernelEventMonitor via `getifaddrs` at start or the KEV_DL_IF_ATTACHED event; the bootstrap
   publishing it is equivalent). Write it as one `SCDynamicStoreSetValue` so the change notification reaches
   wifid if it is already running.
3. configd: `Plugin:InterfaceNamer` = dict containing `*QUIET*` or `*TIMEOUT*` = true, and never rewritten
   afterwards without a terminal marker (every rewrite wakes and re-checks wifid).
4. configd: create the SCNetworkService for en0 (optional for wifid; needed for IPMonitor/DHCP). Do it in a
   process or thread that is not configd's main thread; do not hold `preferences.plist` when wifid runs.
5. Start (or release) wifid: its `WiFiManagerScheduleWithQueue` resumes the XPC listeners, fails the legacy
   check-in (unless declared), registers the dynamic-store and IOKit triggers, runs the initial sync, finds
   en0 in `Interfaces`, creates the device, calls `SCNetworkInterfaceCopyAll` (returns immediately because
   of step 3), opens the driver: driver log sequence to expect: `native client type 43 get bytes=256 -> 0
   from wifid`, `type 12 get bytes=19 -> 0`, `type 19 get`, `type 106 get`, then scans per client request.
6. Power: if no client powers Wi-Fi on, send one `WiFiManagerClientSetPower(true)`-equivalent through the XPC
   path or rely on the stored `AllowEnable` (not determined which default applies on a fresh data volume).
7. launchd: add `com.apple.wifi.manager` with `HideUntilCheckIn` (upstream) only when a legacy client must
   work; consider `HideUntilCheckIn` for the XPC services too if wifid is held for a long time, otherwise
   synchronous CoreWiFi callers park until the listeners resume.

Safety of starting wifid after configd has prepared everything: yes. launchd queues Mach messages sent to a
loaded job's Mach services until the service checks in, so no message is lost; the only cost is that
synchronous callers wait. Nothing in wifid requires being started early; its initial sync (0x10006c844 and
0x10006c8a8) is written for the "already there" case. The 60 s driver-request watchdog is compatible: the
first driver requests come from `WiFiDeviceOpen` (A2) once DeviceAdded has passed the dynamic-store check and
the `IOServiceGetMatchingService` loop, i.e. normally within a second of release; a kill + launchd restart
re-runs the same path.

## What cannot be known without a device log

- Whether v239's wifid was stuck in `SCDynamicStoreNotifyWait` (mode 1) or elsewhere: need a wifid stackshot
  or the os_log lines "WiFiManagerScheduleWithQueue: entering/exiting", "__WiFiManagerDeviceAddedCallback",
  "%@ does not exist in dynamic store", "Detected %ld wifi device(s)", "WiFiDeviceOpen: entering".
- Whether the driver saw any `from wifid[pid]` request (type 43/12/19): its absence means DeviceAdded never
  reached the applier; its presence means the queue got past `SCNetworkInterfaceCopyAll` at least once.
- Whether configd answered SC RPCs during the first seconds (a configd stackshot while the bootstrap runs).
- Whether Settings/WiFiKit uses synchronous CoreWiFi calls, and what removed the battery percentage.
- The stored `AllowEnable`/power default on this data volume.

## Commands and tools used

- `/tmp/claude-501/cont/research-b/wf.py` (written here): annotated disassembly of wifid → `wifid.dis`,
  `wifid.methods` (8042 functions, 1771 ObjC methods, 61 classes; objc stubs decoded to selectors);
  `xrim.py` → `wifid.calls`/`wifid.xref` (caller/callee index); `fn.py`, `at2.sh`, `lit.py` (constant
  objects), `imports.py` (chained-fixup imports), `stubcallers.py`.
- `/tmp/claude-501/cont/macho.py`, `calls.py`, `xref.py` (helpers provided); `xcrun llvm-objdump --macho -d`.
- `ipsw dyld disass --cache /tmp/claude-501/cont/dsc.a2s --image SystemConfiguration|IO80211 <DSC>`,
  `ipsw dyld a2s`, `ipsw dyld dump`; `scutil show State:/Network/Interface` on the host Mac (macOS 26.6.2).
- Read: `PongWifiNet.cpp`, `wifi-bootstrap.c`, `board_services18.py`, upstream notes 93/97/98/99/102/104/
  106/107/108/111, v238 console (`wifi-net` lines).
- Not modified: no image mounted writable, no git state, no existing file changed; scratch output is under
  `/tmp/claude-501/cont/research-b/` (delete when done).
