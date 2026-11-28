# v245：v244 在手机上的结果，以及这一版改了什么

2026-11-27 17:30–19:45。

| 文件 | 字节 | SHA‑256 |
|---|---|---|
| `thor-out/v245-unified/boot-thor-unixnu-v245-unified.img` | 95473664 | `ee62dc97b68baf022fa78834b8d5e5c503dea76458287707bd964e153ea11eba` |

内核 UUID `97961EAC-5BC5-309C-AF8C-63A1E3C0B5DC`（父：v244 `84BD6754-7DEA-3E51-BD26-6AA922E84023`）。记录：`v245-unified/unified-verification.json`。

启动参数与 v239–v244 逐字节相同（1005 字节）；
设备树与 v244 相同（有 `wlan` 节点）。启动栈预算：深度 9 空闲 14240 字节，深度 10 空闲 13808 字节。

## 1. v244 在手机上看到的（快照 `recovery-v244-full-20261127T092935Z`，一次开机 5983 秒，无 panic）

用户的话：进桌面了，还是有毛刺、掉帧，壁纸黑的，无边记还是崩溃，**状态栏有电量数字了**，键盘调不出来，
搜索里的"取消"卡顿，Wi‑Fi 还是连不上。

| 现象 | 日志里的原因 | 状态 |
|---|---|---|
| 电量数字 | 日志捕获库在七个进程里都工作（每行 "os_log mirrored"），电池已发布 | **已好**（用户确认） |
| 毛刺、掉帧 | 渲染进程从 v242 起按 1206×2622（iPhone 16 Pro 的像素尺寸）软件绘制，驱动最近邻放大到 1440×3200，每帧约 3.6 ms | v245 做实验（§3） |
| 壁纸黑 | PosterBoard 建不了数据库：`[Migration] Beginning migration to Version 59` → `Failed to migrate to Version 61 from 59` → 每次请求都 `could not initialize data store`（一次开机 16 次）；SpringBoard `SBHomeWallpapers: none`。UI 适配库里有针对这同一问题的存储适配（"Pong Poster18"），但在 Thor 上从未注入 PosterBoard | v245 注入（§4） |
| 无边记崩溃 | 开始画之前 Metal 断言 `computeFunction must not be nil`（一个 Metal 计算函数为空）。同一份日志里还有 UIKit 找手机键盘布局：`LayoutStar keyboardName iPhone-PortraitChoco-Pinyin-Simplified resulted in (null)` | v245 只加诊断（§5）；根因在 §6 |
| 键盘调不出 | kbd 在第一次用到键盘时死掉，没有任何一行错误输出；同样的"手机键盘布局找不到" | 同上 |
| Wi‑Fi 连不上 | wifid 这次不卡（请求 1 ms 内返回）。加入开放网络 "<network>"（2462 MHz）：`connecting to a8:50:81:f8:e2:61` → 72 ms 后 `association refused: 48` → wifid 正常收尾（`END REQ [ASSOC] … err=-3906`）。另外：网卡地址一直是射频固件的默认值 `00:03:7f:12:34:2e` | v245 改地址、补全关联请求、加诊断（§7） |
| 渲染进程之外 | 5861.9 秒 SpringBoard 因断言 `Invalid condition not satisfying: processIdentity` 重启过一次（按电源键熄屏再亮屏之后） | 未处理 |

## 2. 毛刺：查到了哪里，没查到为什么

- v239 与 v244 在显示这条路上**完全一样**：帧缓冲驱动、UI 适配库、图形桥、设备树、启动参数。用户空间拿到的选择器调用序列也一样。
  不同的只有结果：v239 的第一帧交换面是 1440×3200（`PONG SCANOUT: native CPU 1:1`），v242 起是 1206×2622。
- backboardd 的 `-[BKSystemFacade startWindowServer]`（0x100034fe4）把 `+[CADisplay mainDisplay]` 的 `-bounds`
  当作 "initial main screen size"（v244 的日志：`{1206, 2622}`），窗口服务器的显示、各个场景、交换面都跟着它。
- 框架侧 `IOMobileFramebufferGetCanvasSizes` 画布列表来自驱动报的显示尺寸（1440×3200），所以 1206×2622 不是驱动给的。
- 查过并排除的：偏好文件（`com.apple.backboardd.plist`、`com.apple.springboard.plist` 无相关变化；`com.apple.UIKit.plist`
  只有字号类别 XL→M）、`CAEnableDisplayReferenceMode`、适配库和图形桥里的尺寸逻辑（都没有）、QuartzCore / BackBoardServices /
  ProgressUI 里的常量 1206、2622（都没有）。
- 没查完的线索：MobileGestalt。v244 的日志里每个进程开头都有 `invalid product id '(null)'`、
  `Could not open …/systemgroup.com.apple.mobilegestaltcache/Library/Caches/com.apple.MobileGestalt.plist`。
  这个缓存目录不在快照里，v239 那时的状态无从比较。

## 3. v245 对毛刺做的事：一个实验

新库 `libpong-display-v245.dylib`（85,824 字节，只依赖 libSystem 和 libobjc，名字与 Apple 自己的 backboardd 相同）只注入 backboardd。
它替换 `-[CADisplay bounds]`：前几次调用把显示器自己的说法（原生尺寸、缩放、当前模式）报到控制台
（`PONG PROBE backboardd[pid]: Pong Probe: display v245: call N of -bounds …`）；
如果一个显示器报的是 {1206, 2622} 而它的原生尺寸正是面板 {1440, 3200}，就回答 {0, 0, 1440, 3200}——v239 的尺寸。
其他显示器、其他尺寸原样返回。启动参数 `pong_ca_native=0` 可以关掉，但启动参数已满，改不了。

