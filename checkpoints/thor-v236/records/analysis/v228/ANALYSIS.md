# Thor v228 — 分析、改动与待验证项

日期：2026-11-24 ｜ 基线：v227（启动 `A7236BA8-5419-4800-870F-B4CE8FA81926`）
镜像：`thor-out/v228-unified/boot-thor-unixnu-v228-unified.img`（94,515,200 字节）
SHA-256：`96b04ff79aaea70c1154a468b70f7cd87286c4def2a162aa9464f3e6955da603` ｜ 内核 UUID：`CCB5CA2C-E2F3-3A45-8276-DCBED929B032`
状态：**仅主机验证，尚未上机**（手机已关机）。所有“改善”均为预期，需 v228 真机日志确认。

## 证据

- 标准只读采集：`thor-out/recovery-v227-history-20261124T120419Z.1NovMO`（5719 条内核记录、384 条日志、11 份线程栈）
- 全量快照：`thor-out/recovery-v227-full-20261124T1213`（5395 个文件、1.40 GB：`logs/`、`mobile/Library/Logs/`、`db/diagnostics/`、`db/uuidtext/`、偏好设置；`*.inventory.json` 内含每个文件的 SHA-256）
- v227 操作序列：天气 686–693 s、播客 694–705 s、天气 705–708 s、App Store 708–714 s、设置 714–724 s；无定格。

## 1. CPU：大核/超大核从 v211 起一直被钉在最低频（本次最重要发现）

v227 每个触摸窗口（`THOR CPU V224`）：三簇负载 100%，小核 1804.8 MHz，大核 **633.6 MHz**（最低档，上限 2496），超大核 **921.6 MHz**（上限 2995.2）。
`THOR CPU LIMIT V227`：0x30c 寄存器第 2 位在大核/超大核**每个采样都置位**（`pending=0/30/30`），小核从不置位；0x20 低字节读数**始终等于已请求的频率**（`equal=0/30/27`，`below=0/0/0`）。

原因（`device/drivers/ArmCpu/PongCpuPolicy.h`，上游 Pong 的负载调频代码）：

```cpp
if(d.regs[0x30c/4]&4) {                         // 该位置位时
    const unsigned hardware=(d.regs[0x20/4]&255)*19200u;
    if(hardware && hardware<cap)cap=hardware;   // 把“当前频率”当成上限
}
```

对照 Linux 主线 `qcom-cpufreq-hw.c`：EPSS 的 `reg_intr_clr = 0x308`、`GT_IRQ_STATUS = BIT(2)` —— 0x30c 是 DCVSH **中断状态**（锁存，需软件写 0x308 清除），不是“正在限频”。主线只在限频中断处理里把 `DOMAIN_STATE & 0xff` 当“被限到的频率”，并用“限频值 ≥ 请求值”判断已恢复。Thor 没有清这位；它一置位，上限就永远等于当前频率，集群只降不升，最终停在最低档。v211 之前是固定最高档策略（`pong_cpu_policy=2`），所以这个问题是 v211 引入负载调频后才出现的。

### 改动
- 不再把 0x30c/0x20 当上限（硬件自己的 LMh 电流/温控上限仍然生效，它作用在请求之下）。
- 触摸后 600 ms 内，只要大核或超大核任一核负载 ≥ 25%，该簇保持最高非 boost 档，期间 8 ms 采样；无负载的簇照常降档。原因：产帧是单线程、每帧约占一核 1/4，按“利用率 80%”选频会刚好卡在“勉强够”的频率，稍重的帧就超预算。
- 诊断：`THOR CPU V224`（不变）与新的 `THOR CPU LIMIT V228: ... boosted=a/b/c below=d/e/f ...`。`below` 计数的是“硬件交付低于请求”的采样（真实限频），预期为 0；`peak_khz` 预期在触摸窗口达到 2496000 / 2995200。

### 主机验证
`thor-out/v228-intake/cpu-tests/run.sh <头文件>`：7 个场景（锁存位+满载、单线程 1/4 核 UI、超过窗口的 UI、无触摸满载、空闲、硬件低于请求、非 Thor 板）。
v227 旧策略在“锁存位+满载”“1/4 核 UI”“硬件低于请求”三项失败；v228 全部通过。日志：`v228-unified/prepare/evidence/cpu-governor-{new,old}.log`。

### 对 120 Hz 的含义
v225/v226 实测的每帧 CPU 侧开销（两次缓存同步合计 5.58 ms、64 MiB 映射 13–18 ms、CPU 拷屏 1.1–2.4 ms、点“通用”主线程 103–235 ms CPU）都是在 633/921 MHz 下测得的。内核侧未改显示管线，预期这些耗时按频率等比下降。**未上机前不能给出帧率数字。**

