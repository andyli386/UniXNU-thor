# Thor v233 / v234：定格就是内核 panic，v234 让内核自己记录现场

日期：2026-11-25。v233 于 15:16 临时启动（`fastboot boot`，未刷写），用户用了约 100 分钟后内核崩溃（照片两张），之后手机进过一次旧系统（boot_a 上的 v202 B），18:01 用 TWRP 只读取出全部日志（`thor-out/recovery-v233-full-20261125T100132Z`，1.4 GB，无写入）。v234 于 19:00 临时启动。

## 1. 照片说明了什么

panic 屏（v233 新增的 "KERNEL PANIC LOG"）第 1、2 页可读出：

```
panic(cpu 7 caller 0xfffffff00a377010): Kernel Instruction fetch abort at pc 0xfffffff5529ebd30, lr: 0xfffffff55297e830
x0: 0xfffffff777c04ea00  x1: 0xfffffff009c96a20  x2: 0xfffffff007970f04  x4: 0xfffffff0b0c31339c
x20: 0xfffffff55297e830   x22: 0xfffffff552980830  fp: 0x0000000000890ec5b
Panicked task: 0xfffffff9949a947c8: 22019 pages, 33 threads: pid 41: backboardd
Panicked thread: 0xfffffff777c04ea00, tid: 30825
Kernel text base: 0xfffffff007004000     mach_absolute_time: 0x34f7e6b2b2
Kernel UUID: 931CE35E-5278-3CF2-ADAC-EC0D5B18CC49 (v233)
```

- 内核在 EL1 取指令失败：CPU 跳到了 `0xfffffff5529ebd30`。这个地址不在内核代码段（`0xfffffff009a74000..0xfffffff00a3d4000`），落在 compressor 区（结束于 `0xfffffff5485b8000`）和 zones 区（起于 `0xfffffff562a5e000`）之间，也就是普通内核映射里的一块数据。
- `lr = 0xfffffff55297e830`、`x20`、`x22`（相差 0x2000）是同一块映射里的三个相邻地址：线程在调用一个**指向数据区的坏函数指针**。
- 出事的线程是 backboardd（pid 41）的 tid 30825，在 CPU 7 上。
- panic 时刻：`0x34f7e6b2b2` 是 19.2 MHz 的计数器，等于内核时钟 11848.614 s。
- panic 日志里打印的回溯不可信：按 v233 内核核对，那些地址落在无关函数的中间（例如 `tcp_newtcpcb+0x278` 处是一条 `b` 指令）。线程跑飞之后栈上的帧指针链已经断了，真正调用者的地址只能从线程内核栈里逐字扫描得到。

所以**一周的"画面定格"（至少这一次）是内核 panic**。v231、v232 是否同一种 panic 无法确认：那时 panic 文字不显示，日志也在 panic 瞬间停止。

## 2. v233 这次启动（内核控制台镜像，5814.3 → 11848.7 s，13,708 行）

内核控制台镜像线程按设计工作：从缓冲区里最旧的一行开始，每 250 ms 落盘，直到 panic 之后约 130 ms（11848.748 s）。用户态的控制台抓取（v232 引入的 durable 版本）这次也完整保留到了 11848.808 s。本次启动的会话 UUID 是 14C83D22。

| 时间（内核秒） | 事件 |
| --- | --- |
| 3.4 | `THOR WITNESS V233: monitor started`、`console mirror thread started`、`console mirror … open` |
| 15.0 → 11820 | 正常使用：6 万多帧、多次应用切换、Wi‑Fi 扫描等，全程没有触发卡死报告（用户态一直在读控制台，帧也一直在出） |
| 11827.9 | 用户打开设置（Preferences，pid 2191），此后一直在前台 |
| 11838.9 | 用户按电源键：显示关 |
| 11839.6 | 电源键：显示开 → backboardd 重新导入 3 块 handoff 表面、`layers RESTORED` |
| 11839.7 → 11843 | 两次触摸（一次拖动到 x=34476、一次左边缘点击） |
| 11846.18 / .52 / .60 / .99 | 4 秒内显示关、开、关、开各一次 |
| 11847.04 → 11847.06 | 再次导入 3 块 handoff 表面（`PONG GPU LARGE IMPORT` ×3） |
| 11847.28 → 11848.71 | 两次左边缘手势 |
| 11848.378 / .403 | `PONG GPU MEM: free backboardd … bytes=4096 spans=1 … drain_us≈6400 passes=6/7` |
| 11848.614 | **panic** |

即：用户停在设置界面，按电源键关屏再开屏，4 秒内又快速关、开、关、开各一次，然后两次从左边缘向右滑；第二次滑动进行中内核崩溃（滑动在 11848.71 s 抬手，崩溃在 11848.61 s）。最后两步内核操作是 backboardd 释放两块 4 KiB 的 GPU 缓冲（每次等了约 6 ms 的 drain）。v232 那次定格前没有开关屏，所以触发条件不止一个；共同点是都发生在 backboardd 的图形路径上。

统一下载的启动参数里没有让 panic 更容易复现的因素，也没有新的线索指向 notifyd 或存储。

## 3. 照片回答不了的问题，以及 v234 怎么回答

