# Thor 下一阶段：TTBR1 激活后的 VM 预检

## 当前真实进度
- ENTRY09 真机通过页表准备，终点在第一次 TTBR1 切换之前。
- ENTRY10G 通过机器码审计：`thor_entry09_vm_finish` 正常返回，`set_mmu_ttb_alternate` 与切换后检查真实存在。
- 本轮用户照片可见绿色、青色、白色状态条与独立琥珀色计时条；结合代码与 Android 恢复，支持已执行**有界的 TTBR1 切换后寄存器/栈成功检查**。
- 这不代表完整 VM 初始化、TTBR0 失效或 Darwin 用户态启动。原来的检查器仍主动停止。

## 单一任务分支
`feature/vm-post-ttbr1`：工作树 `worktrees/xnu/vm-dev`，基于 ENTRY10G，多个实验使用各自的不可变 run ID，不再按颜色或屏幕位置增加独立 ENTRY 分支。

## 本轮实质修改
`thor_entry10_post_ttbr1` 保留 TTBR1/TTBR0/TCR/VBAR/SCTLR 与当前栈哨兵检查，但成功后返回。
随后独立执行 `thor_vm_pre_ttbr0_retire_probe`，并在已启用的 TTBR1 下：
- 通过 `AT S1E1R`、`ISB`、`PAR_EL1.F` 检查内核代码地址可翻译。
- 检查当前 EL1 栈和 VBAR 的高地址映射，并确保即将使用的 invalid TTBR0 根与真实内存范围、页边界一致。
- 只有以上检查全部通过，才绘制白色通过标记并主动停止。
- 保留原来的绿色（切换前）、青色（切换调用返回）、红色（主动失败）以及独立琥珀色倒计时。
- **不执行 `set_mmu_ttb(invalid_ttep)`，不失效 TTBR0，也不清理原始显示/异常路径。**

## 验收与风险
| 门禁 | 必须有的证据 |
|---|---|
| L0 | Git 提交干净、变化仅限新增预检逻辑、没有危险设备操作 |
| L1 | `invalid_ttep` 范围/对齐、PAR.F 和输入高地址的边界检查 |
| L2 | 编译与 Mach-O 审计；反汇编保留 `set_mmu_ttb_alternate`、后续预检调用及 `AT S1E1R` / `PAR_EL1` 指令 |
| L3 | 唯一获授权的临时启动；白色只在新增预检全部通过后出现；Android 正常恢复 |

此阶段完成后，才能单独设计“TTBR0 退役 → TLB 刷新 → `phystokv` 修正 → 继续 `arm_vm_init`”的下一任务。不可用本轮成功替代完整内核引导或驱动验证。

**已知风险**：当前 VBAR 是否完全位于 TTBR1 高半区、`invalid_ttep` 是否落在受控内存窗口，都必须以硬件实测证明；若不满足，显示红色诊断停止，不绕过约束。早期异常处理、重启后 RAM 信息保持性尚无保证。

## 2026-10-25 研发落地（等待 L3 录像测试）

- 内核工作分支 `feature/vm-post-ttbr1`，提交 `9038c32145cdf4ae8007d9fb04a9b21d4845b85f`；机器码证明：`set_mmu_ttb_alternate` → `thor_entry10_post_ttbr1` → `thor_vm_pre_ttbr0_retire_probe`。
- 新 Mach-O `41f36f9efafc0bba7f5222de67fa318e70321b97ea7b8051f7e0f6139d9ef800`，19,274,424 字节，加载跨度 `0x133c000`。
- UEFI 任务分支同名，提交 `9d08a4d5eb7dd10767c258e5987d69e0f7bd83b4`；由布局生成器把 BootArgs、交接、ADT 及保留窗口全体向前偏移 16KiB。固件 FD `f220a02d25370a31e3868c7221854432a2c8fff1ebcf3fb5cca8efaab278ea2a`。
- 运行 `thor-20261025-vm-hw01` 的一用 Android v4 镜像位于 i386，SHA256 `6b82c1e9bf6069bb3819a515ddfe197f14efc1d56a123fd6a8cc58c2a3d946c7`，56 项独立离线检查通过；`l3` 不存在，手机尚未启动该版本。
- 真机验收应观察绿色→青色→白色是否顺序出现；**白色**如今代表额外的代码/栈/VBAR 翻译及 invalid root 边界检查均通过。红色代表主动失败，琥珀黄色只表示倒计时，不代表成功。
- 本轮继续停在 TTBR0 退役之前；不能误读为 VM 全部初始化成功或进入 Unix 用户态。
