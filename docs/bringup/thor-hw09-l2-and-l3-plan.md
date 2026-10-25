# Thor / UniXNU — HW09 L2 验收与一次性真机测试交接

阶段：HW09（2026-10-26 CST）。状态：**L2 READY，尚未得到 L3 真机证据**。

## 执行目标与严格边界

HW09 不是完整 `pmap_bootstrap()` 或 `arm_vm_init()` 返回。真实 XNU 在 HW08 已验证的空 TTBR0、TLBI、`kva_active`、页表指针高地址修正之后，使黄色检查例程返回；随后执行原生 `arm_vm_auxkc_init()`（只允许 no-AuxKC 条件），继续内核缓存物理界限、内存数据和 `ROUND_TWIG` 元数据计算，在调用 `pmap_bootstrap()` **之前**进入独立蓝色高地址检查点，并在约 12 秒后通过 stackless PSCI 复位。

预计顶部颜色：绿 y64–77，青 y78–91，白色空白槽 y92–105，洋红 y106–112，黄 y113–115，蓝 y116–119。蓝色仅覆盖原黄色条下方四行，不是新占用 framebuffer 映射。没有白色成功条或旧低 VA 琥珀倒计时。

## 受审计版本

- Mac mini XNU：`worktrees/xnu/vm-dev` / `feature/vm-hw09-auxkc-pre-pmap`，commit `04dd4e0bb672495a520ef156a467843339761f5b`。
- XNU Mach-O SHA-256：`d099eec672dca18cda4eb8aa2d7e95d5eae03dfa93c272b0a102ac6d76f40439`；与 i386 字节完全相同，entry/vector 与 HW08 同布局。
- Mac 真实机器码证明：`runs/thor-20261025-vmdev-008/l2/hw09-machinecode.json`；真实 `arm_vm_auxkc_init()` 调用、AuxKC 无附加内核路径、到蓝色的前驱 CFG 133 状态，蓝色后的受控停止 CFG 213 状态、5 类故障路径变异拒绝。
- i386 UEFI：`worktrees/uefi/hw09` / `feature/vm-hw09-auxkc-pre-pmap`，commit `995d6f76ffdf5a097e6f22db61db8ccc18fd033a`。
- 最终 UEFI FD SHA-256：`e838a6975c2dbba0c42c15108d121bc5ce2f31fed02e09a7123c66ff1c2d674c`；两次实际链接一致，2 个固件卷、20 个可执行模块、96 个链接输入，最终 500 字节交接 SHA-256 `29ee4adbbb30fe06bcebf9f9af9723f98698c6170636b2bb24e52a7a17fe9d24`。
- 全新 Boot v4 一次性镜像 SHA-256：`4b7ea211580149d5cd086ceb118a74faa05b647068289143c787a048ff838fb4`，25,894,912 字节。随机 seed 仅留在 i386，绝不拷贝到 Mac、Git 或聊天。HW08 和 HW07 镜像消费标记仍存在。

## 独立验收

- i386 `runs/thor-20261025-vm-hw09/l2/package/independent-offline.json`：**72/72 PASS**。
- `thorctl check`、`package --verify` 和 `device prepare`：全部 PASS，证据在同一 HW09 的 `l2/`。
- 机器码可达性：蓝色检查点前已进入原生 AuxKC 及元数据计算；未调用 pmap。错误输入、缺失 TLBI、跳过 AuxKC、绕过蓝色、替换低地址写入或错误颜色均被检查器拒绝。
- Android 最新只读预检：7/7 PASS、设备 `thor`、启动槽位 `_a`、`zygote` running、`sys.boot_completed=1`。
- `l2/controller/controller-preflight.json` 为 `READY_NOT_EXECUTED`；限制最多一次 ADB bootloader reboot、一次 `fastboot boot`，自动重试 0，禁止 flash/erase/format/切槽。
- `l2/final-host-gate.json`：`PASS_L2_READY_AWAITING_EXPLICIT_RECORDED_ONE_USE_HARDWARE_TEST`。**HW09 尚无 L3，镜像未消费。**

## 唯一一次硬件操作协议

操作者以另一台设备连续录制手机屏幕后，回复当轮“录像开始”才能触发 HW09 临时启动。任何错误都不能自动重试。保存 Fastboot、独立洋红/黄/蓝视觉证据、Android 7/7 恢复检查与一次性镜像已消费标记。Fastboot OKAY 和 Android 回归不能独立证明蓝色成功。收到照片后结合链接机器码再决定 HW09 L3 是否达到有界 PASS。

不能把达到蓝色当成 `pmap_bootstrap()` 已执行，也不能宣称完成 `arm_vm_init()`、进入 `kernel_bootstrap()` 或 Unix 用户态。
