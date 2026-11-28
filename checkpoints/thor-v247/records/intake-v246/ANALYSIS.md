# v246：v245 在手机上的结果，以及这一版改了什么

2026-11-27 19:48–22:50。

| 文件 | 字节 | SHA‑256 |
|---|---|---|
| `thor-out/v246-unified/boot-thor-unixnu-v246-unified.img` | 95,473,664 | `4b4fae2b67d21e9b54ff20c3b7835df62b84d70e9189a0cfae793aad88d3070a` |

内核 UUID `ECDC7FAF-CE7B-3F57-B3E9-945A9A7BCE08`（父：v245 `97961EAC-5BC5-309C-AF8C-63A1E3C0B5DC`）。
记录：`v246-unified/unified-verification.json`。启动参数与 v239–v245 逐字节相同（1005 字节）；设备树与 v245 相同
（有 `wlan` 节点）。启动栈预算：深度 9 空闲 14240 字节，深度 10 空闲 13808 字节。

## 1. v245 在手机上看到的（一次开机 19:48–19:54，335 秒，无 panic）

用户的话：进桌面了，壁纸还是黑的，Wi‑Fi 还是连不上，定格了，输入法没有。

| 现象 | 日志里的事实 | 结论 |
|---|---|---|
| **定格** | 内核日志一直活到 335.9 秒，没有 panic。321.8 秒打开搜索，键盘进程加载了语言模型（没有崩）；322.6 秒**“信息”App（MobileSMS）被拉到前台**，之后再没有新帧（`THOR SCANOUT … state=no-new-write swap=6050`）；“信息”没有崩溃也没有起来 | 不是内核问题：那个 App 起不来，桌面在等它。它依赖的 `MessagesBlastDoorService` 在这个移植上每 10 秒崩一次（老问题） |
| **毛刺** | 显示库装上了（`THOR DISPLAY V245: backboardd display library armed`），窗口服务器启动时被问到 4 次 `-bounds`：显示器**没有自己的尺寸**（原生 0×0、没有当前模式、pointScale 是 NaN），报的是 1206×2622。我的条件要求“原生尺寸等于面板”，所以没改。第一帧交换面仍是 1206×2622 | {1206, 2622} 是“显示器没有信息”时的替身。backboardd 自己的日志：`[WindowServer] Display 1 current mode (16383 x 16383), preferred mode (0 x 0)`，并且向帧缓冲驱动要的一批属性（DisplayAttributes、IOMFBUUID、IOMFBScalingLimits、DisplayPipeStrideRequirements、IOMFBNumLayers …）都没有 |
| **壁纸** | 适配库注入成功（`Pong Poster18: native DataStore fallback uses writable …/Pong/PosterBoard`），紧接着初始化数据库时 PosterBoard 以 SIGABRT 死掉，连着 3 次（pid 189、221、243），第 4 次不带适配库起来（自保生效） | 适配库在这个系统上的数据库初始化路径会崩。原因没抓到：适配库自带的信号处理覆盖了日志库的，而日志库在 PosterBoard 里没开崩溃记录 |
| **Wi‑Fi** | 网卡地址变成了 `62:25:b4:61:44:d4`；关联请求 115 字节（caps 0x0421，HT，Power Capability，Supported Channels，Extended Capabilities，WMM）；AP 回 status 48，AID 1 | 见 §5。同一组 AP 在 5 GHz 上也有这个 SSID（a8:50:81:f8:e2:71），而我们的站点永远挑信号最强的 2.4 GHz BSS |
| 输入法 | 键盘进程这次没崩（用户打字时它活着），只是界面出不来 | 仍是 v245 §6 的问题（缺手机键盘布局） |

## 2. 这一版对毛刺做的事

`libpong-display-v246.dylib`（85,824 字节，libSystem + libobjc）只改条件：显示器报 {1206, 2622} 时，只要它的原生尺寸是面板
{1440, 3200} **或者是空的**，就回答 {0, 0, 1440, 3200}。Mac 测试（`prepare/display/hosttest/run.sh`）三种情况都对；
**手机上没跑过**。自保不变：渲染进程连续回来时第 4 次起不注入。

日志里要找：`PONG PROBE backboardd[…]: Pong Probe: display v246: call N of -bounds … the panel's size is answered`，
然后第一条交换面是不是 1440×3200、`PONG SCANOUT: native CPU 1:1`。

## 3. 壁纸进程的崩溃

`libpong-logcap-v246.dylib`（87,632 字节）：PosterBoard 也进入崩溃记录模式（保留最后 40 行，进程死时连同信号、寄存器、栈帧一起送出；
带 terminat / exception / assert 等词的行立即送出），并且在启动 2 秒后和收到第一行输出时**重新挂一次信号处理**，
盖过适配库后来挂的那个。预期下一轮能看到 `*** Terminating app due to uncaught exception '…', reason: '…'`。

## 4. 这一版没变的

适配库仍在 PosterBoard 里（第 4 次启动起不注入）。键盘和无边记的根因（iPhone 身份配 iPad 固件）没动：需要同版本 iPhone 固件的
`KBLayouts_iPhone.dat` 等资源，这台 Mac 只剩约 7 GB 空间，下载和解包要 20 GB 左右。

## 5. Wi‑Fi：三次尝试

v245 的数据帧（都在内核日志里，十六进制）：

- AP 信标：SSID `<network>`，信道 11，国家 "CNI"，没有 RSN（开放网络），HT（能力 0x09ad）+ **2.4 GHz 上的 VHT**（能力 0x33895992），
  RM、Extended Capabilities，厂商元素 OUI 00:e0:fc。共 265 字节元素。
- 我们的请求：caps 0x0421、监听间隔 10、SSID、速率、HT 能力、Power Capability、Supported Channels、Extended Capabilities、WMM。
- AP 应答：status **48**，AID 1，带Supported Rates、HT、HT Operation、Extended Capabilities。

48 在标准表里是 "No direct link"，对普通关联说不通。v244（没有地址、没有这些元素）和 v245（都有）得到同一个结果，
所以不是地址、不是这些元素。同一组 AP 在 5 GHz 上广播同一个 SSID（信标里 e2:61 是 2.4G、e2:71 是 5G），
很多 AP 的“5G 优先 / band steering”就是把 2.4G 上的关联拒掉，让双频客户端去 5G——而我们的站点永远只试 2.4G。

v246 的站点在被拒之后自动再试：

1. 同一网络的 5 GHz BSS（完整能力）——`wifi: joining once more on a 5 GHz BSS of the same network`；
2. 还被拒，就以不带 HT / WMM / Extended Capabilities 的普通 802.11g 身份再试一次——`wifi: joining once more as a plain 802.11g station`；
3. 成功时 `wifi: the access point accepted the plain request (no HT)`（如果是在第 2 次成功，会多这一行）。

每次被拒都把应答帧打出来。另外安装脚本在 TWRP 里只读扫描 persist 等分区找手机的出厂网卡地址（`v246-intake/mac-scan.out`），
之前查过设备树、系统属性、devinfo、misc 都没有。

## 6. 待办（用户 22:40 追加）

通话、摄像头已开始调研（两份只读调研并行进行），结论另记。
