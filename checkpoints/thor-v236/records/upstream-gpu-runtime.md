# 上游 GPU 更新评估（二）：Metal 运行时（AdrenoMetal）

2026-11-25。对照对象：上游 `thor-out/upstream-20261125/AdrenoMetal`（0b7094b，11-25 19:32）、
`thor-out/upstream-20261125/UniXNU`（b04f131）与移植在 v236 里实际嵌入的 `PongMetalBridge.dylib`。
结论由只读研究任务得出，以下带 ✔ 的条目我对源码复核过。全部来自读代码，**没有任何东西被编译或在手机上跑过**。

## 1. 结论

上游这批更新量很大，但**不能直接换到 Thor 上**：它面向的是 iPadOS 17.7.11 / iPadOS 27 / macOS 27.3 和 Adreno 740 的内核 ABI v2，
没有 iPadOS 18.7.10 的配置。要用它是一个移植项目，不是拉取加重新编译。

## 2. 上游改了什么

范围 ✔：基线 `da7aedd` 与上游重写历史后的 `a1b30ed` 树完全相同，所以 `da7aedd..0b7094b` 就是全部增量：
82 个文件，+15,465 / −4,610。

| 部分 | 移植基线 | 上游主线 |
| --- | --- | --- |
| `NativeBridgeMetalFrontend.mm` | 4,951 行 | 6,035 行 |
| `bridge/*.rs` | 3,525 行、2 个文件 | 9,323 行、17 个文件 |
| 随附的运行时补丁 | 25 个（`bridge/patches/`） | 0（都并进了 fork） |

分三段：
1. 11-10 到 11-11：macOS 构建与插件入口、可清除内存、系统着色器缓存、YCbCr 采样、lowering 线程与预热遍。
2. 11-11 到 11-18：基于 metal2vulkan `development` `555b13d5` 加随附补丁（提交标题里的"R2507"没有任何文件解释它）。
3. PR #72（11-22 到 11-25，35 个提交）——"runtime v2"迁移：
   - 固定使用 `UniXNU/metal2vulkan` 的 `development` 分支 `87a869f7`（基于 steelbrain `0c9db497`），最后 5 个补丁并进 fork：
     非呈现实例、原生显示写入排在提交之后、只读主机内存导入、原地零拷贝导入纹理、翻译器 floor 尾部保护；
   - Rust 桥接拆成 16 个模块（Ownership、Pipelines、Heap、HostMapping、Texture、YCbCr、Resources、CacheStore、SystemCache、Archive、Library、Purge、BufferArena、Capabilities、Trace、Wait）；
   - 两平面 YCbCr 用计算着色器转换；流水线提前完成；缓存按运行时构建标识隔离；驻留与屏障改成 `nmb_encoder_use_resource` / `use_heap` / `memory_barrier`。

与 Thor 无关的部分：macOS 专用（`IOGPUMetalDeviceShim.h`、`ShaderCache/`）、arm64e / iPadOS 27（PAC 包装）、Adreno 740 专有（tile 流水线、并发 binning、GMU 时间戳 ABI）。

## 3. Thor 现在的 Metal 桥接是什么

- **内核构建不编译桥接库**：`runtime/thor_metal_pool_payload.h` 用 `.incbin` 把现成的 `PongMetalBridge.dylib`（22,802,560 字节）和
  `libvulkan_freedreno.dylib` 嵌进内核。所以换运行时不需要动内核源码，只需要换这两个文件。
- **它是由一串按哈希钉死的回放脚本产出的**，不是 `build-pong-metal-bridge.py`（那个脚本对当前的树已经跑不通）：
  - 前端源码 `thor-out/v227-metal-work/candidate/NativeBridgeMetalFrontend.mm`（2,994 行）；
  - Rust 归档 `thor-out/v223-unified/prepare/metal/libpong_metal_bridge.a`，由 `thor-out/v223-render-work/build-final.py` 构建；
  - 编译链接命令记录在 `thor-out/v206-metal-analysis/candidate/commands.json`、`v221-unified/prepare/build-metal.py`、`v223-unified/prepare/evidence/metal-final-rust-command.json`。
