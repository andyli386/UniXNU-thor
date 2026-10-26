# Thor / UniXNU — VM-MEM02 真机 L3 交接（2026-10-26）

**目的：** 用户将在下一个会话提供 MEM02 的录像、照片和可能的更多终端结果。此文档以 i386 构建、Mac mini Fastboot 日志和只读 Android 复核为事实依据。**严禁重用已消费的 MEM02 镜像。**

## 一、当前状态：不是待启动，而是已执行一次临时启动

| 检查项 | 事实及证据 |
|---|---|
| 构建版本 | i386 UEFI 分支 feature/vm-mem02-persist-final-fdt-window-hud，commit 1044cb422ccce63cf382c4cc23d30c83e26c787e |
| MEM02 UEFI FD | 3,145,728 B，SHA-256 48297a9a8053b6b6624a1461a6e7ea69b2cedf4439a1b7c0c70fbcab58aaa341 |
| 内核 | 仍是 HW11 早期诊断 XNU，SHA-256 623b444dc57fc8981562277f1eade9f1ed25818badaa3bfd03ae7ff54a5333aa；不是 Darwin22 完整 iOS 内核 |
| L2 | 独立验收 108/108 PASS；源码回归 12 项 FDT + 10 项最终 HUD，2 FV / 20 EFI / 96 inputs，原 EBS 500B 机器码不变 |
| 一次性 Boot v4 | 25,894,912 B；SHA-256 0239eb9a758a9d3f7695a7de0f86c66913d3fe6ad125afdb693eb14a9e85c8ca；手动传输包五项 SHA-256 检查全 OK |
| Mac Fastboot L3 | **已实际执行一次：** Sending boot.img OKAY、Booting OKAY、Finished. Total time: 0.705s |
| Mac Android 回归 | 设备回到 adb device，7/7 只读系统状态匹配，未做分区写入 |
| MEM02 五行窗口/4×4 检查点 | **待用户提供本次照片/视频。** Fastboot OKAY 不能证明 FDT 五行、EBS 后 XNU 或 iOS 用户态完成 |
| 单次镜像状态 | **CONSUMED — 严禁再次启动**。消费标记已补齐 Mac/i386 上同哈希副本 |

真实 Fastboot log：Mac runs/thor-20261026-vm-mem02/l3/fastboot-boot.log；
文件 SHA-256：8469bf5cd5d3220fe2e35cdbaebe344cd5e2545d6e5659303c221bda2cd309a6。
Mac 结构化验收记录：evidence/hardware-tests/thor-20261026-vm-mem02/mac-manual-l3-fastboot-observation.json。
**注意：** i386 目录没有自己执行的 l3，不代表镜像尚未消费，因为实际临时启动发生在 Mac mini。

## 二、交接位置

**Mac mini 工程根：** /Users/<user>/projects/thor-unixnu

- 原始用户同步包：evidence/files_needed/mem02/manual-transfer/。含 boot 镜像、4 份 JSON、SHA256SUMS、transfer-index.json；五个 SHA 校验均 OK。
- 用户实际启动的同 SHA 镜像：runs/thor-20261026-vm-mem02/l2/package/thor-boot.img。
- 已消费锁：runs/thor-20261026-vm-mem02/l2/package/ONE_SHOT_USED-DO-NOT-BOOT-AGAIN。
- 同哈希手动传输副本已消费锁：evidence/files_needed/mem02/manual-transfer/ONE_SHOT_USED-DO-NOT-BOOT-AGAIN。
- 原始日志：runs/thor-20261026-vm-mem02/l3/fastboot-boot.log。
- 当轮 L3 事实收据：evidence/hardware-tests/thor-20261026-vm-mem02/mac-manual-l3-fastboot-observation.json。
- 统一长历史：docs/HANDOFF.md，第 22 节为 MEM02 最新状态；旧章节是历史快照。
- 本交接文件同内容的便捷路径：docs/integration/2026-10-26-mem02-l3-next-session-handoff.md。

**i386 工程根：** /home/vincent/Projects/thor-unixnu

- MEM02 编译源码：worktrees/uefi/vm-mem02，feature/vm-mem02-persist-final-fdt-window-hud，固定 Git 1044cb422ccce63cf382c4cc23d30c83e26c787e。
- FD：worktrees/uefi/vm-mem02/Build/thorEntry09/RELEASE_CLANGPDB/FV/SILICIUM_UEFI.fd。
- 完整独立门禁：runs/thor-20261026-vm-mem02/l2/package/independent-offline.json。
- 手动交接包源：runs/thor-20261026-vm-mem02/l2/manual-transfer/。
- 两组测试：worktrees/uefi/vm-mem02/Platforms/Xiaomi/thorPkg/Tests/test_mem02_final_overlay.py（10 项）和 test_mem01_5_window_readonly.py（12 项）。
- i386 的 MEM02 package、manual-transfer 和 MEM01 历史 package 均已补写消费标记。注意：它们原本没有本机 l3，已通过跨机器日志与完整镜像哈希确认真实消费状态。

