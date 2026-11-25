# Thor v230 — 改动、自动抓取与上机后要看的日志

日期：2026-11-25 凌晨 ｜ 基线：v229（两者都还没上机）
镜像：`thor-out/v230-unified/boot-thor-unixnu-v230-unified.img`（95,084,544 字节）
SHA-256：`5eb2b784f5c419de36183080f92770a4735126df6278a6ec258478eaf7cb8610` ｜ 内核 UUID：`3404B397-B9D3-3FD9-BEE1-B52EF5654509`

## 本版新增（相对 v229）

| 项目 | 做了什么 | 开关 |
|---|---|---|
| 蓝牙探测 | 移植 Fold 2 的 QCA6490 探测：给 SE20 串口加时钟、拉 BT_EN、发 HCI Reset 和 EDL_PATCH_GETVER/GET_BID，记录 ROM 版本号和 board id。不发射射频 | `/chosen thor-bt-probe=2`（1 = 只读盘点；3 = 下载 `/chosen` 里的固件，本版无固件） |
| 音频探测 | 电量工作线程的 APSS↔ADSP GLINK 边上再挂一个 `adsp_apps` 监听通道（20×512 字节 intent），记录 ADSP 何时打开它、先发来什么。不回应、不建图、不请求时钟或 DMA | 默认开启（仅诊断） |
| 自动抓取 | Mac 上有一个等待进程：手机一出现在 FASTBOOT 就自动做三件事（见下） | 见第 2 节 |

v229 的 Wi‑Fi 驱动、电源键息屏、壁纸，v228 的 CPU 调频、ADSP 电源轨、基带 rmtfs 原样保留。启动参数没变（1000/1023 字节）。

## 1. 蓝牙与音频探测的依据

- **蓝牙**：Fold 2 做到了“设置里显示蓝牙开启并列出附近设备”，靠的是 `drivers/ArmDTPlatform/ZizhanBtProbe.h`（644 行）加四个 XNU 补丁和 `userland/bt_compat18.c`。Thor 的硬件与它相同：UART 是 SE20 `0x894000`（SPI587，引脚 76–79），PMU 引脚 81/82/204，TLMM 基址 `0xf100000`。探测头文件自带全部寄存器操作，所有等待都有上限。
- **音频**：Thor 的 ADSP 固件由 v228 的电源轨改动启动（未验证）。ADSP 的音频服务起来后会向 AP 打开 `adsp_apps`（GPR：SPF、APM、PRM），原厂 DT 给这个通道的 intent 是 `0x200 × 0x14`。此前 Thor 上没有任何代码监听它，所以“音频服务到底起没起”完全未知。

### 代码
- `device/drivers/ArmDTPlatform/`（v230 里改成真实目录）：`ThorBtProbe.h` 由 Fold 2 的头文件改写（日志前缀 `THOR BT V230`，模式取自 `/chosen thor-bt-probe`，上限 3）；`ArmDTPlatform.cpp` 里调用 `thor_bt_probe::start()`。
- `device/scripts/build-pong-platform-macos.py`（v230 里改成私有副本）：原先是上游软链，脚本按自身位置找源码，实际编译的是上游的 `ArmDTPlatform.cpp`。
- `device/drivers/PongPower/ThorBatterySource.cpp`：`Edge` 增加 `audio` 通道和 20×512 字节的 intent；新增 `audioState`/`audioReceive`；发布后的 10 秒睡眠改成“每 100 ms 轮询一次链路”，发布节奏不变。

### 主机验证
全部编译、链接通过；内核含 35 处 `THOR BT V230` 日志串和 `THOR AUDIO V230` 日志串。**没有跑过硬件。**

### 风险
- 蓝牙探测会改 SE20 的引脚复用和串口寄存器，并拉一次 BT_EN；结束时恢复引脚，时钟投票只增不减。它等待根文件系统就绪后再等 30 秒，以避开 Wi‑Fi 上电。
- 音频监听改了电量工作线程的睡眠方式。如果电量显示回归，优先怀疑这一处。

## 2. 自动抓取（手机连上后自动执行）

等待进程已启动（`v230-intake/auto-capture.pid`），最长等 48 小时。手机以 FASTBOOT 出现后依次：

1. 内存启动 TWRP（不刷写）。
2. 走已验证的只读路径抓上一次开机的全部日志（`v227-history-work/capture-history.sh`）。
3. 抓原厂固件（只读）到 `thor-out/capture-stock-<时间>/`：
   - 分区表、`by-name`、逻辑分区映射、挂载、内核参数、系统属性、声卡、电池状态；
   - `vendor_a`、`odm_a` 只读挂载后拉取 `firmware/`（含 CS35L41 功放固件）、`etc/wifi`、`etc/bluetooth`、`etc/audio`，ACDB 数据不超过 200 MB 时也拉取；
   - `bt_firmware` 分区整块读出并压缩；
   - 抓取前后比较 userdata 的写入计数，并生成 `SHA256SUMS.txt`。
4. 重启回 fastboot，内存启动 v230。

日志在 `v230-intake/auto-capture.log`，结束状态在 `v230-intake/auto-capture.status`。任何一步失败都不会阻止后面的步骤。**取消：** `kill $(cat ~/projects/thor-unixnu1030/thor-out/v230-intake/auto-capture.pid)`

等待进程只在本会话开着时有效。

## 3. 结果分别通向哪里

| 拿到的东西 | 下一步 |
|---|---|
| `THOR BT V230: ... getver-event`、`board_id=` | ROM 版本决定固件文件名；配合 `bt_firmware.img.gz` 里的文件，下一版做模式 3（下载 rampatch/NVM、读 BD_ADDR），再接 iOS 的四项移植 |
| `THOR AUDIO V230: adsp_apps up` 与首批 GPR 包 | 说明 ADSP 音频服务在 Thor 上能起来；之后才能建 APM 图。若从未出现，先看 `THOR PAS1 V228: final=ready` 是否成立 |
| `vendor/firmware` 里的 CS35L41 文件 | 把 Fold 2 的功放驱动按 Thor 的地址（`0x42/0x43`）、复位脚（GPIO24/66）和听筒切换（GPIO52）移植 |
| Wi‑Fi、电源键、壁纸、电量、CPU | 按 `v229-intake/ANALYSIS.md` 第 6 节和第 1 节的日志行 |

仍然没有可行路径的：麦克风（需要 SoundWire/WCD938x 驱动）和通话（需要语音通路、IMS 和电话界面）。

## 4. 更正一处构建事实

`device/scripts/` 里大部分脚本是指向 `UniXNU-pong/scripts/` 的软链，它们按**脚本自身位置**找驱动源码，所以除非替换成私有副本，实际编译的是上游文件。v229 的平台驱动就是这样编译的（结果等价，因为 Wi‑Fi 启动入口上游文件里也有）。v230 起平台驱动和平台脚本都是私有副本。
