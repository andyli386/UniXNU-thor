# v241：壁纸的根因与修复、两个镜像、蜂窝与通话的调研

> **2026-11-27 10:50 追记：v241 的两个镜像不要再刷。** v241‑unified 刷入后进不了系统（壁纸库把 MobileGestalt 的名字写错了，
> SpringBoard 每次启动即崩溃）；v239 的电量数字消失和 Wi‑Fi 不可用另有原因（固件 LUN 的分区表检查在从闪存启动时失败），
> 下文第 2 节里"wifid 卡住"的推测不是这两件事的原因。原因、证据和修正见 `v242-intake/ANALYSIS.md`。
> 本文其余内容保留为当时的记录。

2026-11-27 凌晨。手机不在手边（无法连电脑），这一轮全部是主机上的工作：读 22H374 的系统二进制（WallpaperKit、
wifid、MobileWiFi、SystemConfiguration、launchd、CommCenter 等）、读上游 Pong 和小米 10（umi）的源码与记录，
然后在 v240 的基础上做 v241。**v241 的任何一项都还没有在手机上验证。**

v239（现在刷在 `boot_a` 里的版本）在手机上看到的结果：设置 → 通用 → 关于本机能打开了；电量图标没有数字；
设置的 Wi‑Fi 页面空白（先点通用再返回点 Wi‑Fi 会停在通用）；壁纸仍是黑的。v239 没有取到日志。

| 镜像 | 设备树 | Wi‑Fi 守护进程 wifid | 用途 |
|---|---|---|---|
| `boot-thor-unixnu-v241-unified.img` | v238 的（没有 `wlan` 节点） | 从不启动；问 Wi‑Fi 的调用立刻失败（同 v236–v238） | **日常用**：壁纸修复 + 关于本机修复，其余保持 v238 的状态 |
| `boot-thor-unixnu-v241-wifi.img` | v239 的（有 `wlan` 节点） | launchd 照常启动它，但被一个很小的库扣住，等 configd 准备好、电池发布后才放行 | **试验**：看 wifid 能不能工作，并留下它自己的日志 |

两个镜像用同一个内核（UUID `5874B07A-A7B8-3341-8673-159073AD7E1A`），只差设备树里的一个节点。

| 文件 | 字节 | SHA‑256 |
|---|---|---|
| `thor-out/v241-unified/boot-thor-unixnu-v241-unified.img` | 95,371,264 | `1502e0062c2d568a015bd6d2ca6fd33319178fff84b116687abf07b7879408df` |
| `thor-out/v241-unified/boot-thor-unixnu-v241-wifi.img` | 95,371,264 | `3552a3629c599851c316fdb74766cc81f8977a6c74eff528335cd408b271af09` |

## 1. 壁纸：根因（主机上从 WallpaperKit 读出来的）

WallpaperKit（22H374，`/System/Library/PrivateFrameworks/WallpaperKit.framework`）的逻辑，逐条从二进制读出：

1. `+[WKDefaultWallpaperManager defaultWallpaperLookupURL]`：打开
   `/Library/Wallpaper/DefaultWallpapers~<设备名>.plist`，设备名是 MobileGestalt 的 `GSDeviceName` 转小写。
2. `-[WKWallpaperRepresentingCollectionsManager _loadSystemWallpaperCollections]`：集合的顺序表是
   `Collections~<设备名>.plist`（找不到就用不带后缀的 `Collections.plist`）。
3. `WKLogicalScreenClassString()`：用主屏尺寸和 `DeviceClass` 拼出 `"<宽>w-<高>h@<倍率>x~<类>"`；
   `+[WKWallpaperBundle shouldLoadWallpaperBundleAtURL:]` 丢弃 `logicalScreenClass` 与它不同的每一个壁纸包，
   除非 `-[NSUserDefaults(WKAdditions) wk_ignoreLogicalScreenClassForWallpaperBundle]` 返回 YES。这个 getter
   先问 `-[UIDevice sf_isInternalInstall]`：不是内部安装就直接返回 NO，不读任何偏好。
4. `-[WKDefaultWallpaperManager _loadDefaultWallpaperInformation]`：查表文件不存在时，按 `-[UIDevice sf_isiPad]`
   退回到写死的 iPhone 或 iPad 集合；还没有就**自己造一个叫 "Solid Black" 的壁纸包**（黑色的 PNG）。

这台机器给系统看的是 iPhone 的身份（`iPhone17,1`——PosterBoard 的偏好文件里记着
`StashedDeviceType = iPhone17,1`），而恢复镜像只有 iPad 的目录：

