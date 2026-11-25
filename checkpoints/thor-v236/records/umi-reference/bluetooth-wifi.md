# HakiOS umi (Xiaomi Mi 10, SM8250, XNU 8796.121.2): Bluetooth and Wi-Fi reference

Read-only study of `R = ~/projects/thor-unixnu1030/thor-out/reference-umi/hakios-xnu-kernel-umi-fixes`.
Aliases: `D/` = `R/devices/xiaomi-mi-10/`; `dev/` = `D/overlays/xnu/files/bsd/dev/`; `sys/` = `D/overlays/xnu/files/bsd/sys/`;
`iok/` = `D/overlays/xnu/files/iokit/Kernel/`; `lx/` = `D/overlays/wifi/lx/src/` (the copies that get built: `D/tools/stage-wifi.py:16-19`);
`ul/` = `D/overlays/userland/`; `gen/` = `R/` (`drivers/wifi/`, `wifi/lx/src/`, `docs/`).
"(inferred)" marks my reading; everything else is cited as `file:line`.

## Summary

- **Bluetooth**: kernel-only H4-over-UART transport for the QCA6390 ("Hastings"); firmware embedded in the kernel at build
  time; Apple's `bluetoothd` reaches it through its built-in `H4:POSIX` backend over a kernel-control socket. No IOBluetooth
  kext emulation. Userland is opt-in (`BLUETOOTH=1`) and pins offsets to iOS 16.5 (20F66) `bluetoothd`. Nothing documents
  pairing, audio or tethering working.
- **Wi-Fi**: complete stack: PCIe0+MHI boot, QRTR/QMI "WLFW", unmodified Linux ath11k (Linux 7.0.14 on FreeBSD 15.1 LinuxKPI
  headers, `R/tools/mk-ath11k.sh:26-29`) on a home-written mac80211/cfg80211 stand-in with its own WPA2-PSK supplicant;
  Ethernet-mode frames into a BSD-KPI `en0`; property-only fake IO80211 IOKit tree plus `SIOCG/SIOCSA80211` handling.
  Documented on device: scan, a join, DHCP, HTTPS (`ul/mkjobs.py:134-145`). The only join trigger in this build is iOS's
  ASSOCIATE ioctl (`dev/hakios.apple80211.c:580` is the sole caller of `hakios_sta_ask_join`; `join_given()` runs only on the
  generic WPSS thread, `dev/hakios.wifi.c:4418-4495`, which umi skips), so that join came from wifid (inferred). The
  "cannot be joined from Settings yet" comment at `ul/build.sh:119` is inherited from the generic tree.

## Boot arguments

- `D/boot.args:1` contains `wifi=1 umi_bt=1`. The runtime appends the private `wifi_mac=AA:BB:...` from `fw-wifi/mac`
  (`D/tools/stage-runtime.py:60-72`); the firmware reports a zero MAC (`dev/hakios.wifi.c:4231-4239`).
- `wifi=1` on umi: `umi_wifi_init()` takes over and never enters the WPSS path (`dev/umi_wifi.c:860-895`,
  `dev/hakios.wifi.c:4519-4521`). Effective options via `lx_os_option()` (`dev/hakios.lx.c:289-320`): `wifi_lx=2`,
  `wifi_station=1`, `wifi_ba=1`, `wifi_wide=1`, `wifi_quiet=1`, **`wifi_irq=0`** (rings polled, no GIC vectors), `wifi_imps`
  only if `/private/var/db/umi-wifi-imps-on` exists.
- `umi_bt=1` = runtime; `umi_bt_probe=1..5` = diagnostics (survey, clock vote, loopback, ROM HCI, firmware+radio scan);
  both together refused (`dev/umi_bluetooth.c:636-643,655-678`).
- Others: `wifi_scan=1` (`D/WIFI-SCAN.md:3-16`), `screenoff_pm=1 screenoff_wifi=1` (`dev/umi_screenoff.c:128-136`), `net_trace=N`,
  `net_names=1` (`dev/hakios.net.c:74-80`), `umi_wifi_step=N umi_wifi_return=1` (`dev/umi_wifi.c:884-893`). `R/docs/boot-args.md:104-134`
  lists only the generic `wifi_*` set.

