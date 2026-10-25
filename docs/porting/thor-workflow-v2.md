# Thor / UniXNU — P0 研发闭环 v2（2026-10-25）

## 定位与职责
- Mac mini 是构建和版本化证据的权威源，i386 是独立 UEFI 构建兼容节点及唯一手机 USB 实验节点。
- Git 工作分支按开发任务划分；同一任务可含多个 run ID；只有硬件证据审查后才能打已验证标签。
- 当前 last visually verified bounded milestone：ENTRY10G 真实 XNU 在首次 TTBR1 切换调用返回后完成寄存器/栈检查，绿色 + 青色 + 白色标记可见；不代表 `arm_vm_init` 返回或 Darwin 用户态启动。
- `SESSION_STATUS.json` 是旧式快照，不作为双机间互相覆盖的状态源。每次构建和测试保存不可变记录；手机动作必须独立授权。
- 原始备份、原厂 NV/校准、一次性熵和 Bootloader 密钥不进 Git，亦不允许经普通构建工具清理。

## 唯一构建意图文件
`tools/thor-build.toml` 是配置模板；工作区根 `thor-build.toml` 使用同一字节。
仅填写 board、构建 profile、源分支/来源、固定的原厂输入和单次启动策略。
内核体积、Mach-O 区间、SHA256、UEFI 提交和镜像身份必须从链接文件及签收记录派生，不能复制上一轮数字。

## 工具命令
```bash
./bin/thorctl check --host macmini
./bin/thorctl inspect --host macmini --emit-run thor-20261025-vm-001
./bin/thorctl test --affected osfmk/arm64/arm_vm_init.c
./bin/thorctl run verify --run-id 20261025-i386-entry10g-01
# i386 only
./bin/thorctl check --host i386
./bin/thorctl package --verify --host i386
./bin/thorctl device prepare --host i386
```
`device prepare` 必须拒绝已消费或失效的一次性镜像。P0 的通用 `device boot` **故意不开放**；仅使用事先独立审核、固定散列、经过用户录像授权的一次性控制器。禁止重试、flash、erase、format、切槽。

## 主机门禁
- L0：格式、差异、路径边界、配置 schema、禁止危险命令与 Git 身份。
- L1：根据改动范围执行 UEFI 模块、ADT、VM 表项/地址、SDK 与 Python 工具测试。
- L2：XNU Mach-O/编译与机器码控制流检查、UEFI 真实链接输入和固件卷审计、Kernel↔FD↔Boot v4 完整签收。
- L3：经用户明确授权的单次临时 Fastboot、视觉/结构化内核检查点、Android 恢复、两端证据哈希归档。
- ENTRY08/ENTRY09 必须保留真实实测哈希；新的增量构建必须证明字节一致，或解释所有差异，才能替代旧构建入口。

## 已落地的 P0 子模块
`tools/thorctl/firmware_audit.py` 是可参数化的 FV/FFS/INF 审计；沿用现有解析器，按实际路径审查输出。
`tools/thorctl/uefi_build.py` 用 `--source --run-id --build-id` 参数替代每阶段复制构建脚本。
`tools/thorctl/package.py` 用真实 frozen kernel、firmware audit 与 board stock pins 派生镜像输入；Linux 的 `os.getrandom` 只在真实打包时调用。
`tools/thorctl.py` 实现 check、inspect/lock、affected tests、package verify、device prepare、run verify/report，缺乏独立批准的设备启动始终阻断。

## 明确未完成的 P0
- Mac mini 原生 UEFI 构建尚未与 i386 经过产物等价验证；现阶段 UEFI 正式编译仍在 i386。
- 跨网段 Mac→i386 使用可信连接传输+接收端 SHA；自动传输/锁的生产方案尚未闭环，不宣称已实现持续后台同步。
- 新参数化工具先与 ENTRY10G 实际产物做等价审计和只读检查；旧历史脚本保留，不删除或覆盖。
- 旧 ENTRY08/09 的整机编译重现、完整驱动依赖闭包和一体化自动播报仍列为后续验收。

## 运行的事实边界
`fastboot boot OKAY` ≠ 内核运行成功；照片可见 XNU 白色成功条 ≠ VM 初始化结束；Android 恢复 ≠ Darwin 启动。
遇到两轮无新增执行证据，停止硬件启动，优先改善主机机器码审计和安全异常观测。

## 2026-10-25 P0 实际验收进展

- 已完成单一 `thorctl`：`check`、`inspect --emit-run`、`test --affected --execute`、`build`、`audit`、`package --verify/--create`、`device prepare` 和 `run verify/report` 的最小安全工作流。真正的 `device boot` 仍锁定，由审核过的单次测试控制器执行。
- `firmware_audit.py` 用 UEFI 实际 FD 重新验证，结果为 2 卷 / 20 个模块 / 96 个源码链接输入，未改变审计结论。
- `uefi_build.py` 参数化后对 ENTRY10G 完成真实重新编译；FD 与原件字节一致。
- `package.py` 用 ENTRY10G 重建 Android v4 镜像；移除每次 CSPRNG 的 64 字节差异，其余全部源载荷相同。
- 不可变 `runs/<run-id>/` 和按 run 生成的 header/provenance 替代按 ENTRY 手写 SHA。用户的原始照片未保存成 Mac 本地源文件时，必须报告「有界视觉审查，原始照片 SHA 尚未验证」，不能把录屏证据虚报为存档哈希。
- VM 新任务 `thor-20261025-vmdev-001` 已完成 XNU 新增 EL1 翻译探针的宿主机编译、审计及实际机器码核验；与原 ENTRY10G 相比，装载跨度减少 16KiB。
- 按真实 Mach-O 派生的全部 UEFI 交接/ADT/transition 偏移已自动生成，新的 UEFI 独立编译与固件审计通过。
- 单次候选 `thor-20261025-vm-hw01`：i386 宿主机 56 项离线门禁通过，单次启动控制器及安卓只读预检通过，MAC 保存 XNU/UEFI 两个未验证分支、审计收据和新 FD。镜像与一次性随机种子仅留 i386，未进行本轮手机启动。