- **翻译器**：`thor-out/v221-cadence-work/translator`（非 git 目录），基线 steelbrain/metal2vulkan `624bab3`，
  加 `UniXNU-pong/patches/metal2vulkan/parity114/` 的 24 个补丁，再加 3 处手改（`memory.rs`、`render/shadows.rs`、`Cargo.toml`）。
  `UniXNU-pong/patches/metal2vulkan/` 顶层的 0008–0021 系列属于更旧的 `720ef1ce`，在当前树上一个都打不上，也没有用于出货的桥接库。
  18.7.10 的像素格式策略实际来自 `parity114/0021-pong-guest-format-info.patch`（在 iOS 上调用客体自己的 `MTLPixelFormatGetInfo`）。
- **Turnip**：Mesa `85fda3b3` 加 `patches/mesa/parity167/pong-darwin22h374.patch`，最后一次链接再加
  `thor-out/v227-turnip-pack-work/0001-bound-specific-timeline-waits.patch`。
- **构建约定**：前端 `xcrun clang++ -target arm64-apple-ios18.0 -std=c++17 -O3 -DPONG_GUEST_22H374=1`；
  Rust `cargo 1.98.1 build --locked --offline --release --target aarch64-apple-ios`，fat LTO、单 codegen unit，约 25 分钟。
  `ipados18` 这个 cargo feature 没有任何代码使用。
- **移植前端相对 AdrenoMetal 基线是另一个谱系**（fork 自 NativeMetal `c6d29ca`，+1070 / −3155 行），移植专有部分：
  设备 ABI 门（22H374 上 `_MTLDevice` 600 字节 ✔）、内核主机缓存交接（`tu_darwin_sync_host_range/batch`、`tu_darwin_va_budget`）、
  22H374 的 Core Image 选择器、真正实现的 `setResponsibleTaskIDs:count:`、驻留/屏障选择器、追踪事件 30–61、Turnip 钩子。

## 4. 障碍

1. **设备大小门** ✔：上游 `NativeBridgeMetalFrontend.mm:5474` 在 `_MTLDevice` 不是 536 字节时直接返回 nil；22H374 是 600。
2. **前端与 Rust 桥之间的 C ABI**：4 个结构体字段不同（`NmbTextureDescriptor` 上游 15 字、移植 11 字，另有 3 个流水线/通道描述符），
   1 个函数签名变了（`nmb_texture_new_on_surface`），11 个函数只有移植有。混用会静默读错字段。
3. **目标系统** ✔：上游组件声明 `os_abi = ["ipados-17.7.11-21H461", "macos-27.3-26C5035r"]`、`verified_devices = ["sheng"]`；
   `ios` 目标最低 17.0。上游没有 22H374 的选择器和真正的 `setResponsibleTaskIDs:`。
4. **表面缓存策略**：目标低于 27 时上游自己对接 IOSurface 整块做 `sys_dcache_flush`；Thor 把这件事交给内核主机缓存交接。
5. **内核 ABI** ✔：上游 Adreno740 `NativeAdrenoAbiVersion = 2`，Thor 的 PongAdreno 是 1；方法 17–20 的含义不同。
   上游的 Turnip 不能用，Thor 的 Turnip 留着。
6. **追踪接口**：记录布局相同，但 Thor 的内核为事件 46/47/48/56–59 和 Import 保留元数据，上游不再发这些；事件 65 两边含义不同。
7. **工具链** ✔：上游脚本要求 `cargo +1.98.0`、LLVM 21 的 `llvm-as`（本机有）、4 个 AIR 夹具、6 个 `NMB_*` 变量、精确钉住的 git 检出、非离线构建。
   本机只有 1.98.1 和 1.99.1。
8. 启动参数已用 1005/1023 字节，没有余地加开关。

## 5. 上游对移植各项需求的覆盖