## Bluetooth: bring-up

- Entry `umi_bluetooth_init()` is patched into `bsd_init.c` right after `hakios_audio_init()` under `#if HAKIOS_UMI`
  (`D/prepare.py:131-135`); gated on `/chosen/hakios-board == "umi"` (`dev/umi_wifi.c:417-433`).
- Maps: GCC 0x00100000, TLMM 0x0f900000, UART 0x00998000 (`dev/umi_bluetooth.c:645-646,673`). Clocks: vote bits 6/7/16 in GCC
  0x52008, HWCG bit 1 cleared at 0x17008, UART root = GPLL0-even × 384/15625 = 7.3728 MHz (`:571-578,524-538`). Power: RPMh rails
  `smpa6 950, smpf2 950, smpa5 1900, smpc8 1350, ldoa16 3025 mV`, shared with WLAN (`dev/umi_rpmh.c:593-624`).
- GPIO: BT reset GPIO21 (cfg 0x241), SW_CTRL GPIO124 (0x240); reset low 56 ms, high, wait 56 ms; UART pins GPIO16-19 as QUP6
  (`dev/umi_bluetooth.c:582-591`).
- Controller: Qualcomm GENI SE UART (QUP6), **115200 8N1, no flow control** (other rates rejected `:417-418,508-509`). HCI:
  `bt_hci_command()` writes H4 command packets and polls for Command Complete/Command Status, about 1 s (`:144-212`). QCA IBS
  wake/ack bytes 0xfd/0xfc are answered (`:170-177,457-463`; `sys/hakios.bluetooth.h:20`).
- Runtime transport: kernel control socket **`com.hakios.bluetooth`**, `CTL_FLAG_REG_SOCK_STREAM`, 64 KiB queues, uid 0/501, one
  client; a worker thread owns the UART and polls every 1 ms; `TIOCGETA/TIOCSETA/TIOCMGET` emulated (`:337-523`); "bluetoothd's
  built-in H4:POSIX backend uses a stream kernel-control socket" (`:337-338`).
- Published object: plain `IOService` named `bluetooth` under the platform with `local-mac-address` (6 B),
  `HakiosBluetoothSupportedCommands` (64 B), `transport-speed` 115200, `compatible` `qca,qca6390`, `HakiosBluetoothReady`
  (`iok/hakios.bluetooth.cpp:16-43`). An IOBluetooth-family class: not found.

## Bluetooth: firmware

- Yes. `D/build-kernel.sh:7-9` runs `D/tools/embed-firmware.py` on `fw-bluetooth/htbtfw20.tlv` (TLV type 1, patch) and
  `htnv20.bin` (TLV type 2, NVM), producing `bsd/dev/umi_bt_firmware.inc` (`umi_bt_patch[]`, `umi_bt_nvm[]`;
  `embed-firmware.py:12-36`). Not in git (`D/README.md:36-38`). Their on-device origin: not found (inferred: Android
  `/vendor/firmware_mnt/image/`, cited for the other firmware at `dev/hakios.wifi.c:7-9`).
- Chip identity: vendor command `0xfc00` sub-op `0x19` returns a 21-byte event; requires product 0x10, ROM 0x200, SoC
  0x400a0200; the patch header's product/ROM must match; `patch[14]` mode 0 or 3 (`dev/umi_bluetooth.c:240-259`).
- Download: EDL `0xfc00 {0x1e, len, data}` in 243-byte segments; patch first (events skipped when mode == 3), 10 ms, then NVM
  with the tag-17 baud byte forced to 115200 (`:213-237,263-268`; `sys/hakios.bluetooth.h:65-87`). Then `0xfc17` (diag log
  off), HCI Reset, Read Local Version (LMP 0x1c, subversion ≠ 0x0d2b), Read Supported Commands (71 B), Read BD_ADDR (13 B,
  reversed) (`:269-289`).
- Runs once before launchd; on failure reset is reasserted, GCC/GPIO restored, endpoint stays absent (`:619-634`).

