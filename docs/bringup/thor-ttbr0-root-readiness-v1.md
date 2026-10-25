# Thor / UniXNU：VM-HW03 成功基线与下一阶段 TTBR0 根页验收

> 本文来源于 2026-10-25 真机照片、实际 Mac ARM64 链接产物、i386 离线门禁。任何“可启动 Unix 系统”的表述都不成立。

## 真机里程碑：VM-HW03

- 镜像 SHA256：`49f7668d79c28d70d3d7eb14eada3f1c00ca0e2e59a5dd6d58e0b9e8889644de`。
- XNU：`7339f432cbc5e0e3e5f116b98171b1cc532a93d8`，UEFI：`02ca0331f8dca10bc6603ef1ea72339b9761bf66`。
- 用户原图 7 张；原图 SHA256 列表已在 `evidence/hardware-tests/thor-20261025-vm-hw03/visual-milestone-review.json` 留存（原图仍属于 ChatGPT 附件，未传入两台机器）。
- 绿色 = 页表预备和切换前检查；青色 = 真正 TTBR1 调用返回；白色 = 原代码的 3 次高 VA 翻译、高地址 VBAR_EL1 写入 + ISB + 读回；琥珀色 = 独立倒计时。
- UEFI 的新版诊断卡片真实显示，Android 7/7 项恢复通过。**TTBR0 未退役，`arm_vm_init` 未返回，也没有 Darwin 用户空间。**
- Mac 本地两仓库均有 `thor-vm-hw03-ttbr1-vbar-verified-20261025` 标签，指向各自源码验证提交；没有推送到原始上游。

## 技术差距：为什么不能直接使 TTBR0 失效

1. 当前 `arm_vm_init` 中正常顺序为 `set_mmu_ttb_alternate(cpu_ttep)` → `set_mmu_ttb(invalid_ttep)` → `flush_mmu_tlb()` → `kva_active` → `phystokv` 修正 → `pmap_bootstrap` 等。
2. 现有早期汇编 `thor_entry09_mark/stop/fail` 的显示写入仍依赖低地址 framebuffer 和当前 TTBR0。**直接撤销 TTBR0 将破坏诊断和可能的受控退出路径。**
3. 需要先验证空 TTBR0 根页确为零、地址通过新的 TTBR1 可访问、CPU 根表与新物理孔径对齐、异常向量已是高地址。仍需单独设计退役之后的安全输出/复位，不依赖低地址帧缓冲。
4. `phystokv` 与内存管理目前仍限定在已拥有的 64 MiB 窗口；不能把后续 `pmap_bootstrap` 或完整 DarwinBoot 所需内存视作已经解决。

## 当前下一候选：VM-HW04（**仅 L2 主机通过**）

- XNU 任务分支：`feature/vm-post-ttbr1`，内核源码提交 `d2e4da2`，构建流程修复提交 `b5e4dd2`。
- 真实新内核 SHA256：`4eedcd2a9223746bfcb6c730d9f1aa25d91de67776d50458883817fe1fe4360e`。
- UEFI 源码提交：`f697772480b52af5b88fa51a698d2e22f4e27028`；新 FD SHA256：`758510fa21314305f45331a85c19988f51315d9b3a5f6517c9e5ea3d5c6adc27`。
- i386 新镜像：`runs/thor-20261025-vm-hw04/l2/package/thor-boot.img`；SHA256：`3edd1173c327dc78c4590abde4721b8006600567f5c5f274fd7937b41a625882`。
- `thor_vm_ttbr0_ready_gate` 真正编译进内核。两次 `phystokv`、五次 `AT S1E1R`、完整 4KiB 零页遍历后才画白条并主动停止。
- `runs/thor-20261025-vmdev-004/l2/ttbr0-root-machinecode.json`：`PASS_LINKED_ZERO_ROOT_AND_HIGH_PHYSVA_GATE`。
- 固件审计 2 卷 / 20 模块 / 96 链接输入，交接代码 500 字节实际 PE 的 SHA 再次与源码预期匹配。
- 独立离线门禁 **62/62 PASS**；一用镜像未执行；当前手机仍在 Android，手机操作 0 次。

## 构建可复现与归档限制

历史十多层 XNU patch 的重复反向/正向重放曾造成源树冲突。原残缺状态保留在 `.cache/xnu-work-after-replay-conflict-20261025`，没有删除；新的源树使用只读 ENTRY10G 克隆顺序应用 3 个 VM 专用补丁。构建脚本通过 `THOR_XNU_PATCH_MODE=prepared` 和最新补丁反向检查实行快速构建。

Mac 已在 `repos/UniXNU-thor` 归档 `experimental/vm-ttbr0-root-readiness-unverified`。i386 保有新的 UEFI 工作树/提交与一次性镜像。**i386→Mac UEFI 固件与 Git bundle 完整归档尚未完成**：传输过程中连接器阻断，Mac staging 只保留不完整 `.partial`，不可使用或宣称为完整副本。不要绕过限制，也不要以未校验部分替代真实制品。

## 真机 L3 门禁

测试必须再次明确录像授权；一轮一张随机种子启动镜像，`fastboot boot` 最多 1 次，禁止 flash、erase、format、切槽和自动重试。

预期：原绿/青色先出现；只有新 TTBR1 下的空 root 高地址映射、完整零页、当前栈和高 VBAR 均通过时才出现白条。琥珀色仍表示计时。白色仍**不代表 TTBR0 已失效**。确认 Android 返回后，将屏幕证据与构建、设备恢复独立归档；拒绝单靠 Fastboot OKAY 认定内核通过。
