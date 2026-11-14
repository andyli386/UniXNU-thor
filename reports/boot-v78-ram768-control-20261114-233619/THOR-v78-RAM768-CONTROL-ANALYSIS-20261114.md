# Thor v78 768 MiB 对照包 · 视频与 Recovery 联合分析

2026-11-14 北京时间。测试对象为 `boot-thor-v78-ram768-control.img`。本轮在真机恢复了 **768 MiB**，视频拍到由启动文字直接切到 SpringBoard 主屏。内核首次可见帧为 **178.854 秒**，比同 FAT 的 665 MiB 版早 **52.865 秒**。本轮只验证了 control。阶段 B 的诊断镜像需要先修正最终 EFI 内存图的输出路径。

## 1. 视频与主机记录

Mac mini 原文件 `/Users/<user>/abc/111/微信视频2026-11-14_233233_684.mp4`，354,024,465 字节，1080×1920 竖屏，约 30 帧/秒，161.415 秒。下表采用视频帧 PTS。i386 USB 事件另外记录了 23:25:24 离开 Fastboot，23:31:59 枚举为 Recovery。视频从 UniXNU 菜单已经在倒计时开始，文件日期不能当作 Fastboot 提交时间。

| 视频时间 | 画面或阶段 |
| --- | --- |
| 0.000 秒 | UniXNU 菜单，默认 iPadOS 18 条目约剩 7 秒倒计时。这个倒计时属于引导菜单。 |
| 6.400 秒 | 最后确认无内核文字覆盖的菜单画面。 |
| 6.433 秒 | 开始可见 XNU 文字覆盖。6.500 秒已是完整文字控制台。 |
| 7.469 秒 | 控制台出现 APFS OS、SystemOS、Data 建立及装载 `/sbin/launchd` 的文字。 |
| 158.983 秒 | 仍为完整文字控制台。 |
| 159.016 秒 | 相邻下一帧首次清楚出现 SpringBoard、Settings 图标和底部 Dock 区域。 |
| 161.415 秒 | 视频结束。主屏至少在最后约 2.40 秒内持续可见。 |

从第一帧出现 XNU 文字到第一帧主屏约 **152.58 秒**。内核在 L121313 记录释放启动控制台，在 L121315 记录 backboardd 第一帧软件合成提交。视频从出现装载 launchd 到显示主屏耗时 151.547 秒，内核对应间隔 151.533 秒，差约 14 毫秒，小于一帧。视频与内核时基相差约 19.84 秒。扫描全部 4,842 个解码帧的中央画面文字特征，并复核 33 个全分辨率 OCR 样本、异常帧和切换相邻帧。未见独立的 Apple 标志或迁移进度条画面，最后两帧之间也没有完整的黑帧。开头的 UniXNU 标志、倒计时和最后的圆角 Dock 都是不同的界面元素。视频只建立主屏已显示，当前包的 `pong_touch=0`，本轮未验收手指交互。

## 2. Recovery 只读证据

采集目录在 i386 的 `/home/vincent/Projects/thor-unixnu/PongBoot-pull-v78-ram768-control-20261114-233619/`。Recovery UUID 为 `efb6ef80-5761-4743-b1b1-f2294fe3d836`。23:36:19.910 开始，23:40:34.055 完成 Data 窗口读取，共 8,591,003,648 字节。读前后写入和丢弃计数一致。离线导出 331 个文件，18,177,733 字节，零错误、零跳过。Recovery `pstore` 为空，`logfs` 的只读副本未检出相关关键字。

新 `PongConsole.txt` 的前 15,698,914 字节逐字节等于上一轮 665 MiB 版。分析新增的 1,029,474 字节，略过边界残行 45 字节，对旧采集器重复回放的相同行去重，保留原始行号。新段有一组新启动内存日志，延续至内核时钟 343.151 秒。主机没有该次 Fastboot 命令回执，归属由用户现场确认、USB 时间和新段实际内存交接交叉验证。

实际交接三段分别为 `0x800004000 + 0x10000000`、`0x800000000 + 0x4000`、`0x810004000 + 0x1fffc000`。合并后为 `[0x800000000,0x830000000)`，共 805,306,368 字节，内核保留前 **768 MiB**，使用 4 KiB 页。中间的 `0 MiB` 是 16 KiB 段的整数取整。终点正好止于 HYP 起点。没有 `0x840000000` 起始或更高的新增 bank 交接记录。

## 3. 三轮对照

下表为各轮内核启动时钟，单位为秒。

| 事件 | v77，768 MiB | v78 root-fix，665 MiB | 本轮 control，768 MiB | 本轮 Console 行 |
| --- | ---: | ---: | ---: | ---: |
| 根卷 UUID 匹配条件 | 26.521723 | 26.842190 | 26.981675 | 117101 |
| 找到 BSD 根卷 | 26.642230 | 26.961793 | 27.090160 | 117151 |
| Data 可写挂载 | 26.873904 | 27.183358 | 27.320417 | 117237 |
| XNU 请求装载 launchd | 26.875808 | 27.184237 | 27.321292 | 117238 |
| launchd 第一条启动任务 | 40.810602 | 41.164107 | 41.280953 | 117281 |
| 首次可见帧 | 164.982383 | 231.719470 | 178.854084 | 121313 |
| 装载 launchd 到首帧 | 138.106575 | 204.535233 | 151.532792 | — |

