# 近期故障原因与 Rust 适用范围

分析日期：2026-11-19。此次只读核对历史采集、实际构建输入及已有故障注入结果，没有修改产品源码或操作手机。

目前确认的是多种不同故障类型，不能统一称为内存崩溃。最新 v187 卡死的瞬间没有完整线程栈，尚不能建立“某个源码缺陷导致某一次卡死”的因果链。

## 日志实际证明了什么

| 现象 | 已证明的直接原因或现场 | 仍未确定 |
| --- | --- | --- |
| Preferences 被终止 | 七份 IPS 的 termination.code 都是 0x8badf00d，分别为启动、场景创建/更新或退出超过时限的 watchdog | 七份属于七个旧 boot，均非当前 447D7F03；正文在 EOF 截断，没有 Preferences 目标线程栈，无法知道究竟等哪一个锁、IPC 或 GPU 操作 |
| 内存压力终止 | 四份完整 Jetsam JSON 包含 highwater、vm-pageshortage 等终止原因 | 报告内核为 2026-11-10 构建，当前控制台为 2026-11-17 构建；不是本次启动的新证据，也未证明内存越界、泄漏或 UAF |
| 最新 v187 桌面定格 | 当前 boot 447D7F03-83DC-436F-A4C0-DDBE3F6F7C40；末尾 518.927681 秒仍派发 UP，518.980779 秒仍写入不同 fingerprint 的 CPU 源画面，519.074 秒仍有 GPU 状态记录 | 这些记录不能证明玻璃上画面正常，也不能排除某条上层队列阻塞；519 秒之后的终端现场没有保存，不能断言整个 GPU 永久停止或已经排除 panic |
| 当前服务高唤醒 | CommCenter 在 67.476523 秒打印 1703 wakes/s；同 boot 共 18 个进程有同类告警 | 是资源告警，没有因此被终止的记录，不能单独认定为卡死根因。PAS4 首命令在 94.492455 秒，不能造成更早的这次告警 |
| 关于本机/存储页卡住 | 原生链路存在 Metal 初始化、同步 XPC 容量查询、应用大小计算等等待边界 | 既有采样早于卡死；没有本次终端线程栈把症状关联到其中某一处 |

原始证据与审查入口：

- [IPS 完整性与 boot 归属](recovery-v187-appdiag-20261119T122345/ips-evidence.json)，[补充分析](recovery-v187-appdiag-20261119T122345/supplemental-analysis.md)。
- [Preferences watchdog 原文](recovery-v187-ips-20261119T121011/Data/mobile/Library/Logs/CrashReporter/.Preferences-1970-01-01-000320.000.ips)，第 22/23 行为终止原因和 boot UUID。
- [Jetsam highwater](recovery-v187-ips-20261119T121011/Data/mobile/Library/Logs/CrashReporter/JetsamEvent-1970-01-01-000508.0002.ips)，第 2396 行；[vm-pageshortage](recovery-v187-ips-20261119T121011/Data/mobile/Library/Logs/CrashReporter/JetsamEvent-1970-01-01-000552.0003.ips)，第 61 行；内核构建信息均在第 5 行。
- [独立控制台](recovery-v187-appdiag-20261119T122345/Data/logs/PongConsole-v172.txt)，第 5866/5867/5868 行为最后触摸、CPU 写回和 GPU 状态。
- [CommCenter 代码与时间关系审查](recovery-v187-appdiag-20261119T122345/commcenter-readonly-code-review.md)；[原始控制台](recovery-v187-appdiag-20261119T122345/Data/logs/PongConsole.txt) 第 87587 行为当前 boot BEGIN，88406 为高唤醒，88822/88823 为 PAS4 attach/首命令。告警代码首次触发后关闭该任务重复通知，没有第二条告警不代表已恢复。
- [存储页分析](recovery-v179-storage-20261118.vQMJ5QbN/analysis-storage-v179.md)。

日志中的 1970 年日期来自设备未校时，不能按 IPS 文件名当作真实发生日期；本分析使用 boot UUID、内核构建与启动后时间分组。

## 已确认的代码缺陷与 Rust 对应关系

以下四项已经进入 v188 候选，离线验证通过；没有现场调用栈证明最近某一次卡死命中了它们。

| 缺陷 | 失败时的结果 | Rust 是否能帮助 |
| --- | --- | --- |
| Metal 空 completion handler 仍把 group leave 派发到客户端队列 | 调用者若正在同一串行队列中 waitUntilCompleted，会挡住解除等待的任务；GPU 完成也不能解除这个循环等待 | 借用检查器不识别队列依赖，安全 Rust 也能写出这种死锁；必须改调度设计 |
| Turnip 提交失败出口遗漏 submit_mutex 解锁 | 特定分配失败后锁一直持有，后续提交无法继续；创建 submit 失败还可能返回成功 | 常规作用域 MutexGuard 能在 return/问号传播错误时自动解锁；C++ RAII 也能做到 |
| visibility buffer 扩容先释放旧 BO、更新尺寸，再分配新 BO | 分配失败破坏旧所有权与可重试状态 | 所有权可以防止安全引用悬空；仍须主动实现“先成功分配，再替换旧状态”，语言不会自行选择这个策略 |
| PAS4 WAITQ 第一次两秒超时后进入无 deadline 睡眠 | 若没有晚到唤醒，worker 可永久挂起 | Rust 不自动提供超时、硬件恢复或协议终止；需要明确 deadline 与状态机 |

源码与验证锚点：

