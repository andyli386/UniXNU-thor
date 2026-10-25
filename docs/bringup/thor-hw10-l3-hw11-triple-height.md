# Thor / UniXNU — HW10 L3 视觉确认与 HW11 三倍行高诊断

记录日期：2026-10-26（北京时间）。本文件是可版本化阶段说明；原始镜像与真机记录以独立 run 收据为准。

## 1. HW10 真机检查点结论

- HW10 Run ID：`thor-20261026-vm-hw10`。Fastboot 单次临时启动成功，Android 7/7 回归，镜像消费锁已写入，**绝不可再次引导原镜像**。
- 用户提供 5 张手机拍屏。结合已验证的实际 XNU 机器码，可见 4×4 矩阵前 **00–10 共 11 格完成色**。
- 按实际手机方向读取：R1 00–03 为绿/青/白/浅紫，R2 04–07 为洋红/黄/橙/蓝，R3 08–10 为薄荷绿/紫/白；R3C4(11) 和 R4C1–C4(12–15) 为未定义的灰色保留位。这些灰格**不表示失败**。
- 第 10 格白色意味着走到已编译并审核的 pmap 前保护检查点；它**不证明 `pmap_bootstrap` 已执行，也不证明 `arm_vm_init` 返回**。
- 双机不可变实测证据：Mac `evidence/hardware-tests/thor-20261026-vm-hw10/visual-review.json`；i386 `runs/thor-20261026-vm-hw10/l3/visual-review.json`，SHA-256 均为 `958a9877be29b85d6c347dbbed67e47587dfab8e0d91a843ca419fe363033272`。
- 原始五张 JPEG 保留在聊天附件，没有放入 Git 或向设备传输；视觉和机器码证明有界检查点，但没有逐条 UART/ETM 寄存器轨迹。

## 2. HW11：诊断 UI 物理空间变化

- 用户需求：原 HW10 格高 14px 太窄，将每格高度**精确放大到 42px（3 倍）**；纵向行间距由 2px 调整为 12px，保持 4 行 × 4 列。
- 每格宽 328px；横向格间距仍 24px。矩阵 16 格，每格固定下标，灰色表示尚未完成，完成后颜色不会覆盖其他格。
- 将已知 GOP framebuffer 基地址 `0xb8000000`、1440×3200、pitch 1440 的 y64..287 定为 XNU 绘图区，物理 `[0xb805a000, 0xb8195000)`，总高 224 行，恰好 315 个 4KiB 页。
- 新的高 VA framebuffer alias 占已确认全零的原 TTBR1 L3 **0..314**；既有 HW07 别名 480..499 不变；没有新的 L1/L2/L3 页表分配，不扩大内核 64MiB DRAM 授权。
- 所有 315 个物理页都须在改写页表前由 EL1 写翻译检查逐页确认。新 alias 设置后 TLBI/DSB/ISB，并逐页检查写映射；画格子时逐行 DC CVAC + DSB SY。
- 具体格子位置：`x=16+col×352`，`y=8+row×54`（相对 y64 起点）；42px 格高和 12px 竖向间距均落在 y64..287 内。

## 3. UEFI GOP 界面避让

- 旧版 UEFI HUD 开始 y160，与放大后矩阵冲突。HW11 在独立 UEFI 源码工作树整体将 HUD 的固定 y 坐标下移 **192px**。
- GOP 每次整屏清除后，**y0..319** 保留给 XNU，UEFI 第一条非背景绘制从 y352 开始；四张 UEFI 阶段卡从 y581 开始，完成结果卡从 y736 开始，最下方绘制不超过 y1036。
- 继续保留 `UEFI 3 BEFORE EBS` 的静态阶段背景；这不是 XNU 的实时内核日志。GOP 不在 ExitBootServices 后被调用。
- 更改了 `ThorReportHud.inc.c` 和受 Git 管理的 `VM_POST_TTBR1_PROVENANCE.json`，其中 HUD SHA-256 与本轮最终固件发布证明完全一致。

## 4. 冻结版本和主机验收

- XNU 分支：`feature/vm-hw11-large-grid-3x`，提交 `3ea909494494863a0d64c83f34c727955bbfebea`。
- 内核 Mach-O：SHA-256 `623b444dc57fc8981562277f1eade9f1ed25818badaa3bfd03ae7ff54a5333aa`。二次可复现链接字节相同，entry、vectors、segment/loaded span 等 ABI 几何与 HW10 一致。
- 真实机器码审核 `runs/thor-20261026-vmdev-010/l2/hw11-grid-machinecode.json`：16 格、315 页映射、42px、12px、243 个 pmap 前 CFG 状态、9 类故障变异全部拒绝。
- UEFI 分支：`feature/vm-hw11-large-grid-3x`，提交 `bffe024d5960bce9387d17a8c874fff4f059e6a1`。
- 固件 FD SHA-256：`c755420c2be7c100349c277f3462087fa8aaddb6e7fd6bc036093b0635fde5c5`；两轮真实链接一致，固件 2 卷、20 模块、96 链接输入，500 字节 UEFI 交接代码与固定哈希一致且无区间重定位。
- 全新一次性 Boot v4：i386 `runs/thor-20261026-vm-hw11/l2/package/thor-boot.img`，SHA-256 `b66998bf69d9f31ea2910df3a757ca30482654599fbe5db9e7e2b135238094cf`，25,894,912 字节。
- 独立门禁 **82/82 PASS**；`thorctl check/package --verify/device prepare` 全部 PASS；最新 Android 7/7 实时只读检查通过。
- 启动控制器 `runs/thor-20261026-vm-hw11/l2/controller` 为 `READY_NOT_EXECUTED`。i386 新镜像未消费、`l3/` 不存在；所有 HW07–HW10 镜像消费锁保留。
- Mac mini `evidence/build/thor-20261026-vm-hw11/` 已镜像 i386 的 7 份主机 L2 证据，逐份 SHA-256 核对。没有转移 boot 镜像或随机 seed。

## 5. 本轮 L3 验收协议（必须再次授权）

- 只有用户使用另一设备开启录像并明确回复“录像开始”后，才能运行单次 `adb reboot bootloader` + 单次临时 `fastboot boot`；严禁永久写入、擦除、格式化、切槽或自动重试。
- 录像重点：界面最顶部的 4×4 矩阵是否变为更高、更宽间隔的独立格子；是否保持前 11 格递增点亮；保留位 11–15 应继续灰色。
- 若 315 页的 EL1 翻译检查失败，内核会在退役 TTBR0 前执行原有有界失败路径，不能仅凭 Fastboot OKAY 宣称 HW11 成功。另行核对 Android 恢复 7/7。
- 下一步推进 `pmap_bootstrap()` 前，必须独立设计映射生命周期、异常路径和 64MiB RAM 授权闭包；不能直接删除 HW11 的 `noreturn` 停止检查。
