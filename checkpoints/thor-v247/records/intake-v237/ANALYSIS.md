# v237：设置"关于"、壁纸、Wi‑Fi、蜂窝

2026-11-26。v236（定格修复）12:48 在手机上临时启动，到写这份记录时还没有回过 fastboot，所以
**v236 的设备结果仍然未知**，日志要等手机回到 fastboot 由 `v236-intake/auto-collect.sh` 取回。
v237 是在 v236 之上再做四件事，镜像 `v237-unified/boot-thor-unixnu-v237-unified.img`。
所有结论都来自读代码和已有的 v234/v235 日志，**没有任何一项在手机上验证过**。

启动参数与 v236 逐字节相同（1005 字节），设备树没动，FAT 里只有内核、清单、菜单标题三项变了。

## 1. 设置 → 通用 → 关于：卡死

### 证据

v234 的看门狗报告（`CrashReporter/Preferences-*.ips`，0x8BADF00D，一次开机里 3 次）：

- 主线程在 `PSGAboutDataSource _loadValues` 里等一把 unfair 锁；
- 锁的持有者是线程 `com.apple.SettingsApp.PrimaryListLoading`，它停在一次同步 XPC 里。

这次用 `session-tools/macho_calls.py` 把设置主程序（`Preferences`，UUID c5b985a1-…）卡住的那个函数
（`0xb950..0xbb98`）反汇编出来，返回地址 `0xba1c` 前一条调用是：

```
b9ec: ldr x0, [x8, #0x568]   ; PSSystemConfigurationDynamicStoreEthernetWatcher（类引用）
b9f8: ldr x1, [x8, #0x620]   ; 选择子 sharedManager
b9fc: bl _objc_msgSend
ba14: ldr x1, [x8, #0x998]   ; 选择子 hasEthernetNetworkInterfaces
ba18: bl _objc_msgSend       ← 卡住的线程停在这个调用里（返回地址 0xba1c）
```

也就是 `[[PSSystemConfigurationDynamicStoreEthernetWatcher sharedManager] hasEthernetNetworkInterfaces]`
这次调用再也没有返回。对端是谁日志里看不出来。

### 改动

注入到设置里的诊断库（`libpong-settings-v185.dylib`）加一个文件 `about237.c`：

1. 进程启动后把这个类的实例方法 `hasEthernetNetworkInterfaces` 换成直接返回 NO（这台设备没有以太网）。
   先用 `class_addMethod` 在这个类上盖一层，这样即使方法定义在父类里，也不会改到父类。
   类找不到时在 1、3、8、20 秒各重试一次。
2. 一个主队列看门狗：每 400 ms 往主队列投一个空块，连续 3 秒没被执行就抓一次全线程快照
   （最多 3 次，间隔 3 秒），把"栈里有 Settings 或 PreferencesUI 帧"的线程按 `镜像+偏移`
   打出来——内核 stackshot 里只有裸地址，这份在进程内用 `dladdr` 解析。

源码链：`prepare/settings/base/`（v225 的基线）+ `settings.patch`（v225–v236 的库）+ `about237.patch`
→ `libpong-settings-v185.dylib`（115440 字节，SHA-256 82bb1690…，UUID C0590443-…）。
`prepare/settings/replay.py` 会从基线重新构建并逐字节比对 v224、v236、v237 三个库。
在 Mac 上用同名类做了主机测试：自有方法被替换、继承方法被覆盖（父类不受影响）、参数个数不对的方法不动。

### 日志里看什么

- `THOR SETTINGS V180: staged /private/var/pong/libpong-settings-v225.dylib bytes=115440`（每次开机都会重写这个文件）
- `PONG SETTINGS[pid]: Pong Settings: about237 hasEthernetNetworkInterfaces answers NO on this device (replaced)` 或 `(override added)`
- 如果"关于"仍然卡：`Pong Settings: about237 hang=1 threads=… shown=…` 和随后的 `hang=1 tid=… name=… frames=…: 镜像+偏移 …`

## 2. 壁纸

### 证据一：内核拒绝打开保护类文件

v235 内核日志里 94 行：

```
apfs_is_valid_class:2452: disk5s2 rejecting class open (class N) because we're not content protected
```

`disk5s2` 是数据卷，挂载时写明 `handle_mount:829: … (unencrypted; flags: 0x1; features: 8.0.2)`。
class 2（B）在 72.57–98.97 s 之间零星出现，class 1（A）在 90.32 s 一次 44 条、90.65 s 10 条
——PosterBoard 84.22 s 启动后 6 秒。

把预链接进内核的 APFS 扩展反汇编（新工具 `session-tools/kc_kext_dis.py`，用 `kernel/prelink-thor.json`
的绑定记录把扩展里的桩解析成内核符号名）：