## Bluetooth: how iOS uses it

- `BLUETOOTH=1` adds jobs `com.apple.bluetoothd`, `com.apple.BlueTool` and injects `/redwood/lib/libhakios.bluetooth.dylib` into
  `bluetoothd` (`ul/build.sh:128-129,151-152,210-212`). `ul/mkjobs.py:318-321` sets `BT_HCI_TRANSPORT=H4:POSIX:com.hakios.bluetooth,115200`
  and `BT_BAUDRATE=115200` for `bluetoothd`.
- `ul/src/inject_bluetooth.c` (constructor): needs IOService `bluetooth` and its `HakiosBluetoothSupportedCommands` (`:65-73`);
  patches `/usr/sbin/bluetoothd` only when LC_UUID = `963930f1-d565-32b5-91d6-43af71e2c85a` (`:78-96`). It rewrites two loads at
  `+0x282ff4`/`+0x28311c` to `mov wN,#-1` so Apple's Broadcom/Apple VSC ranges never match (`:106-125`), allows LE Set Random
  Address during init (`+0x10c968`, `:126-134`), swaps init-table entries `+0x7fbde0`/`+0x7fbdb0` to skip the unsupported
  "Write Default Erroneous Data Reporting" step and program a static random LE address (`:150-167`), and sets debug prefs
  `DisableLowPowerCoreScan`, `DisableVSCScanParams` (`:138-147`). No HCI reply is altered (`:1-4`).
- BlueTool replaced: job relabelled `com.hakios.bluetool`, program `/redwood/bin/umi_bluetool` (`ul/mkjobs.py:322-328`). It listens
  on XPC `com.apple.BlueTool` and answers only `RunBuiltinScript` `boot`/`init`, 0 when the controller is ready (`ul/src/umi_bluetool.c:49-68,72`).
- Limits as documented: no claim of pairing, audio, tethering or a BSD BNEP interface (`D/HOTSPOT-umi.md:261-266`). The radio test
  (3 s LE scan, 4 s inquiry) runs only in diagnostic mode (`dev/umi_bluetooth.c:292-336,612`).

## Wi-Fi: bring-up chain

1. `hakios_lx_net_early()` creates `en0` at once (iOS looks for it once, early; `dev/hakios.lx.c:485-498`); thread `umi-wifi`
   runs `umi_wifi_pcie_diagnostic(10)` (`dev/umi_wifi.c:795-800,832-858`).
2. Regions: GCC 0x100000, TLMM 0x0f900000, PARF 0x01c00000, PHY 0x01c06000, DBI 0x60000000, iATU 0x60001000, ECAM
   0x60100000, BAR0 0x60300000 (1 MiB) (`:486-498`).
3. `umi_pcie_enumerate()` (`dev/umi_pcie.c:161-279`): PERST GPIO79 low → RPMh (`smpa4 1800, smpa6 950, smpf2 950, smpa5 1900,
   smpc8 1350, ldoa9 1200, ldoa5 880 mV`; `dev/umi_rpmh.c:532-542`) → CLKREQ GPIO80, WAKE GPIO81 → WLAN_EN GPIO20 high → GDSC
   0x6b004, XO/1 aux 0x6b038, refgen 0x6f014 = 100 MHz (shared with modem PCIe2, `D/MODEM-MAINLINE.md:153`), branches
   0x8c004/0x6f02c/0x9000c/0x8d058, votes 0x52008 = 0x2f, resets 0x6b000/0x6c01c → PHY table → PIPE vote → 10 ms → PERST high →
   Gen2 x1 → PCI ID `0x110117cb` → BAR, memory decode on, bus mastering off.
4. SMMU: `hakios_wifi_umi_smmu_run()` installs an owned context for stream **0x1c01** (BDF-hash SID table, bypass disabled;
   `dev/umi_pcie.c:281-314`), 130 tables for the 256 MiB IOVA aperture, registers saved and restored
   (`dev/hakios.wifi.c:3598-3600,3665,3942-4040`).
