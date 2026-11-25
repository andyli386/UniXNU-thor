# 上游 GPU 更新评估（一）：内核显卡驱动

2026-11-25。上游取到本地并建了只读参考检出：`thor-out/upstream-20261125/Adreno740`（UniXNU/Adreno740 主线 2962e28）。
对照对象是 v236 配方里的 `device/drivers/PongAdreno`。结论由只读研究任务逐提交比对得出，关键数字我已对 v235 内核日志复核。

## 1. 最重要的结论：掉帧的大头不在上游提交里

v235 那次启动的内核日志（`v235-intake/console-v235-kernel.txt`）：

| 行 | 含义 |
| --- | --- |
| `[gpu phase] slot=0 pid=41 publish calls=1024 maps=4100 bytes=60016336896 total_us=1587465 … gpu_total_us=777331` | 每 1024 次 backboardd 提交：提交前对 60.0 GB 做缓存维护，CPU 1.59 s（每次 1.55 ms）；同期 GPU 每个任务平均 0.76 ms |
| `[gpu phase] … publish calls=1024 maps=6642 bytes=103112908800 total_us=2309935 … gpu_total_us=6352972` | 同一次启动后半段：每次提交 98 MiB、2.26 ms；GPU 平均 6.2 ms |
| `[gpu cache] submits=5120 completed=5119 … maintain_us=21907450 MiB=916309` | 全程 5120 次提交，缓存维护共 **21.9 s CPU 时间，平均每次提交 4.3 ms** |
| `[gpu host sync] calls=13056 MiB=45752 total_us=1388703` | 显式同步通道只覆盖了 45.7 GiB（约 5%） |
| 同一行的 `distrust=0 dirty=0 stale=0 reset=0` | 缓存行审计没有发现过脏行或过期行 |

原因在 `submit/NativeAdrenoVm.cpp`：`publishMapped()` 对客户端 IOVA 窗口里的**每一个映射**整体做 clean+invalidate
（`m.memory->publishForDevice(0, m.memory->bytes(), …)`），`acquireImported()` 对每一个可写导入整体做 invalidate，
只有三类会跳过：一致性映射、`importedUntouched`、以及已登记显式同步的缓冲（`explicitSync()` / `explicitAcquire()`）。
导入的表面（1440×3200×4 ≈ 18 MiB）都没有登记，所以每次提交、每次完成都整块维护。

120 Hz 一帧 8.3 ms、60 Hz 一帧 16.7 ms；每次提交 4.3 ms 的 CPU 维护时间是帧预算的四分之一到一半。
**上游主线对导入缓冲的策略与此相同**（非显式同步的导入同样整块维护），所以最近这批提交里没有针对它的修复。
可用的杠杆在移植自己手里：显式同步 ABI（`submit/NativeAdrenoUserClient.cpp` 约 572–688 行，登记后 `useExplicitSync(true)`），
以及运行时把哪些缓冲登记进去。

## 2. 上游 50 个提交的实际情况

- 前 18 个（`6a31d45`…`c6cb924`）是历史整理后的重复提交，树与移植基线完全相同；**净变化是 30 个提交（`161c2eb`…`2962e28`）**。
- 按内容看，移植的核心文件并不真的来自 `6fa7bae`，更接近上游 10-20 到 10-26 的版本加若干挑拣；移植没有双环抢占、
  每进程地址空间、hang check / stall handler / 恢复路径、任务时间线。因此 30 个提交里大多数（约 20 个）是 N/A。
- 上游自己标注"在硬件上跑过"的只有 3 个（`8e90985`、`a7219e6`、`3fd4496`），其余写明未上硬件或没有说明。

分类汇总：能直接用的只有 2 个（`2962e28`、`3a73197`），移植已有等价实现的约 5 个，需要改造的约 7 个（多为诊断），其余不适用。

## 3. 值得拿的（按价值/风险排序）

1. `2962e28` BoMap 失败记录里对 `vm_` 判空。移植有同样的未判空读取（`submit/NativeAdrenoUserClient.cpp:881`），
   但只在 `close()` 里会把 `vm_` 置空且受 `g.lock` 和 `live_` 保护，属于潜在问题。两行，零风险。
