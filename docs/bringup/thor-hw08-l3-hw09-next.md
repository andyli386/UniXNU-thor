# Thor / UniXNU — HW08 L3 结论与 HW09 内核初始化路线

日期：2026-10-25（北京时间）。状态：**HW08 有界真机 PASS；正常 XNU 初始化尚未恢复**。

## 1. HW08 真机证据与代码映射

- Run：`thor-20261025-vm-hw08`；XNU commit `70188b2150d3880c8e8796c81bee90f73f56c938`；kernel SHA-256 `96b152809ded39de6c8316b031536896924a9b7b4416d97e1cb1b6a3d4876553`。
- UEFI commit `8a5a6bf7992142a8106b80a9bd6e82ab447a290a`；FD SHA-256 `ad296c65d1eaf4d90f885adb593c11d1974bdc88bcf160589ebf9cf12027a951`。
- Boot v4 SHA-256 `8170a15033dcbfe01e5b8396da66bfd77dc91e91fac1b0946b852eddace1620b`；**单次已消费，绝不再次引导**。
- L2 独立检查 72/72，实际链接代码 143 个后退役 CFG 状态，3 项危险路径故障注入均被拒绝。
- 23:35:20 单次 Fastboot boot 命令被接收；23:36:08 Android 7/7 恢复，_a 槽位未改变。无刷写/切槽/自动重试。
- 用户 4 张照片覆盖 UEFI 加载/内核校验及随后绿、青、洋红（视觉偏红）、黄四段 XNU 标记，未出现白色成功带或琥珀倒计时。
- 绿 y64–77：旧映射下预检查；青 y78–91：TTBR1 切换后检查；**空白 y92–105**：以前的白色检查槽位，本轮故意不绘制；洋红 y106–112：真实 XNU 空 TTBR0 和 TLBI 后高 VA framebuffer 写入；黄 y113–119：kva_active 及 cpu_tte、invalid_tte 高 VA 重绑定后的写入。
- 此次洋红/黄色没有调用旧低 VA framebuffer；黄色由 `thor_vm_hw08_post_retire_gate` 内的地址翻译和寄存器检查保护，匹配链接机器码的执行顺序。
- UEFI dashboard 的 `UEFI 3 BEFORE EBS` 及进度信息是在交接前绘制的静态背景。HW08 禁止旧低 VA `thor_entry09_stop()` 倒计时，因此没有以前的琥珀进度条。

## 2. 现阶段真正进入了哪里

已完成：UEFI 交接、XNU `__start`、`arm_init()`、`arm_vm_init()` 的前半阶段，以及原生 TTBR0 空根切换、TLBI、`kva_active=TRUE`、两个页表指针高地址重绑定。

**有意停止的位置：** `arm_vm_init.c` 在重绑定后直接调用 `thor_vm_hw08_post_retire_gate(args)`，该函数 `noreturn`，画黄色标记、等待约 12 秒并使用无栈依赖的 PSCI reset。HW08 不恢复旧 TTBR0，不返回其余内存初始化代码。

**尚未完成：** `arm_vm_auxkc_init()`、`pmap_bootstrap(dynamic_memory_begin)`、`arm_vm_init()` 返回、`serial_init()`/kprintf 输出、`machine_startup()`、`kernel_bootstrap()`、`bsd_init()`、用户态程序或可用的驱动。

真实源代码顺序：`arm_vm_init.c` 中 **`arm_vm_auxkc_init()` → `pmap_bootstrap()` → `arm_vm_init` 返回**；然后 `arm_init.c` 才能执行 `serial_init()`、后续 `machine_startup()`。不可把 `pmap_bootstrap` 写成 `arm_vm_init` 返回之后才发生。

## 3. HW09 推荐分阶段验收

1. **HW09-A 仅主机：** 审查移除 `noreturn` 门禁之后的全部高地址引用、辅助 kernelcache、可用页/物理窗口。当前 BootArgs 仅授予明确的 64 MiB owned RAM；不能推断其他 DRAM 已获授权。确认临时 framebuffer 高 VA alias 会不会与 XNU 低全局映射或 pmap 初始化冲突。
2. **HW09-B 边界探针：** 在仍保持空 TTBR0 和受控复位前提下，先通过 `arm_vm_auxkc_init()` 或进入 `pmap_bootstrap()` 前的新检查点；保持可用于空 TTBR0 状态的高 VA 诊断，不调用低 VA UEFI/GOP 接口。
3. **HW09-C 正常 VM：** 解决内存授权/页表生命周期后，推进 `pmap_bootstrap` 完成以及 `arm_vm_init` **真正返回**。分别记录调用进入、返回、异常、余量；进入下一段前验证全部安全边界。
4. **后续 Mach：** 从 `arm_init` 的 `serial_init` / kprintf、`machine_startup` 推进到 `kernel_bootstrap` 和实际调度，最后才是 BSD/userland；另行完成 GIC、timer、UART/console、存储、显示等必要适配。

## 4. 验收底线与时间判断

- 完成 HW08 并不等于 Darwin 启动。它证明已有真正 XNU 内核执行和有限的 MMU 迁移里程碑。
- 最近的可交付里程碑是 `arm_vm_init returned`；成功后才有条件争取完整 kernel startup。硬件设备驱动、根文件系统和用户态还需要独立迭代。**无法据目前证据负责任地承诺具体哪一天能够进入用户态。**
- 不能直接删除 HW08 停止函数并运行原生后续代码；先解决 64 MiB owned RAM 的资源边界、临时 framebuffer 别名生命周期和后续映射前提。
- 后续真机验证只能用新 kernel / 新 seed / 新镜像，先全部 L0–L2 再明确授权录像，最多一次 `fastboot boot`，不执行 flash/erase/format、切槽和自动重试。

原始主机收据：`/Users/<user>/projects/thor-unixnu/evidence/hardware-tests/thor-20261025-vm-hw08/technical-summary-awaiting-video.json`（历史快照）；最新视觉审核 `visual-review.json`（四张照片 SHA/观察，原图保存在聊天附件，不在 Git）。