- 实际 Metal frontend 基线：[NativeBridgeMetalFrontend.mm](v158-metal-staging/bridge/NativeBridgeMetalFrontend.mm)，第 1870–1875 行派发完成任务，第 1933–1937 行等待；[v188 补丁](v188-candidate/runtime-build/metal/0001-metal-release-empty-completion-group-v188.patch)。它不是旧报告引用的 device checkout frontend。
- [Turnip 对照源码](v188-candidate/runtime-build/turnip/control/src/freedreno/vulkan/tu_queue.cc)，437 行加锁，446/460/465/471 行早退路径绕过 593 行解锁；194–202 行为缓冲替换。[修复源码](v188-candidate/runtime-build/turnip/patched/src/freedreno/vulkan/tu_queue.cc)，[故障注入结果](v188-candidate/runtime-build/turnip-host-verification.log)。
- [PAS4 对照源码](v187-cellular/device/drivers/PongPower/ThorPas4Kernel.inc)，77–95 行；[候选源码](v188-candidate/device/drivers/PongPower/ThorPas4Kernel.inc)。候选只限制 WAITQ，不构成整个固件调用链的两秒总时限，也没有建立 modem READY/注册。
- [v188 候选报告](v188-candidate-report.md)。候选临时引导成功不等于已验证进入桌面、加载新 bridge 或消除卡死。

## 历史修复中不能靠 Rust 自动发现的问题

- v178 曾将全局 GPU 锁内的 VM 解除接线移到锁外。锁守卫可以保证最终解锁，但不能判断持锁时执行一项可能长时间阻塞的操作是否合理。[正式修复](v178-freeze-guard/device/drivers/PongAdreno/submit/NativeAdrenoPublish.cpp)，455–461 行；[回归结果](v178-display-staging/test-close.log)。未取得该次卡死的终端栈。
- 同轮修过 BoCpuUnmap 的逻辑竞态：两个调用都判断自己不是末次引用，最后计数归零却没有解除映射。v178 串行化整个转换；源码同上，1331 行起。Rust 避免数据竞争不等于自动保证分成多段的操作具有事务一致性。
- v179/v180 的诊断库使用 `-no_uuid`，缺少加载器要求的 LC_UUID；签名通过却未通过加载格式验证，诊断覆盖因此不完整。这属于链接配置错误，Rust 也能传入同样的错误参数。[加载验证证据](recovery-fastswipe-20261118.OwVa3Uzm/runtime-uuid-diagnosis.md)。
- v183 解码器遇到按下转悬停直接跳过，可能保持按下；v184 增加抬起转换。Rust enum 便于表达状态，但错误转换规则在 Rust 中同样成立。[分析与修复](recovery-v183-tv-20261118T114408/analysis-v183.md)，16、32 行；原始报文不足以确认它就是那次 TV 长按现象的触发点。
- v185 使用了另一控制器型号的 watchdog/ESD 错误码；v186 改成 SALIXP 的 0x06/0xF0 并恢复扫描。协议常量错了，Rust 不会自动知道。[分析](recovery-v185-fastswipe-20261118T133527.vZiKtk/analysis-v185.md)，20 行；当轮与最新现场错误计数均未证明命中此缺陷。
- v186 旧持久日志物理基址 0x85200000 落在 Thor PIL 保留区，确认执行过日志头写入；v187 关闭这处写入。缺少硬件读回，不能确定实际破坏的固件数据或归因为卡死。Rust 无法自动核实板级物理地址布局。[地址冲突分析](recovery-v186-freeze-20261118T160210.hYqsKl/analysis-v186-and-v187.md)，29 行起。

## 64 帧窗口结论更正

当前 v187 实际编译树的 MiniIOMobileFramebuffer.cpp 已有睡眠后 slot-ID recheck。不能继续将旧快照中缺少 guard 作为当前版本新缺陷。64 槽仍会覆盖旧完成结果，反复查询旧 ID 可持续得到 Busy/Timeout；这是保留窗口与接口语义问题，Rust 不会自动修正。v188 没加入该项补丁。

源码：[MiniIOMobileFramebuffer.cpp](v187-cellular/build-tree/.cache/xnu-work/iokit/Kernel/MiniIOMobileFramebuffer.cpp)，2645 行为 64 槽，3626–3634 行为查询和 recheck，3795 行为覆写。

## 采用 Rust 的合理范围

安全 Rust 适合减少悬空引用、重复释放、越界和数据竞争；MutexGuard、OwnedBuffer、Result、Option/枚举可以让资源生命周期和失败状态更容易审查。它不保证无死锁、无内存耗尽、无泄漏，也不保证调度、协议常量、GPU fence、DMA、缓存一致性或硬件超时正确。Result 让错误显式，并不会自动决定正确的恢复策略。

本项目已经含 Rust 图形 runtime：[NativeMetalObjects.rs](v188-candidate/runtime-build/metal/headers/bridge/NativeMetalObjects.rs)，19 行导入 runtime，64–77 行通过裸 handle 和 unsafe 访问/释放对象。外部调用方是否仍在使用 handle、是否恰好释放一次、驱动是否仍在 DMA，都需要正确的边界协议，不能仅凭内部使用 Rust 就得到保证。

当前优先级应是保存同 boot 卡死瞬间的 SpringBoard/backboardd/Preferences 与内核等待栈，结合触摸、提交、GPU 完成、CPU 写屏和扫描序号定位停滞阶段，再验证修复。Rust 可优先用于新写的缓冲管理、请求生命周期与协议状态机；没有证据支持为了这批故障先全量重写。