| 问题 | v234 的做法 |
| --- | --- |
| 谁调用了坏指针 | panic 时逐字扫描出事线程的整个内核栈，打印每一个落在内核代码段的地址，并用内核自己的符号表和函数起始表解析成 `函数+偏移`（`THOR WITNESS V234 PANIC EXTRAS`） |
| 出事地址里是什么 | 打印 pc / lr 是否有映射（`kvtophys`）和物理地址；lr 前后 128 字节的十六进制 |
| 线程当时在用户态做什么 | 打印该线程的用户态 pc / lr / sp / fp / x0–x3 / x16 |
| 显卡驱动刚才做了什么 | 内核启动时打开 GPU 驱动的 trace 环（不用启动参数），panic 时写入最后 72 条提交 / 完成 / 导入记录 |
| 照片拍不全、要人在场 | panic 日志和以上附加信息写入 `/private/var/logs/PongPanic-v234-N.bin`：一块 256 KiB 的预分配文件，启动时取好每个 4 KiB 块的设备偏移，并把 64 块全部经 UFS 驱动读回核对；panic 时直接按块写入（WRITE(10) FUA，再 SYNCHRONIZE CACHE），不经过文件系统 |
| 卡死（非 panic）时栈里是什么 | 卡死报告里的内核栈现在也带符号名 |

细节：

- panic 记录槽由内核控制台镜像线程在启动约 4 秒时准备。先找第一个没有记录的 `PongPanic-v234-N.bin`（N = 0…11，有记录的不再使用，所以多次 panic 会各自保留），写入 256 KiB（每块带"槽号 + 块号 + 本次启动的随机数"标记），fsync 两次，用 `VNOP_BLOCKMAP` 取块映射，把 64 块经 UFS 驱动原始读回逐字节比较，再对最后一块做"改 1 字节 → 读回 → 还原"验证写路径。只有全部通过，panic 时才写。
- panic 时的写入不取锁（其它 CPU 已停住），只在 UFS 控制器空闲（doorbell 为 0）时进行；如果 panic 正好发生在存储驱动里，屏幕上会显示 `NOT stored`，附加信息照样显示。
- 屏幕顺序：先 panic 日志，然后附加信息，每页 15 秒循环。
- 内核符号表：启动镜像（strip 过的 `kernel-ipados18-thor.macho`）自带 81,941 个符号和 49,969 个函数起点。主机上用同一份代码对同一镜像验证过：`_thor_witness_read+18`、`_tcp_newtcpcb+278`、`_panic_init+84`、`_sleh_synchronous+370`，代码段外的地址标为 outside the kernel text（`thor-out/v234-intake/symbol-test/`）。

**注意符号化必须用同一个版本内核的符号表**：各版本代码地址不同，v233 的地址拿 v234 的内核去查会得到无关的名字（例如 `tcp_newtcpcb` 被标成 `panic_display_kernel_aslr`）。

镜像：`thor-out/v234-unified/boot-thor-unixnu-v234-unified.img`，95,088,640 字节，SHA-256 `9700c65ed7b221ec89631a1b5c0e1de8f93f15da1ab787bccb4c5398a678330`，内核 UUID `75FA9433-2D74-33F0-866D-C9026142E68B`。驱动行为、设备树和启动参数（1005 字节）与 v233 相同；新增的只有取证代码。源码差异记录在 `v234-unified/prepare/witness.patch`（相对 v233，可逐字节复现）。启动栈预算：`v234-intake/boot-stack-budget.txt`（6451 项深度 10，kalloc 视图 1164 项深度 10，最坏 0x0a10/0x4000）。**以上都还没有上机结果。**

## 4. 旧系统启动过一次的影响：没有丢失

| 文件 | 结果 |
| --- | --- |
| `PongConsole-kernel-v233.txt`（内核控制台镜像） | 未受影响：旧系统不认识这个文件，13,708 行完整 |
| `PongConsole-v172.previous.txt`（用户态控制台，v233 的 731 → 10658 s） | 保留 |
| `PongConsole-v172.txt` | 前 494 KB 是 v233 的最后一段（10659.4 → 11848.808 s，3002 行，完整）；后面接旧系统那次启动（6A96541B，10 → 327 s）追加的内容 |
| `PongConsole.txt`、`.previous.txt`（主控制台） | 多个启动追加，v233 的内容还在 |
| runningboardd / SpringBoard / backboardd 的手势日志 | v233（14C83D22）的记录都在：4230–4268、2220–2347、2278–2405 |

v234 的 panic 记录文件旧系统同样不会动。

## 5. 上机后看什么

1. 正常使用，照上次的方式操作（开关屏、滑动、切换应用），直到再次定格。
2. 定格后屏幕会先显示 panic 日志、再显示附加信息，每页 15 秒。可以拍，但不是必须的——记录已自动写盘（屏幕上有一行 `panic record STORED (blocks written: N)`）。
3. 音量下 + 电源回 fastboot。等待脚本会自动只读取日志（`v234-intake/auto-collect.sh`）；启动后内核日志里应出现 `THOR WITNESS V234: panic record slot … proven`，如果出现 `no panic record slot` 说明存储通道没通过验证，需要告诉我。
4. 解码：`session-tools/panic_record.py <PongPanic-v234-N.bin>`。

如果这次没有 panic 而是再次"卡住但内核还活着"，屏幕顶部会出现 `HANG n: …` 横幅（3 秒内），报告同样会写进内核控制台镜像。

## 6. 取日志流程的修正

17:51 等待脚本在手机进 fastboot 9 秒后上传 TWRP，USB 在传输中再次断开（主机 USB 日志：此后 4.5 分钟里每 0.5–3 秒一次 "hardware connection lost"），上传失败后手机停在"等待下载"状态，对所有命令不应答。已处理：`fbprobe fill` 补全下载后恢复；`v234-intake/collect-full.sh` 改为最多等 8 分钟、每次不应答就补全下载、上传限时。

工具：`session-tools/fbprobe`（libusb 直接操作 fastboot 设备：`info`、`cmd`、`fill`）、`session-tools/fstarts.py` / `symat.py`（函数边界 + 符号）、`session-tools/disrange.py`（按地址范围反汇编）、`session-tools/panic_record.py`。