```c
bool apfs_is_valid_class(struct mount *mp, int class, bool isdir, vfs_context_t ctx)
{
	if (isdir ? class == PROTECTION_CLASS_F : class == PROTECTION_CLASS_DIR_NONE) return false;
	if (!cp_is_valid_class(isdir, class)) return false;
	if (class == PROTECTION_CLASS_E && <卷角色>(mp) == 3) return false;
	if (<卷标志> & 9) return true;                       /* 卷是受内容保护的 */
	if (class == PROTECTION_CLASS_A || class == PROTECTION_CLASS_B)
		return proc_ignores_content_protection(vfs_context_proc(ctx));   /* 只有带这个策略的进程能开 */
	return true;
	/* 否则打印上面那行，返回 false */
}
```

这个端口的数据卷没有加密，卷标志那两位是 0，所以 A、B 两类文件只对"忽略内容保护"的进程开放，
PosterBoard 和其它普通进程一律被拒。整个镜像里只有 APFS 这一个组件引用 `_proc_ignores_content_protection`。

### 改动

`bsd/kern/kern_proc.c`：`proc_ignores_content_protection()` 对所有进程返回 yes，第一次被问到时打一行日志。
文件本身在盘上是明文（卷没加密），所以读出来的就是写入的内容。

### 证据二：PosterBoard 第一次调用 IOSurface 就被拒

同一份日志里，PosterBoard 启动后 30 ms：

```
MiniIOSurface: selector 21 not implemented (scalars 4 in, 0 out; structure 0 in, 0 out)
```

选择子 21 是 `SetOwnership`（端口自带的 IOSurface 替身 `MiniIOSurface.cpp` 的枚举里写着），替身没实现，
返回 Unsupported。选择子 44 `SetOwnershipIdentity`（4 个标量：表面 id、任务身份令牌、账本标签、选项）已经实现。
21 同样是 4 个标量，按"和 44 一样、只是任务身份令牌换成任务端口名"来实现，前 24 次调用把四个值
和调用者打进日志——如果这个理解不对，日志会直接显示出来，不用再猜。

### 日志里看什么

- `THOR FS V237: protection classes A and B open for every process (the data volume is not content protected)`
- `apfs_is_valid_class:2452 … rejecting class open` 应该不再出现
- `MiniIOSurface: set-ownership V237 sid=… token=… tag=… options=… caller=… result=…`（result=0 才是成功）
- `CrashReporter/` 里有没有 `PosterBoard-*.ips`

两处都只是"与壁纸相关的、内核里看得见的拒绝"，壁纸是否因此出现，要看了才知道。

## 3. Wi‑Fi：关联代码一直在，只是从来没有人调用它

调查结论（`Explore` 子代理，只读；关键点我抽查过）：

- 驱动 `PongWifi` 里**认证、关联、四次握手、装密钥的代码都有**：`build-tree/build/pong-ath11k/nw_sta_pong.c`
  （`start_connect`、`send_auth`、`send_assoc`、`handle_eapol`、`install_key` → `WMI_VDEV_INSTALL_KEY_CMDID`）。
- 它唯一的入口是 `nw_wifi_connect()`，只被 ioctl 的 `kAssociate`（类型 20）调用。系统的 Wi‑Fi 进程只通过
  端口的用户客户端发过三种**读取**请求（v235 日志 66.65 s 和 66.82 s：类型 43、12、1），从未发过加入请求。
  所以一次关联都没发生过。
- `en0` 是真正的 ifnet，关联成功后 `post_link(true)` 置 `IFF_RUNNING`、发 `KEV_DL_LINK_ON`，DHCP 报文有完整的计数和日志。

### 改动

- `device/scripts/build-pong-wifi-native.py`：恢复 Fold 2 参考里的做法，从环境变量
  `PONG_WIFI_TEST_CONFIG` 指向的私有文件（`{"ssid": …, "psk": …}`）生成 `PongWifiTestInputs.h`。
- `PongWifiPlatform.cpp`：加 `joinNetwork()`，在网络起来后每个循环调用一次：
  只加入扫描列表里出现过的 BSS；失败或 60 秒没结果就隔 10–15 秒重试，找不到就做定向扫描；
  不打印名字和密码。`/chosen` 的 `thor-wifi-join = 0` 可以不重编就关掉。
- `prepare/env.sh`：`PONG_WIFI_TEST_CONFIG=thor-out/wifi-test-private.json`（这个文件不存在）。

**这个镜像里没有配置任何网络**，所以 Wi‑Fi 不会有任何变化：长度是编译期常量 0，连接部分的代码
根本没被编进来，日志里只有一行
`PongWifi[6]: V237 association fixture: no private network is configured for this build, nothing to join`。
要真正关联，需要 Wi‑Fi 名称和密码（见最后一节），然后重编、重打包。

## 4. 蜂窝：基带其实每次都启动了

调查结论纠正了我之前的印象：v235 里基带**启动成功过**（v228–v235 每次开机都是）：
74.9 s 加载固件 → 77.79 s `AUTH_AND_RESET` 被接受 → 77.916 s `ready-wait state=ready elapsed_ms=122`
→ 随即 `THOR SERVE V228: smem partition rc=-2` → 停止、关断。这是一次性的试验，停掉是设计如此。
日志里的 `auth=blocked`、`diagnostic_only=1` 是常量，不是算出来的。

