# Thor v232 / v233：应用切换"定格"的取证分析与 v233 的内核取证

日期：2026-11-25。v232 于 13:14:07 临时启动（`fastboot boot`，未刷写），用户操作：点了几个图标（App Store、设置、天气、图书），再点设置后定格；13:18 回到 fastboot。日志于 13:37 用 TWRP 只读取出（`recovery-v232-full-20261125T053704Z`，1.3 GB，无写入）。

## 1. v232 这次启动确认了什么

| 项目 | 结果 | 证据 |
| --- | --- | --- |
| v231 的两个修复仍然有效 | 进桌面，内存 7 个 RAM bank | `PongConsole-v172.txt` 第 4 段（boot 855617CD） |
| PosterBoard 崩溃循环 | 消失（v231 36 分钟内 401 次） | 整段控制台没有 `WKWallpaperRepresentingCollectionsManager` |
| 控制台落盘（v232 新增 F_FULLFSYNC） | 有效：最后一行到内核 160.757 s，尾部无坏块 | 文件 mtime 160.754 s 与最后一行一致 |
| 蓝牙 | 上次 v231 已确认 HCI Reset / EDL 应答；固件分区 `bluetooth_a/b` 已取出 | `capture-stock-bt-20261125T051343Z` |
| Wi-Fi | 扫描到 48 个 BSS，一次扫描后不再关联 | `PongWifi: runtime state=1 scans=1 BSS=48` |

时间线（内核秒）：App Store 前台 126.6–129.1；设置 133.2–136.4；天气 137.6–141.9；图书 141.8 起；160.65 点击 → 160.835 **设置回到前台**、160.866 图书退后台 → 之后没有任何持久记录。

## 2. 定格的形状（v231 与 v232 相同）

| | v231 | v232 |
| --- | --- | --- |
| 最后一次前台切换 | 2200.191 s App Store 成为前台 | 160.835 s 设置成为前台 |
| 内核还活着的最后证据 | runningboardd 日志 2200.19 s | runningboardd 日志 160.866 s |
| 之后 | 控制台、日志、崩溃报告全部停止 | 同左 |

两次都是"某个应用被切到前台后不到 0.25 s，一切记录停止"。

## 3. 为什么一周来每次都抓不到现场（这次查清的原因）

1. **用户态控制台读取线程本身依赖会一起死掉的东西。** `console-v205.c` 在读控制台的同一个循环里调用 `notify_set_state` / `notify_post`（notifyd 的 Mach IPC）、写日志并做存储同步。前台切换恰好触发一次 `notify_post`（记录里每次前台切换都伴随 `thor-return-v214` 记录）。只要 notifyd、这个写盘或调度卡住，控制台文件就停在定格那一刻——所以"日志停在定格"不能说明内核死了。
2. **运行时的内核 panic 在这个移植上看不见。** `MiniIOMobileFramebuffer.cpp:3467` 在界面启动时执行 `PE_initialize_console(NULL, kPEGraphicsMode)` 和 `kPEReleaseScreen`，文本控制台从此关闭；panic 日志只写在易失的 RAM 缓冲里（`pong_volatile_panic_log`），没有持久化路径。开机阶段的 panic（v230）能看到文字，是因为那时文本控制台还开着。也就是说，**运行中的 panic 表现出来就是"画面定格"**。
3. 统一日志的时间戳在约 38 s 之后被压缩（内核 54 s 的事件标成 38.88 s，150 s 的事件标成 40.56 s），之前"统一日志 70 s 就停"的结论不成立——它没有停，只是时间不可用；它也只保存到约 145 s，没有覆盖定格。

结论：到目前为止没有证据区分"内核 panic / 内核硬挂起 / notifyd 或存储死锁 / 实时线程独占 CPU"这几种情况。

## 4. v233：让定格自己留下证据

