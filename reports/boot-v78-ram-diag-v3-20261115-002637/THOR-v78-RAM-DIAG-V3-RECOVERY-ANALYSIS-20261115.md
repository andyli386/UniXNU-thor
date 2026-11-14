# Thor v78 RAM diag v3 Recovery 分析

采集时间为 2026-11-15 00:26–00:31（UTC+8）。测试镜像为 `boot-thor-v78-ram-diag-v3-repair.img`。

## 真机结果

**v3-repair 已完成根卷、SystemOS、Data 和 launchd 启动，首帧在 163.970 秒出现。Phase B 的 EFI 内存图已读出。当前可交给 XNU 的范围仍是 768 MiB，扩容的下一步应先修正 Thor UEFI 对 HYP 保留区的描述，并采集 B/C 的实时固件归属。**

| 启动里程碑（内核秒） | 768 MiB control | v3-repair |
| --- | ---: | ---: |
| XNU 接收内存 | 26.183 | 25.102 |
| BSD root | 27.090 | 26.216 |
| Data 读写挂载 | 27.320 | 26.408 |
| 请求加载 launchd | 27.321 | 26.409 |
| launchd 执行首个可见启动任务 | 41.281 | 40.319 |
| 首个可见帧 | 178.854 | 163.970 |
| 请求加载 launchd → 首帧 | 151.533 | 137.561 |

v3 根卷 UUID 为 `B07DF049-7AD9-47B5-9F8F-9EE728056398`。`PongConsole.txt` 第 290、330、360、392、417 行分别确认 UUID 匹配请求、`BSD root: disk4s1`、OS root binding、SystemOS union 和 Data RW。第 3175、3177 行分别确认首帧和 backboardd 提交 `gpu=0 graphics=1 swap=1`。可读记录到内核 t=251.326 秒，未见本轮 panic 签名。`PongConsole` 有环形缓冲覆盖标记，早期及被覆盖窗口需用更早的载体补证。现有记录已足够确认进入 iPadOS 和本轮没有重现 v2 的根卷挂载失败。

首帧比 control 早 14.884 秒，两组各有一次样本。root 仅早 0.875 秒，主要差异在用户态。13 段 cache pre-read 都读取 333,987,840 B（318.516 MiB），日志的累计 `read_ms` 由 53.133 秒降至 33.611 秒。旧的 v73 UI dylib 仍加载，SpringBoard 主队列 t=55 排队、t=156 执行、t=158 接受 Home，请求排队 101 秒，control 是 123 秒。这些数据支持优先检查 SpringBoard 主线程和 I/O 争用，单次运行无法量化各自贡献。t=48.223 有 SpringBoard 5,848 次唤醒每秒告警，t=117 的主线程栈通过 22H374 cache 符号化，经过 `CSCoverSheetViewController viewDidLoad`、锁屏日期的分钟计时器，落在 QuartzCore 的 `-[CADisplayLink setPreferredFramesPerSecond:]`。下一轮用多点采样和时长统计确认这条路径占用。lockdownd 有本轮 `Data Migration Completed: true`，历史迁移 plist 字节与 control 相同，保留原有设置。

## Phase B 内存图

`PongConsole.txt` 第 4–12 行来自本次启动。下表为 DarwinBoot 保存的 EBS 接受的高地址描述符，地址区间右端点均排除，EFI attributes 均为 `0x100f`。

| EFI 类型 | 地址 | 大小 |
| --- | --- | ---: |
| 2 LoaderData | `[0x800000000,0x800004000)` | 16 KiB |
| 1 LoaderCode | `[0x800004000,0x810004000)` | 256 MiB |
| 2 LoaderData | `[0x810004000,0x829900000)` | 409 MiB − 16 KiB |
| 7 ConventionalMemory，WB | `[0x829900000,0x839900000)` | 256 MiB |

