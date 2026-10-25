# Thor 迁移审查：UniXNU Format 2（2026-10-25）

## 已核对的权威版本
- 官方 UniXNU `56aaad72df854febb77ee67a68ed486d0d882f1b`（本地隔离只读克隆：`references/UniXNU-format2-20261025`）。
- 官方 SM8550 SoC 示例 `23b2049fcd9e30356161def84231334fb55ecca9`；sheng 设备 `0e1f6e52638137db15905db8d775ad73757f39fa`。
- 官方阅读入口：https://unixnu.com/zh/porting/migration 与 https://unixnu.com/zh/porting/ 。
- 本机 ENTRY09 已验证 XNU `97506cd13f2cccf09cb8135c5fccc9d33317fadd`、UEFI `12d5dee1fc839926455b2264143e0fd43b1e6b50`，均保持冻结。

## 学习并采纳的分层
1. ARM64 通用：共同初始化、4 KiB XNU 基础补丁、构建器与 DarwinBoot，放公共 UniXNU 主线，不在 thor 复制同名模块。
2. SoC 公共：CPU affinity、GIC、timer、SMMU、UFS/PCIe 控制器资源，未来可形成 SM8475 common；必须从 thor/pong 的真实证据抽取，不能引用 SM8550 寄存器地址。
3. OS profile：Darwin 23.6.0 / XNU 10063.141.1 的原始 ABI、预链接、启动参数和兼容补丁。不得直接改用 sheng 的 iPadOS 17.7.11 预链接条件。
4. thor 设备：bootloader/UEFI、内存保留区、板级 DTS、固件二进制与哈希、设备电源策略、临时启动控制器。
5. 使用 `components.toml` 的规范 ID、固定提交与依赖闭包，而不是按旧目录名称无差别导入驱动。名称调整必须保留 runtime_identifiers。

## 与现有架构的差异
- 当前集成仓库仍继承旧 UniXNU 的完整源码和 Format 1 `board.toml`；不是通过 Format 2 解析器完成构建的设备仓库。
- 当前 21 个旧 Gitlink 中只有 3 个在原 XNU 工作树有同提交独立检出。完整依赖闭包和 Format 2 的组件 ID 需要逐项核对。
- 已验证的 64 MiB 早期 XNU 实验内存窗口，不等于正常 DarwinBoot 所需 256 MiB+ 连续内存已解决。
- ENTRY09 的手工 Apple ADT 只有早期初始化所需结构；统一 `DTS -> DTB -> ADT`、NVRAM proxy、/arm-io 与内存映射完整性尚待迁移。
- 原始 ENTRY09 构建仍通过隔离的旧构建链复现；迁移完成后再要求 `status --json`、`tree`、`kernel`、`firmware`、`payload`、`bootimg` 六关通过。

## sheng 可复用范围（必须按硬件 ABI 判定）
| 子系统 | 当前公开源码与文档事实 | thor 采用方式 |
|---|---|---|
| GPU/Metal | Adreno740 和 AdrenoMetal：sheng/SM8550/Adreno 740，已记录 sheng GPU 合成 | thor/SM8475 的 Adreno 730 不直接套用 740 低层驱动。可研究 Metal 前端、Turnip、shader cache 和测试方式 |
| 显示/亮度 | GopFramebuffer 抽象及 sheng 背光策略 | 先复用 GOP/帧缓冲抽象；panel/backlight 的寄存器、电源与屏幕几何按 thor 实测 |
| Wi-Fi | Ath12kWifi 目录含 MHI、QRTR、PCIe board 回调；sheng porting.toml 的 network 为 implemented | 先确认 thor 芯片总线、固件和硬件接口，验证关联/收发后才能标记 device-verified |
| DSP/音频 | sheng 的 ADSP/GLINK/IPC 通道有实现记录；文件明确区分固件启动和真正扬声器播放 | 不宣称 thor 音频可用；等路由/codec/时钟/固件证据后再移植 |
| 充电/电源 | sheng 记录 PMIC 电量接口与 PD/PPS 的实现/限制，缺少适用于 thor 的电池与适配器认证证据 | 仅复用分层方法和接口设计，不复用未经核对的属性索引和电流策略 |
| 存储 | ShengUfs 专用于 SM8550；RamBlock、VerifiedRoot 机制可参考 | thor 的 SM8475 UFS PHY、SMMU stream、电源门控、LUN 和根卷策略单独适配 |

## 与 ENTRY10 的关系
- **先完成当前实验目标**：受控切换 TTBR1，读取新页表寄存器，确认 TTBR0/VBAR/TCR 未变，证明代码/栈/诊断帧缓冲仍可用，然后主动停止。
- ENTRY10 的新逻辑仅作为旧 ENTRY09 的增量补丁，不能混入 Format 2 新构建流程；新旧两套 ABI 和构建输入未审查一致性前，不进行强制主线重基。
- 任何 FAIL/异常只能算“该阶段未过”；待机、颜色条、USB/Android 返回分别保存独立证据。没有手机实测前只能标为 host-verified。
- 不触碰分区、userdata、固件 flash、厂商密钥或原机备份。

## Format 2 后续迁移验收（不阻塞 ENTRY10）
1. 冻结当前 ENTRY09、ENTRY10 各自源码与二进制哈希；检查 `AGENTS.md`、Format 2 的模板与 porting.toml 记录字段。
2. 以新独立实验分支建立真实的 `schema_version = 2` board，不覆盖当前 Format 1 profile；选定实际 OS profile。
3. 清点已知 CPU/DRAM/MMIO/保留区证据，建立 `dts/thor.dts` 和可复用 SoC DTSI，明确每项来源。
4. 选择组件与 commit，引入显式 `[components] include`、`kernel.config_fragments`、`kernel.patch_series`，逐项核对是否重名/重复打补丁。
5. 创建权威 `porting.toml`，每个 area 记录 inputs、procedure、findings、completion、limits、next_action、evidence；planned/implemented/host-verified/device-verified 严格区分。
6. 跑官方解析器/porting-record 检查，再编译全链；只在等价证明完成后才准备替换旧构建流程。

## 参考
- https://unixnu.com/zh/porting/migration
- https://unixnu.com/zh/porting/records
- https://unixnu.com/zh/porting/platform
- https://unixnu.com/zh/porting/device-tree
- https://github.com/UniXNU/UniXNU-sheng
