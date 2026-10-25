# Thor / UniXNU — HW09 视觉复核与 HW10 4×4 多检查点诊断

记录时间：2026-10-26（北京时间）。本文件是可版本化阶段报告，**不能代替不可变 L2/L3 原始收据**。

## 1. HW09 视觉结果：不能判定完整 L3 通过

- `thor-20261025-vm-hw09` 在 2026-10-26 00:30:20 +0800 只执行了一次临时 Fastboot boot；随后 00:31:08 +0800 Android 7/7 恢复，镜像已消费禁止再启动。
- 用户四张手机照片清楚显示 UEFI 校验和 XNU 绿色、青色、洋红色检查条。**黄色及蓝色并未形成可独立识别的检查点**；洋红附近的浅色细边不能据此宣称成功或失败。
- 因此 HW09 的准确状态是 `PARTIAL_L3_GREEN_CYAN_MAGENTA_VISIBLE_YELLOW_BLUE_NOT_VERIFIED`，而不是 AuxKC / pre-pmap L3 PASS。
- 源码的观测缺陷：HW09 蓝色直接覆盖原黄色条的下方四行，所有狭窄条段又紧密排列。观察不到某种颜色既可能是未执行，也可能是覆盖/显示可见性问题；没有 UART/ETM 就不能凭旧照片反推出确切失败条件。
- 双机视觉证据均已保存 `visual-review.json`，原 JPEG 仍在聊天附件。原有 host-only `technical-summary-awaiting-video.json` 保持不变。

## 2. HW10 改造原则

- 用户要求：4 行 × 4 列独立颜色矩阵；一次尽可能多验证几个后续阶段；可由独立格子识别具体停止边界。
- 容器：原先已被 UEFI 和 XNU 确认拥有的 framebuffer 物理窗口 `0xb805a000..0xb80b4000`（y64..127，共 90 页 4KiB），不扩大 DRAM 所有权。
- 真实 TTBR1 L3 第 `384..473` 项形成新高 VA 映射；保留原 HW07 诊断别名 `480..499`。被验证原本全零的表项承载新 PTE，不分配新的 L1/L2/L3 表页。
- 布局：4×4 共 16 个相互隔开的固定格子，每格 328 像素宽、14 行高，列间隔 24px、行间隔 2px。这个矩阵受限于原 64 行 framebuffer 安全窗口，并非整机屏幕 UI。
- 新映射实际执行 90 页物理与高 VA 翻译检查，PTE 安装后 TLBI/DSB/ISB，且所有 cell 绘制均为高 VA + DC CVAC + DSB SY。进入空 TTBR0 后不调用旧低 VA framebuffer。
- 每格默认灰色，成功后填色且**不会被后续阶段覆盖**；真实机器码的递增 bitmask 检查阻止跳过、重复和错序报喜。

## 3. 4×4 检查点编号及判断

| 行列 | 槽位 | 颜色 | 只有经过什么以后才会出现 |
| --- | --- | --- | --- |
| R1C1 | 00 | 绿 | 旧 MMU/TTBR1 前置验证及安全 alias 初始化 |
| R1C2 | 01 | 青 | TTBR1 切换及基础验证 |
| R1C3 | 02 | 白 | 高 VBAR、空根页与映射准备证明 |
| R1C4 | 03 | 浅紫 | 完整 framebuffer 高地址网格映射已通过 |
| R2C1 | 04 | 洋红 | 原生 XNU `set_mmu_ttb(invalid_ttep)` 与 TLBI/DSB/ISB 已通过 |
| R2C2 | 05 | 黄 | `kva_active=TRUE` 及两个页表指针高地址更新已执行 |
| R2C3 | 06 | 橙 | 原生寄存器/地址翻译/可写映射守卫全部通过 |
| R2C4 | 07 | 亮蓝 | 排除了非空 AuxKC；即将调用真正 `arm_vm_auxkc_init()` |
| R3C1 | 08 | 薄荷绿 | 真实 AuxKC 函数已返回无附加内核路径 |
| R3C2 | 09 | 紫 | 内核缓存物理边界、统计及 `ROUND_TWIG` 等元数据已计算 |
| R3C3 | 10 | 白 | 7 项高 VA EL1 翻译和 4 项可写物理映射等 pmap 前守卫通过 |
| R3C4 | 11 | 灰 | 本轮保留，不应变色 |
| R4C1–C4 | 12–15 | 灰 | 本轮保留，不应变色 |

