# v235 的结果、根因与 v236

2026-11-25 晚。v235 在 20:28 临时启动，约 20:33 panic，**这次记录写盘成功**，并且直接给出了根因。
v236 只改一处显卡驱动代码来修它。

## 1. v235 证明了两件事

1. **记录通道可用**。内核日志：
   `panic record slot 0: block numbers 17bde..17c1d, unit 4096, device 13,9, window 0x563f00000 + 0x200105000, media below the device start 0x100000 into it, own base 0`
   → `origin 0x564000000 … every block matches` → `proven: 64 blocks … write-read-restore done`。
   第一个候选（窗口起点 + 分区偏移 1 MiB）就是对的，没用到兜底搜索。
2. **屏幕显示 `panic record STORED (blocks written: 64)`**。记录在 `v235-intake/panic/`：
   `record-stock.txt`（XNU 的 panic 日志：寄存器、esr）、`record-extras.txt`（两条回溯、出错点前后的字节、栈上的代码字、显卡跟踪）。
   你拍的 6 张照片在 `v235-intake/panic-photos/`，内容与记录一致。

## 2. panic 现场

```
panic(cpu 4 caller 0xfffffff00a379c58): Kernel instruction fetch abort at pc 0xfffffff2c69ac880, lr 0xfffffff00a2cfef0
  x0: 0xfffffffbec782858   x1: 0xfffffff2c69ac880   x8: 0xfffffffbf519f5e0   x19: 0xfffffffbed1e2c88
  x26: 0xfffffffbed1e2830  x28: 0xfffffff00a4c3410  x30: 0xfffffff00a2cfef0
  pc: 0xfffffff2c69ac880   cpsr: 0x82400604   esr: 0x000000008600000f   far: 0xfffffff2c69ac880
Panicked task …: pid 41: backboardd        tid 4186，内核栈 fffffffbf5214000..fffffffbf5218000
```

- `esr 0x8600000f`：当前异常级（内核）取指中止，权限错误（跳去了一页不可执行的数据）；`cpsr` 的模式位是 EL1、SP0。
- `lr = NativeAdrenoSubmit::release+0x44`，紧跟在它开头的 `ldr x8,[x28]; cbz x8; blr x8` 之后，`x28` 正是全局函数指针 `native_adreno_vm_now_us` 的地址。
- 回溯（帧指针链）：`mach_msg2_trap → is_io_connect_method → IOUserClient::callExternalMethod → AGXDeviceUserClient::externalMethod → run → runMethod → runCore → NativeAdrenoSubmit::retireCompleted+f8 → release`。

## 3. 根因

出错时的寄存器并不等于 `blr` 那一刻的值：x8 不是跳转目标，x1 才是。这说明 `blr` 调进去的是一个合法函数，
它改了 x0/x1/x8 之后用**尾跳转**（不改 lr）去了垃圾地址。`native_adreno_vm_now_us` 指向的 lambda 正是这样
（v235 内核反汇编，`0xfffffff00a2d4e68`）：

```
adrp x8, … ; ldr x8, [x8, #0xc08]      ; x8 = vm_clock        （dispatch() 里的静态指针）
cbz  x8 → 返回 0
ldr  x1, [x8, #8]                       ; x1 = vm_clock->now_us
cbz  x1 → 返回 0
ldr  x0, [x8]                           ; x0 = vm_clock->cookie
br   x1
```

逐项对照：x8 = `0xfffffffbf519f5e0`、x1 = pc、x0 = cookie、lr 保持 `release+0x44`。

而这个 `vm_clock` 是怎么来的（`device/drivers/PongAdreno/submit/NativeAdrenoUserClient.cpp`，`dispatch()`）：

```cpp
static const NativeAdrenoRingClock *vm_clock=nullptr;
vm_clock=clock;                                                            // 存的是参数的地址
native_adreno_vm_now_us=[]()->uint64_t{return vm_clock && vm_clock->now_us?vm_clock->now_us(vm_clock->cookie):0;};
```

传给 `dispatch()` 的 `clock` 有两种：

- `&g.clock`（静态对象）——平时；
- `&traced_clock`（**调用线程内核栈上的局部变量**）——`NativeAdrenoPublish.cpp` 的等 fence 路径里，
  当 `tracing` 为真时：`desktopTiming`（backboardd 的桌面计时）或 `switcherTiming`（切换器动画期间），
  都在 `#if PONG_GUEST_22H374` 里，是 Thor 自己的追踪代码。

