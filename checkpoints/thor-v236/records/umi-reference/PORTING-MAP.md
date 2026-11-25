# 小米 10 (umi) 参考移植 → Thor (小米 12S Ultra, SM8475, iPadOS 18.7.10) 移植对照

来源：`thor-out/reference-umi/hakios-xnu-kernel-umi-fixes`（HakiOS 2.3.4 系，XNU 8796.121.2 / iOS 16.5，只读研究）。细节在各分册：`bluetooth-wifi.md`、`audio.md`、`cellular.md`、`gpu-display-hang.md`（同目录）。标注"**推断**"的是读代码得出的判断，仓库里没有明说。

两地最大的差别：umi 的系统是 iOS 16.5，Thor 是 18.7.10——凡是用写死的偏移、结构体大小、`MTLDeviceSPI` 之类私有布局接进 iOS 的部分（`bluetoothd` 补丁、Apple80211 ioctl 结构、Metal 插件、IOMobileFramebuffer 选择器），在 Thor 上都要按 18.7.10 重新推导；SoC 相关的地址、GPIO、电源轨、固件文件也全部不同。能直接搬的是**结构**：传输层设计、协议状态机、寄存器序列、内核与 iOS 之间的接法。

## 总表

| 领域 | umi 的做法 | Thor 现状 | 可直接搬 | 必须重做 |
| --- | --- | --- | --- | --- |
| 蓝牙 | QCA6390，H4 over UART(115200)，固件编进内核，EDL 下载；`bluetoothd` 用 `H4:POSIX` 连内核控制套接字 | QCA6490；v231 探测拿到 HCI Reset / EDL 应答；`bluetooth_a` 固件已取出 | H4 解析、控制套接字传输、EDL 分段下载、"假 IOService + 真实 HCI"的接法 | UART/GPIO/时钟/RPMh 地址；`hpbtfw21.tlv`+`hpnv21.*` 的下载流程；18.7 的 `bluetoothd` 是否还支持 `H4:POSIX`（未知），要重新找偏移 |
| Wi-Fi | PCIe→SMMU→MHI→IPCR/QRTR→Linux ath11k；iOS 走 `SIOCG/SIOCSA80211` ioctl + 假 IO80211Controller；自带 WPA2-PSK | 同一套 ath11k+MHI 已接入（`thor-wifi=2`）：M0、扫到 48 个 BSS、没有关联 | ioctl 处理、事件（`KEV_VENDOR_APPLE`）、手写 WPA2 握手、MAC 来源、不需要 `board-2.bin` | Apple80211 结构体大小（iOS 16.5 写死）；关联触发（iOS 的 ASSOCIATE ioctl 或 wifid） |
| GPU/显示 | Metal on Adreno 650（hakios-metal），DPU 页翻转，零拷贝别名 | Metal on Adreno（NativeAdreno/Turnip 桥）已可用，软件扫描输出 | 定格取证法（每核 PC 面包屑+常驻读取器、SpringBoard 主线程采样）、GPU SMMU 故障恢复、DPU 直接扫描 | Metal/IOMobileFramebuffer 私有布局、a7xx 寄存器表、DPU/面板参数 |
| 数据联网 | 外挂 SDX55（PCIe2），CommCenter 桥接 | 集成基带：固件已加载、rmtfs 只读服务、QRTR 就绪，CommCenter 桥未接 | QMUX→QRTR、CTL 本地应答、激活门（TLV 0x15）、运营商包推送、WDS 观察、`pdp_ip0..3`、桥的死锁陷阱清单 | 基带启动/加载器（umi 那套不适用）；18.7 的 CommCenter ABI 与激活门是否仍可绕过 |
| 短信 | WMS 原样转发；修 `MessagesBlastDoorService` 的沙盒判定 | Thor 日志里 `MessagesBlastDoorService` 仍在反复生成尸体 | 同一个修复：沙盒替身要对 `sandbox_check(self)` 如实回答"在沙盒内" | 横幅/提示音在 umi 上也没解决 |
| 声音 | APR（AFE/ASM/ADM）over SMP2P+GLINK；CS35L41 功放；用户态 AudioServerPlugIn | ADSP 监听已通（`adsp_apps up`）；CS35L41 固件、ACDB 已取出；音频代码未编入 | CS35L41 寄存器序列、WMFW/WMDR 解析、位敲 I2C、HAL 插件结构、SMMU 周期环、ADSP 启动套路 | 协议：SM8475 的 ADSP 是 GPR/AudioReach（树里已有 `ShengGpr.c`、`ShengAudioReach.c`，**推断**），APR 操作码不通用；功放固件 ID 检查要重推；麦克风驱动两边都没有 |

