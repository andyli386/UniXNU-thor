# v234 的结果与 v235

2026-11-25 晚。v234 在 19:00 临时启动，19:06 前后 panic；v235 在 20:28 临时启动，取日志等待脚本
`v235-intake/auto-collect.sh` 已就位（手机一进 fastboot 就只读取日志，再停在 fastboot）。

## 1. v234 那次启动发生了什么（证据：`recovery-v234-full-20261125T110646Z`，11,889 个文件）

| 内核时间 | 事件 |
| --- | --- |
| 3340.95 | `THOR WITNESS V234: panic record slot 0 block 0 does not match its file (error 5)` → `no panic record slot`：记录通道在这次启动里从未可用 |
| 3446.7 | 设置（pid 309）启动；3450.70 主线程进入 `PSGAboutDataSource._loadValues`（"关于本机"面板取值），此后再没有返回 |
| 3466.1 / 3482.6 / 3502.9 | 三个设置实例（309、324、331）被 runningboardd 要求退出，5 秒内退不出来，以 `0x8BADF00D` 杀掉（`CrashReporter/Preferences-*.000..003.ips`，各带一份全系统 stackshot） |
| 3503 | 第四个设置实例（338）启动，用户进了"通用"又退出（nav 5–8） |
| 3568.23 / 3568.47 | 设置退到后台，"天气"（373）成为前台应用；3568.61 backboardd 登记它的全屏表面 |
| 3570.09 | `PONG GPU LARGE IMPORT: pid=41 handle=2016 host=0x11d41e000 …` |
| 3570.41 | 屏幕底部中间一次上滑（home 手势区域）抬起 |
| 3570.63 | 最后一条用户态日志写出；panic 在此之后 |

panic 发生在启动后 230 秒，即用户说的"点桌面一个应用后崩溃"。

## 2. 两条结论

### 2.1 v234 没有留下记录：我的 bug，不是手机的问题

panic 记录要写进 `/private/var/logs/PongPanic-v234-0.bin` 的块里。内核用"窗口起点 + 物理块号 × 4096"去读这些块，读回来不是文件内容，
于是校验失败、不写任何东西。手机在 TWRP 里时做了只读核对（`session-tools/apfs_extents.py`，经 adb 原始读 sda36，前后核对无写入）：

- slot 文件是一段连续的 64 块，APFS 物理块 `0x1918c`；
- "APFS 容器起点 + 块号 × 4096"处 **64 块全部与文件一致**；
- "窗口起点 + 块号 × 4096"处（v234 用的）是零。

差别就是 1 MiB：Data 窗口里有一个 GPT，唯一的 APFS 分区从窗口内 1 MiB 处开始
（`0x284600000` = `0x284500000` + `0x100000`），而 `VNOP_BLOCKMAP` 的块号从分区（容器设备）起算。v234 少加了这 1 MiB。

第二个 bug 在面板附加现场里：显卡跟踪的记录被读进一个只有 24 字节的对象，按 19 KiB 写出去，
冲掉了 `trace_header` 之后的内核数据（`_lck_grp_table_array0` 尾部、`_pset_node1`、调度器全局量等）。
这只发生在 panic 之后（手机当时已经停了），但跟踪记录本身全打成零。按 v234 内核的符号表核对，
照片里的函数名是可靠的（`0xfffffff009c6ff00` 正是 `_pmap_enter_options_internal+160`）。

### 2.2 照片那一页说明不了调用者

照片是附加现场的第 4/5 页：线程内核栈上所有"看起来像内核代码地址"的字，按地址从高到低。
其中绝大多数是同一个栈上早先调用留下的旧值（XML 解析、`OSDictionary::setObject`、页表回写……），
只有最底下（最近）的那些才是活的；而最底下那部分在第 5 页，没有拍。
第 4 页最后一条是 `+0x06c0: _pmap_enter_options_internal+160`——这是 `pmap_enter_options_internal`
调 `lck_rw_lock_shared_b` 之后的返回地址，但 `pmap_enter_options_internal` 自己的帧在它下面，
它的调用者在第 5 页。所以**这次 panic 的类型、出事线程、真正的调用链目前都未知**。

## 3. "设置 → 通用 → 关于本机点不开、设置卡死"

这是另一个问题，且每次启动都复现（v234 里连续 3 次）。证据链：

