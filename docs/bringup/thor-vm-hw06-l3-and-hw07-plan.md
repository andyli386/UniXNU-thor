# Thor / UniXNU — VM-HW06 L3 真机结论与 HW07 最小下一步

记录：2026-10-25。范围：有界 XNU VM bring-up；不宣称 Darwin 可启动。

## VM-HW06 有界真机结论（已验证）

- Run：`thor-20261025-vm-hw06`；XNU commit `cde8ecefbb9f3ca424d53954d739dc8aaff921c0`。
- UEFI commit `a2b2300800ea591141b2de18780546f1764c2503`。
- Boot v4 SHA256 `f09f4e773dd4c4d52c53b9cc53167c1f060d67a6a30d588a3d5637a26986d746`（已消费，永不复用）。
- Kernel SHA256 `ea95461dbb83e1d0e4d14e1ee5ed291267f9930d94112f35e4eb28e01eeff35d`。
- FD SHA256 `fc10beccdd11fe4457acc00fcf0b590cf7832b20f6faa6dc54a1369e7c140d83`。
- 主机 L2 66/66；链接实际 CFG 115 状态，2 次 `set_mmu_ttb`、2 次 TLBI、9 次翻译探针。
- 2026-10-25 20:57:33 CST 单次 Fastboot OKAY；20:58:52 CST Android 7/7 恢复。
- 用户 6 张连续照片：先是 UEFI 读取和校验，UEFI handoff-ready 时顶部无条；随后出现独立绿、青、白与琥珀色标志，无红色失败条。
- UI 实现 `ThorReportHud.inc.c` 特意为 XNU 保留帧缓冲 y0..127；所以顶部条不是 UEFI 进度卡。
- 冻结 XNU 源码中**唯一白色 marker**仅在 `thor_vm_ttbr0_roundtrip_gate` 完成换空根+TLBI、翻译验证、恢复原根+TLBI、二次验证后执行。
- 判定：**PASS_BOUNDED_REAL_XNU_TTBR0_TEMPORARY_SWAP_TLBI_AND_RESTORE_WITH_ANDROID_RECOVERY**。
- 以上是照片+固定内核机器码+控制器收据的有界证明；没有 UART/ETM 的逐寄存器动态轨迹。
- 不代表 `arm_vm_init()` 完整返回、TTBR0 长期失效、`pmap_bootstrap()` 完成、设备驱动或 Darwin 用户态启动。

证据入口：项目根的 `evidence/hardware-tests/thor-20261025-vm-hw06/visual-review.json`（Mac）；i386 的 `runs/thor-20261025-vm-hw06/l3/`。

## HW07 最小目标：在旧 TTBR0 不可用时仍有可靠的独立观测

HW06 白色成功是在**恢复旧 TTBR0 后**写入低地址 framebuffer。下一阶段不能直接把原控制器的成功条移到永久退役路径。先验证高 VA 诊断通道，再推进 VM。

### 建议的最小阶段

1. `HW07-A / HOST_ONLY`：盘点 `thor_entry09_mark/stop/fail` 的底层 framebuffer 写入、复位和栈依赖；准确绑定 framebuffer PA、大小、cacheability、MAIR、PTE 与设备属性，不假设其属于当前 64 MiB owned RAM。
2. 为 framebuffer 创建**独立固定高 VA 别名**，只映射被允许的诊断像素区域；不得放宽所有物理内存映射。先静态检查映射权限、指令/数据屏障和不越界。
3. `HW07-B / L2`：机器码校验确保高 VA marker 的全部内存访存只走 TTBR1；异常向量、高栈、可执行代码、常量表、诊断状态数据都不依赖低 VA。
4. `HW07-C / ONE_SHOT L3（以后单独授权）`：在临时空 TTBR0 激活且原 TTBR0 尚未恢复时，以**高 VA**绘制专用检查点；随后仍恢复旧根与 Android，用新颜色/形状区分 HW06 旧的白条。
5. `HW08 / DESIGN_ONLY UNTIL HW07 PASS`：得到 HW07 有界真机证据后，再考虑维持空根进入原生 `arm_vm_init` 的 `set_mmu_ttb`、`flush_mmu_tlb`、`kva_active`、页表指针修正等后续路径。

### 关键禁止项

- 不直接把 framebuffer PA 经 `phystokv` 强转为可写高 VA；须有实际 TTBR1 leaf、属性与映射范围证明。
- 不重用 HW06 镜像，不重复使用 entropy seed，不在 HW07-A/B 阶段碰设备。
- 不引入自动 boot/flash/erase/format/切槽；未得到独立录像授权时绝不进入 L3。
- 不删除原始备份、旧分支/patch、历史机器码与收据。

## 上游后续风险与验收门槛

- `arm_vm_init.c` 当前诊断函数在 `set_mmu_ttb_alternate(cpu_ttep)` 后、原生 `set_mmu_ttb(invalid_ttep)` 前主动 `stop()`；**当前原生 VM 初始化仍被截断**。
- 后续 `arm_vm_auxkc_init`、pmap、physmap 与 kernelcache 地址区间要用精确内存所有权/映射闭包证明；先检查 64 MiB 限制与关键 allocation，而不是推断整机内存已可用。
- 当 TTBR0 真失效时，必须有可在 TTBR1 下工作的 fault/reset 路径；失败不能为了输出而去访问低地址。
- 异常分支先做主机静态证明、故障注入和硬件安全审查；HW07 真机运行最多一次临时 Fastboot。
- L3 判定分别记录 Boot command、XNU 唯一独立检查点、Android 恢复、镜像消费状态，任何一个不可以替代其他。

## Git 与交接

- Mac XNU 研发工作树含 `cde8ece`，但受控主仓 `repos/UniXNU-thor` 此时尚未导入这个完整提交；UEFI `a2b2300` 主要在 i386。
- 先使用经过 SHA/引用校验的 Git bundle 收敛，不覆盖未提交测试改动，不推送到上游。
- 根目录 `docs/HANDOFF.md` 是最新入口；本文件是 Git 管理的阶段记录，后续请按新增 run 编写新记录，不覆盖旧收据。