本轮根卷 UUID 匹配到 BSD root 仅 0.108 秒，正确匹配 `B07DF049-7AD9-47B5-9F8F-9EE728056398`。OS 根卷绑定通过，SystemOS 只读 union 挂载通过，Data 在 `/private/var` 可写。首帧比同 FAT 的 665 MiB 轮快 52.865 秒，比旧 v77 同容量轮慢 13.872 秒。control 与 665 MiB 包内核、ADT、TOML 和完整 FAT 都一致，替换为既有 v77 FD 是受控的镜像改动。每组合各一次，Data 的运行状态也会积累，速度差尚需重复试验确认。

- SpringBoard 自身的客体时钟显示，主线程回调从入队到进入由上一轮 169 秒降为 123 秒，接受主屏请求从 t224 提前到 t174。旧 v77 分别为 102 秒、t160。control 的主线程 t112 栈在 Home 搜索控件的 Bundle 资源加载，t173 在 UIKit 焦点系统和场景连接。两次采样使用 Mac 上同版 22H374 dyld 缓存，当前日志缺少 cache UUID。看到的是初始化中的两个检查点，无法证明某个函数一直阻塞。
- 三轮都预读 13 段、318.516 MiB。各段 read 耗时之和，v77 为 33.724 秒，665 MiB 为 64.837 秒，control 为 53.133 秒。相比 665 MiB 降 11.704 秒。预读与 SpringBoard 并行，这些秒数不能直接加到首帧收益上。本轮去重 Console 未见首帧前明确的 jetsam kill 或物理压力事件，不能据此排除其他内存压力。
- 首帧后 backboardd 有一次清空再恢复，内核 283.318 → 290.213 秒，约 6.90 秒。这段超出启动视频覆盖。当前日志无法判定屏幕实际状态或触发方。

## 4. 进度条与迁移状态

旧版进度窗口路径为 `DMMigrator::_createProgressWindow → PUIProgressWindow`。本轮 `lockdownd.log` L241 在客体 t73.235 读取到 `Data Migration Completed: true`。视频约第 50 秒还拍到 datamigrator 启动相关文字。四份新增 PongDM 日志仅记录诊断注入。迁移 plist 与上一轮逐字节一致，APFS mtime 也一致，`success=false` 是旧结果。现有证据没有本轮新建进度窗口、迁移成功或新 ProgressUI 错误的回执。视频看到的直接切换与此一致，但不会反推出迁移流程的最终状态。

## 5. 阶段 B 镜像审查

control FD 与 v77 已知 768 MiB FD 相同。control、diag 与 v78 root-fix 三个镜像的 FAT 相同。解开 diag 的 FD/FV 后，和 control 相比只有 DarwinBoot24 的 FFS 内容变化，实际 diag 二进制中包含 DB24 MEM 字样。两份产物均已构建。control 已真机通过。**本轮没有启动 diag，因此既没有新的 EFI 描述符，也无法从本轮判断 9 GiB 高地址范围是否进入最终 EFI map。**

固件 `uefi-thor` 提交 `23d5085` 在 `DarwinBootPkg/Driver/DarwinBoot.c:980` 的 `ExitBootServices()` 成功之后，于 L998/L1004 调用 UEFI `Print()`。本机 Mu DXE 此时已把 `gST->ConOut`、`StdErr` 和 `BootServices` 清空，`Print()` 的实现仍访问 ConOut，并申请和释放 pool。这可能导致输出丢失或启动异常。现有摘要中的 owned 也是 reclaim 前的数值。 [UEFI 2.11 §7.4.6](https://uefi.org/sites/default/files/resources/UEFI_Spec_Final_2.11.pdf#page=203)

交给 CC 的修正顺序

1. 保留成功 EBS 用的那份 map。让 DarwinBoot 只在事先预留的、有限长度和带版本的 ADT 缓冲中写入描述符和统计。记录完整范围、类型、属性和截断计数，覆盖跨 4 GiB 边界的描述符。分别记录 reclaim 前后 owned，回收可用统计排除 Runtime。保留 HYP、GOP、现有启动窗口和所有权排除条件。
2. 由 XNU 在现有 `Pong RAM` 附近用内核 Console 输出，沿用可从 Data 抽取的日志路径。若只改 FD，可将有限摘要在 EBS 后通过已捕获 framebuffer 直接绘制，作为摄像取证。完整逐 bank 报告走 ADT 到 XNU。
3. 先验证新 diag 启动和 `DB24 MEM` 的日志来源，再比较候选范围与原厂 FDT、HYP/保留区以及实际资源和硬件访问证据。等这些结果齐全，再进入阶段 C 的 256 MiB 高地址试验。

原始源文件在 i386 上述采集目录，主要为 `extraction.json`、`finish-capture-result.json`、`current-boot-analysis.json`、`extracted/files/logs/PongConsole.txt`、`logs/PongDiag.txt`、`logs/lockdownd.log` 和 `mobile/Library/Logs/PongSB-4.txt`。手机仍在 Recovery。本次分析没有重启、刷写或改动候选镜像。