前 3 段合计 665 MiB，等于 DarwinBoot 回收前自有内存。最后一段的 `[0x829900000,0x830000000)` 为 103 MiB，回收后 XNU 接收 768 MiB（保留内核页以前）。再往上 `[0x830000000,0x839900000)` 为 153 MiB，落入 Thor 原厂固件的 HYP 保留区 `[0x830000000,0x840000000)`，这次被现有回收边界挡住。三条 `Pong RAM: bank` 的实际字节总和正好 768 MiB，全部低于 `0x830000000`。后续还应在 UEFI/预分配入口屏蔽 HYP，避免更改分配策略时接触它。

Recovery 旧记录和 Thor 原厂静态图所示 B `[0x840000000,0x900000000)`（3 GiB）及 C `[0x900000000,0xa80000000)`（6 GiB），与本轮四条高地址 EFI 描述符交集均为零。现有 Thor `MemoryMapLib` 的高地址系统内存表项由 `0x800000000` 起，长度 `0x39900000`，和本次 921 MiB 相符。EFI 图记录了当前 UEFI 发布结果，B/C 的 XBL 归属、保留段和执行态 CPU/DMA 访问权限仍需逐项核实。B 起点的 `[0x840000000,0x850000000)` 可用于核查，当前没有可分配依据。解包 control 和 v3 的 ADT 可见两者 `pong-allocatable-ram` 一致，已经放行 B/C，也含两段覆盖部分 Thor HYP 的范围。其生成器借用 Pong 参考配置。应增加 Thor 专属允许表并交叉扣除 Thor 保留区，当前高地址只允许 `[0x800000000,0x830000000)`。

## 给 CC 的下一批改动

1. **内存基线与固件归属。** Thor `MemoryMapLib` 把 Bank A 的 Conventional 上限截到 `0x830000000`。视平台映射需求为 HYP 保留映射，禁止进入 DXE 通用分配池。`ClaimPongRam()` 的候选筛选、预分配与 EBS 后回收统一排除保留段和 runtime 描述符。保留加载器既有的 256 MiB pool 容量，必要时从修正后的 A 段内重选 pool。这样回收前自有内存可能接近 512 MiB，验收以回收后 XNU 仍接收 768 MiB 为准。审计 PCD 内存类型配额、GCD、加载器分配和 fastboot 启动。用修正后的 768 MiB 镜像验证 HYP 零交集、正确根 UUID、Data RW、首帧和正常回 Recovery。继续只读导出实时 SMEM item 402 或保留的 ABL FDT、DXE GCD 图和最终 EFI map，取证 B/C 的类型、所有者、允许长度和固定/动态保留段。归属、保留边界和访问权限得到确认后，将一个 256 MiB 白名单段作为独立 Phase C 镜像加入，目标 1024 MiB。依次验证 CPU 受控读写与 VM 分配，再做 UFS 校验，之后才允许 GPU DMA 使用这段内存。
2. **触摸，使用已验证的 768 MiB 软件显示基线。** 将 Thor 节点挂到 `/arm-io/pong-touch-fts`，按 Thor Recovery 实测的 `spi@990000/fts@0`，使用 SPI mode 0、CS0、8-bit、≤12 MHz、GPIO20 reset / GPIO21 IRQ、L2C 1.8 V / L9C 3.3 V，并核对时序。先采集实机 SALIXP ID（驱动预期 `36 40`）、FIFO 和 IRQ 计数，再确认驱动帧、`/dev/pong-touch` 和 backboardd/HID 事件。验收用手指点开 Settings、滚动和返回 Home，记录坐标映射和重复手势。本轮启动参数 `pong_touch=0`，触摸未被测量。
3. **缩短启动，和触摸分开测。** `bsd/kern/kern_exec.c` 的 runtime staging 两处调用同时给 `O_NOFOLLOW` 与 `VNODE_LOOKUP_NOFOLLOW_ANY`。`vfs_subr.c` 将后者转为 `O_NOFOLLOW_ANY`，`vfs_vnops.c` 因组合返回 `EINVAL`。两处去掉 `O_NOFOLLOW`，保留 `VNODE_LOOKUP_NOFOLLOW_ANY`，启用 `pong_stage_ui_v77=1`，验收 `THOR UI runtime: PASS`、候选 dylib UUID `9FCCBB70-F843-3FF8-AB78-DAF5B4582068` 和 SpringBoard 实际加载的 dylib/构造器一致。带入日志游标修复 `2c3c25f`，在固化 768 MiB、相同 Data 和相同显示条件下，做 pre-read 默认与 `pong_cache_prefetch=0` 的交替 A/B，每组先做 3 次，报逐次值和中位数。一起采 root、launchd、首次可见帧、Home 可操作时刻、I/O、SpringBoard 队列与 VM 压力。首帧前耗时来自哪些调用，由主线程栈和带时戳的 I/O 进一步验证。
4. **Adreno 730 单独启动。** 参照 zizhan 的顺序，把 Thor 实测寄存器、固件、SMMU stream ID、时钟和电源配置对齐，保留软件显示。逐段验证 ARC/CX、PLL、SMMU、GMU HFI、PAS13、CP、IRQ、fence，以及最小提交的写回校验。硬件稳定后用 22H374 的驱动接 Metal，最后独立验证 scanout 和真机画面。GPU 实验先保留 768 MiB 内存方案，避免把高地址 DMA 可达性和 GPU 初始化混在同一轮。

