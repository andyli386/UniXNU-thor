# v247：在 v246 之上多三样诊断

2026-11-28 00:10–00:26。

| 文件 | 字节 | SHA‑256 |
|---|---|---|
| `thor-out/v247-unified/boot-thor-unixnu-v247-unified.img` | 95490048 | `4092ce88ba0de6bbe6d2bf25ba64c29b69d6e9c7b7b9ab1494054eda9d082d84` |


内核 UUID `65515F4B-662A-3809-AC0D-B19B4A637B83`（父：v246 `ECDC7FAF-CE7B-3F57-B3E9-945A9A7BCE08`）。
记录：`v247-unified/unified-verification.json`。启动参数与 v239–v246 逐字节相同（1005 字节）。
**设备树比 v246 多一个属性**：`/chosen thor-camera = 1`。启动栈预算：深度 9 空闲 14240 字节，深度 10 空闲 13808 字节。

v246 做好后手机一直没回 fastboot，所以 **v246 从未被刷入**，由 v247 取代；v246 的全部内容都在 v247 里。

## 1. v246 的内容（原样带过来，见 `v246-intake/ANALYSIS.md`）

- 显示库：显示器没有自己尺寸的时候也按面板 1440×3200 回答（`libpong-display-v246.dylib`，未改动）。
- 日志库在 PosterBoard 里记录崩溃，并在其他库加载完后重新挂信号处理。
- Wi‑Fi：被拒后先换同一网络的 5 GHz BSS 重试，再以普通 802.11g 身份重试；每次被拒打出数据帧。
- 安装脚本在 TWRP 里只读扫描 persist 等分区找出厂网卡地址。

## 2. v247 新增的三样

| 改动 | 位置 | 作用 |
|---|---|---|
| **每个进程的死亡都记一行** | `kern_exit.c`，`proc_prepareexit` | 以前只有固定名单里的进程（设置、PosterBoard 等）退出时有记录；而每次开机 5 个崩溃报告名额头 80 秒就用完，所以键盘、无边记、“信息”的后台服务为什么死从来没被记下过。现在凡是死于会产生 core 的信号的进程都打印 `PONG DEATH <名字> pid= … signal= … exception= … code= … subcode= …（地址）namespace= … reason= … description= …`，每次开机最多 300 行 |
| **“信息”的后台服务进崩溃记录** | 日志库 v247（`libpong-logcap-v247.dylib`，87,632 字节）+ 内核注入表 | `MessagesBlastDoorService` 每 10 秒崩一次，打开“信息”时桌面就卡住（v245 那次“定格”）。现在它死前最后 40 行和信号、寄存器、栈帧都会送出来。按名字匹配 |
| **摄像头只读盘点** | `device/drivers/PongPower/PongCam*.h`（6 个头文件，来自 `v247-camera-staging`） | 根卷挂载 30 秒后，在自己的线程上读一遍：摄像头时钟控制器的状态、各电源域、4 个引脚的配置、I2C 控制器和采集模块的版本寄存器（仅在上电时才读）、前置摄像头三路供电在 SPMI 里的通道。**一个寄存器都不写**。只有设备树里 `thor-camera = 1` 时才映射、才起线程 |

### 摄像头盘点里用到的 Thor 前置摄像头接线（安卓设备树 `v226-intake/recovery-linux.dts`）

| 项目 | 值 | 出处 |
|---|---|---|
| I2C | CCI1（0xac16000）master 1，引脚 GPIO208/209（eGPIO） | dts 24932、24813、4682 |
| 主时钟 | MCLK4，19.2 MHz，GPIO104（"MCLK1" 只是小米表里的标签） | dts 24928、24937、4834 |
| 复位 | GPIO108 | dts 5536 |
| 供电 | PM8010J：`ldoj3` 1.8 V（vio）、`ldoj5` 2.8 V（vana）、`ldoj2` 1.104 V（vdig），走 RPMh | dts 6873、6909、6855 |
| 传感器型号和 I2C 地址 | **本地没有任何来源** | — |

这更正了 `v246-intake/research/CAMERA-22H374.md` 里的一处错误（那份报告把前置摄像头写成了 GPIO112/113 和 MCLK1；GPIO112/113 其实是广角摄像头的 I2C）。

## 3. 验证到什么程度

- **Mac 上**：日志库的对照测试 6 个进程全过（新增 MessagesBlastDoorService：6 行，状态 134）；显示库的测试不变；
  摄像头逻辑的 host 测试（`v247-camera-staging/hosttest/run.sh`，带 ASan/UBSan）通过，其中断言“级别 1 不发生任何写”；
  摄像头探针对着 v246 的内核头文件做过语法检查；镜像打包后逐项核对（内核字符串新旧、运行库字节、设备树确实带上了新属性）。
- **手机上**：都没有跑过。v246 的改动（显示、壁纸崩溃记录、Wi‑Fi 重试）和 v247 的三样都还是第一次上机。

## 4. 日志里要找的行

- `PONG DEATH …`：每一条都是一个进程的非正常死亡。重点看 `kbd`、`Freeform`、`MessagesBlastDoor`、`fitcored`、`biomed` 的。
- `PONG PROBE MessagesBlastDoorService[…]: Pong Probe: log v247 … before the crash: …` 和 `at the crash: …`。
- `THOR CAMERA V247: RUNNING mode=1 readonly=1`，然后 `inventory gcc …`、`pin=104|108|208|209 …`、`camcc …`、`cmd-db ldoj3= … ldoj5= … ldoj2= …`、`spmi ldojN slave=9 peripheral=… channel=…`，最后 `snapshot done camcc_read=… writes=0`。
  `channel=-1` 表示那路供电没有 SPMI 观察通道；三个都有通道，下一步“上电读芯片 ID”才能开。
- v246 的那些行：`Pong Probe: display v246: call N of -bounds … the panel's size is answered`、第一条交换面是不是 1440×3200、
  `PONG PROBE PosterBoard[…]: … before the crash: …`、`wifi: joining once more on a 5 GHz BSS …`。

## 5. 这一步没有做的

- 摄像头探针的级别 2（给前置摄像头上电、读芯片 ID）：需要有人守着，而且要先把级别 1 的结果看一下（供电通道是否都在）。
  它每次开机都会执行，因为属性是写在设备树里的，所以只放在专门的镜像里。
- 键盘：等磁盘空间（同版本 iPhone 固件约 7 GB，解包要 20 GB 左右）。
- 通话：先做基带（上游代码在，Thor 上要接进来），周级的工作；音频按月计。
