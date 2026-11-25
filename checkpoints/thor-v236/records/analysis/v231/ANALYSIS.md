# Thor v231 — 开机崩溃的根因、修复与上机后要看的日志

日期：2026-11-25 ｜ 基线：v230（开机即崩）
镜像：`thor-out/v231-unified/boot-thor-unixnu-v231-unified.img`（95,068,160 字节）
SHA‑256：`777d6ce0c1d48e7d18d63e9f8c2721c6094f1e4c54454d53a11004450679826d` ｜ 内核 UUID：`ABA5CC86-76F0-376A-935E-45D6437167BC`

11:17 手机启动的是第一次打包（保留为 `boot-thor-unixnu-v231-unified.img.1117-first-pack`，SHA‑256 `6ec48b6e…5cb7c18`）。
两次打包的内核、启动参数、设备树、菜单逐字节相同，只差清单里两句说明里的数字。

## 一句话

v230 的 panic 是**启动栈溢出**：内核在 16 KiB 的启动栈上用 `qsort` 给 kalloc 类型视图排序，
v229 的 Wi‑Fi 驱动多带进来 6 个视图，递归从 8 层变成 10 层，而每层占 0x600 字节，
第 10 层离栈底只剩 16 字节，调用比较函数时写进了栈下的守护页。
v231 把 `qsort` 每层的占用从 0x600 降到 0x90 字节，同时把 v229 误删的两个内存启动参数加了回来。

详细证据与照片逐项对照：`thor-out/panic-20261125/README.md`。

## v231 相对 v230 改了什么

| 项目 | 做了什么 | 为什么 |
|---|---|---|
| `bsd/kern/qsort.c` | `swapfunc` 不再内联进 `qsort`，改成不优化（不向量化）的独立叶子函数。`qsort` 每层栈帧 0x600 → 0x90，`swapfunc` 自身 0x80 | 启动时有两处 `qsort` 跑在 16 KiB 的启动栈上：启动函数表排序（10 层）和 kalloc 类型视图排序（v230 也是 10 层）。排序结果不变（主机上 9600 组随机/重复数据，新旧实现输出逐字节相同） |
| 启动参数 | 加回 `tmlow=1`、`tmfull=1`（放在 `fb_filter=0` 之后，和 v227 的顺序一致），现 1017/1023 字节 | 这两个是刷在手机里的固件加载器读的（`DarwinBoot.c:987/1066`），不是内核读的。`tmfull=1` 让加载器把私有高位内存（3072+5632 MiB 及尾部 510 MiB）交给 XNU；`tmlow=1` 交出一段 15 MiB 的低地址内存，一半留给原生扫描输出缓冲，尾部给基带 DMA |
| 设备树、固件、签名、运行库 | 与 v230 逐字节相同 | — |

驱动代码与 v230 相同：三个驱动归档（ArmCpu、ArmDTPlatform、NativeUfs）每个版本都会重新生成，
逐节比较后 `__text`/`__const`/`__cstring`/`__data` 全部一致。整个构建里只有 `qsort.o` 的内容变了
（`kern_exec.o`、`KernelConfigTables.cpo` 每次构建都会因路径而重编，属常态）。

## 启动栈预算（主机计算）

| 版本 | 启动函数表排序 | kalloc 类型视图排序 |
|---|---|---|
| v227 / v228 | 10 层，0x3ef0 / 0x4000，余 272 字节 | 8 层，0x3410，余 3056 字节 |
| v229 / v230 | 10 层，0x3ef0，余 272 字节 | **10 层，0x4010，差 16 字节** |
| v231 | 10 层，0x08f0，余 14096 字节 | 10 层，0x0a10，余 13808 字节 |

- 层数：把内核和 19 个预链接 kext 的视图/启动表按 `kalloc_type_view_parse()` 的顺序取出来，用与 `bsd/kern/qsort.c` 逐句对应的 Python 重放得到（`session-tools/kalloc_orderings.py`、`startup_sort_sim.py`）。
- "余 272 字节"说明启动函数表排序此前也一直贴着上限：以后任何改动启动表顺序（新增 `STARTUP()`）的版本都该先跑一遍预算。
- 以后每版打包前跑：`python3 -I session-tools/startup_sort_sim.py session-tools <内核>`、`kalloc_orderings.py`，再 `boot_stack_budget.py <内核> <层数> <层数>`。

## 主机验证了什么

- 新内核里 `_qsort` 的开头是 `sub sp, sp, #0x90`，`_swapfunc` 是独立函数；打包脚本会读镜像里 `_qsort` 的前 64 字节确认帧 ≤ 0x100（旧内核同一位置算出来是 0x600）。
- 镜像校验（`package-v231.py verify`）：与清单一致；FAT 文件系统 `fsck_msdos -n` 通过；相对 v230 只有内核、清单、菜单三个成员变了；6 个嵌入运行库各出现一次且哈希与父版本相同。
- 旧 `qsort.c` 与新 `qsort.c` 各编成主机程序，对 16 种元素大小 × 5 种数据形态（随机、升序、降序、organ‑pipe、大量重复）共 9600 组输入，结果哈希相同、无乱序。

**没有验证的：v231 在手机上的实际启动。** 下面的内容都要等一次开机。

## 11:16–11:17 手机上自动做了什么

你把手机接进 FASTBOOT 时，等待进程正好已就绪，于是自动走完一遍（`v231-intake/auto-capture.log`）：

