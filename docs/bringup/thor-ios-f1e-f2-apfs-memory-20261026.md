# Thor / UniXNU 原生 iOS 启动推进：F1e、Darwin22、APFS 与内存所有权

日期：2026-10-26。关键目标是 **原生 iOS 16.6.1 SpringBoard**，不是仿制屏幕或只让预内核诊断颜色通过。

## 1. 已取得的真实证据

| Gate | 结果 | 证据 |
| --- | --- | --- |
| F1c | 主机完整 UniXNU UEFI 含 31 EFI 模块，并排除旧版冲突 BDS | i386 runs/thor-20261026-ios-full-f1c-host-01/l2 |
| F1d | 通用 ARM64 EDK II QEMU 运行真正的 UniXNU.efi，成功读 FAT 配置并显示 1280×800 菜单 | Mac runs/ios-f1d-qemu-host-20261026/qemu-visible-menu-proof.json |
| F1e | FAT 48MiB 限制提高至 60MiB，保持 initrd 64MiB 上限；真实 UEFI FD 重新链接及独立审计 | i386 worktrees/uefi/ios-full-f1e，commit b9f46784bb2c339764f17416a4fbc71609db2824 |
| F1e/F2 | 原始 20G81 APFS 内核被写入 FAT56、逐字节取出、THORFAT1 封装并 CRC 回读 | Mac runs/ios16-f1e-full-kernel-on-fat56-host-20261026 |
| F2 | Darwin22/XNU8796 ARM64 原生 Mach-O 真正编译链接；NativeUfs 11 个真实原版存储导入绑定 | Mac reports/thor-ios16-storage-bound-native-host-20261026.json |
| F2+APFS | APFS 等 11 个原版 20G81 可执行组件合并、12972 个指针重绑定、哈希审计 | Mac runs/ios16-apfs-full-closure-host-20261026 |
| 物理存储图 | 厂商 Thor 和 Pong 的 SM8475 UFS MMIO、IRQ、PHY、SID 0xe0 对齐，Interconnect/PHY Reset phandle 存在板级差异 | Mac reports/thor-pong-ufs-dt-diff-20261026.json |
| 完整内存 | 明确 BLOCKED：目前 F1c/F1e 固件可分配 124MiB，DarwinBoot22 至少要求 256MiB | Mac reports/thor-darwin22-memory-gate-20261026.json |

所有前述 F1e/F2 编译结果仍均为 HOST ONLY，**不是手机启动镜像**。HW10 旧有 pre-pmap 真机颜色证据属于不同 Darwin23 内核，不能直接归属 Darwin22；HW11 原始候选仍是未经消费的一次性 L2 安全基线。

## 2. 完全一致的身份和镜像

- Apple iOS 16.6.1 / 20G81 / Pong 参考设备 iPad7,1 j120ap；官方内核原始 ARM64 Mach-O SHA-256：d260defcc2eaac258fd3a2234454dda46791dae45e97608c5416f9ba8fd366e9。
- Darwin22 原生 UFS 第二轮内核：eed2a7c7e6de6fe854fe3dfedc3d61232984f9afd07c4c03ea3dac6c05cb0ab9。
- APFS 11 组件预链接的候选：a798cf368ab013e7389f3c5944cea9381859fb98238b346b33423b00bf8d3a37，共 50,659,328 bytes。
- F1e 固件 FD：2780cbd0c6c72086826a4bc3a16406fa48d3ef37db2abd80bcdbb6a4a35e4f70，3 MiB；31 个真实 PE/FFS 全部匹配构建输出。
- 含完整 F2 APFS Mach-O 的 FAT56：674b1ab2e6ad071448e14808e2e4e245c7d7e29de0635fc172db4b3d154626ec。
- F1e 最终只读 THORFAT1 容器：2ef4082c4d99b5c0766abade72efebd26163f99357d7b994bc20001466016f60，58,724,352 bytes，未写手机。
- 独立 QEMU UniXNU 实拍画面：runs/ios-f1d-qemu-host-20261026/qemu-unixnu-visible.png；PNG SHA-256 e69c70d04aa5b84615fdc4c3243cef56530a87a76c4ee1661273b1d35bf42992。