镜像：`thor-out/v233-unified/boot-thor-unixnu-v233-unified.img`，95,084,544 字节，SHA-256 `fb9e2c3308cdf5c617e5e068e1b03059e3077e6cde3ee9e399c7e2a256af68c2`，内核 UUID `931CE35E-5278-3CF2-ADAC-EC0D5B18CC49`。驱动、设备树、启动参数（1005 字节）与 v232 完全相同；只换了内核、清单和菜单标题。

新增 THOR WITNESS V233（`osfmk/kern/thor_witness_v233.h`，钩子见 `prepare/witness.patch`，由 `prepare/make-witness-patch.py` 生成并验证可从 v232 逐字节复现）：

| 组成 | 做什么 | 依赖 |
| --- | --- | --- |
| panic 上屏 | panic 路径走到 `panic_spin_forever()` 时，用内核自带的 8x16 字体把完整 panic 日志分页画在屏幕上（每页 15 s 循环）；`PE_panic_hook` 时先画一行 panic 字符串 | 只需要面板帧缓冲 |
| 内核监视线程（优先级 95） | 用户态 3 s 没读控制台，或触摸后 3 s 没有新帧 → 生成报告并画在屏幕顶部：各 CPU 上的线程、线程状态统计、最集中的 10 个等待事件、SpringBoard/backboardd/runningboardd/notifyd 及最近前台应用的每个线程（状态、优先级、用户态 pc/lr、内核栈返回地址）、各 CPU 是否应答跨调用 | 不等锁、不写盘、不调用 notifyd |
| 内核控制台镜像线程 | 每 250 ms 把内核控制台复制到 `/private/var/logs/PongConsole-kernel-v233.txt` 并 fsync，报告也追加进去 | 只依赖存储 |

主机的渲染检查：`thor-out/v233-intake/render-test/`（用同一份代码和字体生成示例页，字形方向和换行正确）。启动栈预算：`v233-intake/boot-stack-budget.txt`（启动表 6447 项深度 10，kalloc 视图 1164 项深度 10，最坏 0x0a10/0x4000，余 13808 B）。

## 5. 上机后看什么

定格后屏幕只有三种可能：

1. **出现黑底白字的 "HANG n: …  PAGE x/y  KERNEL ALIVE" 横幅**（或触碰定格的屏幕后 3 s 内出现）：内核活着。每页显示 8 s 轮换，拍照；之后回 fastboot 取 `/private/var/logs/PongConsole-kernel-v233.txt` 里的完整报告。
2. **出现 "KERNEL PANIC … PAGE x/y"**：定格是内核 panic。每页 15 s，逐页拍照。
3. **什么都不出现**（触碰并等 10 s 仍然没有）：内核自己停了，定格前的控制台仍会由镜像线程留在盘上。

回 fastboot 的方法不变：音量下 + 电源。等待脚本会在手机回到 fastboot 时自动只读取日志并停在 fastboot（`v233-intake/auto-collect.sh`）。

## 6. 这次顺带修好的取日志流程

13:18 等待脚本在手机刚进 fastboot 4 s 时上传 TWRP，USB 在 123 MB 传到约 33 MB 时断开；手机的 fastboot 于是停在"等待下载数据"的状态，对任何命令都不应答（`fastboot getvar` 永远 `< waiting for … >`）。主机 USB 日志（`log show --predicate 'sender CONTAINS "AppleUSB"'`）给出了过程。解决办法 `session-tools/fbprobe`（libusb，绕过 fastboot 工具）：`fbprobe info`、`fbprobe cmd "getvar:product"`、`fbprobe fill <字节数>`——用全零字节把中断的下载补完，设备回到 OKAY 后恢复正常。`v233-intake/collect-full.sh` 把这些步骤串起来：限时调用 fastboot、不应答就补齐下载、TWRP 只读全量快照、再跑一遍历史取证、回 fastboot。

查看控制台的脚本：`session-tools/console_sessions.py <控制台文件> --boot <UUID前8位> --tail N | --exclude 正则 | --from-time 秒 | --grep 正则`。