1. 内存启动 TWRP（没有刷写）。
2. 抓上一次的日志：`recovery-v227-history-20261125T031651Z.3Fi7AK`——仍是 v227（11‑24 20:04 那次）的记录，v230 在 17 秒时就崩了，没有写下任何日志。
3. 抓原厂文件（只读，84 MB，`capture-stock-20261125T031650Z`）：`/vendor/firmware` 全树 326 个文件，其中有 **CS35L41 功放固件** `cs35l41-dsp1-spk-cali.wmfw`、`cs35l41-dsp1-spk-prot.wmfw` 和 4 个 `B-/T-cs35l41-dsp1-spk-cali/prot.bin` 配置；`/vendor/etc/acdbdata`；`/vendor/etc/audio/sku_cape/…`；`/vendor/etc/wifi`。userdata 的写入/丢弃计数抓取前后都是 0。
4. 11:17:25 内存启动 v231。

没抓到的：蓝牙固件分区在这台机器上叫 `bluetooth_a`/`bluetooth_b`（不是 `bt_firmware`），等待脚本没认出来。脚本已改，下一次会抓。

## 上机后看什么

手机现在不在 FASTBOOT 也不在 adb 里：要么 v231 已经进系统，要么停在一个新的 panic 画面。

| 现象 | 含义 | 下一步 |
|---|---|---|
| 进桌面 | 栈溢出已解决 | 按下面清单看功能 |
| 又停在 `VM bootstrap done` 之后 | 模型有遗漏 | 再拍一张照片；重点看 `at pc`、`lr` 两行 |
| 走得更远后崩 / 卡住 | 是 v228–v230 的某项新功能，首次上机才暴露 | 用 `/chosen` 开关逐项关掉重打包定位，先怀疑 `thor-wifi=2` |

进系统后请照常试：壁纸、电源键熄屏/亮屏、Wi‑Fi 扫描和加入网络、状态栏电量、滑动和动画帧率。
之后**按住音量下 + 电源键直接进 FASTBOOT**（不要正常重启，否则会进刷在 boot_a 里的 v202）。
Mac 上另有一个只抓日志的等待进程：手机一进 FASTBOOT 就内存启动 TWRP、抓这次的日志、再回到 FASTBOOT 停住，不再启动任何镜像。

抓回日志后对照：

- 内存：`Pong RAM: 7 owned banks`、`bank 3 0xfdda4000`、`bank 4 0x840000000`、`bank 5 0x900000000`、`bank 6 0xa60200000`；`DB24 MEM: accepted by XNU 9997 MiB, 6 descriptors`；`vm_page_bootstrap: 2490641 free pages`。少了哪一行，就是两个启动参数没生效。
- 基带：`THOR MODEM V161: dma=reserved source=owned-bank-tail`（v230 那次这里应当是没有低地址内存可用）。
- Wi‑Fi：`PongWifi: Thor mode=2`、`THOR WLAN V229: firmware file=`、扫描到的网络。
- 蓝牙：`THOR BT V230: RUNNING mode=2` 之后是否有 ROM 版本号和 board id。
- 音频：`THOR AUDIO V230` 是否记录到 ADSP 打开 `adsp_apps`。
- CPU：`THOR CPU V228: governor=load ceiling=request-only`，以及满载时各簇频率是否不再卡在 633.6 / 921.6 MHz。

## 更正以前的记录

- v229 的记录把 `tmlow=1`、`tmfull=1`、`pong_gpu_prime=0` 记为"无人读取"。前两个错：它们由刷在手机里的固件加载器读取。以后判断一个启动参数是否有人读，要同时查内核、运行库和 `thor-uefi-publish-yjbdCAUv/` 下的加载器源码。`pong_gpu_prime=0` 确实无人读（只有上游 PongMetal 的 `PrimeShaders.h` 读，Thor 内核不含它）。
- **v229 和 v230 都不能开机**（同一组视图、同一深度 10）。v228 按模型可以开机（深度 8、参数齐全），但没有上过机。
- 这类 panic（pc 落在异常向量里、`Invalid kernel stack pointer`）真正有用的寄存器是 lr、x8、fp，pc 永远指向 `el1_sp0_synchronous_vector_long` 里压异常帧的那条指令：向量和中断处理跑在被中断的那个栈上，栈一满，压帧本身就会再触发一次异常。

## 风险

- 这是 v228 的 CPU 调频/ADSP 电源轨/基带服务、v229 的 Wi‑Fi 驱动、v230 的蓝牙探测和音频监听**第一次上机**。栈和内存两个问题解决后，它们各自的表现仍未知。
- Wi‑Fi 驱动（ath11k + MHI）编进了内核，首次探测 PCIe 和下载固件；`thor-wifi=1`（只建链路）或去掉该属性可以在不重编内核的情况下缩小范围。
- `qsort` 的交换路径现在是不优化的标量循环，比向量化的慢。启动阶段 kalloc 排序约 1.6 万次比较，开销可忽略；运行期还有十来处调用它（fbt、btlog、端口等），排序的数据量都小，没有单独测过耗时。

## 文件

- `v231-unified/`：配方。`build-tree/.cache/xnu-work/bsd/kern/qsort.c` 是唯一改过的内核源码；`package-v231.py` 打包并校验。
- `v231-intake/auto-capture.sh`：等待进程（抓日志 + 抓原厂固件 + 启动 v231），已修蓝牙分区名。`auto-collect.sh`：只抓日志后停在 FASTBOOT。
- `session-tools/`：分析工具（见 `panic-20261125/README.md`）。