2. `3a73197` 邮箱轮询线程在页面映射前休眠，省掉每毫秒一次的唤醒。低风险。
3. `fb190cb` 的单环核心：中断时用 thread_call 请求一次"完成处理"（取驱动锁、`retireCompleted()`、只唤醒已写回 fence 的等待者）。
   v235 日志里有任务在中断到来后 58–65 ms 才被拥有者轮询时退役；现在 1.14 ms 的等待停顿来自 1 ms 兜底而不是中断唤醒。
   **中风险**：把约 2 ms 的 acquire 挪到内核线程并放在驱动锁下，会重写等 fence 路径——也就是刚修过悬空指针的那段代码；上游未在硬件上跑过。
   动手前先加计数：1 ms 兜底和中断唤醒各占多少。
4. `5eb625f` 的一部分：每次提交用 `peekWords` 读 28 个 IB 头字，而 `peekWords` 是对所有映射的线性扫描
   （`NativeAdrenoSubmit.cpp:386`、`NativeAdrenoVm.cpp:507`）。改成只在诊断开关打开时读，或按 handle 查。
5. `93adbc1` 的一部分：前 16 次提交的行审计改为按启动参数开启。无风险，但那是启动时唯一的缓存视图探测器。
6. 诊断类：`d80b633`、`2ebea4f`（按进程统计 BO/导入/视图）、`8e90985`（记录被拒绝的 BoMap 遇到了什么）。

## 4. 崩溃类结论

- "无地址空间的连接上映射失败不再 panic"（`2962e28`）：移植有同样的读法，目前找不到可达路径。
- "一个进程的地址空间失败后 GPU 仍可用"（`4afa955`、`88241fa`）：不适用。移植只有一个共享 VM，VM 失败即毒化全部 GPU 会话，没有重启路径；
  GPU 访问出错时还有第二条独立途径（板级 `faulted()`）毒化设备。上游"忽略已锁存的 fault"不能单独搬。
- "从完成处理里唤醒等 fence 者"（`fb190cb`）：不是崩溃修复；移植没有它修的那个丢唤醒缺陷，缺的是中断时退役。
- "stall handler 不动正常工作的 GPU"（`a7219e6`）：移植没有 stall handler。将来若加 hang check，必须同时带上 `a7219e6` 和 `87371ec`
  （A730 上 BR 不动而 BV 在跑是正常现象）。

## 5. 不要原样搬的

- 追踪相关提交 `4d02bf8`、`1130044` 会重定义事件 65，而移植的切换器正在用 65（`NativeAdrenoPublish.cpp:1603-1609`）；移植接受事件 30–63，64/65 自用。
- `NATIVE_ACCELERATOR_BASE`、`task_get_gpu_role`、`PRIO_DARWIN_GPU_DENY/BACKGROUND/UTILITY`、`iokit_physical_descriptor_close_notify`：移植的内核里没有（有 `task_is_gpu_denied()`）。

## 6. 下一步建议

1. 先在手机上验证 v236（定格修复）。
2. 掉帧：做"导入表面登记显式同步"的实验。先只加计数和日志（每次提交整窗口维护的字节数、显式通道占比），
   再在运行时把渲染目标类导入登记为显式 acquire，对比 `[gpu cache] maintain_us` 和帧时间；要有画面正确性的检查，因为
   整块 acquire 当初是为了修"渲染目标读回 672/4096 像素过期"。
3. 上游内核提交按第 3 节的 1、2、4、5 先拿（都小），`fb190cb` 放到有了计数数据之后。

## 7. 未核实

- 以上全部来自读代码和一次 v235 日志，没有编译或在设备上跑过任何上游提交。
- 移植的核心文件与上游 10-28 到 11-03 之间约 40 个提交的关系未评估（hang check/重启、按需页表、每进程 VM 等）。
- "移植核心文件接近上游 10-20 到 10-26 的版本"是按改动行数找最近版本的启发式结果，不是证明过的谱系。