5. `umi_pcie_prepare_radio()` clears SHUTDOWN_EN/VDD4BLOW in WLAON power control through window register BAR+0x310c
   (`:316-339`); MSI capability, vectors 0-2 (`:341-376`); DMA on.
6. MHI (`dev/umi_mhi.c`): cold-PBL signature accepted (MHIVER 0xdeadbeef, BHI 0x100, BHIE 0x224; `:121-127`); SBL = first
   512 KiB of `amss.bin` via BHI (`:319-339`); contexts and M0 (`:208-304`); AMSS = whole image as 512 KiB BHIE vectors
   (`:342-376`); mission = EE 2 or 4 (`:379-400`); IPCR UL ch 20 / DL ch 21, event ring 1, 32 × 8192 B (`:16-22,635-701`);
   QRTR hello (`:779-787`); DMA aperture 0xa0000000-0xb0000000 (`:12-13`).
7. `hakios_qmi_set_transport(&umi_qrtr_ops)`; thread `umi-ipcr` polls QRTR every 1 ms (`dev/umi_wifi.c:381-407,735`).
8. `lx_start()` (`lx/lx_bus.c:1102-1267`): `ath11k_frame_mode = ATH11K_HW_TXRX_ETHERNET`; `ATH11K_BUS_PCI`,
   `hw_rev = ATH11K_HW_QCA6390_HW20`; registers reached through dynamic 512 KiB windows (select `0x40000000|n` at BAR+0x310c,
   serialised by `window_lock`; `:330-347`); hw_params overrides `supports_regdb`, `coldboot_cal_mm`, `cbcal_restart_fw`,
   `idle_ps = 0` (`:1181-1203`); `ath11k_core_init`, then `ath11k_qmi_fwreset_from_cold_boot()` (`:1216-1223`). ath11k's own
   `qmi.c` runs the WLFW exchange; `lx_qmi_send_request()` rewrites RESPOND_MEM to fixed IOVAs (DDR type 1 @0xa1000000, CAL
   type 4 @0xa1600000, M3 type 3 @0xa1880000, 0x411bc B; `dev/umi_wifi.c:80-97`, `lx/lx_bus.c:686-715`) and HOST_CAP
   (`num_clients 1`, `wake_msi = data+13`; `:716-728`).
9. Files, in image `fw/`: `amss.bin`→`qca6390/amss20.bin`, `m3.bin`→`qca6390/m3.bin`, `board.bin`→`qca6390/bd_j1gl.elf`,
   `regdb.bin`→`qca6390/regdb.bin`; no `board-2.bin` (`dev/hakios.wifi.c:4202-4229`). Copied from `fw-wifi/qca6390/` plus
   `WCNSS_qcom_cfg.ini` (`ul/build.sh:546-553`). On-device origin: not found.
10. `lx_station_offer()`: station vif up, optional boot scan, then `lx_os_station(&boundary)` and `lx_os_ap_register()`
    (`lx/lx_80211.c:2392-2417`); log "station ready for iOS Wi-Fi requests" (`dev/umi_wifi.c:748`).

## Wi-Fi: IO80211 glue

- No IO80211 kext. Three stand-ins. (a) BSD `en0` from `ifnet_allocate_extended`: family Ethernet, subfamily WIFI, `IFT_ETHER`,
  `ioctl = net_ioctl` (`dev/hakios.net.c:523-554`); unknown ioctls go to `hakios_apple80211_ioctl()` (`:452-455`). (b)
  Property-only classes `IO80211Controller`→`IOEthernetController`→`IONetworkController`→`IOService` and an `IONetworkInterface`
  under the DT `wlan` node, with `IOInterfaceName` = en0, `IO80211InterfaceRole` = "Infrastructure"; `newUserClient` returns
  Unsupported so IO80211 "falls back to IOCTL path" (`R/drivers/wifi/hakios.wlan.cpp:1-33,45-171`). (c) `SIOCGA80211`
  0xc02869c9 / `SIOCSA80211` 0x802869c8 with `struct apple80211req` (`dev/hakios.apple80211.c:48-57`).
