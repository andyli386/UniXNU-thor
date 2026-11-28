# v238 的结果与 v239：关于本机、Wi‑Fi 的根因

2026-11-26 晚。v238 于 21:44 临时启动、21:52 回到 fastboot，运行 7 分 37 秒（内核时间 704.6–1161.7 s），
日志完整取回（`recovery-v238-full-20261126T135305Z`，1.4 GB，只读）。v239 按用户要求**刷入** `boot_a`。

## 1. v238 证明了什么

- **panic 记录槽位恢复**：`THOR WITNESS V235: panic record slot /private/var/logs/PongPanic-v235-1.bin proven … write-read-restore done`，
  用的是 1 号槽（`PongPanic-v235-1.bin` 256 KiB 在快照里）。
- **探针能用**：`THOR PROBE V238: staged`、`PosterBoard probe armed`，40 行采样进入内核控制台。
- **“关于本机”卡死的调用链带函数名了**（见下）。
- `THOR PROBE V238` 只出现了 PosterBoard：**wifid 和 airportd 两个进程在这次开机里从来没有 exec 过**。

## 2. 关于本机：`SCNetworkInterfaceCopyAll()` 在等一个永远不来的标记

`Pong Settings: About v238 hang=1`（三次采样一致）：

```
tid=3822 main:
  libsystem_kernel!mach_msg2_trap
  ← SystemConfiguration!SCDynamicStoreNotifyWait+0x94
  ← SystemConfiguration!<redacted>   (dispatch_sync 的块)
  ← SystemConfiguration!_SCNetworkInterfaceCopyAllWithPreferences+0x1f4
  ← GeneralSettingsUI!<redacted>+0x18  ← GeneralSettingsUI!<redacted>+0x958     (“关于”页取值)
  ← libpong-settings!observe_about_values+0x40
  ← Preferences
```

对照 Apple 开源的 configd（`SystemConfiguration.fproj/SCNetworkInterface.c`，`__wait_for_IOKit_to_quiesce`）：

```c
static void __wait_for_IOKit_to_quiesce(void) {
	static dispatch_once_t iokit_quiet;
	dispatch_once(&iokit_quiet, ^{
		store = SCDynamicStoreCreate(NULL, CFSTR("SCNetworkInterfaceCopyAll"), NULL, NULL);
		key = SCDynamicStoreKeyCreate(NULL, CFSTR("%@" "InterfaceNamer"), kSCDynamicStoreDomainPlugin);
		SCDynamicStoreSetNotificationKeys(store, keys, NULL);
		while (TRUE) {
			dict = SCDynamicStoreCopyValue(store, key);          /* "Plugin:InterfaceNamer" */
			if (dict != NULL && (CFDictionaryContainsKey(dict, kInterfaceNamerKey_Quiet) ||
			    CFDictionaryContainsKey(dict, kInterfaceNamerKey_Complete) ||
			    CFDictionaryContainsKey(dict, kInterfaceNamerKey_Timeout))) quiet = TRUE;
			if (quiet) break;
			ok = SCDynamicStoreNotifyWait(store);               /* 主线程停在这里 */
			...
```

标记名在 `Plugins/common/plugin_shared.h`：`*COMPLETE*`、`*QUIET*`、`*TIMEOUT*`。发布者是 configd 里的接口命名插件
（`Plugins/InterfaceNamer/ifnamer.c`）：IOKit 安静后写 `*QUIET*`，或 300 秒定时器到点写 `*TIMEOUT*`。
v236 的卡死报告里 configd 的 “InterfaceNamer thread” 从 97 秒起就空闲在运行循环里，但 v238 开机 437 秒、746 秒
（设备时钟 491 秒、800 秒）时“关于”仍然卡死——这个插件在这台机器上从不写标记。

**上游 Pong 早就遇到过同一件事**，`UniXNU-pong/userland/wifi-bootstrap.c` 的注释原文：

> 22H374 SCNetworkInterfaceCopyAll waits for Plugin:InterfaceNamer to contain one of its native terminal markers.
> Pong's physical interface is already named/attached by DLIL, but the Apple naming plugin never completes this
> handshake on the custom IONetworkStack. That held wifid's dispatch_once owner, its native manager queue, then
> SpringBoard's sync wifi_manager_open.

上游的做法是一个开机任务 `wifi-bootstrap`：等 IOKit 安静最多 5 秒，往 `Plugin:InterfaceNamer` 写 `*QUIET*` 或 `*TIMEOUT*`。
**Thor 没有这个任务**（`board_services18.py` 负责装它，Thor 的设备树里没有任何东西调用这个脚本）。

同一调用也是 v234 那次“主线程等锁”的根：当时持锁的线程停在 `hasEthernetNetworkInterfaces` 里，里面是同一个调用。

## 3. Wi‑Fi：wifid 被 launchd 的设备树条件挡住了

- 从系统镜像读 `com.apple.wifid.plist`：
  `Program=/usr/sbin/wifid`、`RunAtLoad=True`、`KeepAlive=True`、
  **`_LimitLoadToDeviceTree = ['wlan', 'marconi-wifi']`**。
- Thor 镜像的设备树根节点只有 `arm-io buttons chosen cpus defaults options product`，没有 `wlan` 或 `marconi-wifi`
  → launchd 不加载这个任务 → wifid 从不运行。
- 整个 launchd 缓存（608 个任务）里只有 `com.apple.wifid` 受这两个名字限制。
- 上游在自己的构建脚本 `build-ipados18.py`（`adapt_job18`）里把这一项删掉，`board_services18.py` 再补上旧式
  MobileWiFi 服务名；Thor 启动的是苹果原封的系统卷，这两处都改不了。
