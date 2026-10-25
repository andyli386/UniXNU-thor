# Thor / UniXNU — 早期启动诊断面板 v1

> 状态：已进入 UEFI 真实 PE/FD 编译，L2 检验完成。VM-HW03 尚未上机测试。

## 视觉结构（GOP 1440 × 3200，固定帧缓冲 BGRX）

| 纵向区域（物理像素） | 内容 | 数据源 |
|---|---|---|
| y=0..127 | 仅保留 XNU 的实时检查点色条和早期区域，不预涂“成功” | XNU 汇编，以 TTBR0 已验证的低地址映射访问 |
| y=160..480 | THOR/XNU 标题、任务身份、UEFI 四阶段进度 | 固件状态 |
| y=544..844 | CURRENT RESULT：明确的 PASS/FAILED、阶段 ID 与 EFI_STATUS | mEntry.Stage、返回状态 |
| y=875..1345 | LOADER / OWNED RAM、SECURITY CHECKS 两个卡片 | 真实 BootArgs/Entry/FDT/ADT 与验证位 |
| y=1380..1900 | MEMORY / BOOT CONTEXT、FAULT TRIAGE 两个卡片 | EFI 状态、failed_va、校验记录 |
| y=1925..2333 | Kernel ID、运行身份、检查统计；GuardSnapshotOk 时显示 TTBR0/TCR | UEFI 报告结构与已保存寄存器快照 |
| y=2428..2728 | 标注阶段与结果的醒目状态卡片，文本和颜色同时传达状态 | UEFI 阶段 |
| y=2820..2870 | 自动复位倒计时（仅 UEFI 故障/结束路径） | 固件 |

### 设计规范

背景 `#0A131F`，卡片 `#192534`，弱文本蓝灰，高对比白色字体。青绿色用于确定通过，琥珀色用于交接/计时，红色用于明确拒绝。不能只依靠颜色判断结果。

结构化字段分组，不再在同一个区域堆满 30 行日志。常用标签采用 3x 点阵缩放，标题 4x/5x/6x；所有位置通过固定的 GOP 1440×3200 合同限制。UEFI 绘制仍调用 `EFI_GRAPHICS_OUTPUT_PROTOCOL.Blt`，不建立新帧缓冲映射，不增加 Boot Services 在 EBS 之后的调用。

### 关于 ESR / ELR / FAR

现阶段并没有可证明安全的 **XNU 异常寄存器采集与复位后持久化通道**。UEFI 面板只显示 `NOT CAPTURED IN UEFI`，绝不将占位符“0”误当实测值。保存的 TTBR0/TCR 只有在 `GuardSnapshotOk` 为真时才显示。

UEFI 的大面板是交接前画面；**后续 XNU 阶段仍只有受限 y64..127 色条**，绿色为切换前、青色为切换后、白色为额外 VBAR 检查通过、红色为主动失败。当前没有声称内核可以在任意异常时安全刷新全屏 UI。

### 修复与验收身份

- XNU 工作提交：`7339f432cbc5e0e3e5f116b98171b1cc532a93d8`。
- UEFI 工作提交：`02ca0331f8dca10bc6603ef1ea72339b9761bf66`。
- 内核 SHA-256：`7ad45160bdfd08e17d5bbbd7c99e813b8ed828fa00340389c9b1e7f5386c8fac`。
- 固件 FD SHA-256：`52398953a801d56479a77df5d67917fdffe1705441804cd78b1fc680693146bc`。
- 单次待测 run：`thor-20261025-vm-hw03`，Android Boot v4 SHA-256：`49f7668d79c28d70d3d7eb14eada3f1c00ca0e2e59a5dd6d58e0b9e8889644de`。
- 60/60 项独立镜像验收，11 个 UI/故障标识确实存在于最终 UEFI PE，UEFI 真实 FD 2 卷/20 模块/96 链接输入，交接代码哈希与重新链接 PE 完全一致。
- 尚未得到 VM-HW03 的真机实际画面，因此“好看/可读”只能按设计与静态绑定判断，仍需录像验收。

## 下一轮

真机只执行经授权的一次性临时启动；同时检验修复后的高 VBAR 路径和新报告界面。继续保留独立的 `boot command OKAY`、`XNU status`、`Android return` 三类结果，不把看见启动画面当内核通过。

硬件证明高 VBAR/TTBR1 后稳定运行后，再计划 EL1 异常向量的可归档结构化 fault code、PAR/ESR/ELR/FAR、故障地址及截图自动解析。不要在尚未建立内存映射或异常安全性之前提前引入二维码/全面显示刷新。

## 目前刻意未解决的边界

全屏报告仍按原厂物理竖屏坐标绘制，手机横拍时文字会旋转 90 度。后续若要横屏原生排版，需先单独验证旋转 BLT 的缓存、性能和画面边界；不在 VM 关键切换修复中同时改变两套图形路径。