- GET implemented (`:303-474`): 12 CARD_CAPABILITIES (WPA2, TKIP, AES/CCMP, HostAP, ...), 19 POWER, 15 OP_MODE, 27/254
  SUPPORTED_CHANNELS (4824 B), 207 CHANNELS_INFO (10016 B), 21 ASSOCIATE_RESULT, 11 SCAN_RESULT (1164 B, one BSS per call), 13
  STATE, 1 SSID, 9 BSSID, 4 CHANNEL, 16 RSSI, 103 CURRENT_NETWORK, 2 AUTH_TYPE, 43/44 names. CIPHER_KEY (3), RSN_IE, POWERSAVE,
  TXPOWER and others return `EOPNOTSUPP`.
- SET implemented (`:478-618`): 20 ASSOCIATE (592 B; key_cipher 10 = passphrase 8-63 bytes, 6 = 32-byte PMK; Apple's RSN IE is
  unused), 22 DISASSOCIATE, 19 POWER, 10 SCAN_REQ (5456 B; passive when `scan_type == 2`; up to 64 channels).
- Events (`:622-698`): `kev_post_msg`, `KEV_VENDOR_APPLE`, `KEV_IEEE80211_CLASS`, subclass 1: 1 POWER, 2 SSID, 3 BSSID, 4 LINK
  (32-B struct), 9 ASSOC_DONE, 10 SCAN_DONE; interface name in the first 16 bytes. Join result: 0→1, `EAUTH`→21 "invalid PMK",
  `ETIMEDOUT`→9, else 2 (`:678`).
- Station boundary: `R/drivers/wifi/hakios.sta.c` serialises power/scan/join/leave on one thread and caches the last scan (128
  BSS) and channel list (`:33-157`).
- Shim duties: `lx/lx_base.c` threads, workqueues, timers, completions, DMA pool, skb, idr, rhashtable; `lx/lx_bus.c`
  `ath11k_hif_ops` (windowed register access, start/stop, service→pipe map, `power_up`→MHI boot, MSI data, ring-poll thread
  `wifi-rings`, QMI intercept); `lx/lx_80211.c` `linuxkpi_*` mac80211/cfg80211 entry points plus the whole MLME;
  `lx/lx_wpa.c` crypto.

## Wi-Fi: security/association

- EAPOL (0x888e) frames arrive Ethernet-framed from the firmware: `ethernet_received()` → `handshake_message()` →
  `lx_wpa_message()`; replies go out with `IEEE80211_TX_CTL_HW_80211_ENCAP` (`lx/lx_80211.c:908-958,841-869`).
- The supplicant is hand-written (`lx/lx_wpa.c`): SHA-1/HMAC, PBKDF2-HMAC-SHA1 **4096** rounds (`:268-294`; the header says
  8192, `lx/lx_wpa.h:12`; the code wins), PRF, PTK from min/max(AA,SPA)‖min/max(nonces) (`:505-524`), AES-128 decrypt and
  RFC 3394 unwrap (`:365-460`), MD5/RC4 for TKIP descriptor v1. Message 1 (unsigned) is answered with message 2; signed
  messages need a valid MIC, a strictly increasing replay counter, message 1's ANonce, the ENCRYPTED bit and a GTK KDE
  (`:577-685`).
- Accepted: WPA2-PSK with CCMP (preferred) or TKIP pairwise and TKIP/CCMP group; open only when asked as open. Refused:
  WPA3/enterprise, PMF-required, pre-RSN WPA (`lx/lx_80211.c:1565-1618`).
- Join (`lx/lx_80211.c:1849-2145`): scan up to 3× by name → channel → `sta_state` NONE → open-system auth up to 3× (500 ms each)
  → association request (SSID, rates, 802.11h, RSN, HT/VHT, WMM) → `BSS_CHANGED_ASSOC`, `conf_tx` → wait for the 4-way ≤ 8 s →
  30 ms → GTK length check → `set_key` pairwise then group → AUTHORIZED → `lx_os_net_link(1)`. ADDBA/DELBA are handled
  (`:1322-1385`).