所以：某个线程做了一次带追踪的 fence 等待 → `vm_clock` 指向它的栈 → 它返回后指针悬空 →
之后任何线程调用 `native_adreno_vm_now_us()`（提交、VM、用户客户端共 24 处），都把那块栈当成 `{cookie, now_us}` 来读。
栈内容还没被覆盖时读到的仍是合法值，所以大部分时候没事；一旦被别的系统调用覆盖，读到非零垃圾就跳过去。

证据：

- 出错时 x8 = `0xfffffffbf519f5e0`，在**另一个线程**的内核栈上（出事线程的栈是 `…f5214000..f5218000`）。
- v235 控制台有 48 行 `[gpu waiter]`（只有追踪路径会打印），最后一次在 255.03 s（pid 41）；panic 在 271.75 s。
  中间 16 秒那块栈一直没被改写；最后 1 秒用户做了两次从屏幕底部上滑的手势（`edge_bits=8`），然后崩溃。
- v234 同样有 `[gpu waiter]`，panic 前是"打开天气 → 底部上滑"；v233 是"开关屏 → 边缘滑动中"。
- 整个驱动目录里"函数内静态指针、每次调用赋值"这种写法只此一处。

上游 UniXNU/Adreno740（11-25 主线）里这三行原样还在（`NativeAdrenoUserClient.cpp:565-567`），
但上游没有把栈上时钟传进去的调用点，所以这是 Thor 追踪代码引入的。

## 4. v236 改了什么

只改 `NativeAdrenoUserClient.cpp`：

1. `dispatch()` 把时钟的两个字（`cookie`、`now_us`）**拷贝**进两个静态变量，不再保留指针。
2. 钩子换成普通函数 `native_adreno_vm_now_hook`：读这两个字；目标若不在内核代码段范围内就不跳，返回 0 并计数
   （`native_adreno_vm_clock_refusals`）。
3. 第一次 `dispatch()` 时打一行日志，便于在控制台确认：
   `THOR GPU V236: the VM timing hook keeps a copy of the ring clock; …`

v235 的记录机制原样保留（标签和文件名仍是 V235：控制台镜像 `PongConsole-kernel-v235.txt`，
panic 记录写 `PongPanic-v235-N.bin`，下一个是 slot 1）。设备树、启动参数、嵌入的运行时库都没变。

主机上验证了：编译链接；反汇编确认 `dispatch` 的两条 `str` 和钩子的范围检查；打包校验；启动栈预算（深度 10，余 13.8 KiB，与 v235 相同）；
除该驱动文件外源码与 v235 逐文件一致。**修复效果只能在手机上验证。**

镜像：`thor-out/v236-unified/boot-thor-unixnu-v236-unified.img`，SHA-256 `c3049c63…02b347`，内核 UUID 见 `v236-intake/unified-verification.json`。
启动/取日志：`v236-intake/auto-boot.sh`（手机回到 fastboot 且链路稳定 30 秒后临时启动 v236，再挂上取日志的等待）。

## 5. 这次对读取方法的教训

- `lr` 只能说明"最后一次真正调用之后的返回地址"：尾调用不改 lr，所以 lr 落在 F 里不代表出错分支在 F 里。
  要把保存的寄存器和候选代码逐条对上。
- 附加现场里的"栈上的内核代码字"大部分是陈旧值，这次只用来佐证；真正定案靠的是 stock 日志的寄存器 + 反汇编。
- XNU 保存的 `cpsr`：位 3:2 是异常级（0x4 = EL1），位 0 是 SP1；`esr 0x86……` 是当前异常级的取指中止。

## 6. 仍未解决（与这次 panic 无关）

- 设置 → 通用 → 关于本机卡死：`PSGAboutDataSource._loadValues` 里等一个同步 XPC，对端不明（见 `v235-intake/ANALYSIS.md`）。
- 帧率掉帧：每次提交的缓存维护开销。v235 日志实测平均每次提交 4.3 ms（整窗口维护 916 GiB，显式通道只占 5%），
  上游策略相同，杠杆在移植自己的显式同步登记上。见 `UPSTREAM-GPU-KERNEL.md` 第 1 节。
- 上游 GPU 更新的接入评估已完成（只读，未接入任何东西）：`UPSTREAM-GPU-KERNEL.md`、`UPSTREAM-GPU-RUNTIME.md`。