## 蓝牙（QCA6490）

umi 的链条：复位 GPIO → UART 115200 8N1 → 读芯片身份（厂商命令 `0xfc00/0x19`）→ EDL `0xfc00 {0x1e, len, data}` 按 243 字节分段先下补丁（TLV 类型 1）再下 NVM（类型 2，tag-17 波特率强改 115200）→ `0xfc17`、HCI Reset、读版本/支持命令/地址。运行期是一个流式内核控制套接字 `com.hakios.bluetooth`（64 KiB 队列，uid 0/501，单客户端），工作线程独占 UART 并处理 IBS 0xfd/0xfc 唤醒字节；另发布一个名为 `bluetooth` 的普通 IOService（`local-mac-address`、支持命令位图）。`bluetoothd` 用环境变量 `BT_HCI_TRANSPORT=H4:POSIX:com.hakios.bluetooth,115200` 选传输；`inject_bluetooth.c` 按 UUID 定位 `bluetoothd`（iOS 16.5）改四处：屏蔽 Broadcom/Apple VSC 区间、允许设置随机 LE 地址、跳过不支持的 Write Default Erroneous Data Reporting、关低功耗扫描参数；BlueTool 换成只应答 `boot`/`init` 的替身。umi 文档不声称配对、音频、网络共享可用。

Thor 下一步：先在主机上确认 18.7.10 的 `bluetoothd` 是否还带 `H4:POSIX` 传输（从 IPSW 取出 `bluetoothd` 查字符串）；若带，模式 3 = 内核里起 GENI UART、按 umi 的流程下载 `hpbtfw21.tlv` + `hpnv21.*`、挂控制套接字；若不带，需要换思路（伪造 IOBluetooth 家族，或给 CommCenter/`bluetoothd` 打更大的补丁）。

## Wi-Fi（QCA6490）

umi 的驱动侧与 Thor 现有 `thor-wifi=2` 同源（ath11k + MHI + QRTR）。Thor 缺的是"让 iOS 发起扫描和关联"这一段：

1. BSD `en0`（`ifnet_allocate_extended`，Ethernet 族、WIFI 子族），未知 ioctl 交给 `hakios_apple80211_ioctl()`：`SIOCGA80211 0xc02869c9` / `SIOCSA80211 0x802869c8`，`struct apple80211req`。GET 实现了能力、电源、模式、信道、扫描结果、状态、SSID/BSSID/RSSI、当前网络；SET 实现了 ASSOCIATE（口令或 PMK）、DISASSOCIATE、POWER、SCAN_REQ。umi 版本里唯一的关联入口是 iOS 的 ASSOCIATE ioctl（本次构建）。
2. 事件用 `kev_post_msg(KEV_VENDOR_APPLE, KEV_IEEE80211_CLASS)`：POWER/SSID/BSSID/LINK/ASSOC_DONE/SCAN_DONE，接口名放在前 16 字节。
3. 一个属性齐全但没有 user client 的 `IO80211Controller→IOEthernetController→IONetworkController` 树，让 IO80211 退回 ioctl 路径。
4. 关联与握手（`lx/lx_80211.c`、`lx_wpa.c`）：按名字最多扫 3 次 → 开放系统认证 → 关联请求 → 等 4 次握手 ≤ 8 s → 装密钥 → 上报链路；WPA2-PSK 的 PBKDF2 是 4096 轮，只接受 CCMP（优先）或 TKIP，拒绝 WPA3/企业版。地址由 configd 起 DHCP；MAC 来自私有文件 `fw-wifi/mac`（固件报全零）。
5. 不需要 `board-2.bin`（umi 用 `board.bin→bd_j1gl.elf`、`amss20.bin`、`m3.bin`、`regdb.bin`）——Thor 日志里的 `BOARD-2.BIN missing` 不是问题。
6. 要避开的坑：端点复位后环形缓冲线程再写会挂死 SoC；非任务模式读射频寄存器会中止内核；`idle_ps` 默认关（曾挂内核）；umi 用轮询而不是 MSI 中断（`wifi_irq=0`）。
7. umi 自己没合入的加固项（通用树的 W1/W2/W7/W8：message 1 重放保护、握手结果粘滞、GTK 长度必须等于 16、`want_bssid`）搬过来时应当补上。

