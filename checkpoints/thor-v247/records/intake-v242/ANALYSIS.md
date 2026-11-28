# v242：v241 为什么进不了系统、电量数字与 Wi‑Fi 丢失的真正原因、v242 改了什么

2026-11-27 上午。v241‑unified 09:21 刷进 `boot_a` 后手机一直在循环显示内核日志，进不了系统；同一次取回的日志还解释了
v239 起电量数字消失和 Wi‑Fi 不可用的原因。v242 修这两件事，10:51 刷进 `boot_a`。
**v242 在主机上打包、校验过；手机上的结果要等日志取回（`v242-intake/auto-collect.sh` 已挂好）。**

| 文件 | 字节 | SHA‑256 |
|---|---|---|
| `thor-out/v242-unified/boot-thor-unixnu-v242-unified.img` | 95,375,360 | `07d615c79a0ef81ea1aa56086c0daf048bd66f165de2ea6af03c38e16579d581` |

内核 UUID `91842275-FF55-37A3-AC72-E18AC950F4C3`（v241：`5874B07A-A7B8-3341-8673-159073AD7E1A`），内核 SHA‑256
`e3155a3934a7521a397a2145458cc2bb57fee19545e3a75d2e38c587e4dfa2b5`。启动参数与 v239–v241 逐字节相同（1005 字节）；
设备树与 v241‑unified 相同（v238 的，没有 `wlan` 节点，wifid 不启动）；启动栈预算通过（空闲 14096 / 13808 字节）。
记录：`v242-unified/unified-verification.json`。

## 1. v241 为什么进不了系统

v241 的控制台（`v241-intake/logs/console-v241-kernel.txt`）：

```
[   30.601306]: THOR WALLPAPER V241: SpringBoard catalog library armed
[   30.606136]: THOR APP ROLE V205: at_us=30599822 pid=5 foreground=1 name=SpringBoard
[   30.638741]: SpringBoard[5] Corpse allowed 1 of 5
[   35.881023]: THOR WALLPAPER V241: SpringBoard catalog library armed
[   35.894809]: SpringBoard[80] Corpse allowed 2 of 5
```

内核每次把 `libpong-wallpaper-v241.dylib` 放进 SpringBoard，SpringBoard 都在 10–40 毫秒内死掉（20 次），chronod 同样（21 次）；
没有桌面、没有锁屏进程，屏幕上只剩下内核日志在滚。PosterBoard 和 CollectionsPoster 一次都没轮到。

原因：我给这个库手写的 MobileGestalt 桩文件里，install name 写成了
`/System/Library/PrivateFrameworks/MobileGestalt.framework/MobileGestalt`——这个路径是我按框架的惯例猜的，系统里不存在。
系统里真正的名字是 **`/usr/lib/libMobileGestalt.dylib`**（从恢复镜像里取出的这个库的 install name；苹果自己的
`/usr/sbin/wifid` 等二进制也是按这个名字链接的）。dyld 装入被注入的库时找不到它依赖的这个库，进程在跑任何初始化之前
就中止了。日志里没有 dyld 自己的那一行（它在进程的标准错误输出里，而捕获stderr 的那个库同样还没来得及运行），所以这条
因果是从"每次都在同一处立刻死 + 这个依赖路径不存在"推出来的；v242 的日志里 SpringBoard 不再死，才算在手机上坐实。

Mac 上的对照测试为什么没拦住：测试用的假 MobileGestalt 是按桩文件里那个（错的）名字生成的，名字自洽，测试通过。

## 2. 电量数字和 Wi‑Fi：固件 LUN（LUN 4）的分区表检查

三个启动的同一处（行号见各自的 console 文件）：

| 启动 | 方式 | `LUN5 GPT verified` | LUN 4 的检查 | 之后 |
|---|---|---|---|---|
| v238 | 临时启动 | 709.101 s | `THOR MODEM FW V163: state=gpt-verified lun=4 slots=2` 710.425 s | `AMSS20.BIN state=read bytes=5296128`；`THOR BATTERY V226: published source=pmic-glink percent=100` 771.568 s |
| v239 | 从闪存启动 | 15.516 s | `THOR MODEM FW V163: state=gpt-unavailable status=5` 15.542 s | `firmware-2.bin unavailable rc=5`、`amss20.bin unavailable rc=5`；`WiFi initialization stopped` 16.357 s；`THOR BATTERY V226: stop=no-adsp-edge peer=0 published=0` 43.043 s |
| v241 | 从闪存启动 | 15.950 s | `state=gpt-unavailable status=5` 16.174 s | 同上（16.604 / 16.779 / 43.517 s） |

所以从闪存启动的两次启动里：

