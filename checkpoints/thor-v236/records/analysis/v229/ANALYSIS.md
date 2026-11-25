# Thor v229 — 改动、依据与上机后要看的日志

日期：2026-11-24 深夜 ｜ 基线：v228（尚未上机；v229 同样只做了主机验证）
镜像：`thor-out/v229-unified/boot-thor-unixnu-v229-unified.img`（95,068,160 字节）
SHA-256：`bba9b3ac0575578d64738ee3ff5ec3e04b71ed661e43ff941d4666934ccc498b` ｜ 内核 UUID：`1D49A3FC-4B68-3C54-A6EA-6ED3BE62B343`

本机是 **Xiaomi 12S Ultra（thor），SoC 为 SM8475（cape）**，与 Pong、Fold 2 同一代；v228 文档里写的 SM8450 是笔误。设备树 `compatible = "qcom,cape-mtp", "qcom,cape"`。

## 本版新增（都未上机）

| 项目 | 做了什么 | 开关 |
|---|---|---|
| Wi-Fi | 真正的 QCA6490 驱动：Linux 7.0.14 ath11k + MHI，驱动层取自 Fold 2（真机跑通过扫描/关联/DHCP/HTTPS），平台文件在 Pong 版基础上并入 Fold 2 的修复 | `/chosen thor-wifi=2`（1 = 只建链、记录端点后停止；缺省 = 关） |
| 电源键 | 显示驱动收到息屏请求（选择子 12）后，用 DSI 命令 DMA 通路向面板发 DCS display off/on | `/chosen thor-panel-power=1` |
| 壁纸 | 把 `PosterBoard` 加进 UI 注入列表 | 启动参数 |
| 启动参数 | 删掉无人读取的 `tmlow=1`、`tmfull=1`、`pong_gpu_prime=0`，现为 1000/1023 字节 | — |

v228 的三项（CPU 调频、ADSP 电源轨、基带 locator + rmtfs）原样保留。

## 1. Wi-Fi

### 依据
- Thor 的 PCIe 节点（`recovery-live.dtb`，`/soc/qcom,pcie@1c08000`）：PHY 序列 82 条，与 Pong **逐条相同**，Fold 2 有 6 条不同；`max-clock-frequency-hz` 19.2 MHz 在第 2 项、100 MHz 在第 10 项（与 Fold 2 相同）；GPIO 80/94/95/96/204、复位 4/7、SID 1c00/1c01、全局中断 SPI140、`iommu-map`、`msi-map` 均与 Pong/Fold 2 一致。
- 端点身份（`v221-intake/recovery-pci.txt`）：`01:00.0 0x17cb 0x1103`，子系统 `0x17cb 0x0108`，修订 `0x01` —— 与 Pong 相同。之前文档里“子系统号未知”不对。
- 电源轨：`smpe2 smpb11 smpb10 smpc1 smpb12 ldoe7` 六条（Thor 无 `vdd-wlan-ipa`/smpe3），PMIC 外设号与类型沿用 Pong 的实测值。
- 固件：手机 modem 分区 `image/qca6490/` 有 223 个文件，含 `amss.bin amss20.bin m3.bin regdb.bin regdb_xiaomi.bin bdwlan.elf bdwlang.elf bdwlang.eXX bdwlan.eNN bd_*.elf`（名单在 `v221-intake/stock-radio-file-names.txt`）。**文件内容不在这台 Mac 上**，所以驱动在运行时读取，内核不内嵌。

### 代码
- `device/drivers/PongWifi/`（v229 里改成真实目录，不再是上游软链）：`PongWifiNet.cpp`、`PongWifiPci.*`、`PongWifiLease.h`、`PongWifiNotify.h`、`ath11k/PongAth11k.c`、`ath11k/compat/*` 取自 Fold 2（`zizhan-latest` @ a7f8ae2）。
- `PongWifiPlatform.cpp`：用 `thor-out/v228-wifi-work/make-platform.py` 从 Pong 的文件按行拼出。原样保留的函数：`coldLink`（建链）、PMIC 观察与电源轨、SMMU 读写胶水、DMA 分配器、MSI 中断。替换/新增的部分：
  - DMA 竞技场改为 Fold 2 的做法（普通页 + WC 映射，由 SMMU 提供连续 IOVA）；SMMU 里加了共享 ASID 冲突检查与 EXIDS 有效位。
  - 共享 `mx.lvl`/`cx.lvl` 投票改为“≥256（NOM）的最低档”，不再投最高档。
  - `fwGet()` 改为从 modem 分区读取并缓存（`pong_thor_wifi_firmware`，在 `NativeUfs/ModemFirmwareAccess.h` 新增 `thorWifiFirmwareRead`）。
  - 并入 Fold 2 的循环：空闲等待、每 15 秒运行状态、`pong_wifi_irq_due`（真实 MSI 立即触发，否则 20 ms 一次）。ASPM L1 和 802.11 省电默认关闭。
  - 启动模式：`pong_wifi=1`（上游写法）= 完整；或 `/chosen thor-wifi` = 1/2。