## 取证索引

- i386 原始目录 `/home/vincent/Projects/thor-unixnu/PongBoot-pull-v78-ram-diag-v3-20261115-002637`。重点文件 `extracted/files/logs/PongConsole.txt`、`diag3-evidence.txt`、`db24-accepted-descriptors.json`、`current-boot-analysis.json` 和 `transfer-extracted.zip`。
- Recovery 设备 `<device-serial>`。只读获取 SHNGROOT Data 8,591,003,648 B，完整校验 129 段，块设备写入和 discard 计数无变化。离线提取完成，共 349 个文件。读取 pstore 和 logfs 无本轮输出。采集完成时设备仍在 Recovery。
- 两台宿主镜像 SHA-256 相同，`2845228885451d665158d2eed8df6bc398fe969b3e4f141b8a63b64c6c1c1433`。v3 构建产物内核 LC_UUID 为 `3A84A759-B2CC-35AB-86DA-3F8C96A507F0`，本轮运行日志以新 `DB24 MEM` 标记和正确根卷匹配确认，未记录运行时内核 LC_UUID。
- Mac UEFI 源码 `/Users/<user>/projects/thor-unixnu1029/uefi-thor` 下的 `Platforms/Xiaomi/thorPkg/Library/MemoryMapLib/MemoryMapLib.c`、`DarwinBootPkg/Driver/DarwinBoot.c`。原厂保留图 `analysis/firmware-volumes/thor-memory-map-evidence.csv` 第 40 条原始表项和 `analysis/thor-darwinboot-memory-adt-20261024/memory-adt-review.json` 保存在 i386 工程目录。
- XBL 内存表和 EBS 语义参考 [TianoCore QualcommMinPlatformPkg](https://github.com/tianocore/edk2-platforms/blob/master/Platform/Qualcomm/QualcommMinPlatformPkg/README.md) 和 [UEFI 2.11 Boot Services](https://uefi.org/specs/UEFI/2.11/07_Services_Boot_Services.html)。同平台参考 [zizhan 的真实触摸验收](https://unixnu.com/devices/zizhan) 和 [zizhan A730 bring-up](https://github.com/qiumochen/UniXNU-zizhan/blob/034a0b0037b28f4ff4e46703cbd4d9bb75db25e5/docs/progress/2026-10-30-root-and-autoloop.md#7-gpuadreno-730)。