```
/Library/Wallpaper/DefaultWallpapers~ipad.plist        → 集合 62879BFA-…，壁纸 7570（iPadOS 17）
/Library/Wallpaper/Collections/Collections~ipad.plist
/Library/Wallpaper/Collections/iPadOS_17~ipad.wallpaperCollection/Wallpapers/7570.iPadOS_17-810w-1080h@2x~ipad.wallpaper
```

没有任何 `~iphone` 的文件。所以对 WallpaperKit 来说：查表文件不存在 → 写死的 iPhone 集合也不存在 → 每一次
壁纸请求都落到 "Solid Black"。**这就是壁纸一直是黑的的原因。**

上游 Pong 遇到过同一件事（它的 services42/43 记录：手机身份 + iPad 素材），它的解法是在自己组装的系统卷里生成
一套 `~iphone` 名字的包装文件；Thor 启动的是苹果密封的系统卷，改不了文件。

旁证：v238 里 PosterBoard 启动后一直空闲（探针采样：3 个线程都在运行循环里等消息）——没有任何进程有壁纸可向它要。
`Pong-Poster18.log` 里 407 次相同的崩溃（`WKWallpaperRepresentingCollectionsManager initWithDownloadManager:` →
`_loadCollections` → `_loadSystemWallpaperCollections` 抛出未捕获的异常）来自更早的、还往 PosterBoard 里注入
大适配器的版本；从 v231 起不再注入，它就不崩了，但也没有事可做。

### 修复

一个小库 `libpong-wallpaper-v241.dylib`（`prepare/wallpaper/wallpaper241.c`，86 KB），由内核放进**链接了
WallpaperKit 的进程**——用 `ipsw dyld imports` 查出来是 SpringBoard（经 SpringBoard.framework，主屏壁纸）、
chronod（经 CoverSheet，锁屏壁纸）、CollectionsPoster（渲染系统自带壁纸的扩展），外加 PosterBoard。它只做三件事：

1. 拦截 `MGCopyAnswer`：**当调用者位于 WallpaperKit 内部时**（看返回地址），把 `GSDeviceName` 和 `DeviceClass`
   答成 `iPad`。别的进程、别的调用者一律原样回答，手机身份不变。
2. 把 `wk_ignoreLogicalScreenClassForWallpaperBundle` 和 `wk_ignoreProductTypesForWallpaperBundle` 两个 getter
   换成返回 YES，让 iPad 的壁纸包通过屏幕类别检查。
3. 不写任何文件，不改任何偏好，不画任何东西；没加载 WallpaperKit 的进程里它什么都不做。

这样 WallpaperKit 会读到 `DefaultWallpapers~ipad.plist` → 默认壁纸 7570（iPadOS 17，810w‑1080h@2x~ipad）。
12.9" iPad 的比例是 3:4，这块屏幕（1080×1440）也是 3:4。

主机上的验证：

- 交叉编译、签名通过；interpose 条目按 install name 绑定到真正的 MobileGestalt。
- **Mac 上的对照测试**（`prepare/wallpaper/hosttest/run.sh`）：假的 MobileGestalt 对所有人都答 "iPhone"，假的
  WallpaperKit 经库去问——带库时 WallpaperKit 得到 `GSDeviceName=iPad DeviceClass=iPad ProductType=iPhone17,1`，
  别的调用者仍是 iPhone，两个 getter 为 1；不带库时全部是 iPhone、getter 为 0。打包脚本每次都跑这个测试。
- 另外在 Mac 上确认了 dyld 的 interpose 对**共享缓存里的框架发出的调用**同样生效（拦截 `close()`，看到调用来自
  CoreFoundation 和 Foundation），调用者的归属用返回地址 + `dladdr` 判得出来。

只能在手机上验证的：WallpaperKit 拿到默认壁纸之后，PosterBoard/扩展这一段会不会撞上上游当年修过的问题
（services45–53：nil dataStore 断言、文件保护等级、SQLite 创建、内存策略——Thor 的适配器里有那些修补，但从 v231
起不再注入 PosterBoard）。如果会，下一次日志里探针会报告 PosterBoard 的退出和线程。

日志里要找的行：`THOR WALLPAPER V241: staged …`、`THOR WALLPAPER V241: SpringBoard|chronod|PosterBoard|
CollectionsPoster catalog library armed`、`PONG PROBE …: Pong Probe: wallpaper v241: WallpaperKit at … catalog
hooks installed`、`… "GSDeviceName" answered with iPad for a call from WallpaperKit+…`。

## 2. Wi‑Fi：v239 为什么坏，v241 怎么做

调研报告：`research/WIFID-22H374.md`（地址都是 22H374 的 `wifid` / SystemConfiguration / IO80211，未滑动）。

