# 2026-11-25 v230 开机 panic：分析记录

照片：`photo/panic-photo.jpg`（与用户发来的原图逐字节相同，SHA‑256 `88e6df08…73bfd0`）。
现象：v230 开机，屏幕停在 `VM bootstrap done: 5 maps, 115 entries and 116 holes left` 之后，
紧接 `panic(cpu 0 caller 0xfffffff00a376b30): Invalid kernel stack pointer (probable corruption)`。

## 结论

**启动栈溢出。** 启动早期（`arm_init` → `kernel_bootstrap` → … → `kalloc_init`）CPU 0 跑在 16 KiB 的静态
中断栈 `intstack` 上（`INTSTACK_SIZE = 4*4096`）。`kalloc_type_view_init_fixed()` 用 `qsort()` 给
kalloc 类型视图排序，这个 `qsort` 每层递归占 0x600 字节（向量化的 `swapfunc` 被内联进递归帧）：

| 版本 | 递归层数 | 栈上合计（栈 0x4000） | 余量 |
|---|---|---|---|
| v227 / v228 | 8 | 0x3410 | 3056 字节 |
| v229 / v230 | **10** | **0x4010** | **−16 字节** |
| v231（qsort 每层 0x90） | 10 | 0x0a10 | 13808 字节 |

层数从 8 变 10 的原因：v229 编进内核的 Wi‑Fi 驱动多了 6 个 kalloc 类型视图
（`site.Pong80211UserClient`、`site.IONetworkStackUserClient`、`site.IO80211Controller`、
`site.IO80211Interface`、`site.IONetworkInterface`、`site.IONetworkStack`），排序时的左分区链随之改变。

第 10 层的 `qsort` 帧底在 `0xfffffff00a6f4010`，离栈底只剩 16 字节；它调用比较函数时，
`kalloc_type_cmp_fixed` 的 `stp x20, x19, [sp, #-0x20]!` 要写 32 字节，落进栈下的守护页。

## 照片与模型逐项对照

照片是 1080×1440 的翻拍，0/8、9/a、3/c、b/8 容易混淆；"核对值"一列来自 v230 内核反汇编与下面的推算。

| 字段 | 照片读数 | 核对值 | 含义 |
|---|---|---|---|
| panic 串 | Invalid kernel stack pointer (probable corruption) | 同 | 向量入口发现 SP_EL0 不在任何合法栈内 |
| caller | `0xfffffff00a376b30` | 同 | `panic_with_thread_kernel_state+568`（只在 v230 内核里对得上 → 崩的是 v230） |
| pc | 看着像 `…0a3c80d4` | `0xfffffff009c8b0d4` | 异常向量 `el1_sp0_synchronous_vector_long` 里的 `stp x0, x1, [sp, #0x8]`：回到 SP0、`sub sp, sp, #0x350` 压异常帧的第一条写 |
| lr | `0xfffffff00a0525a8` | 同 | `_qsort+0x16c`，插入排序里 `blr x8`（调比较函数）之后的返回地址 |
| x8 | `0xfffffff00a34c3f4` | 同 | `_kalloc_type_cmp_fixed`（全内核只有这一处把它装进 x8） |
| x19 / x28 | 0 / 0 | 同 | 插入排序第 0 轮、尚未交换 |
| fp | `…0a6f4680` | `0xfffffff00a6f4600` | 第 10 层 `qsort` 的帧指针 = 栈顶 − 0x3f0 − 0x10 − 9×0x600 |
| sp | `…0a6f3cc0` | `0xfffffff00a6f3cc0` | = 第 10 层帧底 `0xa6f4010` − 0x350（异常帧），**逐字节吻合** |
| far | `…0a6f3cc0` | `0xfffffff00a6f3cc8` | `[sp, #0x8]` |
| esr | `0x9600004f` | 同 | EL1 数据中止，写，三级权限错误（守护页） |
| saved state | `0xfffffff00a6ffc60` | 同 | `excepstack` 顶 − 0x3a0 |
| x20–x24, x26, x27 | `…925478090` | 同 | 第 10 层子数组基址（`kt_buffer` 内，3 个元素） |

## 经过（推算，与上表一致）

1. 第 10 层 `qsort`（子数组 3 个元素，插入排序）执行 `ldr x8, [sp, #0x88]; blr x8`，x8 = `kalloc_type_cmp_fixed`。
2. 比较函数第一条 `stp x20, x19, [sp, #-0x20]!` 要写 `0xa6f3ff0`、`0xa6f3ff8`（守护页）→ 数据中止；此时 SP_EL0 = `0xa6f4010` 仍合法。
3. 向量认为栈合法，于是切回 SP0 并在**同一个已经满的栈**上压 0x350 字节异常帧：`sub sp, sp, #0x350` → `stp x0, x1, [sp, #0x8]`（pc = `0xfffffff009c8b0d4`）再次写守护页。
4. 第二次异常时 SP_EL0 = `0xa6f3cc0` 已不合法 → `fleh_invalid_stack` → 本 panic。

注意：这种 panic 的 pc 永远落在异常向量里，真正出错的位置在 lr/x8/fp；直接按 pc 去查符号会查到无关函数。

## 第二个问题（同一张照片）

照片里 `vm_page_bootstrap: 1662854 free pages, 30924 wired pages`，而此前每次开机都是 2490641 / 68703；
`kmem_large_ranges[5]`、`compressor` 等范围大小也都不同。原因是 v229 为了给 `PosterBoard` 腾启动参数，
删掉了 `tmlow=1 tmfull=1`，并记为"无人读取"。实际上它们是**刷在手机里的固件加载器**读的
（`thor-uefi-publish-yjbdCAUv/active/DarwinBootPkg/Driver/DarwinBoot.c:987`（tmlow）、`:1066`（tmfull）；
同目录的 `ThorOwnedLowRam.h`、`ThorPrivateHighRam.h` 是两块内存的范围）：

- `tmfull=1`：把私有高位内存（`0x840000000`–`0xa60000000` 共 3072+5632 MiB，加尾部 510 MiB）交给 XNU；
- `tmlow=1`：把 DXE 堆的一段（`0xfdda4000` + 15 MiB）交给 XNU，其中一半留给原生扫描输出缓冲，尾部给基带 DMA。

v227 及以前所有能开机的版本都带这两个参数（`Pong RAM: bank 3 … bank 6`）。`pong_gpu_prime=0` 确实没人读
（只有上游 PongMetal 的 `PrimeShaders.h` 读它，Thor 内核不含该文件）。

## 文件

- `boot-stack-budget.txt`：四个版本的启动栈预算（`session-tools/boot_stack_budget.py`）。
- `img-small/`：v227、v230 镜像里的菜单（启动参数）、清单和设备树。
- `session-tools/`（上一级）：`kalloc_sort_sim.py`、`kalloc_orderings.py`、`startup_sort_sim.py` 重放排序深度；
  `boot_stack_budget.py` 算预算；`sym.py`、`dis.py`、`disat.py`、`frame_size.py` 看内核；`member_extract.py`、
  `adt_diff.py` 拆镜像和比对设备树。