**不要提交 boot.img 或私有随机 seed 到 Git，不要复制镜像给任何新启动控制器。**

## 三、MEM01 缺陷与 MEM02 改动

上一轮 MEM01 真实照片仅显示 UEFI 3 BEFORE EBS、UEFI CHECKS PASS 等，**没有显示五行内存窗口报告**，因为 FDT 快照早期绘制完成后，ShowEntry 内 HudTop 执行 Fill(Gop,0,0,1440,3200) 清空整屏，未重新绘制该报告。

MEM02 现在把最终绘制顺序改成：
**ShowEntry/HudTop 全屏清空 → 状态卡 → Entry10GCheckpoint → ShowFiveFdtWindows。**
成功和异常路径的最终面板都将绘制报告。主机 22 项回归均通过，但 MEM02 **真实屏幕是否完整显示，还需要用户录像确认**。

MEM02 画面预期：
- 顶部唯一版本 RUN MEM02 / THOR SM8475；
- 底部 MEM02 FDT WINDOW ONLY - NO MEMORY GRANT；
- 旧 RUN VM-VBAR / THOR 文案改为 XNU HANDOFF / VM-VBAR；
- 原 HW11 4×4 彩色矩阵 00–10 有界，11–15 是尚未定义的槽，不能直接视作失败。

## 四、五个候选物理窗口（每个 256 MiB，全部只读检查）

| HUD | 物理起点 | 物理终点 |
|---|---|---|
| L0 | 0xBAC00000 | 0xCAC00000 |
| L1 | 0xCAC00000 | 0xDAC00000 |
| H0 | 0x800000000 | 0x810000000 |
| H1 | 0x810000000 | 0x820000000 |
| H2 | 0x820000000 | 0x830000000 |

每行预期有 S（状态码）、IN（candidate_contained）、DY（动态保留区数）、UN（未解析保留区数）、CF（冲突总数）。状态码具体语义须回到源代码 thor_fdt_gate.h / thor_fdt_gate.c 核对。

**无论 S=0、IN=1 或 FDT 检查成功，都不等于已经取得 256MiB 连续、可读写的 EfiConventionalMemory。** 真实 FDT 只读快照不能取代 UEFI GetMemoryMap、页表、DMA、固件/安全世界占用和所有权验证。

## 五、下一会话必须怎样处理用户提供的结果

1. 先读本文件、Mac docs/HANDOFF.md 第 22 节与原始 fastboot-boot.log，不要提出重复启动同一镜像。
2. 根据用户**新提供的 MEM02** 视频/照片确认顶部 RUN MEM02、五行报告是否出现、是否有 NOT CHECKED，并逐行抄录 S/IN/DY/UN/CF；需要区别 MEM01 旧照片。
3. 检查顶部 4×4 格子具体上色范围和阶段出现顺序，辨别是否真正进入 EBS 后 XNU；只根据可见证据下结论。UEFI3 与 Booting OKAY 均非完整 XNU/iOS 启动证据。
4. 完整记录 Android 返回情况及终端补充输出；已有 Mac 只读回归为 7/7。
5. 将 MEM02 的视觉结论加入 evidence/hardware-tests/thor-20261026-vm-mem02 和 docs/HANDOFF.md 新一节，写清哪些关口 PASS/FAIL/UNKNOWN。
6. 若仍需测试，创建 **全新 Run ID、全新随机种子、独立新 L2 和新一次性 L3 授权**，不得再使用 MEM01/MEM02 镜像。所有测试必须禁止 flash/erase/format、切槽和自动重试。

## 六、与完整 iOS SpringBoard 主线的边界

完整 iOS 16.6.1/20G81 Darwin22/XNU8796、UniXNU 通用 BDS/FAT、NativeUfs/IOStorageFamily、11 个 APFS/CoreTrust 等组件目前有主机侧成果，但 **256MiB 连续安全可分配内存仍未取得真机所有权证明**，Thor UFS/PHY/SMMU、OS/SystemOS/Data、launchd 和 SpringBoard 尚未完成真机运行验收。

MEM02 是受控的早期 Thor EBS/FDT/XNU 有界诊断版本，不是已可加载 iOS 主界面的固件。

---

### 下个会话可直接粘贴的开场信息

继续 Thor/UniXNU VM-MEM02。Mac mini 已经实际执行过一次 Fastboot 临时启动，Sending/Booting OKAY 且 Android 7/7 恢复；L2 108/108 PASS，镜像 SHA256 0239eb9a758a9d3f7695a7de0f86c66913d3fe6ad125afdb693eb14a9e85c8ca 已消费禁止重试。请先用 desktopcmder 读取 /Users/<user>/projects/thor-unixnu/docs/HANDOFF.md 第 22 节与 docs/integration/2026-10-26-mem02-l3-next-session-handoff.md，然后分析我下面发送的真实 MEM02 终端/视频，准确读出五组 FDT 窗口和 XNU 4×4 状态，并给出下一版修复方案。不要执行任何新的 fastboot 命令。
