# v244：v243 / v243‑wifi 在手机上的结果，毛刺的原因，以及这一版抓什么

2026-11-27 14:00–15:45。

| 文件 | 字节 | SHA‑256 |
|---|---|---|
| `thor-out/v244-unified/boot-thor-unixnu-v244-unified.img` | 95,391,744 | `03a5c59c6a06dc59f6de7d224cc673dcdb0c31baadcd15f45a42bb7d26f359b6` |

内核 UUID `84BD6754-7DEA-3E51-BD26-6AA922E84023`（父：v243 `7567DC32-EA2D-3298-AC22-0B19EF51B1A1`）。启动参数与 v239–v243 逐字节相同（1005 字节）；
设备树与 v239 相同（有 `wlan` 节点，Wi‑Fi 守护进程会启动）。启动栈预算与 v243 相同（深度 9 / 10，空闲 14240 / 13808 字节）。
记录：`v244-unified/unified-verification.json`。

## 1. 已经在手机上验证的：从闪存启动时的电量与 Wi‑Fi 固件

| 启动 | 第一次时间戳 | LUN 4 的检查 | 电量 |
|---|---|---|---|
| v242 10:51（刷入，槽位未验证） | 10.46 s | 14 次全部失败，都是第 8 步（主/备分区表不一致） | 未发布 |
| v243 13:44（槽位已标记成功） | 从闪存 | 第一次就通过 | 82%，已发布 |
| v243‑wifi 14:4x | 从闪存 | 通过 | 已发布，射频驱动起来（固件读完、en0 挂上、收到信标） |

标记之后每次正常启动，分区表不再被引导程序改写；引导程序回答 `slot-successful:a yes`、`slot-retry-count:a 7`、`slot-unbootable:a no`。
用户 14:1x 的反馈：进了桌面，电池图标正常，设置里打开了电量百分比但状态栏没有数字。

## 2. v243‑wifi（14:4x 那一轮，426 秒）看到了什么

- **Wi‑Fi 能扫描。** wifid 在 75.5 秒放行，76.2 秒开始向驱动发请求；扫描结果带 0 / 2 / 102 三种返回值。
- **没密码的网连不上**：180.7 秒 "join: ssid length 13, bssid given 0, key length 0 cipher 0" →
  **"wifi: association refused: 48"** → "join finished: module result 3"。48 是 AP 在关联应答里给的状态码，
  通常是"不支持该 AKM"——这个开放网络很可能是 OWE（增强开放）。`nw_sta_pong.c` 只实现了纯开放和 WPA2‑Personal。
- **"桌面定格"不是内核崩溃**：设置（pid 333）在 172.8 秒就卡在同步的 XPC 等待里（栈：mach_msg → libxpc → PreferencesUI/WiFiKit），
  在点连接之前；269.9 秒被 RunningBoard 杀掉，第二个设置 390–425 秒又卡住；控制台一直活到 426.2 秒，没有 panic。
  几乎可以肯定是 wifid 不应答。没有线程采样，所以说不清它卡在哪。
- **没有输入法**：键盘进程 `kbd`（pid 125）在 246.7 秒第一次被调用时崩溃，273.6 秒被重新拉起。
- **无边记** 275.8 秒崩溃。
- 这两个崩溃都没有报告：每次开机的 5 个尸体名额在头 83 秒就被别的进程占满
  （MessagesBlastDoorService ×2、biomed、fitcored ×2）。
- wifid 的日志文件只有 129 字节：环境变量 `OS_ACTIVITY_DT_MODE` 要 AMFI 放行才生效，Apple 自己的进程在这里不被放行。

## 3. 毛刺的原因

| 版本 | 渲染进程每帧的软件绘制尺寸 | 帧缓冲驱动 |
|---|---|---|
| v237–v239 | 1440×3200（`PONG SCANOUT: native CPU 1:1`） | 原样送出，每帧 1.1–2.2 ms |
| v242、v243、v243‑wifi | **1206×2622**（iPhone 16 Pro 的像素尺寸） | 最近邻放大到 1440×3200，每帧 2.7–3.6 ms |

SpringBoard 的 UIScreen 两边都是 1440×3200 / 480×1066.67 pt / 3 倍。变化与"壁纸库注入进 SpringBoard"同时出现（v242 起），
机制还不清楚。v244 不再注入壁纸库，这一版能看出原生渲染是否回来。放大还多花 1.5–2 ms/帧，是掉帧变重的一部分。

## 4. 壁纸：v243 的库为什么没有效果