### wifid 的工作方式（从二进制读出的）

- **它只有一条要紧的串行队列** `com.apple.wifid.managerQueue`。所有 CoreWiFi 的 XPC 请求、旧式的 MobileWiFi
  MIG 服务器、IOKit 的设备通知、动态存储（SCDynamicStore）回调都在这一条队列上处理。
- **XPC 监听很早就恢复**（`-[WiFiXPCManager activate]`），在有任何设备之前、在旧式端口签到之前。所以客户端能连上、
  能发消息；消息排在队列上，队列走到才回答。
- **设备到达**（`__WiFiManagerDeviceAddedCallback`）先查全局键 `State:/Network/Interface` 的 `Interfaces` 列表里
  有没有 en0：没有就记一条 "does not exist in dynamic store" 然后返回，不阻塞；之后 en0 被列进去时会再触发一次。
- 设备建立后它在**同一条队列上**调用 `SCNetworkInterfaceCopyAll` → `__wait_for_IOKit_to_quiesce`：读
  `Plugin:InterfaceNamer`，没有终止标记就 `SCDynamicStoreNotifyWait`（向 configd 发 RPC，然后**无超时地**等这条
  键变化）。标记在调用前就在 → 立刻通过；标记永远不来，或者 configd 不应答 → 队列永远卡住，XPC 和 MIG 都没人回答。
- **v239 最可能的失败**就是这一条：wifid 的队列卡在这个等待里（或卡在向一个当时被引导线程占住的 configd 的 RPC
  上）——监听都已签到但不应答，正好是"Wi‑Fi 页面空白、设置界面停住"的样子。而不是"设备列表缺失"：wifid 自己会给
  它的接口建默认配置，没有网络服务也不影响它。

### 已经确定的其他事实

- **launchd 一旦加载了 wifid 的任务，它的九个服务端口就是"预留"的**：客户端开机就能拿到发送权，消息排在 launchd
  里直到 wifid 签到。wifid 若永远不应答，客户端就永远等。v238 里没有 `wlan` 节点、任务根本不加载，查找立刻失败。
- `_LimitLoadToDeviceTree = [wlan, marconi-wifi]` 是 launchd 向 IOKit 注册表查节点，在它加载任务时就定了，开机后
  无法补救。
- 任务的 MachServices 里**没有**旧式的 `com.apple.wifi.manager`；MobileWiFi 的客户端 `WiFiManagerClientCreate`
  会 `bootstrap_look_up` 这个名字再同步做 `wifi_manager_open`，并要求调用者带 `com.apple.wifi.manager-access`
  授权（configd 没有）。所以 v239/v240 里"configd 请求开机"那一步必然没有生效。
- 上游在 22H374 上让 wifid 工作的经过（它的 services93–113 记录）：97 发现 22H374 的 wifid 先要求 en0 在
  `State:/Network/Interface` 里；106/107 拿到真实线程栈（SpringBoard 主线程等 `wifi_manager_open`，wifid 的管理器
  队列卡在 `SCNetworkInterfaceCopyAll` 的接口命名等待里），发布命名标记后解除；108 用户确认系统 Wi‑Fi 修复；
  111 "能扫描但不能连接"（关联请求结构体是新的布局——Thor 的驱动里已经有这个 0x384 字节的布局）；113 用户确认
  Wi‑Fi 连接正常。

### v241 的做法

**v241-unified（默认）**：设备树回到 v238 的，没有 `wlan` 节点。launchd 不加载 wifid 的任务，没有任何端口被
预留——和 v236–v238 完全一样的、已经在手机上证明稳定的状态。

**v241-wifi（试验）**：设备树带 `wlan` 节点。v240 那种"在 exec 里等"的做法去掉了——等在 exec 里会占住 launchd
的 spawn 调用，拒绝 exec 又会让端口一直预留着没人应答。改为：

1. launchd 照常启动 wifid；内核往 wifid 里放一个很小的库 `libpong-wifid-v241.dylib`
   （`prepare/wifid/wifid241.c`），它的构造函数每 100 ms 读一次内核的只读开关 `kern.pong_wifi_gate`，为 1 才返回，
   wifid 的 main 才开始跑。wifid 本身一行没改。wifid 自己的初始化就是按"东西已经在了"写的，晚启动没有问题。
2. 开关在这两件事都发生后打开：configd 报告"标记已发、en0 已列出"（探针发 `wifi gate open`），电池已发布到
   IOKit（PongPower 发布时通知内核）；兜底是 wifid 第一次询问后 150 秒。这样 wifid 第一次调
   `SCNetworkInterfaceCopyAll` 时标记和 en0 都已经在那里，它不需要等 configd 任何东西；电池的来源是音频处理器，
   它在开机一分钟左右才起来——v239 里 wifid 从第一秒就在跑，是否干扰过这条链无法知道，这样至少把它排除了。