- Mac 上验证过的（`prepare/display/hosttest/run.sh`）：名为 backboardd 时，面板上的 {1206, 2622} 被答成 1440×3200，
  原生尺寸不是面板时保持 1206×2622；名为 Other 时方法仍是 QuartzCore 自己的。
- **手机上没跑过**。可能的结果：回到原生（最好）；或者窗口服务器按 1440×3200 出帧、场景仍是 1206×2622 被图形处理器线性放大（毛刺也没了）；
  或者没变化。
- 自保：如果渲染进程因为它连续回来，第四次启动起不再注入（`THOR DISPLAY V245: not armed: the render server is back for a fourth start`），
  桌面按 v244 的方式出来。最多黑屏三次重启的时间。

## 4. 壁纸：把 UI 适配库也放进 PosterBoard

适配库（`/private/var/pong/libpong-ui-v158-frame-cellular.dylib`，与 SpringBoard 等进程里的是同一个文件）的构造函数里
有 PosterBoard 的专门分支：`poster_image`（数据目录、数据库初始化、文件保护属性）、`poster_ci_image`、致命信号记录
（`/private/var/mobile/Library/Logs/Pong-Poster18.log`）。目录 `/private/var/mobile/Library/Pong/PosterBoard/{Shared,Legacy}`
由它自己以 mobile 身份创建。Thor 的启动参数表满了，一直没有 PosterBoard；v245 由内核表在适配库落到 Data 分区之后注入
（`THOR UI runtime V245: PosterBoard adapter armed`）。这是上游的做法，**在 Thor 的手机上没跑过**。
同样的自保：PosterBoard 第四次启动起不再注入。

日志里要找：`PONG PROBE PosterBoard[…]: Pong Probe: log v245 PosterBoard: Pong Poster18: native DataStore fallback uses writable …`、
`… initialize absent database with native schema …`、`… native database initialization failed: …`。

壁纸目录（`~ipad` 的默认壁纸）是另一个问题：`pong_wallpaper_catalog_override=1` 没有设，启动参数里也放不下。

## 5. 崩溃诊断

`libpong-logcap-v245.dylib`（87,600 字节，libSystem）在 v244 的七个进程里，多三样：

- 无边记、kbd 的致命信号：信号号、代码、地址，出错线程的 pc / lr / sp / fp，共享缓存的偏移，以及上面的栈帧记录
  （`PONG PROBE kbd[…]: Pong Probe: log v245 kbd: crash: fatal signal 11 code 2 at 0x…`、`crash: pc …`、`crash: frames …`），
  然后才是最后 40 行。处理函数跑在自己的栈上，栈溢出也能报。
- backboardd：从第一行起 10 秒内每一行都传（最多 2600 行），窗口服务器启动时的日志不再被关键字截掉。
- 图形桥在无边记、kbd 里的 "Pong Metal: …" 行进控制台（原来只收设置和渲染进程的）。探针通道总预算 5000 → 12000 行。

Mac 对照测试（`prepare/logcap/hosttest/run245.sh`）：SpringBoard 4 行；backboardd 4 行；Freeform 14 行、状态 134；
kbd 6 行（`fatal signal 11 code 2 at 0x123456789`）、状态 139；Other 什么都没有。

## 6. 键盘和无边记：一个共同的根因方向（这一版没修）

这个移植用的是 **iPhone 16 Pro 的身份**（设备树 `/product`），系统镜像却是 **iPad 的**。iPad 固件的
`TextInputUI.framework` 里只有 `KBLayouts_iPad.dat`，没有 iPhone 的键盘布局 `KBLayouts_iPhone.dat`。
UIKit 按 iPhone 身份去找 `iPhone-PortraitChoco-Pinyin-Simplified`，找不到，返回 (null)，kbd 随后死掉。
上游（`UniXNU-pong/migration/ipados18/services112`）的解法：从同版本（18.7.10 / 22H374）的 iPhone 固件里取
`KBLayouts_iPhone.dat` 等手机资源补进系统镜像（`userland/phone_components18.py`）。这需要下载约 7 GB 的 iPhone 固件
（这台 Mac 现在只剩 9 GB 空间）并改系统镜像，是单独的一轮。无边记的空 Metal 函数可能也是同一类（按 iPhone 身份找资源），等 v245 的栈确认。

## 7. Wi‑Fi

- 地址：`device_get_mac_address` 一直返回"没有"，ath11k 就用固件准备好的默认地址，每一台跑这个移植的设备都一样。
  v245 的板级驱动从设备树 `/chosen` 的 `unique-chip-id`（引导程序写入的 SoC 唯一 ID）推出一个本地管理的地址，每次开机相同
  （`PongWifi[6]: wifi-net: en0 is up, hardware address …` 会变）。
- 关联请求：补上 Power Capability、Supported Channels、Extended Capabilities、WMM 四个元素；短时隙和短前导只在 AP 也用的时候才声明。
- 被拒时把关联应答、我们的请求、AP 信标里的元素都按十六进制打到内核日志。
- 48 这个状态码在标准表里是 "Direct link not allowed"，对一个普通开放网络说不通；这个 AP（"<network>" 是有十几个 AP 的办公网络）
  为什么拒，要等 v245 的 dump。

## 8. 这一版没有修的

键盘、无边记（见 §6）、掉帧的另一半（图形处理器等待和缓存维护，v236 起没动）、蜂窝、通话、声音。
