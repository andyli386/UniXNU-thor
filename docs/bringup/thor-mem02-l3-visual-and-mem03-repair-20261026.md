# VM-MEM02 L3 视觉结论与下一版修复依据（2026-10-26）

本轮结论：MEM02 最终五行 HUD 留存修复通过；五个内存候选均被未解析保留区阻塞；XNU 已在 EBS 后运行并进入显式早期拒绝路径。4×4 网格未观察到，不能验收 TTBR0 退役、AuxKC、pmap 或 iOS。
本次工作仅分析、归档和更新交接；尚未实施源码修复、构建新镜像或执行手机命令。

## 1. 固定输入与证据等级

- 既有 run：thor-20261026-vm-mem02。
- UEFI commit：1044cb422ccce63cf382c4cc23d30c83e26c787e。
- FD SHA256：48297a9a8053b6b6624a1461a6e7ea69b2cedf4439a1b7c0c70fbcab58aaa341。
- XNU 固定 commit：3ea909494494863a0d64c83f34c727955bbfebea。
- kernel SHA256：623b444dc57fc8981562277f1eade9f1ed25818badaa3bfd03ae7ff54a5333aa。
- 已消费 Boot SHA256：0239eb9a758a9d3f7695a7de0f86c66913d3fe6ad125afdb693eb14a9e85c8ca，禁止重用。
- 原 Fastboot 日志 SHA256 重新核对为8469bf5cd5d3220fe2e35cdbaebe344cd5e2545d6e5659303c221bda2cd309a6；Mac 两个消费锁均仍存在。
- 主证据：evidence/hardware-tests/thor-20261026-vm-mem02/visual-review.json。四张 JPEG 的字节数、SHA256、原附件ID均登记；原图未复制进工程或 Git。
- 本次收到的是四张拍屏，无完整视频时间轴；不能据此测量20秒实际停留、复位触发原因或首次失败PC/PAR。Android 7/7恢复沿用原主机收据和用户本轮陈述。
- 108/108 是启动前 L2 历史结果，保持原件；当前源码工作树已到其他分支，分析始终绑定上述旧提交与同SHA产物。

## 2. 五组 FDT 内存窗口逐项结果

所有区间采用左闭右开，每组256MiB。四张照片结果一致，未见 NOT CHECKED。

| 窗口 | 起点 | 终点 | S | IN | DY | UN | CF | 判定 |
|---|---|---|---:|---:|---:|---:|---:|---|
| L0 | 0xBAC00000 | 0xCAC00000 | 22 | 1 | 15 | 2 | 0 | BLOCKED |
| L1 | 0xCAC00000 | 0xDAC00000 | 22 | 1 | 15 | 2 | 0 | BLOCKED |
| H0 | 0x800000000 | 0x810000000 | 22 | 1 | 15 | 2 | 0 | BLOCKED |
| H1 | 0x810000000 | 0x820000000 | 22 | 1 | 15 | 2 | 0 | BLOCKED |
| H2 | 0x820000000 | 0x830000000 | 22 | 1 | 15 | 2 | 0 | BLOCKED |

固定 thor_fdt_gate.h/c 的语义：
S22=THOR_FDT_UNRESOLVED_RESERVATION。IN1只代表完整窗口包含在启用的memory bank。DY15是全树dynamic_count，不是每窗口15处冲突；UN2是全树两个未解析保留节点。CF0只代表已解析的FDT blob、memreserve、fixed reg和initrd没有区间重叠，不覆盖未解析项、安全世界、DMA与完整UEFI所有权。
因此五组没有“谁已可用”的排序结论，也不能据此开放ConventionalMemory。UEFI CHECKS PASS针对既有64MiB诊断加载流程，与新增256MiB候选的BLOCKED并不矛盾。

首张还能读到：FDT GATE22、LIBFDT0、COMPLETE1、CRC32 FFCF5833、PAD WARNING1 OFFSET0000269B。
固定解析器先在581–584行拒绝unresolved，再在586行检查padding。因此UN2解决后可能仍返回S11 BAD_PADDING；不能静默清零原始快照来获得通过。

既有证据已提供UN2强候选：trust_ui_vm_region@e0b00000 / phandle0xB8，以及ramoops_region / phandle0x263。
i386 analysis/hardware-tests/20261024-fresh-held-fdt03-02/screen-evidence.json、analysis/thor-memory-probe-03/runtime-report.json及旧recovery-runtime-fdt.dtb可复用。
旧节点均无reg/size/status，不能按disabled占位忽略；节点名@地址也不能替代完整占用范围。
本轮CRC与旧记录不同，MEM02只显示计数，所以本轮UN2身份仍UNKNOWN，不能把旧候选写成已确认事实。

## 3. 实际启动阶段与4×4逐槽判定

照片依次覆盖UEFI2 KERNEL HASH PASS、UEFI3交接就绪，以及同一UEFI3冻结背景上出现的窄条。
固定内核对应：绿y64..77是thor_entry09_vm_finish预切换检查通过；青y78..91是在set_mmu_ttb_alternate返回后进入thor_entry10_post_ttbr1时写入，位于其后各项寄存器检查之前。
红y106..119对应thor_entry09_fail显式拒绝，琥珀y120..127对应thor_entry09_stop有界停止路径。
因此可以确认EBS后XNU已执行、arm_vm_init已进入、首次TTBR1安装调用已返回；不能单凭青条宣称全部TTBR1读回、栈、VBAR或root守卫通过。
UEFI3 BEFORE EBS是静态背景，不能用它否定EBS之后的XNU执行。

