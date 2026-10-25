# Thor / UniXNU 原生 iOS 启动主线 — F1c 与 F2 交接（2026-10-26）

本报告记录真实主机编译和缺口，不代表已启动 iOS。严格保留此前 HW10 真机 L3 与 HW11 82/82 离线成功基线；未操作手机、分区、启动槽位。

## F1c：完整 UniXNU UEFI 的主机闭环

- i386 独立分支：feature/thor-ios-full-firmware-f1，commit fce50848544dd40a99dee47f240b9da0afcffc1a。
- 实际接入并编译：UniXNU.efi、UniXNUDxe、DarwinBoot22、通用 BdsDxe、DiskIo、Partition、FAT、Console、ThorBootMemoryDxe。
- 从 FDF/DSC 排除了原本独占 BDS 并拒绝 BlockIo / SimpleFileSystem 的 ThorEntryBdsDxe。最终只有通用 BdsDxe。
- ThorBootMemoryDxe 只提供只读 RAM BlockIo。THORFAT1 封装校验 magic、CRC32、尺寸（最大 64 MiB initrd、48 MiB FAT）、FAT 引导扇区及每页可读映射。CRC32 不是加密真实性证明。
- 原 SEC 发布的 gSiliciumEntryArgHobGuid 提供 FDT 指针。新增过程先校验物理窗口和 MMU 映射，再复制到限定 DXE 保留区，验证 FDT Header 后安装 gFdtTableGuid；真实完整 FDT 拓扑和 ReadyToBoot 顺序尚未在手机验证。
- 15 项 FAT 畸形输入测试通过。mkfs.fat 生成的 32 MiB FAT16 真实镜像封装与解析一致，但没有 Apple 系统文件，也没有有效的 UniXNU 启动配置。
- FD SHA256 f27554567e28f785de2f66014e87a57011c4772b31395f5935db228314c0fac9，FD 3 MiB。内部 FVMAIN 自动计算容量：864256 字节，其中 861440 已用；外层 FD 仍空余 2895696 字节。内 FV 99% 不代表固定 3MiB 固件满。
- 独立审计结论 PASS_REAL_F1C_FV_ONE_GENERIC_BDS_RAM_FDT_AND_31_LINKED_EFI，内层 31 个 EFI 模块与实际构建输出逐字节一致，外层唯一 SEC TE；7 种模块缺失/重复/旧 BDS 变异被拒绝。
- 主机路径：i386 /home/vincent/Projects/thor-unixnu/runs/thor-20261026-ios-full-f1c-host-01/l2。
- Mac 交接：evidence/build/thor-20261026-ios-full-f1c/，3 份 SHA 精确证据和 mirror-index.json；没有复制 FD、镜像 seed、手机分区或苹果恢复文件。
- **F1c 结论仅限 HOST LINKED。不能用于 Fastboot**：还缺真实 BDS/EFI console 顺序、FDT 拓扑、RAM payload 发布、完整 DRAM 所有权和运行用户态的 XNU。

## F2：版本一致的 Darwin22 / iOS16 准备

- Pong 同 SM8475 参考 iOS16.6.1 / 20G81，DarwinBoot22；当前 Thor HW11/HW12 为 XNU10063/Darwin23，不能互混 ABI。
- Mac 只读参考源码目录 references/ios16-thor-pinned：XNU8796.141.3 1b191cb58250d0705d8a51287127505aa4bc0789；libplatform 1b2b22f2e37384af2c562bd95d5e93ce2e192451；libdispatch ca80d30399cf4854d3b970076f83dde4a6e74e78；AvailabilityVersions f3b52ca75ee842a8ac593bd7b03f9f4768c15755。
- Pong 设备源码参考提交 ab74f075320fe54eee24d5d150386e5b0f0f427f。Apple Silicon 兼容脚本在独立工作树 worktrees/xnu/ios16-pong-port-tools，不改上游。
- 完整 Pong 参考构建已执行到 NativeUfs，但旧式 XNU C++ 头文件被 Xcode27 的编译警告拦截；针对性修正后，进一步遇到缺少 20G81 原始 IOStorageFamily ABI 派生头文件 IOBlockStorageDevice.h 和真实方法绑定，不能构造假头文件绕过。
- 另一条不带存储的独立编译研究线 runs/ios16-pong-core-host-only 在应用补丁时发现 Pong SystemOS/Data patch 存在跨补丁依赖；关掉系统卷后 zzzzzz-pong-writable-data.patch 不适用。这不是可独立复用的 Darwin22 补丁闭包。
- 尚无已审计的 Darwin22/XNU8796 Mach-O 成品。两次宿主构建失败日志、直接 NativeUfs 错误及精确 SHA 均记录在 reports/thor-ios16-darwin22-build-gaps-20261026.json。
- 本次未下载 Apple IPSW、OS/SystemOS、trustcache、Data，也没有写手机分区。

## 下一轮按真实里程碑推进

1. F1：建立包含 EFI/UniXNU/unixnu.toml 的真实 FAT 配置测试载荷，在主机模拟其枚举、Boot#### 注册和 DarwinBoot loader 探测；硬件 L3 独立授权。
2. F2：拆出真正最小的 Darwin22 Thor 补丁闭包，保证 pmap/CPU/timer/ADT 按 Thor 真实内存范围；NativeUfs 需获取并验证真实 20G81 IOStorageFamily ABI。
3. F3–F5：完整内存和异常日志、GIC/调度/IOKit，UFS/APFS/SystemOS/Data 和 launchd；系统卷、签名和 Cryptex 必须版本一致。
4. F6：只能用真实 SpringBoard/显示服务输出确认用户要的 iOS 界面，不能把彩色诊断 HUD 当作原生 iOS。
5. 安全：不刷写、不切槽、不重启未授权手机；HW11 一次性镜像仍未测试，其身份和 L2 收据不得改动。

机器可读主索引：Mac reports/thor-ios-fullchain-progress-20261026.json。