3. 放行后 60 秒内 wifid 没有向驱动发过任何请求 → 内核杀掉它，launchd 会重新启动它（库里不再等待）。每杀一次，
   所有等它的客户端都会收到"连接中断"而不是无限等。正常情况下放行后一秒内驱动就会看到它的第一批请求
   （类型 43 带 256 字节、12 带 19 字节、19、106）。
4. wifid 自己的日志：内核给 wifid 加环境变量 `OS_ACTIVITY_DT_MODE=YES`（os_log 写到进程的标准错误输出），
   那个小库把这个输出接到 `/private/var/logs/PongWifid-v241.txt`，每次启动保留放行后的前 120 秒。
5. 驱动侧（v240 已有）：每个请求带调用者进程名，wifid 的前 60 个请求全部记录。

**探针（configd）在 v241 里变小了**：只发布两个动态存储值——接口命名标记、把 en0 写进
`State:/Network/Interface`——然后发 `wifi gate open`。去掉了 v239/v240 里的两步：在 configd **进程内部**用
`SCPreferencesLock` 给 en0 建网络服务（锁的 服务者就是 configd 自己；上游的辅助进程当年就卡在这把锁上），
以及向 Wi‑Fi 管理器请求开机（configd 没有那个授权，旧式端口也没有被任务声明）。wifid 建立设备时会自己给接口建
默认配置；IPMonitor/DHCP 要的网络服务等关联能通了再用一个独立的、带授权的小进程补。

这一版**没有任何一项在手机上验证过**。它比 v239 多的是：wifid 在标记和 en0 都准备好之后才开始跑；wifid 不说话时
每分钟被打断一次；以及 wifid 自己的日志。

电量数字：v239 里消失的原因没有证明。SpringBoard、PowerUIAgent 的导入表里没有 Wi‑Fi 框架（弱证据，它们的框架在
共享缓存里）。v241-unified 里 wifid 根本不启动，回到 v238 的状态；v241-wifi 里 wifid 在电池发布之后才启动——如果
那里数字仍然没有，原因就不在 wifid，而在从闪存启动或 configd 的引导本身。

## 3. 蜂窝与通话：调研结论（`research/CELLULAR-22H374.md`）

- **CommCenter 在 18.7.10 上的内核接口和 16.5 一致**：服务 `baseband`（属性 `config` ≥ 24 字节、`class`），
  `AppleBaseband` 用户客户端的 27 个选择子（umi 实现的那 27 个编号、参数个数全部相同，另有 8 个落在 umi 的
  "不支持"默认分支里），`AppleBasebandPCIInterface` 的 18 个通道名和 13 个接口选择子，`AppleIPAppender` 选择子 16。
- **现成的驱动**：上游 Pong 的 `pong-baseband`（`Chengdong-Yu/pong-baseband`，公开，@817a285，本地在
  `thor-out/v161-rmtfs-staging/pong-baseband`，5.7k 行）就是 **SM8475 / X65** 的完整桥：PAS/SMEM/SMP2P/GLINK/QRTR、
  IPA5.1/GSI3.0、rmtfs、pdp_ip 接口。它的 README 记录了真机证据：SIM 识别、网络注册、IPv4/IPv6 数据、
  "仅蜂窝"的 HTTP 请求返回 200、IMS 注册；通话"仍然立即挂断"。11-17 曾为 v161 暂存过它，**没有接进现在的树**
  （v240/v241 里没有桥、没有 IPA、没有 `pdp_ip`，基带只是 24 秒的一次性试验，而且 ADSP/电池要等这次试验正常结束
  才启动）。它依赖上游 PongPower 的 `pong_modem_*` 接口，Thor 的 PongPower 已经分叉。
- **身份**：CommCenter 在用户态按 MobileGestalt 选协议栈——`HasBaseband` 必须为 true，再看 `HWModelStr` 查一张
  静态表：厂商 1 用 QMI（`libBasebandManager.dylib`，也就是这个桥会说的），厂商 2/3 用 ICE/ARI。上游是用
  iPhone17,2 的身份做通的；Thor 现在是 iPhone17,1。这台 iPad（iPad Pro M2 蜂窝版）自己的基带是 ICE18，
  22H374 里根本没有 "Mav17"——所以**蜂窝接入时要保持 iPhone 身份**，不能换成这台 iPad 的身份。