- **Wi‑Fi 固件读不到**（`amss20.bin unavailable rc=5`），驱动根本没有启动，没有 en0。这发生在 wifid 启动（约 30 秒）
  之前——与 wifid 无关。v240/v241 里对 wifid 的推理不是电量症状的原因。
- **固件启动工序没有被安排**（它只在 LUN 4 的检查通过后安排）→ 没有调制解调器的试运行 → ADSP 不启动 →
  `THOR BATTERY V226: stop=no-adsp-edge` → 电量永远不发布。v239 里状态栏电量数字消失就是它。

检查本身（`modemFirmwareGptVerifyLocked`，`device/drivers/NativeUfs/ModemFirmwareAccess.h`）每回启动只做一次：
TEST UNIT READY、READ CAPACITY、读主/备份 GPT 头和分区表项，逐项与固定的 LUN 4 几何比较。返回 5（EIO）说明头两条
命令和容量是对的（否则是 6，ENXIO），失败在后面的读或数据比较上——但旧代码不记录是哪一步、设备说了什么。
两次失败用时不同（v239 在上一行日志之后 0.76 毫秒，v241 11 毫秒）。

### 不是"从闪存启动就会失败"（2026-11-27 11:30 追记，翻遍本机保存的全部控制台之后）

把 11 个快照里的控制台文件按启动切开（`kprintf initialized` 开头一段），每一段看第一行的时间戳、驱动版本标记和这次检查的结果：

| 内核 | 启动方式 | 启动数 | LUN 4 的检查 |
|---|---|---|---|
| v202 B（在 boot_a 里的旧日常镜像：标记 `CONSOLE:202`，没有 WLAN/ADSP/见证） | 从闪存 | 8 | **8 次通过** |
| v203–v238（每次一个不同的版本） | 临时启动 | 34 | 全部通过 |
| v239（3 次）、v241（1 次） | 从闪存 | 4 | **4 次失败** |
| v239 以后的任何内核 | 临时启动 | 0 | 没有试过 |

"从闪存"是推出来的：这 8 次启动都在计数器复位后 10.1–10.8 秒开始（和 v239/v241 的 10.2–10.5 秒同一类），而脚本做的
临时启动在 14.5–18 秒、手动的更晚；并且这 8 次都是同一个内核，而临时启动每次都是不同的版本（另有 1 次同一类内核在 18.4 秒开始的启动，也通过）。另外两点：
从正在运行的系统按音量下 + 电源强制重启，计数器**不会**复位（11-25 一次 55 分钟的 v202 B 启动之后，下一次临时启动
的第一行时间戳是 3337 秒）——所以时间戳只能区分"复位/关机后"与"强制重启后"。

v238（临时启动，通过）和 v241‑unified（闪存，失败）之间：启动参数逐字节相同、设备树相同（SHA‑256 都是 `a8164f4d…`）、
`/chosen` 选择器相同、存储驱动的源码相同（v236–v241 四个文件哈希一致）、内核源码在检查（启动后 5.6 秒）之前没有任何
相关的改动。所以剩下的可能只有两种：

1. 新内核（v236 以后的某处改动）只在从闪存启动的条件下出问题；
2. 11-25 之后手机上有什么变了（最后一次记录到的 v202 B 从闪存启动在 11-25 上午；之后到 11-26 晚全是临时启动）。

**v242 的日志是第一个能分开它们的证据**：失败在哪一步、设备回了什么、14 次重试里有没有一次通过。如果 14 次都以同一步
失败，下一步是**临时启动同一个 v242 镜像**（`v242-intake/boot-temp-v242.sh`，不刷写；之后挂 `auto-collect-temp.sh`，
快照标签 `v242temp`）：同一个内核、同一个镜像，只换启动方式。临时启动通过 → 是第 1 种；临时启动也失败 → 是第 2 种
（或者与启动方式无关的内核问题）。

## 3. v242 的改动

**壁纸库（修正）**：`libpong-wallpaper-v242.dylib`（86,304 字节，`otool -L`：
CoreFoundation、**`/usr/lib/libMobileGestalt.dylib`**、libobjc、libSystem）。逻辑与 v241 相同（见 `v241-intake/ANALYSIS.md`
第 1 节），只改了依赖名字和版本字符串。源码差异：`diffs/wallpaper241-to-wallpaper242.c.diff`、`diffs/wallpaper-tbd.diff`。

**固件 LUN 的检查（重试 + 记录）**：`ModemFirmwareAccess.h`（`diffs/ModemFirmwareAccess-v241-to-v242.diff`）

- 每次失败记录：失败的步骤（1–12）、该命令的 IOReturn、SCSI 状态、sense、残余长度、协议错误码、OCS、读的 LBA、
  读回来的前四个字节、具体是哪一项比较不过。