唯一失败的那一步是个一行错误：`ThorMssServe.inc` 查 APPS↔基带的共享内存分区时传的是
`kModem`（= 0，基带的 QRTR 节点号），而基带的 SMEM 主机号是 1（`QcomSmem.c` 里其它三处都用 1）。

### 改动

- `ThorMssServe.inc`：查分区用 `kSmemModemHost = 1`，并打一行 `THOR SERVE V237: partition lookup apps=0 modem-host=1 rc=…`。
  这之后的代码（GLINK 通道、QRTR、给基带提供 rmtfs 文件、两条只读 QMI 查询）是第一次在设备上运行。
- 试验上限从 60 秒改成 24 秒：音频处理器和电池服务要等它结束才启动（v235 里是 78.2 秒），
  否则电量百分比会晚一分钟才出现。只读查询从 20 秒提前到 12 秒发出。
- **这段代码第一次在设备上跑，所以启动镜像之前做了一次独立审查**（`review.md`：我先读了一遍，
  再让一个只读的审查代理独立过一遍）。结论是可以跑；按它的建议加了三处小保护：
  共享内存的"空闲位置"如果超出分区就拒绝遍历（`QcomSmem.c`）、安全调用的锁不在忙等睡眠时占着、
  应答构造器每次写之前检查缓冲区边界（都在 `ThorMssServe.inc` / `QcomSmem.c`）。
  另外改了一处会让试验提前结束的逻辑：采样时基带恰好在分配共享内存会返回"不稳定"，
  原来任何错误都算"基带不见了"并结束试验，现在只有条目真的消失才算。
  基带服务的原有主机测试（44 项，带地址/未定义行为检查）在 v237 的源码上全部通过（`serve-tests/run.sh`）。
  如果它仍然在试验里 panic（线程 `thor_pas4`，开机约 100 秒），下一版用设备树属性 `thor-rmtfs-serve=0` 把它关掉。

### 日志里看什么

`THOR SERVE V237: partition lookup … rc=0` → `glink start … channel=1` → `qrtr ctrl type=2 from 0:…`（基带的 HELLO）
→ `announced locator=1 … rmtfs=1` → `assign buffer=… x0=0` → `rmtfs open '/boot/modem_fs1' … rc=0`
→ `read-only queries sent dms=1 uim=1` → `THOR SERVE V228: final=…`。之后仍是原来的停止、关断流程。

到"蜂窝数据能用"还有大约 9–13 个阶段：基带完成初始化、常驻启动、NAS 注册、主机侧服务、
IPA/GSI 数据面（没有代码）、CommCenter 桥（没有代码）、激活、短信沙盒、稳定性。

## 核对记录

- 四处改动都编进了新内核：`THOR WITNESS V237`、`THOR FS V237`、`THOR SERVE V237`、`V237 association fixture`、
  `MiniIOSurface: set-ownership V237` 在 v236 里都是 0 次；新的设置库在内核里恰好 1 份，v236 的 0 份。
- 启动栈预算与 v236 相同：两张启动表的 qsort 深度都是 10，剩余 14096 / 13808 字节（`boot-stack-budget.txt`）。
- `package-v237.py verify` 通过（要在 `v237-unified/` 目录里运行），镜像 95105024 字节，
  SHA-256 `ee4a590a7b59a7ee8bdba821f19555d6c3514a54eb5481a0f37774a722f26f50`（`image-sha256.txt`）。
- 在 Mac 上跑过的：设置库的两段新代码（`v237-unified/prepare/settings/hosttest/run.sh`）——
  用同名类验证了"自有方法被替换、继承方法被覆盖、参数个数不对的不动"；把主线程堵住 4.5 秒，
  看门狗在 3 秒阈值后打出一份带 `镜像+偏移` 的线程快照，主线程恢复后不再重复。

## 接下来

手机回到 fastboot 后：先取 v236 的日志，再自动临时启动 v237（`v237-intake/auto-boot.sh`），
v237 的日志由 `v237-intake/auto-collect.sh` 在下一次回到 fastboot 时取。
基带服务那段代码是第一次在设备上跑，启动之前做过审查，结论和加的三处保护记在 `review.md`
（标记文件 `review.ok` 已写）。两个等待脚本只在本次会话开着的时候有效。

v237 起来之后值得看的：设置 → 通用 → 关于能不能打开；桌面和锁屏有没有壁纸；
状态栏的电量百分比是否比以前晚大约 20 秒出现（基带试验变长的代价）。

Wi‑Fi 要有变化还需要一个私有的网络文件，例如
`printf '{"ssid":"<名称>","psk":"<密码>"}' > thor-out/wifi-test-private.json && chmod 600 thor-out/wifi-test-private.json`，
然后在 `v237-unified/` 里 `bash prepare/build.sh`、重新打包并 `v237-intake/record-image.sh`。
