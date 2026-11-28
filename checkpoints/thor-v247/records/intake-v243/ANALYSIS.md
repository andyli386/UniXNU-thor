# v243：从闪存启动时电量与 Wi‑Fi 失效的根因、槽位重试次数、壁纸拦截为什么无效

2026-11-27 13:00–13:50。v242 从闪存启动的日志、同一个 v242 临时启动两次的日志、用 TWRP 只读读出的分区表、
引导程序对槽位状态的回答、22H374 的 WallpaperKit 二进制——这些把前面几版里靠推断的部分都坐实了。

| 文件 | 字节 | SHA‑256 |
|---|---|---|
| `thor-out/v243-unified/boot-thor-unixnu-v243-unified.img` | 95,391,744 | `15429c8bf22412f1facf79f094115e41bc8a05b06cd03992154605655b977bf0` |

内核 UUID `7567DC32-EA2D-3298-AC22-0B19EF51B1A1`（父：v242 `91842275-…`）。启动参数与 v239–v242 逐字节相同（1005 字节）；
设备树与 v238/v241/v242 相同（没有 `wlan` 节点）；启动栈预算通过（空闲 14240 / 13808 字节，`boot-stack-budget.txt`）。
记录：`v243-unified/unified-verification.json`。

## 1. 同一个镜像，两种启动方式

| 启动 | 方式 | 第一行时间戳 | LUN 4 的检查 | 电量 |
|---|---|---|---|---|
| v242 10:51 | 从闪存 | 10.46 s | 14 次全部失败，**每次都是第 8 步**：主表 CRC `f7db6cb5`，备份表头 CRC `3c6348c8` | 未发布 |
| v242 12:35 | 临时启动 | 124.37 s | 第一次就通过，固件 109 MB 全部校验通过 | 70%，+70 s 发布 |
| v242 12:58 | 临时启动 | — | 通过（用户看到电量数字） | 有 |

所以内核、启动参数、设备树都相同，只有启动方式不同。之前"从闪存启动就会失败"的说法也不对：v202 B 从闪存启动
8 次都通过。区别在**镜像是怎么刷进去的**（见第 2 节）。

## 2. 根因：引导程序在每次正常启动时改写主分区表

通过 TWRP 只读读出 LUN 4 两端的块（`v243-intake/read-gpt.sh`，`session-tools/gptcmp.py` 解析）：

- LUN 4 不是"基带固件 LUN"，是**固件 LUN**：96 个分区项，`uefi_a aop_a tz_a hyp_a modem_a bluetooth_a abl_a dsp_a
  keymaster_a boot_a devcfg_a vbmeta_a dtbo_a vendor_boot_a recovery_a …` 以及全部 `_b`，磁盘 GUID `97b0659c-…`。
- 主表和备份表在临时启动之后完全一致（12:44 与 13:18 两次读取逐字节相同）。
- `boot_a` 的分区属性 `0x0037000000000000`，`boot_b` 是 `0x003b000000000000`，其余 `_a` 固件分区是 `0x…7f`。

引导程序的只读回答（`fastboot getvar`）：

```
current-slot           a
slot-successful:a      no
slot-unbootable:a      no
slot-retry-count:a     6        (两次临时启动之后仍是 6)
slot-retry-count:b     7
```

属性的字段与这四个回答完全吻合：位 48–50 优先级，位 51–53 剩余重试，位 54"已成功"。`boot_a` 是 7/6/未成功。

机制（前两条由上面的数据坐实，第三条是与全部观察吻合的推断）：

1. `fastboot flash boot_a` 把 a 槽位重置成"未验证、7 次重试"（v239 之前用 TWRP `dd` 刷的 v202 B 从不触发；
   v239、v241、v242 各刷一次，刷后第一次正常启动的重试数都是 6）。
2. 临时启动不消耗重试；**从闪存正常启动每次消耗一次**。
3. 正常启动时引导程序把新的重试数写进**主**分区表（重试数就在 `boot_a` 的分区属性里），备份表没有跟上，
   所以内核读到主/备不一致。槽位被标记为"已成功"后引导程序不再改表（v202 B 时期的 8 次从闪存启动都一致）。

**风险：** 重试数到 0 时，引导程序通常把 a 槽位标为不可启动、改从 b 槽位启动原厂 Android。

## 3. 修复

**内核（v243）**：`ModemFirmwareAccess.h` 的检查改为**主表决定**。主表是钉死的（表头有效、分区项的 CRC 与表头一致、
两个预期的分区在预期的位置）；备份表照样读、照样算，但只记录不失败：

```
THOR MODEM FW V243: the backup table of the firmware LUN differs from the primary one: what=… header-fault=…
entries-crc=… differing-bytes=… first=entry N offset N primary=NN backup=NN; the primary decides
```

读固件的范围只由主表决定，固件本身之后还要过钉死的摘要（MDT 摘要、每段 SHA‑384、Wi‑Fi 固件 SHA‑256），
所以放松备份表的检查不降低安全性。v242 的重试和失败记录保留。

**槽位（一次性写入，用户 2026-11-27 同意）**：`boot_a` 的属性 `0x0037000000000000` → `0x007f000000000000`
（重试 7、已成功，与其余 `_a` 分区相同的样式）。只改 9 个字节：两个分区项块里 `boot_a` 属性的 1 个字节，
两个表头里各 4 个字节的表 CRC 和 4 个字节的表头 CRC。`session-tools/gpt-mark-ok.py` 只接受 11-27 读到的那个布局。