Apple80211 的结构体大小在 umi 里是对 iOS 16.5 写死的（`_Static_assert`），18.7.10 需要重新核对每个请求的长度。

## GPU、显示与定格取证

Thor 的 GPU 路径已经比 umi 完整（Metal 可用，帧由 CPU 写到面板）。值得借的是取证与恢复：

- **面包屑**：每核一条 192 字节记录（中断时的 PC/LR/SP、空闲标志、自旋锁持有者与等待、GIC 中断号、pmap 阶段、panic 记录），只由本核用普通存储写；加载器留在 CPU0，内核静默 50 s 后从内存读出并画在面板上 30 s。Thor 的 v233 把同样的思想放进了内核监视线程。
- **SpringBoard 主线程采样**：一有超过 100 ms 的主线程轮回就打一行栈（`tl: A turn_ms=... top=NAME:COUNT...`）。Thor 的 v205–v227 诊断库已有同类机制（`ThorStall`/`ThorSwipeStack`）。
- **GPU 故障恢复**：`gpu_recover=1`，SMMU 故障在等 fence 时触发：复位核、丢弃在途提交、保留 512 项映射环；恢复是否成功看 fps 和 `fbstat` 是否继续增长。
- **显示**：`fb_dpu=1` 在 UEFI 的 splash 专用内存里翻页；`fb_dpu=3` 用 VIG0 硬件缩放；`fb_dpu=4` 把 CA 表面的 GPU 页表别名到扫描帧，CPU 不再搬像素。umi 记录的"退出应用后界面冻结"是**对已释放内存的 GPU SMMU 故障**——Thor 每次定格前的最后一条内核日志都落在 GPU 大块导入/释放上（`PONG GPU LARGE IMPORT`、`PONG GPU MEM: free backboardd`），v233 的报告会直接给出此时各线程在等什么。
- 帧率口径：umi 文档里 100–118 帧是 POCO X5 Pro（Adreno 642L）的数字；小米 10 面板 59.8 Hz。

## 蜂窝数据与短信

umi 的基带是外挂 SDX55（`17cb:0306`，PCIe2，BHI/Sahara 加载 17 个镜像，EFS 靠 Sahara 镜像加内存同步，没有 rmtfs）。Thor 是集成基带，固件加载、RMTFS、QRTR 这几步 Thor 已经自己走通，所以 umi 里要搬的是它没有写进驱动的那一层——CommCenter 桥：

- 给 CommCenter 的沙盒看的 IOKit 类名：`AppleBaseband`（名为 `baseband`，接受 Start/StopDevice）、`AppleBasebandPCIControl`、每通道一个 `AppleBasebandPCIInterface`、`AppleBasebandPCIMAVPDPManager`、`AppleIPAppender`。Thor 树里的 `pong-baseband/PongBasebandService.cpp` 是同源移植（**推断**，只读了头文件），umi 多出的是：未应答请求表、客户 ID 池化（96 个，永不删除）、WDS/QOS 绑定时插入端点 {3,4}、ABM 的 DMS 模式 4/5 改写成 1、合成 WDS 状态指示、激活指示 TLV 0x15、IMS 走"路由 B"、小区信息/GNSS 客户端。
- 激活门：16.5 在"没有票且允许激活"时拒绝联网；不改 CommCenter 的办法是 BSP(`0xe2`) `0xb00c` 的每条指示里放 `0x15=1`，再用一个钩子让 mobileactivationd 报告"已激活"。17.7.11 上参考树需要给 CommCenter 打补丁——18.7.10 属于哪种未知。
- 运营商包：CommCenter 通过 BSP `0xa000` 分段推送，每段用下一段号应答，文件结束发指示 `0xa001`，否则基带每约 12 s 被复位一次。
- 数据面：MHI IP_HW0，raw IP over QMAP(5)；`pdp_ip0..3` = mux 1..4（`IFNET_FAMILY_CELLULAR`）；地址、路由、DNS 都由 CommCenter/configd 负责，内核不装。umi 观测到 LTE 注册、运营商、信号格、Safari 可用（但文档没有逐条确认）。
- 桥的陷阱（每个都修过）：请求无人应答 → `ATCS_TIMEOUT` 每 26 s 复位一次；客户 ID 耗尽；只按 mux 绑定 → `INVALID_ARG 0x30`；复用端口上的跨服务指示 → ABM 崩溃；被 3GPP 拒绝的 Start → CommCenter 等 360 s 后复位。
- 短信：WMS(`0x05`) 请求和指示逐字节转发即可；UMI 上收不到是 `MessagesBlastDoorService` 因 `sandbox_check(self)==0` 而 SIGTRAP——Thor 的控制台里同样的进程在反复生成尸体，短信接通前要先修沙盒替身。横幅和提示音在 umi 上也没解决；发送未验证。VoLTE：没有打通（IMS 承载能起来，无 SIP 注册，无通话）。
- 状态栏信号走系统原生路径（CommCenter→CoreTelephony→SpringBoard），不要用旧的 sysctl 钩子。