- **激活门仍在**（"Device is not activated! activationState="），BSP 指示里 ActivationState 的 TLV 编号是 0x01；
  ICCID/IMSI 的编号变了（0x16/0x13）。mobileactivationd 的钩子点还在。
- **短信**：Thor 的 v238 日志里有和 umi 一模一样的 `MessagesBlastDoorService` 尸体循环（每 ~10 秒一次）；根因
  同样是沙盒替身对 `sandbox_check` 不如实回答（umi 是内核里的小修补）。
- **通话：近期做不到。** 苹果的通话音频模型是 AP 与基带之间的 I2S/PCM 声码器；SM8475 上语音在基带和 ADSP 之间走，
  AP 没有到基带的 I2S，任何一棵树里都没有"苹果 IMS ↔ QMI IMS"的信令翻译。umi 没打通，上游 Pong 的电话也是
  立即挂断。

### 蜂窝的路线（按依赖顺序；每一步都需要手机验证）

1. 沙盒替身对带 `seatbelt-profiles` 授权的进程如实回答 `sandbox_check`（小，低风险）。
2. 基带常驻：READY 之后不再走"停止 + PAS 关断"；先解决"ADSP/电池要等试验结束"这个约束。
3. 主机服务补全：SM8475 的 MPSS 实际要哪些 locator 服务、TFTP、rmtfs 的语义——要看基带常驻后几分钟的日志。
4. 身份：确认 CommCenter 为 Thor 当前的 MobileGestalt 回答选的是 QMI 栈。
5. 把上游的 `pong-baseband` 移植到现在的内核。
6. 发布 `/baseband` 节点（`config` 40 字节、`class=3`），CommCenter 才会启动。
7. 激活、运营商包推送、IPA 数据面、短信转发。

这一轮没有动这些：第 2、5、6 步在没有手机的情况下盲做，风险远大于收益（会把现在稳定的电池/音频链也搭进去）。

## 4. v241 相对 v240 的改动清单

| 文件 | 改动 |
|---|---|
| `prepare/wallpaper/wallpaper241.c`（新） | 壁纸目录库 + 构建脚本 + Mac 上的对照测试 |
| `prepare/wifid/wifid241.c`（新） | wifid 的等待与日志库 + 构建脚本 |
| `prepare/probe/probe241.c`（由 probe240 改） | configd 里只留两个动态存储值和放行信号；去掉建网络服务和请求开机 |
| `bsd/kern/kern_exec.c` | 三个库的暂存与注入表（按进程名 + 完整路径核对）；`kern.pong_wifi_gate` 只读开关；wifid 启动登记与 60 秒看门狗；`exec_applog_oslog()` 对 wifid 返回真；v240 的 exec 内等待/拒绝删除 |
| `bsd/kern/kern_exit.c` | 诊断通道放行 chronod、CollectionsPoster、SpringBoard 的 "Pong Probe:" 行 |
| `device/scripts/build-xnu-ipados18.sh`、`build-kernel.sh` | 嵌入三个小库 |
| `osfmk/kern/thor_witness_v235.h` | V241 那一行 |
| `package-v241.py`（新） | 两个变体；默认镜像的设备树逐字节等于 v238 的 |

设置诊断库、控制台捕获库、UI 适配器与 v240 逐字节相同；启动参数与 v239/v240 逐字节相同（1005 字节）；启动栈
预算与 v240 相同（`v241-intake/boot-stack-budget.txt`）。源码差异：`v241-intake/kern_exec-v240-to-v241.diff`、
`kern_exit-v240-to-v241.diff`、`witness-v240-to-v241.diff`。

## 5. 刷写与取日志

刷写（在另一台电脑上，手机先进 fastboot，尽快执行）：

```
fastboot flash boot_a "boot-thor-unixnu-v241-unified.img" && fastboot reboot
```

要试 Wi‑Fi 就换成 `boot-thor-unixnu-v241-wifi.img`。这种刷法是 11-26 刷 v239 时用过的。

取日志：`v241-intake/thor-log-capture-v241.zip`（在另一台电脑上解开后 `bash capture.sh`，说明见里面的
README）。取回来后要看的行：

- `THOR WALLPAPER V241: …`、`PONG PROBE SpringBoard[…]: Pong Probe: wallpaper v241: …`
- `PONG PROBE PosterBoard` 的采样（它还空闲吗？有没有退出？）
- `THOR BATTERY V226: published`（电量链有没有走完）
- （wifi 镜像）`THOR WIFI V241: wifid may run: …`、`wifid asked the driver for something`、
  `wifi-net: native client type … from wifid[…]`、`PongWifid-v241.txt`