v243 的日志里库已加载，但 WallpaperKit 在 v243 / v243‑wifi 的每一次启动里**都没有去找目录**（没有一次 "lower-cased to ipad"），
所以系统沿用它保存过的壁纸选择（黑色）。`MGCopyAnswer` 拦截在这个系统上做不到（共享缓存里的直接跳转，见 `v243-intake/ANALYSIS.md` §4）。
v244 去掉这个库，改为抓 WallpaperKit 自己的日志，看它为什么不找目录、凭什么给出黑色。

## 5. v244 的内容

| 改动 | 位置 | 作用 |
|---|---|---|
| 不再注入壁纸库 | `kern_exec.c` | 见 §3、§4 |
| 日志捕获库 `libpong-logcap-v244.dylib`（86,864 字节，只依赖 libSystem） | `prepare/logcap/logcap244.c`、`trace_flags.h`；注入 SpringBoard、backboardd、PosterBoard、CollectionsPoster、PowerUIAgent、无边记、kbd | 让进程自己的 os_log 镜像到它的错误输出（libsystem_trace 的三个开关，直接写，不靠环境变量），一个线程读出来，按关键字送到控制台：`PONG PROBE <进程>[pid]: Pong Probe: log v244 <进程>: …` |
| 无边记、kbd 的致命信号处理 | 同上 | 进程死前把最后 40 行错误输出送到控制台（`before the crash: …`、`at the crash: …`）——未捕获异常的原因就在这里 |
| wifid 自己的日志 | `prepare/wifid/wifid244.c` → `libpong-wifid-v244.dylib`（86,000 字节） | `/private/var/logs/PongWifid-v244.txt`，进程活多久记多久（上限 24 MiB） |
| 探针回到 wifid | `prepare/probe/probe244.c` → `libpong-probe-v244.dylib`（90,064 字节） | 在 5、25、60、90、100、110、140、200、320、560 秒采样 wifid 的线程：它卡在哪一帧 |
| 诊断通道 | `kern_exit.c` | 允许 backboardd、PowerUIAgent、无边记、kbd 写；行数预算 1500 → 5000 |

os_log 的三个开关（22H374 的 libsystem_trace，libtrace‑1643.120.5，`__DATA_DIRTY`）：
`0x1f28efbbe` 镜像到错误输出、`0x1f28efbbf` 工具模式（info/debug 也组合）、`0x1f28efbc0` 工具隐私。
`pong_trace_flags()` 只在 `kern.osversion == 22H374` 且三个地址落在该镜像的 `__DATA_DIRTY` 里时才写。

**在 Mac 上验证过的**：捕获库的对照测试（`prepare/logcap/hosttest/run.sh`，打包时又跑了一次）：名为 SpringBoard 时 4 行关键字、状态 0；
名为 Freeform 时 11 行、状态 134；其他进程什么都不输出。**没有在手机上跑过的**：这个库在这七个进程里的全部行为、
三个开关的实际写入（Mac 上不是 22H374，函数直接返回）、探针与放行库同时在 wifid 里（v240 及以前探针单独在 wifid 里跑过）。

## 6. v244 没有修的

壁纸、Wi‑Fi 连接（关联被拒 48）、键盘进程崩溃、无边记崩溃、掉帧——这一版是为了拿到它们各自的原因。
设置的 Wi‑Fi 页面仍可能像上一轮那样卡住。

## 7. 日志里要找的行

- `THOR LOGCAP V244: staged `、`THOR LOGCAP V244: <进程> log capture armed`
- `Pong Probe: log v244: capturing the error output of <进程> (… os_log mirrored|not mirrored)`
- 第一条 swap 记录和 `PONG SCANOUT`：原生 1440×3200，还是 1206x2622
- `Pong Probe: log v244 SpringBoard: …`、`… backboardd: …`、`… PosterBoard: …`、`… PowerUIAgent: …`（壁纸、电量百分比、软件渲染）
- 无边记、kbd 的 `before the crash: …`、`at the crash: …`
- `Pong Probe: wifid hold v244: released after … ms`，`PONG PROBE wifid[…]: Pong Probe: 5s / 25s / 60s / …`
- `THOR MODEM FW V163: state=gpt-verified`、`THOR BATTERY V226: published`（应当和 v243 一样）

安装记录：`install.log`、`auto-install.log`。

## 8. 在手机上的结果（15:48–17:29，5983 秒，无 panic）

进了桌面；状态栏有电量数字了；毛刺、掉帧、黑壁纸、无边记崩溃、键盘调不出、Wi‑Fi 连不上都还在。
每一项在日志里看到的原因、以及 v245 据此改了什么，写在 `v245-intake/ANALYSIS.md`。