- Known failures: wrong passphrase → AP deauth or unanswered message 2 → `EAUTH` (`:2107-2115`). Idle IMPS hung the kernel, so
  `idle_ps` is off by default (`lx/lx_bus.c:1185-1203`). All-ones reads back off 100 ms (`D/HOTSPOT-umi.md:1655`).
- **Hardening gap**: the umi overlay lacks the generic tree's fixes W1/W2/W7/W8 (`R/docs/driver-review/wifi-stack.md:269-277`):
  no `replay_known` guard on message 1 (`lx/lx_wpa.c:612-618` versus `R/wifi/lx/src/lx_wpa.c:484-488`), handshake result
  not sticky (`lx/lx_80211.c:935-941,2102`), GTK length `<= 32` instead of `== 16` (`lx/lx_wpa.c:566`), no `want_bssid` /
  `target_fixed`. Group-frame PN check and drop-unencrypted rule are absent in both (review W3/W4).
- Test tooling is host-only: `D/tools/test-wpa-auth.py` (AP authenticator against hashlib, then the generic `tools/wpa-test`
  compiled against the overlay) and `D/tools/test-wifi-ap-datapath.py` (`to_ethernet`, `ap_input`, `frame_send` under ASan/UBSan).
  A runtime "wpa auth" tool: not found.
- Hotspot: in-kernel AP authenticator `lx/umi_wpa_auth.inc`; `hakios-hotspot-native` owns XPC `com.apple.wifi.hostapd`
  (`ul/src/hakios_hotspot_native.c:181-209`); `ap1` is net unit 3, WPA2-PSK/CCMP, channels 1-13/36-48/149-165, at least 8 peers,
  HT20, PS, kickout, GTK rekey 3600 s; native misd/bootpd/pfd own the IP side (`D/HOTSPOT-umi.md:1191-1228,1230-1256,1406-1416`).
  AP and STA are mutually exclusive (`dev/hakios.apple80211.c:781-782`).

## Wi-Fi: data path

- The firmware does Ethernet encap/decap and ciphering (`lx/lx_80211.c:13-14,1108-1114`). RX: `linuxkpi_ieee80211_rx()` →
  `ethernet_received()` → `lx_os_net_input()` → `hakios_net_input()` builds an mbuf with the L2 header in headroom →
  `ifnet_input()` (`:944-958,1030-1078`; `dev/hakios.net.c:316-395`). TX: `net_output` → 256-frame queue → thread `wifi-send` →
  `lx_transmit()` (`dev/hakios.net.c:161-194`, `dev/hakios.lx.c:368-483`).
- `en0` is plain Ethernet to the stack (MTU 1500, media `IFM_IEEE80211`; `dev/hakios.net.c:424-440,585-591`). Link up/down posts
  `KEV_DL_LINK_ON/OFF` (`:462-497`); "configd starts DHCP" (`sys/hakios.net.h:16`), kicked by job `com.hakios.wifi` =
  `/redwood/bin/net_test wifi` (`R/userland/com.hakios.wifi.plist:7-13`). Observed `en0 192.168.9.243`, DNS over TCP, TLS to Apple
  (`ul/mkjobs.py:134-139`). No custom DHCP in the Wi-Fi path. The MAC comes from `wifi_mac` (`dev/hakios.wifi.c:4238-4276`); `ap1`
  gets a locally administered derivative (`dev/umi_wifi_ap.inc:31-39`).

## Wi-Fi: power/scan

- IMPS is off by default; the opt-in file is read once in `lx_start()` (`lx/lx_bus.c:1185-1203`, `dev/hakios.lx.c:297-312`).
- Screen-off: with `screenoff_pm=1 screenoff_wifi=1` a 1 s work item requests `vif->cfg.ps` on screen-off unless scanning,
  `dynamic_ps_timeout` 100 ms, DTIM taken from the beacon TIM (`lx/umi_wifi_screenoff.h:3-44`, `dev/umi_screenoff.c:21-24`).
  No rail or clock power-off (`sys/umi_screenoff.h:1`).
