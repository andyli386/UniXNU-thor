# Thor / UniXNU — VM-HW04 退役前检查候选冻结记录

记录日期：2026-10-25。状态：**已通过 L2 构建验收，未进行 L3 真机启动**。

## 前一条可证实的真机里程碑

VM-HW03 (`thor-20261025-vm-hw03`) 的用户照片显示绿色、青色、白色状态条与独立琥珀色倒计时；源码与链接机器码证实白色仅在实际 TTBR1 切换、代码/栈/向量高地址翻译、`VBAR_EL1` 写入和读回检查之后出现。Android 7/7 恢复。**没有退役 TTBR0，`arm_vm_init` 没有完整返回**。

## VM-HW04 的范围与实际证据

新增 `thor_vm_ttbr0_ready_gate`，目标是在不修改 TTBR0 的情况下：

1. 确认 `invalid_ttep` 和 `cpu_ttep` 与已知内存分配、页对齐及 TTBR1 root 一致。
2. 使用 `phystokv` 得到两个页表根的高地址映射，执行五次 `AT S1E1R` 相关翻译探测。
3. 通过已映射高地址逐个读取 4 KiB 的 `invalid_ttep` 页表项，验证整个候选根全为零。
4. 全部通过后才保留白色阶段标志，并沿**保留旧 TTBR0 映射的安全停止路径**回到 Android。
5. 不调用失效 TTBR0、全局 TLB flush 或 `pmap_bootstrap`，也不宣称正常 Darwin/Unix 启动。

新增检查由实际 Mach-O 反汇编证实，不只是源码搜索或封装工具估计。`phystokv` 中途会读取 `ptov_table`，因此只有第一次真机执行取得绿色/青色/白色的独立视觉证据后，才能升级阶段结论。

## 可重复核验的源码、产物与收据

| 记录 | 值 |
|---|---|
| XNU 任务提交 | `b5e4dd24b0df617d8c5113cd4b32e0ebbd95ac2c` |
| UEFI 任务提交 | `f697772480b52af5b88fa51a698d2e22f4e27028` |
| XNU 内核 SHA256 | `4eedcd2a9223746bfcb6c730d9f1aa25d91de67776d50458883817fe1fe4360e` |
| UEFI FD SHA256 | `758510fa21314305f45331a85c19988f51315d9b3a5f6517c9e5ea3d5c6adc27` |
| 一次性 `boot.img` SHA256 | `3edd1173c327dc78c4590abde4721b8006600567f5c5f274fd7937b41a625882` |
| 真机 run ID | `thor-20261025-vm-hw04` |
| 独立 L2 条件 | 62/62 PASS |
| 实测 Fastboot 次数 | 0 |
| 编译/构建工具回归 | 新增 Mac 双机真实性测试，`16/16` PASS |

Mac mini 的版本化收据存放于 `evidence/build/thor-20261025-vm-hw04/`，最新 `crosshost-verification-receipt-v2.json` 已取代旧的“未镜像”状态快照。UEFI 与 XNU 均建立了 `experimental/vm-ttbr0-root-readiness-unverified` 本地实验引用；没有修改正式集成主线或推送外部仓库。

i386 的一次性镜像存放于 `runs/thor-20261025-vm-hw04/l2/package/thor-boot.img`。**只保存在设备试验节点，不复制熵种子到 Mac/Git**。原始手机备份与旧测试镜像没有被删除或覆盖。

## L3 触发与验收

只有在用户明确同意录屏并要求开始时，才运行审查过的单次控制器。该控制器在当次执行前再次核对设备身份、7 项 Android 状态、镜像 SHA256、固件版本、未消费标志；最多一次 `adb reboot bootloader` 与一次 `fastboot boot`，不执行 `flash`、`erase`、`format`、切槽或自动重试。

验收 **必须分别记录**：Fastboot 接受命令、XNU 独立的白色根检查点、Android 返回。若看到红色停止，首先依照新 `thor_vm_ttbr0_ready_gate` 的条件分析，不能将 Fastboot 命令 OKAY 当作内核通过。

## 阻塞的后续阶段

VM-HW04 若获得白色成功标记，才能规划真正的 TTBR0 失效、后续 TLB 一致性及完整 `arm_vm_init()` 延续。旧 framebuffer 与安全停止路径仍使用低地址，**真正撤销 TTBR0 前需要单独建立不依赖旧低地址映射的诊断与恢复通道**。