未改、仍存在的显示瓶颈（按 v227 日志）：
- 1611 个触摸期采样的帧：等写入方释放（`ready-enqueue`）均值 9.0 ms、P50 8.65、P90 13.7、P99 26.5 ms；CPU 拷屏均值 2.32 ms；帧间隔 P50 15.7 ms。
- 单缓冲原地拷贝与面板扫描重叠（有撕裂风险）；GPU backing 在 4 GiB 以上，DPU 只能扫 32 位地址，现有两块固件低地址缓冲各只够一帧。
- 触控为轮询（活动 4 ms/空闲 8 ms），无硬件时间戳。

## 2. 电量：ADSP 在电源轨一步从未通过

v227 日志：`THOR PAS1 V226: rpmh_read=response-timeout-retained addr=0x30030` → `rails=0` → `final=rails`，每次启动都停在 `PAS_INIT_IMAGE` 之前。

原因：v226/v227 先对 `lcx.lvl`/`lmx.lvl` 做“带响应的 TCS 读”。`ThorActiveTcs::transfer` 对这类作用域事务只在**该 TCS 的中断状态位**置位时才认定完成，而本机只有 ACTIVE0 的中断是使能的（`irq_enable=0x1`），所以读必然超时，还留下一条已触发的表项。上游 Pong 与 Fold 2 的 ADSP 启动都不读：直接写每条电源轨的最高合法 ARC 档位（`qcom_rpmh_level_index(&res, 0xffffffff, &idx)`）。

### 改动（`PongPower/ThorPas1Kernel.inc`）
`rails()` 改为与调制解调器 CX/MSS/XO 相同的、本机已多次 ack 的写路径：`rawVote(addr, highest)`，不读；`releaseProxy()` 不再碰硬件（启动期的投票保留，与 Linux 附加 power-domain 的生命周期一致）。日志：`THOR PAS1 V228: rails=1 lcx=<corner>/<highest> lmx=<corner>/<highest> retained=1`。

### 待真机确认的后续环节（此前从未到达）
INIT_IMAGE → MEM_SETUP → 装载固件段 → AUTH_AND_RESET → LPASS READY/交接 → `PMIC_RTR_ADSP_APPS` 通道 → BATTMGR 四项查询 → 发布 `ThorBattery`。每一步都有阶段名日志（`final=inputs|metadata|cold-prepare|load-on|rails|...|ready`）。失败时按原有路径回滚（PAS shutdown + AOSS load off）。

## 3. 蜂窝：调制解调器一直在等 AP 提供 EFS 服务

v227：READY → GLINK 双向 VERSION → IPCRTR → QRTR HELLO → 基带只公布两个服务（66 = SERVREG `msm/modem/root_pd`，43 = SSCTL）→ 查询目录 → 5 秒后被驱动停机。Fold 2 的记录（`thor-out/v209-present/zizhan-latest/docs/progress/2026-11-19-evening-daily-default.md`）显示同样的“只有两个服务”状态，直到 AP 提供 rmtfs（QMI 服务 14，实例 1）后基带才公布 45 个服务并停在 OFFLINE 模式。

### 改动（新增 `PongPower/ThorMssServe.inc`，由 `ThorPas4Kernel.inc` 在 READY 后调用）
- 传输层用 Thor 树里已有的 `QcomGlink.c`（与 Pong、Fold 2 逐字节相同）；协议处理照 Fold 2 已验证的 `ZizhanMpss.cpp`/`ZizhanRmtfs.h`。
- 应答 QRTR HELLO，公布 SERVREG locator（0x40/0x101，回答 Thor 自己 modem_a 里的 7 个域）和 rmtfs（14/1）。
- rmtfs：OPEN/CLOSE/RW_IOVEC/ALLOC_BUFF/GET_DEV_ERROR。modemst1/modemst2/fsg/fsc 只读进内存影子（`pong_ufs_factory_nv`，与 v227 预检同一接口），基带的写入只落在影子。共享缓冲用预留的 RMTFS lease（0xfed24000+0x280000），先用 MP_ASSIGN（SMC 0x0c/0x16）借给 MSS_MSA。
- 20 s 后发送两条只读查询：DMS Get Operating Mode(0x2d)、UIM Get Card Status(0x2f)。无任何改状态的 QMI，不发射射频。
- 最长保持 60 s；四个关键服务（WDS/DMS/NAS/UIM）都公布且两条查询都得到答复后再留 3 s 提前结束。之后走原有的停机路径（SMP2P stop → PAS shutdown → AOSS off → 再启动 ADSP）。副作用：电量显示最早要在调制解调器停机之后才会出现。
- 开关：设备树 `/chosen` 的 `thor-rmtfs-serve=1`（本次镜像已加入）。去掉它即回到 v227 的探测流程，不需要重编内核。

### 主机验证
`thor-out/v228-intake/serve-tests/run.sh`：44 项（ASan+UBSan）。locator 回复与 v227 中真机基带接受过的 `ThorPdLocator::respond` 输出逐字节一致；覆盖分页、未知服务、不支持的消息、rmtfs 全部消息、越界拒绝、影子写入、待发队列顺序、DMS/UIM 应答解析、畸形包。测试过程中发现并修掉了三处缺陷（HELLO 多带了 32 字节头、待发队列第二项不再重试、分页回复的条目计数写成了总数）。
**未覆盖**：GLINK 传输本身在 Thor 上跑 server 角色的真机行为、MP_ASSIGN 的真实返回值、基带是否接受该 rmtfs。