- Scan: `station_scan()` builds the channel list from the regulatory domain (NO_IR/radar skipped for active scans), 40 s
  timeout, then `cancel_hw_scan` (`lx/lx_80211.c:1162-1274`). `wifi_scan=1` runs one passive scan before the station is published
  (`:2406-2412`; `D/WIFI-SCAN.md:11-16`) and "does not test association, DHCP, or connectivity" (`:32`).
  `D/STATUSBAR-SIGNAL-umi.md` is about cellular bars; `D/WEATHER-umi.md` only shows the network works (`:35,61`).

## Device-specific vs generic

- Mi 10/SM8250 constants: every address, GPIO and GCC offset above; RPMh rail names; PCI ID; SMMU base 0x15000000
  (`dev/umi_wifi.c:561`) and stream 0x1c01; fixed IOVAs; firmware names; `hakios-board` "umi".
- Reusable as is: `hakios.sta.c`, `hakios.wlan.cpp`, `hakios.net.c`, the `hakios.apple80211.c` core, the `hakios.lx.c`
  contract (`sys/hakios.lx.h`), `lx_base.c`, `lx_wpa.c`, the H4 parser (`sys/hakios.bluetooth.h`) and the control-socket
  transport design.
- Version-pinned, not portable: all Apple80211 struct sizes (`_Static_assert`s `dev/hakios.apple80211.c:181-193`,
  `dev/umi_apple80211_ap.inc:22-28`) and the `bluetoothd` UUID and offsets.
- XNU-8796 APIs used (diff these against 11417): `kalloc_data`/`kfree_data`/`kalloc_type`, `kmem_alloc_contig(...
  KMA_KOBJECT|KMA_ZERO|KMA_DATA, VM_KERN_MEMORY_IOKIT)`, `ml_io_map_unmappable(..., VM_WIMG_WCOMB)`, `lck_mtx_sleep_deadline`
  (`dev/hakios.lx.c:93,104,206,245`); `ifnet_allocate_extended` with `IFNET_INIT_LEGACY|IFNET_INIT_NX_NOAUTO`,
  `ether_frameout_extended` (`dev/hakios.net.c:535,549`); `kev_post_msg` (`dev/hakios.apple80211.c:625-636`);
  `ctl_register`/`ctl_enqueuedata` (`dev/umi_bluetooth.c:490-510`). Their own private helpers: `gic_v3_spi_*`, `loader_return`,
  `loader_log_line`, `hakios_tl`, `umi_cpu_pc_mmio_*`. Any 11417 difference: not found in the checkout.

## Dead ends

- The generic WPSS path (`hakios.wifi.c` steps 1-16, the `hakios.wlfw.c` stand-in) is bypassed on umi; `hakios_wlfw_query()`
  serves only diagnostic steps 7-9 (`dev/umi_wifi.c:767-772`).
- A GIC-MSI interrupt path exists (`dev/hakios.lx.c:518-674`) but umi forces polling (`:296`): "MSI descriptors, with polling
  until IRQ integration" (`lx/lx_bus.c:1172`).
- Sharing the PCIe refgen at 19.2 MHz broke the modem probe; it is 100 MHz now (`D/MODEM-MAINLINE.md:153`).
- Hazards they hit: a DSP shut down by TrustZone without an SMP2P stop request reset the redwood phone (`dev/hakios.wifi.c:69-77`);
  radio registers outside mission mode abort the kernel (`:3427-3438`); the rings thread writing after an endpoint reset hangs
  the SoC (`dev/umi_wifi.c:755-759`).
- Stock `hostapd` cannot be used (needs RawGet 73, RawSet 74-78, 3), hence the in-kernel authenticator (`D/HOTSPOT-umi.md:1193-1197`);
  Bluetooth PAN was judged the wrong first move (`:261-266`).
- `ul/src/inject.c` and `inject_sb.c` contain no Bluetooth or Wi-Fi hooks (grep: 0 hits). `D/drivers/umi/hakios.bluetooth.cpp`
  is a one-line wrapper for `umi_bluetooth_impl.cpp`, the staged copy of `iok/hakios.bluetooth.cpp` (`D/prepare.py:123-125`).