## 声音

- **协议不通用**：umi 走 APR（AFE 4 / ASM 7 / ADM 8，语音 MVM 9 / CVS 10 / CVP 11），GLINK 通道 `apr_audio_svc`；SM8475 的 ADSP 固件是 GPR/AudioReach（Thor 树里已有 `PongPower/ShengGpr.c`、`ShengAudioReach.c`，**推断**）。扬声器/麦克风/语音的 APR 操作码序列在 Thor 上只能当"流程参考"：时钟 → AFE 端口参数与启动 → ASM 共享内存映射 → ADM 打开 → ASM 打开写 → 路由 → 每周期 `WRITE_V2`、完成事件回补。
- **CS35L41 功放可搬**：复位 → 读 DEVID `0x35a40` / REVID → OTP 修调 → `.wmfw`（"WMFW"，记录类型 0x10/0x11/0x12 → XM/YM/PM，每块写回校验）→ `.bin`（"WMDR"调参）→ DSP 启动 → 每次播放：静音、`0x4c00=0x32`、GLOBAL_EN(`0x2014`)、音量 `0x6000`（1/8 dB，2048 = 0 dB）、解除静音。umi 的功放在 I2C `0x40/0x42`、位敲 SDA/SCL；Thor 是 I2C SE15 `0x880000` 上的 `0x42/0x43`，复位 GPIO24/66。固件 ID 锁（`0x400a4`/版本 `0x300`/算法 `0xcd`）是对 umi 的 `…-prot-revb2.wmfw` 写死的，Thor 的 `cs35l41-dsp1-spk-cali.wmfw` / `-prot.wmfw` 要重新读出。
- **接 iOS 的方式可搬**：umi 不做 IOKit，给两个 BSD 设备（`/dev/hakios-audio` 写 + 音量 ioctl，`/dev/hakios-mic` 非阻塞读），用户态放一个 AudioServerPlugIn（`HakiosAudio.driver`，挂在 `/System/Library/Audio/Plug-Ins/HAL/`），对系统呈现 VirtualAudio 期望的设备名（iPhone X 的表：Speaker / Hawking / Actuator / Codec）。**推断** iPadOS 18.7 的设备名表可能不同。Thor 现有的 `PongAudio.cpp`/`PongIOAudio2.h`/`PongAdsp.cpp` 是另一条实现路线，需要对照决定沿用哪条。
- **麦克风**：umi 是 WCD9385 → SoundWire → Bolero TX；Thor 的编解码器没有现成驱动，这是两边共有的空白。
- 坑：从 AP 读 LPASS 时钟寄存器会挂内核；不给 `lcx/lmx` 供电 DP 报 "SFR Init" 错；SMP2P 条目顺序错则永不"up"；未确认的 stop 之后重启会复位手机；读 LPASS 寄存器要在上电后。

## 建议的顺序（针对 Thor）

1. 先结束定格问题（v233 的内核取证）——它挡住所有交互测试。
2. Wi-Fi 关联：接 Apple80211 ioctl 路径（按 18.7.10 重核结构体）+ WPA2-PSK 握手 + MAC 来源。驱动侧已完成大半，收益最大。
3. 短信的沙盒修复（改动小，umi 已验证），随后接 CommCenter 桥通数据。
4. 蓝牙模式 3（先确认 18.7.10 的 `bluetoothd` 传输选项）。
5. 声音：在 Thor 现有 GPR/AudioReach 路线上接 CS35L41 序列与 HAL 插件。