- v238 控制台里 63 秒时驱动收到的 6 次读取（类型 43、12、1）来自 **configd**（紧接着 `configd[16]` 的唤醒统计行和
  `Reinit'd ND information for interface en0`），不是 wifid。

所以 Wi‑Fi 从来没被系统的协议栈驱动过：没有开机请求、没有扫描请求、没有关联请求。

## 4. 壁纸：仍然没有原因

- PosterBoard（pid 200）88 秒启动，探针显示它启动后 5 秒、20 秒、60 秒都是 3 个线程，主线程在运行循环里空等，
  没有退出、没有崩溃报告。壁纸存储目录 `/private/var/mobile/Library/com.apple.wallpaper` 不存在；
  `/Library/Wallpaper/Collections` 有 5 项（Collections~ipad.plist、iPadOS_17~ipad、Stripe、Lake~ipad、Bokeh）。
- `com.apple.Home.wallpaper.plist`：`{ Identifier = HUSunrise, Type = 5 }, hasUpdatedToSunrise = true, Version = 2`
  ——主屏壁纸配置是有的（默认的 Sunrise）。
- v237 那次“`MiniIOSurface: selector 21 not implemented` 出现在 PosterBoard 启动 30 毫秒后”的推断是错的：
  v238 里这个调用的使用者是 **chronod**（锁屏的 Chrono 服务，它创建了一串 IOSurface）。
- 快照里有一份旧开机的 PosterBoard 看门狗报告（pid 405，`process-launch watchdog transgression … 30.00 seconds`），
  说明它曾经启动超时被杀；v238 里它启动正常。
- 统一日志对短时间运行不落盘（v237、v238 两次开机都不在存储里），v236 的 8 小时日志又从 150 秒才开始，
  PosterBoard 启动（84 秒）那一段不在里面。这是下一步取证的主要障碍。

## 5. 掉帧：v236 和 v237 相同

开机后同样 391 秒内帧间隔分布：

| | < 10 ms | 10–18 ms | 18–26 ms | 26–35 ms | > 35 ms | 最长 |
|---|---|---|---|---|---|---|
| v236 | 2141 | 1772 | 534 | 271 | 115 | 81 ms |
| v237 | 2376 | 2206 | 694 | 348 | 152 | 89 ms |

每帧等 GPU 约 7.4 ms（中位数）；驱动每次提交做 3.9 ms 的 CPU 缓存维护（v237：7168 次提交 27.8 秒、1.17 GB）。
不是 v237 引入的，v239 没有动它。

## 6. v239 改了什么

1. **设备树加根节点 `wlan`**（只有 `name` 和 `compatible`）→ launchd 加载并启动 wifid。
2. **探针库 v239 注入 configd**，在里面跑上游 `wifi-bootstrap` 的移植：
   - 开机后立刻往 `Plugin:InterfaceNamer` 写 `*TIMEOUT*`（configd 自己的服务没就绪就每半秒重试，最多一分钟），
     en0 出现后等 IOKit 安静 5 秒再按结果写 `*QUIET*` 或 `*TIMEOUT*`，之后 10 分钟内每 50 秒补一次
     （命名插件自己的更新会把标记冲掉）；
   - 把 en0 加进 `State:/Network/Interface`，在偏好设置里给 en0 建网络服务并应用；
   - 通过系统的 MobileWiFi 管理器请求开机。
   wifid、airportd 的标准输出和错误输出改到控制台。
3. 其余不变：v238 的槽位修复和带函数名的卡死转储、v237 的改动、v236 的显卡修复、v235 的见证。

FAT 里四个成员变了：内核、设备树、清单、菜单（标题）。启动参数与 v238 逐字节相同（1005 字节）。

### 日志里看什么

- `THOR WITNESS V239`、`THOR PROBE V239: staged`、`THOR PROBE V239: wifid probe armed`、`… configd probe armed`
- `PONG PROBE configd[16]: Pong Probe: bootstrap early marker=1 after N attempts`
- `… bootstrap namer after-en0: iokit=quiet|busy publish=1`、`bootstrap service: en0 service created steps=5 of 5`、`bootstrap power: devices=1`
- `PONG PROBE wifid[...]` 的采样；驱动里 `wifi-net: native client type 19`（开机）之类的设置请求、扫描、关联
- 打开“关于本机”后不再出现 `Pong Settings: About v238 hang=`

## 7. 刷写

v239 按用户要求直接刷入 `boot_a`（`v239-intake/flash-boot-a.sh`，与 11-20、11-23 写入 v202 B 相同的做法）：
紧凑镜像补零到 192 MiB（`0xC000000`），TWRP 内存启动后 `dd` 到 `/dev/block/bootdevice/by-name/boot_a`，
读回哈希比对，然后正常重启。写入前的分区哈希应为 `632c4629…`（即上次写入的 v202 B 补零镜像）；
不同则先整分区保存到 `v239-intake/boot_a-before-v239.img`。
恢复用的镜像都在 Mac 上：`v202-swipe-recovery/persistent-boot-a-v202b-20261120-171544.YSQ9Qp/` 里的
`boot_a-v202b-compact-padded.img` 和 `original-boot_a.img`。

第一次刷写（22:31）没有执行：手机在 22:10 之后自己离开了 fastboot（什么都不写，脚本在第一步就停了）。
之后挂了等待脚本 `v239-intake/auto-flash.sh`。

## 核对记录

- `package-v239.py package`、`verify`、`fullverify` 均通过；紧凑镜像 95203328 字节，SHA-256 见 `v239-intake/image-sha256.txt`。
- 启动栈预算与 v238 相同（qsort 深度 10，剩余 14096 / 13808 字节）。
- 探针库的主机测试：`v239-unified/prepare/probe/hosttest/run.sh`（引导部分在主机上编译掉，不碰 Mac 自己的网络配置）。
- 设备上的结果：**待填**。