**以后怎么刷**：不再用 `fastboot flash boot_a`。`v243-intake/install-v243.sh` 在一次 TWRP 会话里完成：
核对手机身份（型号、root、LUN 和 boot_a 的大小与路径，全部从手机现读）→ 取刚才那一轮的日志（只读）→
读分区表、生成并两次核对计划 → 四个块逐个在手机上核对 SHA‑256 后写入（先备份表后主表）→ 回读整表与计划逐字节比较
→ 镜像用 `dd` 写入 boot_a、其余补零、整个分区的 SHA‑256 与镜像比较 → 回到引导程序，**只有**它自己回答
`slot-successful:a: yes`、`slot-retry-count:a: 7`、`slot-unbootable:a: no` 才执行 `fastboot reboot`，否则留在
fastboot。所有写入都只经由 `adb -s <序列号>` 发给手机上的 `/dev/block/sde`（四个块）和 `/dev/block/sde14`；
本机除了 thor-out 下的文件不写任何东西，本机不执行任何针对设备的 `dd`。

13:38 的演练（`--dry-run`，不写）通过了全部检查；13:42 起的正式安装见 `v243-intake/install.out`。

## 4. 壁纸：v242 的库为什么无效

v242 的日志：SpringBoard 里 `WallpaperKit at …, catalog hooks installed`（两个放行 getter 替换成功），
但**一次拦截都没有**；chronod 和 PosterBoard 里"WallpaperKit is not among the images of this process"。

原因（从 22H374 的 WallpaperKit 读出）：它有 10 处调用 `MGCopyAnswer`，全部经同一个桩
（`0x1bc034348`：`adrp x16 …; add x16, x16, #0x368 ; _MGCopyAnswer; br x16`）。WallpaperKit 和 libMobileGestalt
都在共享缓存里，这条调用是建缓存时就定死的直接跳转，没有 dyld 可以改写的绑定，`__interpose` 无从生效。
（在 Mac 上验证过的"拦截对共享缓存里的调用者也生效"不适用于这里的这条调用。）

WallpaperKit 实际怎么找目录：`+[WKDefaultWallpaperManager defaultWallpaperLookupURL]` 和
`-[WKWallpaperRepresentingCollectionsManager _loadSystemWallpaperCollections]` 取 `MGCopyAnswer("GSDeviceName")`，
调用 `-[NSString lowercaseString]`（都保留了栈帧，不是尾调用），再拼 `DefaultWallpapers~%@.plist`、
`Collections~%@.plist`。集合若带 `supportedDeviceClasses`，还要包含 `MGCopyAnswer("DeviceClass")`
（iPadOS_17~ipad、Lake~ipad 没有；Bokeh 只给 iPhone）。

这个恢复镜像里的目录：`DefaultWallpapers~ipad.plist` → 默认集合 `62879BFA-…`（iPadOS 17）、壁纸 7570，
类型 **LayeredAnimation**（两个 .ca 动画，类别 `810w-1080h@2x~ipad`）；另有 Lake~ipad（7560）和 Stripe（7365–7395）。

**v243 的库**（`prepare/wallpaper/wallpaper243.m`，只依赖 Foundation）：

1. 在实现 `-[NSString lowercaseString]` 的类上替换它：返回地址在 WallpaperKit 内且字符串是 `iPhone` 时答 `ipad`；
   其他调用者、其他字符串一律走原实现。
2. `wk_ignoreLogicalScreenClassForWallpaperBundle`、`wk_ignoreProductTypesForWallpaperBundle` 两个 getter 答 YES。
3. 只注入 SpringBoard 和 CollectionsPoster；chronod、PosterBoard 不再注入（它们不加载 WallpaperKit）。

Mac 上的对照测试（`prepare/wallpaper/hosttest/run.sh`，打包脚本每次都跑）：带库时 WallpaperKit 替身得到
`device name=ipad`、另一个字符串仍为 `ipados_17`、本程序自己的调用仍是 `iphone`、两个 getter 为 1；不带库时
`iphone / ipados_17 / iphone / 0 0`。

**只能在手机上验证的**：WallpaperKit 是否因此找到 iPad 的目录；PosterBoard 能否渲染动画壁纸（上游在这里遇到过
nil dataStore 的断言）。

日志里要找的行：
- `Pong Probe: wallpaper v243: WallpaperKit at … getters replaced; the device-name hook is on N class(es) (…)`
- `Pong Probe: wallpaper v243: "iPhone" lower-cased to "ipad" for WallpaperKit+0x…`
- `THOR MODEM FW V163: state=gpt-verified`、有没有 `THOR MODEM FW V243: the backup table … differs`
- `THOR BATTERY V226: published`
- `PONG PROBE PosterBoard` 的采样

## 5. 没有解决的

- **掉帧**：没有动。每帧仍有大量时间在等 GPU，内核每提交一次的缓存维护约 2.5 ms。
- **无边记（Freeform）画布一动笔就崩溃**（13:21 那一轮两次）：内核日志只有 `Freeform[554] Corpse failure, too many 6`
  ——崩溃报告的名额被别的进程（MessagesBlastDoorService 的尸体循环）占满，没有留下堆栈。下一步：把 Freeform 加进
  内核的标准错误捕获表，或先修沙盒替身对 `sandbox_check` 的回答。
- **Wi‑Fi（wifid）**：这个镜像没有 `wlan` 节点。等电量链在从闪存启动时稳定后，用带 wifid 的镜像试。
- **蜂窝、通话**：没有新的实现。