| 格号 | 固定意义 | 本轮状态 |
|---|---|---|
| 00 | 预VM/TTBR1设置基线 | NOT_OBSERVED，窄绿条不等于00 |
| 01 | TTBR1基线 | NOT_OBSERVED，窄青条不等于01 |
| 02 | 高地址向量/root有效 | NOT_OBSERVED |
| 03 | 高地址grid alias安装 | NOT_OBSERVED |
| 04 | 原生TTBR0空根+TLBI | NOT_OBSERVED |
| 05 | kva_active与指针写入 | NOT_OBSERVED |
| 06 | 指针重绑后守卫通过 | NOT_OBSERVED |
| 07 | 即将调用AuxKC | NOT_OBSERVED |
| 08 | AuxKC返回 | NOT_OBSERVED |
| 09 | metadata完成 | NOT_OBSERVED |
| 10 | pmap前守卫通过 | NOT_OBSERVED |
| 11 | 保留 | 不作为失败项 |
| 12 | 保留 | 不作为失败项 |
| 13 | 保留 | 不作为失败项 |
| 14 | 保留 | 不作为失败项 |
| 15 | 保留 | 不作为失败项 |

grid_init成功时会清除旧窄条并绘出16格。现有画面保留绿/青/红条与低VA倒计时，与网格初始化之前失败的固定控制流一致。
没有本轮TTBR0退役或AuxKC返回证明；pmap_bootstrap、完整arm_vm_init返回和用户态本来就不在此有界诊断路径的完成范围内。

## 4. 确定性缺陷：低地址映射90页，消费者要求315页

对artifacts/candidates/thor-20261026-vmdev-010/kernel.macho重新计算SHA，并由Mach-O段表定位实际指令，独立解码：

| 生产者/消费者 | 指令VA | 文件偏移 | 指令字 | 实际页数 |
|---|---|---:|---|---:|
| thor_entry09_build_tables | 0xfffffff00740370c | 4192012 | 0x52800b50 / mov w16,#90 | 90 |
| thor_vm_hw07_fb_prepare | 0xfffffff0073faba0 | 4156320 | 0x52802776 / mov w22,#315 | 315 |

实际低地址identity覆盖[0xb805a000,0xb80b4000)，后续要求[0xb805a000,0xb8195000)。
若执行抵达该检查循环，第i=90页（第91次）PA0xb80b4000会缺少所建PTE，writable探针应返回0并走thor_entry09_fail。
PAR_EL1.F=1是由固定页表与指令推导的预期，并非本次寄存器采样。红条也可能由更早post-TTBR1、VBAR/root守卫触发；本次首次失败PC仍UNKNOWN。
该缺陷足以阻断网格初始化，是下一版应先修复的确定性问题；不能通过删守卫或强制返回成功绕过。

固定patch来源：zzzzz-thor-entry09-vmprep.patch:588仍mov w16,#90；zzzzzzzzzzzzzzzzzzzz-thor-vm-hw11-large-grid-3x.patch扩大了315页检查。
旧scripts/audit-thor-hw11-grid-linked.py检查prepare的315/TLBI/grid调用，却没有联查bootstrap生产者；所以108/108历史PASS与本缺陷可以同时成立，应补验收能力而不是修改旧收据。

## 5. 下一版修复与验收顺序

1. P0：在独立新revision中统一bootstrap低地址identity、315页高地址alias和消费者范围。315页占用现有L3索引90..404，仍在512项表内；保留属性、全零/边界守卫，无需新页表，更不涉及256MiB RAM授权。
2. P0：补跨函数真实机器码/PTE覆盖验收。旧90/315组合必须拒绝；新范围逐页检查VA/PA/属性，覆盖首尾页及范围外负例，避免只搜315常数。
3. P0：失败诊断写入已有安全90页显示区，显示阶段号、失败页号/PA、PAR原始值；区分post-TTBR1、VBAR/root、20页旧alias、315页identity和high-alias。不得依赖尚未成功建立的grid。
4. P1：复用ThorFdtBdsDxe.c现有unresolved渲染，显示同次快照的节点名/phandle/offset、reg/size/alloc-ranges/status/no-map；每窗口增加dynamic_may_allocate_in_window_count，与全树DY分开。
5. P1：保存快照CRC/hash与padding位置，核实UN2候选和padding来源；不白名单放行、不篡改原始快照。
6. P2：256MiB授权继续独立BLOCKED。复用analyze_thor_ddr_ownership_candidates.py与darwinboot_thor_memory_gate.py，待真实UEFI descriptors、活动占用、映射/PA能力、固件/secure/GOP/DMA排除闭环后再讨论内存发布。

任何下一轮L3均需新Run ID、新seeded image、完整新L2和当轮录像授权。MEM01/MEM02已消费镜像不再启动；本轮没有执行adb/fastboot或分区操作。