- 端口自带、注入在设置进程里的诊断（`PONG SETTINGS[309]`）记录：`_loadValues` 开始后 1.5 秒超时，
  主线程停在一个 unfair lock 上；
- 同时刻的全系统 stackshot（在 watchdog 报告里）：主线程 `_ulock_wait_continue`，等线程 3779 的锁；
  3779（队列 `com.apple.SettingsApp.PrimaryListLoading`）拿着锁在 `_ipc_mqueue_receive_continue`，
  即一次**同步 XPC，对端永远不回**；
- 内核控制台在那一瞬间没有任何守护进程报错；也没有守护进程被挂起。

对端是哪个服务还不知道：两边的用户态栈只有地址，而 dyld 共享缓存在 SystemCryptex 卷里，
那个卷在盘上是加密的（对象映射标志全是 8，OS 卷是 0；Mac 上的源镜像 `ipsw-components/components/094-33089-038.dmg`
读出来完全一样，所以不是手机数据坏了，是只读读取器没有密钥）。
下一步最省事的是让设置诊断库在进程内用 `dladdr` 把两条栈的镜像名解析出来（需要重新编译那个注入库）。

顺带看到的、与这次 panic 没有直接关系的现象：

- 5 个"退不出"的进程都是崩溃尸体（`fitcored[189]`、`MessagesBlastDoorService[251/285/298/302]`，
  "Corpse allowed N of 5"），即已知的 BlastDoor 沙盒问题，尸体没有被回收；
- `fairplayd` 每 10 秒退出一次（`PONG EXIT fairplayd.A2`，signal 0），持续整个启动；
- 19–20 个线程（backboardd 13 个，CommCenter、fseventsd、audiomxd、nearbyd、mediaplaybackd、mediaparserd 各 1–2 个）
  阻塞在 `0xfffffff00c4fe428`，在 `__PLK_TEXT_EXEC` 里，不在内核符号表内，未解析；多数是空闲等待，CommCenter 那个已 118 秒没运行。

## 4. v235 改了什么（驱动行为、设备树、启动参数都没变）

1. **slot 的起点**（`bsd/kern/kern_exec.c`、`device/drivers/NativeUfs/PongUfsTransport.cpp`）：
   `thor_ufs_container_of_bsd` 给出窗口起点加上文件系统设备以下各层 IOMedia 的 `Base`（即分区偏移）；
   裸窗口和卷自己的 base 作为备选；每个候选都要让 64 块全部读回一致才算数；
   都不行时在窗口里按每块自带的标记（slot 号、块号、每次启动的随机数）直接找这些块，上限是窗口大小和 30 秒。
   通过后对最后一块做"改一字节 → 原始读回 → 还原"验证写通道。控制台会打印每个候选的结果。
2. **显卡跟踪**：头部和记录放进同一个缓冲区。
3. **两条真正的回溯**：从出错代码的帧指针走的帧记录链（谁一路调用到出错处），以及在 `PE_panic_hook` 里
   读到的帧记录链（谁调用了 `panic()`）。栈上的内核代码字照旧列出，属于这两条链的会标 `*`。

主机上验证了：编译、链接、打包校验（`package-v235.py verify`）、启动栈预算（深度 10，余 13.8 KiB，
与 v234 相同）、补丁可从 v234 源码逐字节复现（`prepare/witness.patch`）。**写盘通道只能在手机上验证。**

镜像：`thor-out/v235-unified/boot-thor-unixnu-v235-unified.img`，SHA-256 `fc15cb8d…a2ff0368`，内核 UUID `65407879-1854-3E78-8CC5-461BFDEF956A`。

## 5. 拿到日志后先看什么

```
grep -a "THOR WITNESS V235" <snapshot>/Data/PongConsole-kernel-v235.txt
```

- `panic record slot /private/var/logs/PongPanic-v235-N.bin proven: 64 blocks … write-read-restore done` → 通道可用；
- `origin … differs: … the medium holds X where the file holds Y` → 哪个候选、差在哪；
- `the window was searched for the blocks: n of 64 found` → 走了兜底搜索。

有记录时：`session-tools/panic_record.py <snapshot>/Data/PongPanic-v235-N.bin --kernel v235-unified/kernel/mach.development.pong.unstripped`，
先看 `frame records from the fault` 和 `frame records at the panic call` 两段。