| Thor 需要 | 上游主线 |
| --- | --- |
| `arm64-apple-ios` 最低 18.0 | 出货配置是最低 17.0，二进制兼容但按 iOS 17 的 ABI 假设编译 |
| 22H374 像素格式（客体 `MTLPixelFormatGetInfo`） | 未知；上游从翻译器的 `profile()` / `metal_properties()` 回答 |
| `_MTLDevice` 600 字节 | 否（硬编码 536） |
| 22H374 Core Image 选择器、真正的 `setResponsibleTaskIDs:` | 否（后者是空操作） |
| 驻留、屏障、`useHeap` | 有，名字和形状不同 |
| 两平面 YCbCr | 有，做法不同（计算转换 + seed 刷新） |
| 内存的"先放置后增长"、房间上限（backboardd 128 MiB、其它 32 MiB） | 只有部分（`nmb_device_release_kept_memory`、purge） |
| `nmb_command_buffer_stage_summary`、追踪事件 30–61 | 否（有格式不同的 `nmb_stage_ring_read`） |
| PongAdreno ABI1 的方法 17–20 | 否 |

两个 Rust 桥接库的导出：移植 156 个，上游 206 个，共有 145 个；其中 142 个签名一致。

## 6. 建议的路线

**不建议**先做"保留移植前端、只换运行时 crate"：那要把 24 个补丁重放到重构过的运行时上，并把移植 2,479 行的桥接 crate 改写到 v2。

主机上、不碰手机的两个实验：
- **实验 0**：在临时克隆的 UniXNU（`b04f131`，`drivers/AdrenoMetal` 在 0b7094b）里，取回 fork `87a869f7`，
  用脚本私有副本把 `+1.98.0` 改成本机已有的 `1.98.1`，先 `--target ios --check-only` 再 `--target ios`。
  回答"这些钉住的版本和工具链在本机能不能构建"，产出 `libadreno_metal_bridge.a`（fat LTO，移植那次用了 1471 秒）。
- **实验 1**：用移植记录下来的编译参数把**上游自己的**前端编成 `arm64-apple-ios`，和上面的归档链成一个临时的 `PongMetalBridge.dylib`，
  只加 600 字节这一处改动，然后做静态检查（未定义符号只来自 Apple 框架、`dyld_info -validate_only`、三个导出入口、4 KiB 页签名）。

之后才上手机：
- **实验 2**：借助内核已有的"metal pool trial"机制（只给 backboardd 第一次 exec 提供试验桥接库，进程重启后回退到原库）打一个试验镜像。
  通过标准：backboardd 第一次 exec 用上试验库、设备打开、命令缓冲完成、扫描输出帧号前进。失败时内核自己回退，桌面应仍能起来。
- 再往后：给 `drivers/AdrenoMetal` 建移植分支（ABI 门、22H374 选择器、`setResponsibleTaskIDs:`、表面缓存策略与内核交接对齐）；
  追踪事件对齐；内存策略对照 Thor 的预算；设备上验证帧节奏、wired 内存、休眠唤醒、定格见证；最后才退役 `drivers/PongMetal/bridge` 和 parity114。

主要风险：单采样、阴影、YCbCr 路径不同带来的画面回退；`sys_dcache_flush` 带来的额外 CPU 开销；归档更大（`debug = 1`）而启动镜像的内核成员已 93.8 MB；
backboardd 首次 exec 崩溃一次；fork 里的 floor 检查可能拒绝 A730 的 Turnip（未核实）。

## 7. 更值得先做的事

对 Thor 当前最疼的两个问题，这批上游更新帮不上：
- **定格**：根因在 Thor 自己的追踪代码（已在 v236 修）。
- **掉帧**：每次提交 4.3 ms 的整窗口缓存维护（见 `UPSTREAM-GPU-KERNEL.md`），是移植自己的策略，上游同样如此。

所以建议顺序：验证 v236 → 掉帧实验（导入表面登记显式同步）→ 上游内核小提交 → 上游运行时按上面路线试验。

## 8. 未核实

- fork `UniXNU/metal2vulkan` 本机没有检出，`87a869f7` / `0c9db497` 不在任何本地对象库里：fork 内部、它对 iOS 18 格式策略、parity114 能否应用，都未知。
- 上游的桥接库能否在本机编译，未测。
- 移植前端与基线的差异是按选择器和调用点归纳的，不是逐行。
- 原 `PongMetalBridge.dylib` 和 backboardd 之外的 GUI 进程怎么拿到 Metal，未核实。
- `VK_EXT_external_memory_host`、`VK_EXT_attachment_feedback_loop_layout` 在 Thor 的 Turnip 里有扩展字符串，但在 A730 上是否对外声明未知。