这是一轮可观察的**多检查点前进**，不是跳过失败守卫。若例如 05=黄但 06=灰，说明到达指针更新，但没有证据证明后续完整守卫通过；若 07=亮蓝而 08=灰，重点审查真实 AuxKC 调用及返回。

## 4. 源码、链接机器码和固件固定版本

- Mac mini XNU 实验分支：`feature/vm-hw10-grid-five-gates`，提交 `dcc14fef9909350d8c0eb3388e45880ca3b2667f`。
- 冻结 Mach-O：`artifacts/candidates/thor-20261026-vmdev-009/kernel.macho`，SHA-256 `bd893b1d9c2532ea84f933911950fe6e9a67f4c40fbd6e584c6f7b8afebe5b25`。
- 真实机器码审计：`runs/thor-20261026-vmdev-009/l2/hw10-grid-machinecode.json`，覆盖 244 个 `pre-pmap` CFG 状态，16 格无重叠/越界、90页 alias 不覆盖旧20页映射、6 类故障注入检查全部拒绝。
- Git 阶段提交包含新 VM patch `zzzzzzzzzzzzzzzzzzz-thor-vm-hw10-grid-4x4-fivechecks.patch` 和机器码审计工具 `scripts/audit-thor-hw10-grid-linked.py`；构建已完成 bit-for-bit 二次复验。
- i386 UEFI 独立分支：`feature/vm-hw10-grid-five-gates`，提交 `f76ead914526768d10a779f2d9d44cbe2b18de8b`。
- 最终 FD SHA-256 `9b885a4811db7d4c9d8d86efa56d6208c8995d7cec0e851a148db55a2f577516`；两次真实链接一致，2 个固件卷、20 个模块、96 个链接输入，500 字节交接 SHA 与头文件完全一致。

## 5. HW10 L2 准备状态与安全约束

- i386 全新一次性镜像：`runs/thor-20261026-vm-hw10/l2/package/thor-boot.img`，SHA-256 `7fcbbafef7583419e53474a44f8c12341ec6eba4d571b63d7b55302f2f52f044`，长度 25,894,912 字节。
- 独立离线验证 `independent-offline.json`：**77/77 PASS**；`thorctl check`、`package --verify`、`device prepare` 全部通过。
- 全新一次性启动控制器 `l2/controller/` 已完成 SHA 固定和 host-only review；HW10 改用仅适配新 PE 证明 schema 的更严格审查工具，不改写旧收据。
- 最新 Android 实时只读预检 7/7 PASS，设备 `thor`；HW07/HW08/HW09 的消费锁保留。**HW10 的 L3 不存在，镜像未消费，也没有重启手机。**
- 唯一允许的 L3 协议：用户录屏开始后明确回复，才能一次 `adb reboot bootloader` + 一次 `fastboot boot`；禁止 `flash`、`erase`、`format`、切槽、自动重试。
- 预期新矩阵有固定排列、灰色保留格、通过格独立留色；截图需要拍下全部 16 个格子。白色有两个不同位置，必须以槽位坐标判定，不能仅用颜色判断阶段。
- 预期检查结束受控 PSCI 复位、Android 7/7 恢复；实际结果必须由 L3 录像结合链接代码重新判定，不凭 Fastboot OKAY 推断成功。
- **HW10 依然不会执行 `pmap_bootstrap()`，不会让 `arm_vm_init()` 正常返回，也不是完整 Darwin / UnixNU 系统启动。** 本轮只解决 HW09 的观测歧义并积累更多连续、安全的内核检查点。