- `PongPlatformResources.cpp`：驱动启用时跳过 v221–v226 的 PCIe 探测（它们会在链路运行时改写并恢复电源和时钟）。
- 脚本：`device/scripts/build-pong-ath11k.py`（Fold 2 版）、`build-pong-wifi-native.py`（Thor 版，不打包固件）；配置 `device/configs-thor/thor-wifi-sm8475.json`；`thor-gpu.defconfig` 里 `CONFIG_XNU_PONG_WIFI=y`。

### 主机验证
ath11k 库 39 个目标文件编译通过，未定义符号只剩白名单；平台与网络代码对 Thor 内核头文件编译零错误；内核链接成功，`ArmDTPlatform.o` 引用了 `pong_wifi_start_optional`。**没有跑过任何硬件。**

### 未验证的风险（按可能性）
1. 运行时固件读取是新代码；失败时 ath11k 起不来，日志有 `THOR WLAN V229: firmware file=... state=...`。
2. SMMU：`planUfsSmmu(0x1c01, …, 78, 120)` 在 Thor 上会跳过已启用的上下文（UFS 用 74 号 bank）。新加的日志 `other enabled SMMU bank=%u asid=%llx` 会列出它们。
3. MSI 是否经 GIC 送达 Thor（日志 `QCA6490 MSI delivered by GIC`）。
4. BDF 选择：驱动按 QMI 的 board id 请求 `bdwlang.elf` 或 `bdwlang.eXX`；Thor 的分区里两种命名都有。
5. 上电后 QCA6490 常驻 M0，会增加耗电和发热。

### 日志行
`PongWifi: Thor mode=2` → `SM8475 WLAN rail requests acknowledged` → `cold REFCLK PHY_CTRL=...` → `QCA6490 PCIe0 link and BAR established` → `PongWifi: QCA6490 PCIe link established (mode=2)` → `PongWifi: 64MiB wired DMA mapped from ...` → `exclusive SID1c01 ...` → `ath11k dispatcher running` → `firmware amss20.bin bytes=... from the modem partition` → `QCA6490 station ready; starting actual scan` → 每 15 秒 `runtime state=... MSI=...` 与 `MHI dev_state ...`。失败：`WiFi initialization stopped; normal iOS boot continues, DMA retained`。

## 2. 电源键

依据（子任务只读核查）：按键读取在 Thor 上已工作。v227 日志 `Pong keys: arbiter v7 channels=768 PON=407 GPIO6=434; power=1 up=1 down=1`；上一次开机里一次 146 ms 的按下让 backboardd 清空图层并让电源管理进入显示器睡眠配置。断点在 `MiniIOMobileFramebuffer.cpp`：选择子 12 直接返回成功，面板只是被填黑，亮度不变；且没有背光服务。

改动：`thorPanelDisplayOn()` 用 120 Hz 切换时已验证的 `thorDsiTransferBatch()` 发一条 DSI 短写（布局照搬已验证的 NOP：`02 00 05 80 28 00 00 00` / `... 29 ...`）。`IOMobileFramebuffer::panelPower()`：第一次请求只记录“哪个参数代表开”（避免极性猜错把开机画面关掉），之后按跳变动作；关屏前先把帧缓冲清黑。不发 sleep-in（0x10），因为 DPU 仍在自动刷新。

未验证：DCS 短写在这块面板上的编码是按 NOP 的布局推断的；选择子 12 的参数含义靠首次请求学习。日志：`THOR FB POWER V229: request=N raw=... enabled=1 off=...`、`THOR DSI 120: displayOff result=...`。

## 3. 壁纸