- 检查失败后在自己的线程调用上重试，间隔 20、50、100、200、400、800、1600、3200、6400、12800、25600、51200、102400 毫秒
  （共 14 次，约 205 秒）；每次失败一行 `THOR MODEM FW V242: preflight attempt N failed status=… step=… …`。
- 第三次失败后，在主机锁内再向设备发一次 START STOP UNIT（工作电源模式，与传输层启动时发的同一条），然后立刻再试。
- 无线驱动读固件前等这张分区表，最多 45 秒（`THOR WLAN V242: the partition table of the firmware LUN answered at attempt …`）。
- 检查通过后的流程不变（`THOR MODEM FW V163: state=gpt-verified …`，27 秒后校验 Wi‑Fi 固件，30 秒后加载调制解调器，
  之后 ADSP 和电量）。

**打包脚本**：`package-v242.py` 把每个被注入的库的每一条 `LC_LOAD_DYLIB` 与苹果自己的二进制
（launchd、wifid、configd、SpringBoard、PosterBoard、chronod、contextstored）使用的 install name 对照，名字对不上就拒绝打包。
壁纸库的 Mac 对照测试改成按真实名字链接。

其余与 v241‑unified 逐字节相同（探针 v241、设置诊断 v185、控制台 v205、UI 适配器、Metal 桥）。`kern_exit.c` 没变。

## 4. 验证到哪一步

主机上做过：内核重编；打包脚本的全部断言（见证字符串、父镜像、启动参数、启动栈预算、依赖名字对照、壁纸对照测试）；
镜像哈希。

**手机上还没看到**：SpringBoard 和 chronod 是否不再崩溃；WallpaperKit 是否接受修正后的库、壁纸是否出来；
LUN 4 的检查在第几次成功、还是 14 次都失败（失败在哪一步、设备怎么说）；电量和 Wi‑Fi 是否因此回来。

## 5. 刷写与取日志（这台 Mac）

```
thor-out/v242-intake/boot-temp-v242.sh                               # 临时启动同一个 v242 镜像（不刷写），用于与从闪存启动对照
thor-out/v242-intake/auto-collect-temp.sh                         # 临时启动之后挂的取日志器，快照标签 v242temp
```

```
thor-out/v242-intake/flash-fastboot.sh <紧凑镜像> <sha256>     # 等链路稳定 30 秒 → 核对哈希、槽位 a、已解锁、boot_a 大小 → 刷 → 重启
thor-out/v242-intake/keepalive.sh                               # 编译期间每 15 秒问一句只读的查询，免得 fastboot 自己退出
thor-out/v242-intake/auto-collect.sh                             # 手机下次进 fastboot 时自动 RAM 启动 TWRP 只读取日志
python3 -I thor-out/v242-intake/split.py <新快照> <上一次的快照> <输出>   # 把这一次的启动从控制台镜像文件里切出来
```

`fastboot fetch` 不支持，所以没有回读。v242 的刷写记录：`v242-intake/flash-fastboot.log`（10:50:19 开始，10:51:23 重启）。

## 6. 取回日志后要看的行

- `THOR WALLPAPER V242: SpringBoard catalog library armed` 应只出现一次；`SpringBoard[…] Corpse allowed`、
  `crashed: SpringBoard`、`crashed: chronod` 不应再出现。
- `PONG PROBE SpringBoard[…]: Pong Probe: wallpaper v242: WallpaperKit at …`、`"GSDeviceName" answered with iPad …`。
- `THOR MODEM FW V242: preflight attempt …`、`THOR MODEM FW V163: state=gpt-verified`（或 `THOR MODEM FW V242: state=gpt-unavailable … after 14 attempts`）。
- 这一次启动的第一行时间戳（`kprintf initialized` 之前几行）：从闪存启动应在 10–11 秒之间。
- `THOR WLAN V229: firmware file=AMSS20.BIN state=read`、`PongWifi: firmware amss20.bin bytes=5296128`。
- `THOR MODEM FW V174: slot=a state=scheduled`、`THOR MODEM FW V186: stream=delivered`、`THOR BATTERY V226: published`。
- `PONG PROBE PosterBoard` 的采样（有壁纸之后它还空闲吗、退出了吗）。

## 7. 还没有做的

- v241‑wifi / v242‑wifi（带 `wlan` 节点、wifid 被扣住等放行）还没有在任何一次启动里试过；要等 LUN 4 在从闪存启动时可靠了再试。
- 探针在 configd 里无条件把 en0 列进动态存储；没有 en0 的启动里应当不列（下一次改）。
- 蜂窝、通话：调研结论见 `v241-intake/ANALYSIS.md` 第 3 节，没有新的实现。