日志前缀 `THOR SERVE V228:`；结论行 `final=<原因> ... servers=N mask=... rd=... wr=... refused=... dms= uim=`。

### 已知仍欠缺（之后的步骤）
- QRTR 无流控（不发 confirm/RESUME_TX），Fold 2 同样没发且稳定；需观察 45 个服务时是否够用。
- `ThorUimStatus::rootUp()` 把 `0x1fffffff`（SERVREG 的 DOWN）当成 UP；rmtfs 生效后 PD 会变成 `0x7fffffff`，旧探测流程若复用需先改。
- 之后还需：TFTP 文件服务（4096/1）、memshare（0x34/0x101）、把 QMI 接到 iOS（Pong 的 `PongBasebandService`：伪造 AppleBaseband* IOKit 服务 + `cbq_bridge` 做 QMUX↔QRTR）、数据面 IPA/GSI。
- 之前日志里的验证是“无卡”状态；插卡测试要等 UIM 服务出现之后。

## 4. Wi-Fi：本版未改，阻塞点已查清

- Thor 构建里 Wi-Fi 驱动被编掉（`XNU_PONG_WIFI=0`），v221–v226 只做了上电/时钟探测后复原。
- 上游 Pong 驱动本身就是 DesignWare PCIe 主机驱动（裸 MMIO，XNU 树里没有 IOPCIFamily）：`PongWifi/PongWifiPlatform.cpp` 的 `coldLink()`。
- 对照 Thor 自己的 DTB（`thor-out/v221-intake/recovery-live.dtb`）与 Recovery dmesg：PCIe0 寄存器、GDSC、SMMU SID `0x1c01`、MSI/SPI140、复位、全部 82 条 PHY 序列、GPIO 与 Pong 一致。差异：Thor 无 `vdd-wlan-ipa`（smpe3）电源轨；100 MHz 时钟在索引 10（Pong 为 11）；子系统设备号未知（Pong 为 `0x0108`）；固件名为 `AMSS20.BIN`/`M3.BIN`/`REGDB_XIAOMI.BIN`，BDF 尚未取得。
- 打开驱动需要本机没有的源码：Linux v7.0.14 树、`Ath12kWifi`（`nw_abi.h`、`lx_rt.c`、`nw_sta.c`、`nw_mac80211.c`）、`UniXNU-sheng` 的 `QcomRpmh.c`（`UniXNU-pong/configs/pong-native-sources.toml` 里的 pin）。`build-pong-wifi-native.py` 还写死拒绝 Thor。
- 启动参数已占 1022/1024 字节，`pong_wifi=1` 需要先缩短一个日志过滤参数。
- 建议的下一步：取回上述源码后，先做只建链模式（`pong_wifi=2`：跑 `coldLink()` 到读取端点身份即停，不做 DMA/SMMU/固件），一次启动拿到子系统号与修订号。

## 5. 构建与复现

```
thor-out/v228-unified/
  prepare/clone.sh + relocate.py      # APFS 克隆 v227 配方并改写生成文件里的绝对路径
  prepare/build.sh                    # build-kernel.sh final → prepare/build.log, build.status
  package-v228.py package|verify      # 以 v227 镜像为父，替换内核/ADT/manifest/menu 四个成员
  prepare/evidence/                   # CPU 调频与调制解调器 server 的主机测试日志
thor-out/v228-intake/                 # capture.sh、full-snapshot.py、cpu-tests/、serve-tests/
```

用户空间运行库（Metal 桥、Turnip、采集器、设置库）与 v227 完全相同，暂存路径不变（`/private/var/thor-metal-pool-v227-…`、`/private/var/thor-console-v227/`），因此新启动仍由内核 UUID 与 `THOR … V228` 日志行识别。

## 6. 上机后看什么

1. `THOR CPU V224 … peak_khz=…/2496000/2995200`、`THOR CPU LIMIT V228 … below=0/0/0`；`PONG TOUCH FRAME` 的 `ready_us−enqueue_us` 与 `done_us−start_us` 是否明显小于 v227 的 9.0 ms / 2.32 ms。
2. `THOR PAS1 V228: rails=1` → `THOR PAS1 V228: final=ready`（或停在哪一步）→ `THOR BATTERY V226: published source=pmic-glink percent=…`。
3. `THOR SERVE V228: assign … x0=0`、`rmtfs open '/boot/modem_fs1' … rc=0`、`alloc_buff`、`modem server service=…` 的数量、`final=` 行里的 `servers`/`mask`/`rd`、`uim card_status … present= absent=`。
4. 若启动异常：先去掉 `thor-rmtfs-serve` 重打包（约 10 秒），可单独排除调制解调器试验。