依据（子任务核查）：`PosterBoard` 不在 `pong_exec_inject` 列表里，所以 UI 适配库里的 PosterBoard 存储适配从未运行；它按原厂逻辑对普通 APFS 发起文件保护类操作，启动即失败（日志里 PosterBoard 出现后紧跟一串 `apfs_is_valid_class ... rejecting class open (class 1)`），从没到过渲染。快照里没有 `Pong-Poster18.log`（适配库一旦注入就会生成）。

改动：启动参数追加 `,PosterBoard`。日志：`THOR INJECT: armed from boot args (260 chars)`；新文件 `/private/var/mobile/Library/Logs/Pong-Poster18.log`；若到了 GPU 阶段会出现 `NativeAdreno: PosterBoard VA lease=...`。

## 4. 声音、麦克风、通话、蓝牙、蜂窝语音：本轮结论

这些都不只是“接一下”的工作，诚实结论如下。

**声音（扬声器）**。Thor 用两颗 CS35L41 功放（I2C SE15 `0x880000`，地址 `0x42/0x43`，复位 GPIO24/66，听筒切换 GPIO52），与 Fold 2 同型但地址和复位脚不同。上游现状：Pong 的数字链路通、用户只确认过一次很小的锁屏音；Fold 2 图/DMA 都确认到了功放引脚，但**没出声**；Thor 上从未跑过任何音频代码。Thor 独有的两处前置缺口：音频探测挂在 `pong_adsp=2` 上（而它会让 Thor 的 ADSP 启动失效）；`adsp_apps` 的 GLINK 通道没有可用的边（电量工作线程独占一条边，且每次发布后睡 10 秒）。另外 **CS35L41 固件要从手机的 `/vendor/firmware` 读取**，这台 Mac 上没有。

**麦克风**。WCD938x 走 SoundWire，三个代码树里都没有 SoundWire 主机和从机驱动，也没有采集通路；Fold 2 的采集尝试同样没拿到数据。比扬声器更靠后。

**通话**。需要蜂窝语音（QMI VOICE/IMS）、modem↔ADSP 的语音通路（MVM/MVS，代码树里完全没有）和 iOS 电话界面三方面。Pong 的基带自己都记录“通话仍立即结束”；`MobilePhone` 在 22H374 上未注册为可运行应用。**近期做不到。**

**蓝牙**。Fold 2 做到了“设置里显示蓝牙开启并列出附近设备”，靠 644 行的 UART/HCI 探测 + 4 个 XNU 补丁 + 600 行用户态适配。Thor 的 UART（SE20 `0x894000`，SPI587，引脚 76–79）、PMU 引脚（81/82/204）与 Fold 2 相同，可以移植。硬性前置：**蓝牙固件在单独的 `bt_firmware` 分区，没有抓取过**（不在 modem 分区里）。本轮未写代码。

**蜂窝数据/短信**。见 v228 的 rmtfs；仍需 TFTP、memshare、QMI→iOS 桥。

## 5. 构建与注意

```
thor-out/v229-unified/
  prepare/clone.sh, relocate.py     # 从 v228 克隆
  prepare/env.sh                    # Wi-Fi 依赖：Linux 7.0.14、Ath12kWifi、UniXNU-sheng（均在 UniXNU-pong/refer/ipados18/）
  prepare/build.sh                  # 先 source env.sh，再 build-kernel.sh final
  package-v229.py package|verify
```
- `device/scripts/` 里的脚本**大多是指向 `UniXNU-pong/scripts/` 的软链**；往里 `cp` 会改掉上游仓库。本轮我误覆盖过 `build-pong-ath11k.py` 和 `build-pong-wifi-native.py`，已用 `git checkout HEAD -- <文件>` 还原，v229 里用的是私有副本。
- `device/configs` 也是上游软链，所以 Thor 的 Wi-Fi 配置放在 `device/configs-thor/`。
- 固件、Apple 二进制等私有输入都没有放进镜像。

## 6. 上机后

启动后依次看：壁纸是否出现、按电源键屏幕是否熄灭/点亮、设置里是否有 Wi-Fi 并能否扫描和加入网络、状态栏电量、点图标与滑动的跟手程度。然后重启到 fastboot 取日志。
若某一项异常：去掉对应 `/chosen` 属性重新打包即可单独关闭（Wi-Fi：`thor-wifi`；息屏：`thor-panel-power`；基带：`thor-rmtfs-serve`），不需要重编内核。
