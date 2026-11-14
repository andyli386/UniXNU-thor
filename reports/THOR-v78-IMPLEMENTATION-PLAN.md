# THOR 下一批实施清单

2026-11-14 · Xiaomi 12S Ultra / Thor · iPadOS 18.7.10 / 22H374 / Darwin 24

## 基线和完成标准

v77 在内核时间 26.874 秒挂载 Data，164.982 秒记录首个可见帧，间隔 **138.108 秒**。19:21 的照片已经拍到真实主屏，距临时启动约 8 分钟。这是主屏出现的上界，首个可见帧的具体内容未知。另一次持续 **16 分 31 秒**的空图层发生在系统已经出过画面后，按锁屏、熄屏和唤醒问题单独追踪。v77 仍在用 v76 用户态库，新的预读、限额和清黑观测尚无运行结果。XNU 的 768 MiB 内存交接和 IOSurface selector 44 成功回执已在真机确认。 [v77 证据和报告](https://chatgpt.com/library?file_id=libfile_fde3_f-9e9dbfd416b88191ab6b35ddd8b34646_f68619b71560819180f5afcfaa513788&account_id=personal)

- **启动**先定「冷启动到真实主屏」中位数 ≤3 分钟，下一目标 ≤2 分钟。单独记录首帧、真实主屏、首次可操作、Settings 首屏及锁屏唤醒。时间目标是工程验收线。
- **触摸**用真实手指点开 Settings、滚动、点四角、双指操作和重复唤醒，核对每次按下、移动和抬手。
- **GPU**先拿到实际 GPU 运算及正确输出，再用真实 CP fence 和面板 latch 验收 GPU 合成的主屏、Settings、连续交互和锁屏唤醒。

## 可复用的上游

| 来源 | 已有证据 | 在 Thor 上采用的部分 |
|---|---|---|
| [MIX Fold 2 / zizhan，22H374](https://unixnu.com/devices/zizhan) | 同 SM8475、A730v3，ST FTS 经 GPIO SPI 和 Pong HID 实机点开 Settings。A730 先在软件 UI 下跑通，再切 Metal 和扫描输出，已实测 GPU 主屏及多个应用。 | 触摸复用传输和 HID 结构，GPU 复用 A730 硬件与 22H374 用户态/KMD 的衔接。芯片协议、资源表和显示按 Thor 核对。 |
| [AGXMX666 / Pong，22H374](https://unixnu.com/devices/pong-agxmx666#gpu) | A730、SMMU、Turnip 与 22H374 Metal ABI 有桌面硬件渲染记录。复杂壁纸和长时间稳定仍未完全验证。 | 复用工程布局、绑定和构建配方，逐项核对现有 PongPlatform24 接口。 |
| [Pong 当前主线](https://unixnu.com/devices/pong#gpu) · [通用 GPU 指南](https://unixnu.com/porting/gpu) | 主线 Pong 有 A730/Turnip/Metal 和进程级 GPU 地址空间，公开配置为 22G100 / arm64e。通用指南的工作示例为 A740。 | 参考初始化顺序、资源生命周期和诊断思路。Thor 继续使用本机 22H374 绑定和 A730 板级资源。 |

## A. 启动提速与显示稳定

| 任务 | 一次做齐的改动或验证 | 完成条件 |
|---|---|---|
| **A1 修运行库交付** | 修 XNU kern_exec.c 中创建、重开两处的 EINVAL。保留 VNODE_LOOKUP_NOFOLLOW_ANY 的全路径保护，移除额外的 O_NOFOLLOW；分阶段记录查找、创建、写入、同步及回退。补上 PosterBoard 的进程匹配与采样开关。 | 成功写入版本专用库，磁盘 LC_UUID、实际注入路径、目标进程 ctor 和日志 boot ID 一致。失败时走原有适配库。 |
| **A2 量清挂载后约 138 秒** | 记录 Fastboot 交付、XNU、OS/Data、PID1、SpringBoard 主线程运行/排队/等待、BBS 请求/处理/回复、scene/CoverSheet、Home、CA 提交、swap 和面板 latch。一个 boot ID 下用单调时间串联，照片和主机时间另行对齐。 | 能指出启动关键路径占时最大的区间，给出等待方、负责方和实际完成点。v77 的一部分 SpringBoard 主线程样本已进入 CFRunLoop，采样不能复现 v75 的 BBS 长等待，故从新样本找瓶颈。 |
| **A3 修真实启动依赖** | 优先检查 BBS、LaunchServices 的回包、恢复和播种、原生迁移插件、PosterBoard 启动，以及 gethostuuid/MobileGestalt 的真实响应。核实现有稳定 UUID 和 ADT 身份。把 launchd 高唤醒记录与该时段的实际工作关联。 | 修复已证实的漏回复、初始化条件或可延后工作。给出关键阶段缩短的数据。触觉子系统后台等待可与正常主屏同时存在，维持较低优先级。 |
| **A4 降低 UFS 和 VM 竞争** | 同一套新库比较关闭预读与有界窄预读。分别测队列、设备完成、前台缺页、缓存、wired/压缩与 Jetsam。验证 lsd 的实际 32/12 MiB。保留已验证的 768 MiB；进一步扩容另行验证 Thor 物理范围与所有权。 | 判断哪些工作在持续拖慢前台。避免启动更慢、内存压力回升或挂载回归。v77 旧预读同样 13 个窗口，累计应用 read 约 33.7 秒，v76 约 35.1 秒。 |
| **A5 定位空图层与唤醒** | 在 MiniIOMobileFramebuffer.cpp 关联清黑、有效 scene、空层提交者、背光请求、面板状态及 latch。先分清主动灭屏、待唤醒与前台应亮却未提交，再修实际失败条件。 | 软件路径连续使用 5 分钟。至少 3 次真实锁屏/唤醒，都恢复有效画面与触摸。 |
| **A6 同口径测启动** | 以 v77 软件显示为对照，固定 Data、GPU 开关、供电和近似温度。各 3 次冷启动、连续重启；保留失败和最慢值。CPU 调度与时钟优化只在取样显示主线程可运行但拿不到 CPU 时做对照。 | 记录每次首帧、主屏、可操作和 Settings 首屏，不以某次最快的日志代替整体结果。 |

上游 zizhan 通过线程栈定位 gethostuuid/UDID 问题后，**Settings 首屏**由 6.4 秒降到 1.2 秒。它还通过等待栈定位 IOResources/APFS 挂载循环，采用同步 UFS 加延后提交，并按窗口统计 I/O。Thor 当前 Data 已能在约 27 秒挂载；根挂载补丁只有出现同样的等待环才纳入。 [上游身份与启动记录](https://unixnu.com/devices/zizhan#image-assembly) · [存储与根挂载](https://unixnu.com/devices/zizhan#storage-and-root) · [早期真机逐轮记录](https://github.com/qiumochen/UniXNU-zizhan/blob/034a0b0037b28f4ff4e46703cbd4d9bb75db25e5/docs/progress/2026-10-30-root-and-autoloop.md)

## B. 触摸接入

| 任务 | 一次做齐的改动或验证 | 完成条件 |
|---|---|---|
| **B1 订正板级描述和驱动选择** | 在 scripts/make-thor-adt-ipados18.py 把 Thor 的 pong-touch 从 Pong Goodix Berlin-D 和 1080×2412 改为 Thor 专用 ST FTS 描述。保持 bsd/dev/pong_input.c、/dev/pong-touch 及现有 backboardd HID 桥。 | Thor 只匹配正确的 FTS 前端。现有 Pong 行为不受跨板配置影响。 |
| **B2 先读到真实芯片** | 同机 Recovery 证实 spi@990000/fts@0、CS0、SPI mode 0、8 bit、最高 12 MHz，GPIO20 reset、21 IRQ。VDD L2C 初始 1.8 V，AVDD L9C 3.3 V。复用 GPIO SPI 结构做有界识别，同时独立核查 Thor 的 GENI/GPI；Recovery 报告 TX/RX FIFO 16/16。Thor SALIXP 资料的 ID 读命令为 0xFB + 大端 0x20000000 + 1 dummy，期望 36 40，以实读为准。 | 回执包含实读 ID、复位和 ready。失败有界并保留软件 UI。使用本机在片固件。 |
| **B3 接通物理事件** | 采用 Thor 的 8 字节 FIFO、ready/扫描和事件解析。按 10 个触点槽、X 0–14399、Y 0–31999 校准，四角实测轴向。接入 pong_touch_device_ready / pong_touch_post、共享队列和 userland/touch.c。 | 从物理 IRQ/FIFO 经内核队列和 /dev 到 HID/backboardd 逐段看到同一真实动作。正确处理按下、移动、抬手与多点。 |
| **B4 验收触摸、CPU 占用和生命周期** | 首次用手点开 Settings，再测滚动、四角、拖动、多点、锁屏后滑动、三次唤醒。用有界的物理采样→HID→手势→swap→面板时间点量出延迟。若 GPIO SPI CPU 成本过大，再验证 GENI/GPI 优化。 | 真手可点、滑、滚，重复唤醒后可用。通过真实动作验证排除粘指、漏抬和坐标翻转。 |

zizhan 上游使用的是 FST2 36 52，供电和折叠屏轴向也不同。Thor 用本机参数和原生公开协议，复用的代码保留原许可证。 [zizhan 实机触摸](https://unixnu.com/devices/zizhan#input) · [zizhan 历史 FTS 补丁](https://github.com/qiumochen/UniXNU-zizhan/blob/034a0b0037b28f4ff4e46703cbd4d9bb75db25e5/patches/xnu-ipados18/0002-zizhan-fts.patch) · [Thor 原生 fts_spi](https://github.com/LineageOS/android_kernel_xiaomi_sm8450/tree/lineage-23.2/drivers/input/touchscreen/fts_spi)

## C. Adreno 730 GPU

| 任务 | 一次做齐的改动或验证 | 完成条件 |
|---|---|---|
| **C1 按 Thor 建 GPU 板级数据** | 同机 Recovery 已确认 Adreno730v3、chip ID 0x07030002、GPU 0x03d00000、GMU 0x03d68000、GPU SMMU 0x03da0000；GIC 332/336/337、GMU SID 5 和 ZAP 保留区起点 0x8b91a000 可由本机 DT 核对。补齐 GPUCC/RSCC、RPMh、其他 SMMU SID/context、OPP/DDR、GMEM 及 ZAP 区间边界。Recovery 默认挂载路径未找到固件，后续用同机 Android 或对应原厂包确认 a730_sqe.fw、gmu_gen70000.bin 与签名 ZAP 组件，留作本机构建输入。 | 每个参数和固件有可复查来源。先验证保留区、地址宽度、DMA 域和实际可用内存。 |
| **C2 接通 22H374 内核 ABI** | 使用 XNU_PONG_ADRENO_PLATFORM、drivers/PongAdreno 及 configs/pong-iogpu-22H374.json。Thor 的 prelink-thor-kernel.py 默认缺 IOGPUFamily 根项；参考现有 build-ipados18-prelinked-boot.py 的条件闭包，核对 IOGPUFamily、IOSurface、IOReportFamily、IOSlowAdaptiveClockingFamily 的库式依赖及本机 MiniIOSurface。保持现有 160 MiB FAT 打包与原生 UFS。 | 22H374 绑定、vtable/构造入口、预链接闭包可重复生成。继续使用本机存储和 surface 提供者。 |
| **C3 GPU 硬件资格测试，软件显示保持可用** | 先启 A730 内核平台，Metal 全局入口和 GPU scanout 关闭。根卷就绪后再异步跑 power/clock、GPU SMMU、GMU/HFI、CP、ZAP/PAS、IRQ、fence 及最小有确定输出的命令。 | GPU 确实执行并完成命令、有输出回读；超时有边界、错误可定位，桌面和触摸测试独立可用。 |
| **C4 单客户端 Metal** | 以本地 PongAdreno 3e8ffa2、PongMetal ccdbc29、AdrenoMetal da7aedd 为比对起点，锁定匹配的 Mesa Turnip 和 metal2vulkan。用现有 build-pong-turnip.py、build-pong-metal-bridge.py、build-pong-metal-plugin.py、refresh_metal 组装 22H374 bundle 和配置。当前本地 platform worker 会直接启动，native glue 缺上游明确的 rootvnode 门控。分别给启动和 provider 发布接好根卷就绪条件；provider 还要等 GPU 和 OS bundle 就绪。先枚举、buffer、clear/triangle、回读、命令完成及销毁。 | 能将 CommandBuffer 和实际 CP 完成、正确输出像素对应。退出或出错后映射、页及 BO 可回收。 |
| **C5 BackBoard GPU 合成到面板** | 先启单个合成客户端，关联 CA surface、IOSurface 映射、实时 fence、swap 和 DPU latch。沿用已知 Thor 面板策略，核对 stride、格式、alpha、同步、清黑与唤醒。GPU 预算按 Thor 768 MiB 的现有压力定，实际测 wired 和映射峰值。1440×3200 BGRA 未计行对齐时约 17.6 MiB/帧，三帧约 52.7 MiB，还未计其他 BO。 | GPU 渲染的主屏和 Settings 真实显示，持续滚动与唤醒无 GPU 丢失、SMMU fault 或明显内存压力回归。 |
| **C6 性能和功能扩展** | 测 submit→实时 CP→panel 延迟、帧间隔分位和拷贝。稳定后再评估缓存、DPU 直出、更高刷新率、ASTC、共享事件和壁纸客户端。异常保留软件回退。 | 每项改善都有同负载对照。独立状态报告能区分已提交、实时完成和面板可见。 |

上游先保持软件桌面测试真实 A730，再推迟 Metal provider 的发布。真实 CP 读取分别有 763/763 和 665/665 的完成证据。旧等待快照曾少一个完成项，实时硬件读数证明已退休，因此 Thor 也应记录两者。上游 256 MiB GPU 池和 120 Hz 属于其平台配置，不直接作为 Thor 默认值。 [分阶段 bring-up](https://github.com/qiumochen/UniXNU-zizhan/blob/034a0b0037b28f4ff4e46703cbd4d9bb75db25e5/docs/progress/2026-10-30-root-and-autoloop.md#7-gpuadreno-730) · [实时 fence 与面板证据](https://unixnu.com/devices/zizhan#gpu)

## D. 同批准备，独立验收

| 同一套源码的配置 | 要验证的成果 |
|---|---|
| **软件显示对照** | A1–A6、B1–B4。先到可点开 Settings 的桌面，让启动提速和触摸有可独立重跑的对照。 |
| **GPU 内核验证** | C1–C3。保留软件桌面，拿到 GMU/HFI、SMMU、ZAP、IRQ、实测完成与输出证据。 |
| **GPU 合成验收** | C4–C6。单客户端 Metal 通过后启用合成，把硬件完成和面板画面对应，再测试滑动和唤醒。 |

Mac mini 保留一个版本化构建基线，分别记录镜像、编译、运行库与真机结果。i386 沿用临时 Fastboot 启动和 Recovery 原始日志只读采集。输出同一 boot 的时间线、实际触摸、GPU 完成和画面数据，以及 PASS/FAIL/SKIP。保持现有 Data；需要更新运行时时只部署匹配的增量。迁移、音频 RPC、部分 stackshot 和更多内存仍参加本批核查，按实测是否阻塞主屏或 GPU、是否导致重启决定修复顺序。