这些 Apple 文件与研究镜像均保存在本地非 Git 构建/证据目录，不纳入公开提交。

## 3. 致命剩余阻塞：连续内存所有权

- 当前 i386 F1c/F1e 固件 MemoryMapLib.c 仅把物理 0xF8000000..0xFFC00000 声明为 EfiConventionalMemory（124MiB）。这里可以服务 DXE，但**容纳不了 DarwinBoot22 至少 0x10000000 (256MiB) 的单次 AllocateAddress 请求**。
- 厂商 Thor 设备树给出的 0xBAB00000..0xE0000000 是约 597MiB、未被静态 reserved-memory 覆盖的原始 DDR 研究空档。然而 F1c 故意把这段内存当作 NoHob/Reserved；厂商 FDT 另有 38 项静态和 16 项位置不固定的保留区。**不能直接改成 Conv**。
- 运行时需要证明真实 FDT、所有 reserved-memory、UEFI GetMemoryMap、页表翻译、Android kernel/initrd/HOB、固件/GOP/安全世界/DMA 的区间所有权，并确认 contiguous 256MiB 对齐窗口真实可访问，才能将映射改入隔离的下一版本。
- 禁止从公版 Pong 地址或静态厂商 DTS 直接推断 Thor 有权覆盖内存。工具 tools/darwinboot_thor_memory_gate.py、tools/analyze_thor_ddr_ownership_candidates.py 只会生成 BLOCKED 和研究候选，不会修改任何映射；15 项安全回归测试通过。

## 4. 两机交接和统一 14 项真相矩阵

Mac mini 统一门禁：
- reports/thor-ios-fullchain-gates-20261026.json
- docs/integration/thor-ios-fullchain-gates-20261026.md
- 脚本 repos/UniXNU-thor/tools/build_thor_ios_gate_matrix.py；6 项真值/伪造证据拒绝回归测试通过。

当前 14 Gate：6 项 PASS_HOST、1 项 PASS_SOURCE、1 项 BLOCKED（加载窗口）、6 项 PENDING（Thor 真机 UEFI/VM、UFS/APFS 根卷、launchd、SpringBoard）。

i386：
- F1e 隔离工作树：/home/vincent/Projects/thor-unixnu/worktrees/uefi/ios-full-f1e。
- 原 F1c、F1d、HW11 的源和固件均不修改，不能重用一次性镜像。
- 最近 ADB 设备列表为空，未进行任何真机操作。

Mac Git：repos/UniXNU-thor 在 feature/thorctl-p0；历史分支和用户原有未提交内容保留。F2 工具/源码在独立 worktrees/xnu/ios16-f2-storage-bound-host，未修改原始 Pong 或 Apple 开源源码锁定提交。

## 5. 下一步实施优先级

1. **优先获取 Thor 实时内存证据（只读）**：重新接上手机后读取 FDT/reserved-memory 和 Android 当前启动区间，再与 UEFI 固定保留区做交集证明；无设备可用时不得伪造通过。
2. 在一个全新的隔离 UEFI 版本中，仅当 256MiB 真实内存证明全部通过，才设计扩大的 ConventionalMemory 窗口；需要验证 MMU 和动态 DMA 所有权，不允许盲目解除 NoHob/Reserved。
3. 在 QEMU/主机继续进行 DarwinBoot22 实际载入、ADT/TrustCache 格式、UEFI 驱动注册和内存窗口拒绝检查；QEMU 不能证明 Thor 硬件 UFS。
4. 将 Pong NativeUfs 的板级供电、PHY reset、IOMMU、UFS DMA 与 Thor 厂商 FDT 对应关系隔离出来，执行真正 Thor 的 read-only 设备探针和回归验证。
5. 原版 SystemOS、Data 和 launchd 仍未实际挂载；Apple CDN 当前约 170KiB/s，已经中止/删除未校验的大文件片段，不能将它标为已安装。
6. 内核、根卷、用户态实际完成后，必须以真正的 SpringBoard 帧和启动日志验收；而不是用 UI 模拟图或静态 UEFI HUD 宣称进入 iOS。

操作边界：禁止 flash/erase/format、切槽、重用已消耗镜像；所有真机临时启动必须在新的录屏授权后执行。永久分区和 userdata 改动需另外确认并核实备份。
