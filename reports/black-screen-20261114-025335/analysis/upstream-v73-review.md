# THOR v73：上游修复核查

## 025335 本轮数据到齐后的结论修订（优先于下文早期候选排序）

本轮 v73 的 5 批快照中 audiomxd 都是 PID 83，直至最后 time=968 的采样；mediaremoted 都是 PID 10。最新 SB PID 5 / T248 快照已经进入原生 window scene 连接：`displayAssertionDidGainControlOfDisplay → _createSystemShellSceneWithOrientation → UIApplication _connectUISceneFromFBSScene → SBEmbeddedDisplayWindowSceneDelegate _configureForConnectingWindowScene`，叶子为 `SBSystemPointerInteractionManager initWithMultiDisplayUserInteractionCoordinator → SBMultiDisplayUserInteractionCoordinator addPointerInteractionObserver:+0`。这是一份 `run=1` 的单次样本，不足以证明 pointer / 多显示驱动死锁，但**不再是 v71 的媒体 once 等待或 v72 的 NSError 格式化栈**。

因此，以下上游音频病例现在应作为 **v72 历史回归与未落地集成项**，排在本轮 scene-create 归因之后。不能把它们说成本轮黑屏已经定位的首要原因。此前“audiomxd 崩溃”的比较点指历史轮次；本轮没有同样的稳定性失败证据。稳定 PID 也不自动证明音频请求全部完成。

核查时间：2026-11-13 UTC（Mac / i386 当地已进入 11 月 14 日）。范围：当前 Mac 工作树继承的 Pong 22H374 历史、UniXNU 与 UniXNU-sheng 的公开主线、与当前音频故障相近的一个独立项目案例。只读核查，没有修改、构建或刷写。

## 可以给出的结论

上游确实解决过“启动到 SpringBoard 前卡住”“首帧不出现”“音频时钟错误”等具体问题，其中同一 22H374 的 InterfaceNamer 等待链有记录明确的真机首帧结果。但目前没有证据说明公开主线存在一个可以直接套用、已经证明能解决本轮 THOR 黑屏 / audiomxd 崩溃 / PreferencesMigrator 超时的补丁。

尤其要区分：仓库里有实现、构建链接了实现、当前手机加载了实现、真机观察到修复结果。这四件事不能互相替代。当前协作构建审查发现 THOR 的 `libPongPower.a` 只含 QcomSmem / PongSmmuLease，而没有 PongAudio / ADSP / AudioService；这与继承的 Pong 文档和旧 build-report 描述不同。因此不能以 Pong 或 Sheng 已有音频功能来认定本轮 THOR 的音频后端完整。

## 可复核候选与当前适用性

| 来源 | 已经解决或实现了什么 | 证据等级 | 对当前 THOR 的判断 |
|---|---|---|---|
| Pong `services98-first-frame-launch-policy.md` | `SwapWait(0, 0x80000003)` 正确处理 readiness 查询；空队列可成功，有真实未完成提交返回 Busy，非零 frame 保留实际退休语义。另将 mobile 身份 audiomxd 的 stderr 从 root 日志路径改回 `/dev/console`，避免 xpcproxy 因 EACCES 拒绝 spawn。 | 文档记录源码 / 主机构建 / fastboot 接受；该记录的设备结果仍待确认。 | 这是首帧与 spawn 阶段的真实接口问题。当前已有 CPU framebuffer clear；v72 的 audiomxd 是启动后崩溃，本轮未保存同类崩溃，不能直接归为同因。`userland/board_services18.py:42–50` 已包含 console 修复；是否进入 THOR Data 仍需看本轮产物。 |
| Pong `services107-interface-namer-handshake.md` | SpringBoard 同步 `wifi_manager_open` 等待 wifid；wifid once owner 卡在 `SCNetworkInterfaceCopyAll → __wait_for_IOKit_to_quiesce → SCDynamicStoreNotifyWait`。在实际 en0 存在后执行有界 IOKitWaitQuiet，并发布真实 `*QUIET*` / `*TIMEOUT*` 结果。 | 记录明确真机超时回执、SB 正常 RunLoop、首帧和持续 swap。 | 同 22H374，优先级高的历史对照。当前仓库 `wifi-bootstrap.c:127–166` 已有该修复。只有本轮 SB / wifid 等待链复现时才能认定命中；v71/v72 的媒体 / NSError 栈以及本轮 scene 连接均不等同于这条等待链。 |
| Pong `services125-wake-scanout-usb.md` | SM8475 / A730 在休眠唤醒后 `scanout copy not submitted status=0x3` 导致 worker 永久退出；保留资源 / generation，未提交资源不足时有界重试，真实 GPU fault 仍停止。 | 修改与构建记录；该文档仍明确真机唤醒修复待验证。 | 本轮 THOR 为软件 framebuffer 路径，尚无相同 `0x3` 未提交证据。不能当作当前黑屏修复；更不能复制 A730 GPU / 电源常量。 |
| Sheng commit `dea464ef093349dd746582691b0eaa3807fe1441` | 真实板时钟 19.2 MHz，而 CoreAudio 常量假定 24 MHz；共享页校正并修复 IO 时间线。 | `porting.toml` 有对应 revision 的设备记录：播放和录音从启动平滑，4822 / 4843 IO 周期在 15–25 ms，0 late periods。 | 可借鉴接口与诊断。Pong 当前 `PongAudioPlugIn.c:154–168` 已有这段校正；它依赖真实 `gConnect` 与 `ShengAudioSetHostClock` selector，缺后端时直接返回。它修复的是时钟 / 断续，并非已证明的 audiomxd 异常退出。 |
| Sheng commit `0215f34a6d5307dab53e9a81dff9ae0fd047f3be` | macOS 27.3 下将音频插件 segments 常驻，减少 IO 线程缺页引发的 overload；另有 dyld pager 和 GPU / 调度工作。 | 提交明确写 compiled / linked，not run on hardware；当前 porting 亦标该音频变更未跑硬件。 | 不能宣布上游已经修好 THOR 的内存压力或黑屏。先明确实际后端 / 端点契约与首次异常，再评估是否有 IO 缺页 overload。 |
| Sheng `reports/macos27-memory-run139.md` | 5 GiB 真实负载使 swap 增长再回收，压力回到正常；明确没有理由据此修改 compressor / swap policy。 | 真机 macOS 27.3 / SM8550 测量，含限界。 | 证明上游有完整的测量办法；不能把 macOS 的 jetsam / VM 策略移到 iPadOS 18.7。当前 665 MiB / 压力警告要先核 THOR 可用内存来源、保留区及压缩负担。 |

前三个 Pong 文档在本地 git 历史对应整合提交 `a6a8a7f8ae49590412a5bc30bf996383f2fbdbb9`（2026-11-02，Integrate accumulated Pong runtime repairs and GPU PAC preparation）。它们是本地版本化证据。

## 公开主线新鲜度与边界

- UniXNU main：`7d85ede80092050f4d87f72ac7229bb605ce07fb`，2026-11-13T06:04:20Z，`docs: refresh device records`。
- UniXNU-sheng main：`1108068537d5396edb2c63b2f8c6f5b97b0a4bbc`，2026-11-12T22:03:37Z，`fix(storage): restore macOS root windows`。
- 查了 Sheng 最近 40 个提交和音频插件最近 6 个提交；近期集中在 macOS APFS / 内存、GPU 诊断和电源、视频 / Wi-Fi。没有在这批记录中找到直接匹配当前 22H374 PreferencesMigrator 超时或 MRAVLocalEndpoint 等待 / audiomxd 崩溃的已验证修复。这是有限范围的检索结论，不能扩大为“所有上游都没有”。
- Sheng README 当前维护方向是 27，源码还涵盖 17 / 26；SoC 是 SM8550。OS 版本、CPU / GPU、ADSP、功放、保留内存和 DMA 所有权都要逐项适配。
- `https://github.com/Chengdong-Yu/UniXNU-pong` 和 main API 在本次未认证读取返回 404，网页与 raw 也不可取得。这只能说明当前公开访问不可用，不能判断删除 / 私有，也不能把本地文档说成今天远端已核实的新修复。

## v71/v72 音频问题的历史排查候选（不作为 v73 当前优先级）

1. **先补齐产物选择与服务契约的证据。** 以本轮 kernel archive 成员、link symbols、Data 中实际 HAL / launchd 配置、设备 IORegistry 为准，明确 audiomxd 看到了哪一个音频插件、插件是否发布设备、实际提供者是否存在。旧 build-report 文字不能作证明。
2. **锁定 audiomxd 第一个原始异常及 SB 等待 owner。** SIGABRT / corpse / 重启只说明退出结果；要保留首次 throw / assertion / crash reason 和触发栈，再对应当前 22H374 二进制。不能根据后续 once wait 直接认定根因已经在 MediaRemote。
3. **按真实后端状态定义 HAL 行为。** 缺后端时不能发布一个已可用的虚假端点；可对照上游 HAL service-ready、端点枚举、properties、start/stop、clock 合约。真正移植硬件音频必须按 THOR 板的固件、ADSP / SMMU / GPIO / 功放资料实施，不能开启 Pong 或 Sheng 的现成硬件地址就宣布完成。
4. **显示链跟踪真实提交者、非空内容和最终扫描输出。** 当前观察到清黑不能证明桌面已绘制；与 GPU submit / scanout 未提交恢复要按实际分支区分。v73 增加 SwapEnd 提交者回执的方向适合关闭这个证据缺口。
5. **内存压力单独归因。** 可以借鉴上游记录线程缺页 / swap / 压缩页 / 对象保留的方法。不能用扩大内存窗口、禁用 jetsam 或冻结 PosterBoard 来掩盖尚未确认的保留区 / 服务缺口。

## 一个独立项目的相近案例（不是 UniXNU 上游）

Lakr233/vphone-cli issue #658 记录：一个 iOS 26 beta 的 VirtualAudio 防护补丁找不到对应 throw，虚拟 speaker 已安装但缺少保护信息，导致 audiomxd 多次 abort；维护者推测媒体等待可阻塞 SB / backboardd。它没有复现 / 证明 THOR 当前根因。issue #680 的 iOS 18.6.2 又是类似补丁匹配失败但不崩，只表现路由静音。两者一起说明：**音频异常值得优先拿首次原始异常，但相似黑屏不足以复制补丁。**

## 原始来源

- 官方核心：https://github.com/UniXNU/UniXNU
- 官方 Sheng：https://github.com/UniXNU/UniXNU-sheng
- 官方板级记录：https://github.com/UniXNU/UniXNU-sheng/blob/main/porting.toml （网页引用 ref：turn15view2；音频设备记录 revision dea464ef）
- 时钟修复提交：https://github.com/UniXNU/UniXNU-sheng/commit/dea464ef093349dd746582691b0eaa3807fe1441
- 音频常驻 / macOS smoothness：https://github.com/UniXNU/UniXNU-sheng/commit/0215f34a6d5307dab53e9a81dff9ae0fd047f3be
- 内存真机报告：https://github.com/UniXNU/UniXNU-sheng/blob/1108068537d5396edb2c63b2f8c6f5b97b0a4bbc/reports/macos27-memory-run139.md
- 官方移植架构：https://unixnu.com/zh/porting/migration （ref：turn9view3）
- 相邻项目原始 issue：https://github.com/Lakr233/vphone-cli/issues/658 （ref：turn13search0）
- 相邻项目 18.6.2 issue：https://github.com/Lakr233/vphone-cli/issues/680 （ref：turn13search1）

直接提交页 / raw 页面在网页工具有部分 DisabledError；已用 Mac 上未认证 GitHub API / raw 只读请求核实上述提交与报告正文。公开 porting.toml 可直接网页读取。对应 API 输出另存 `upstream-v73-evidence.json`，保留查询结果、时间与状态，不含凭据。

## 补充核实：没有音频后端，并不等于发布了虚假 speaker

本节是在当前 THOR 构建差异出现后做的窄范围只读源码对照。没有读取或修改手机，也没有进行镜像校验。**当前缺音频驱动说明能力边界；单靠这一点不能断言它导致 audiomxd 崩溃。**

### 当前代码已经具备的保护

- `scripts/build-pong-power-native.py:48` 当前只编译 `QcomSmem.c` 和 `PongSmmuLease.cpp`；`PONG_BUILD_RESULT.json` 对应最近构建日志 `build-20261113T173550Z.log`。该脚本第 59 行仍输出历史 SM8475 / ADSP / TFA9873 描述，不能当作包含那些对象的证明。
- `userland/PongAudioPlugIn.c:75–90` 必须找到真实 IOKit 服务、成功映射共享控制页和 PCM ring，并通过 magic / version / rate / channels / ring / OutputReady 检查才连接。`ready()` 在 53–59 行要求物理就绪，且已有 native output 时不再发布重复端点。
- 同文件 `317`、`349–355`：未 ready 时 `DeviceList` / owned objects 为空，UID 转换返回 unknown。没有后端不会从正常枚举路径宣布 `PongAudioDevice` 可用。
- 同文件 `403–405` 已采用上游 Thunderbolt transport，注释明确避免 BuiltIn 要求的 Apple port descriptions。因此“这里误用了 BuiltIn transport”不是当前源码可支持的结论。
- `userland/build-ipados18.py:1570–1583`：默认把 `.driver` 放 `/pong/staged-audio18/`，仅显式 `--enable-audio-hal` 才放 `/pong/audio-hal18/`。`refresh_ui` 不带该参数时只是不新增，不能仅凭参数默认值推断已有 Data / OS 中旧插件已被移走。
- `userland/init.c:330–337` 在目录权限条件满足后，把 `/pong/audio-hal18` 只读 bind 到 `/System/Library/Audio/Plug-Ins/HAL`，保留另一路 `/Library/Audio/Plug-Ins/HAL/VirtualAudio.plugin`。实际 bind 是否发生需要当前启动回执，Recovery 下静态目录不能证明运行时挂载。
- `ui_compat18.c:2499–2502` 对 audiomxd / mediaremoted 仅安排快照然后返回，不创建 HAL speaker。SpringBoard 部分不同：`2467–2470` 会调用音量适配器，`settings_compat18.c:564–570` 在 15 秒执行音量恢复、20 秒主队列执行铃声恢复、20 秒另一队列选择提示音。这些调用不创建假端点，但会进入真实音频路径；当前 SB 等待需要辨明是否由这些回调触发，不能把整个 UI 注入都称为纯观察。

### 更接近当前媒体等待的同版本历史病例

`migration/ipados18/services73-smmu-audio.md` 记录了同 22H374 的 services72 真实 crash report：

- audiomxd 原始理由为 `RPCTimeout`，调用 `com.apple.coremedia.routediscoverer.xpc` 的 `DiscoveryMode`。
- 等待链为 MediaExperience discovery manager → CoreMedia remote endpoint manager → airplayd。airplayd 反复 exit(1)，endpointmanager 受到 launchd 节流。
- **当时实际 HAL、功放与 DSP 已 ready，仍无 StartIO**；因此这是“有后端也会发生”的媒体服务路由问题。
- 当时 SB 的 35 / 90 秒快照正常 RunLoop，文档明确不能把该音频超时归于 UI 主线程锁死或缺内存。
- services73 实现的修复是先于 launchd 创建 `AirPlay/airplayd=false`，使 CoreMedia 与 launchd 一致选择 audiomxd 内置的真实 endpoint 实现。保留原守护进程与 Mach 服务，不伪造 XPC 响应，也不关闭原始超时。
- **该 services73 文档只确认主机构建，用户当时要求不启动，所以该页没有修复后的真机验证。** 它是高相关候选，不是本轮根因或已治愈证明。

当前对应代码：`build-ipados18.py:396–406` 生成种子 `{'airplayd': {'Enabled': False}}`；`init.c:342–364` 调用 `copy_new`，仅在目标不存在时创建策略文件，保留已有 operator override；`init.c:467–468` 主流程先准备 HAL 视图，再准备音频策略。THOR 的已编译 `kern_exec.c:7888–7893` 仍有受 `pong_init_path=/pong/init` 控制的 helper 分支，实际是否走该 helper 必须核本轮命令行与日志。

### 本轮应补取的文件与回执

| 对象 | 路径 / 关键字 | 能证明什么 |
|---|---|---|
| 生效策略候选 | `/private/var/preferences/FeatureFlags/Domain/AirPlay.plist`，检查 `airplayd.Enabled` | 当前 Data 落地值；需要配合启动顺序判断是否被服务读取 |
| 配置种子 | `/pong/etc/AirPlay-local-audio.plist` | init 原本想创建的配置，不能替代目标值 |
| 对外 HAL 候选 | `/pong/audio-hal18/` | 是否有被暴露的 PongAudio.driver |
| 隔离 HAL staging | `/pong/staged-audio18/` | 插件是否仅暂存、未放入 HAL 视图 |
| System HAL | `/System/Library/Audio/Plug-Ins/HAL/` | OS 原文件；实际 boot bind 视图需日志确认 |
| 原生 VirtualAudio | `/Library/Audio/Plug-Ins/HAL/VirtualAudio.plugin` | 保留的真实原生音频 IPC / routing 插件 |
| helper 回执 | `Pong init: loading helper /pong/init` | 本轮确实尝试启动静态 init |
| HAL mount 回执 | `Pong init: Darwin24 board HAL view result=` | bind 成功或失败与 errno |
| 策略成功回执 | `Pong init: native integrated CoreMedia audio policy prepared` | 已通过 init 配置路径；旧目标可能被保留，仍应读取文件 |
| 策略失败回执 | `Pong init: audio provider policy unavailable errno=` | 没有完成策略准备，保留现有策略 |

下一步应把这些文件、airplayd / endpointmanager 的运行状态与实际异常对应。只有实际证据命中 DiscoveryMode / routediscoverer / airplayd 路径，才可把 services73 的策略作为针对性修复；缺驱动或黑屏现象本身不能完成这个归因。025335 数据已到齐后的优先级见本文开头与下一节。

## 025335 实际策略生效核对

数据来源：`THOR-v73-025335/extracted/manifest.json`、`inventory.jsonl`、`files/logs/PongConsole.txt`；Mac 对应目录为 `/Users/<user>/projects/thor-unixnu1030/UniXNU-pong/reports/black-screen-20261114-025335/extracted/`。manifest 标记 complete=true，完整 inventory 4341 节点，errors / skipped 为空。本节仅读取已完成导出的文本，不重新读取镜像。

### 三个证据层

1. **确定：显式 `/pong/init` 分支未走。** Console 第 407 行（重复环形转储中还有同一记录副本）为 raw 27.096138 秒的 `load_init_program: attempting to load /sbin/launchd`。实际构建源 `thor-out/build-tree/.cache/xnu-work/bsd/kern/kern_exec.c:7888–7896` 如果进入 `pong_init_path` 分支，会加载 `/pong/init` 后 return，失败则 panic；该普通候选路径打印在后面的 7900–7904 行。这是正面分支证据，不只是没有看见 helper 字样。
2. **确定：Data 目标策略不存在。** manifest 的 `preferences/FeatureFlags/Domain/AirPlay.plist` 为 exists=false。这个 Data 相对路径正是运行时 `/private/var/preferences/FeatureFlags/Domain/AirPlay.plist`。本次也没有 `Pong init:`、`native integrated CoreMedia`、`board HAL view` 或 `PongAudio HAL` 回执。
3. **未知：最终 v73 bootargs 的逐字内容，以及 OS 卷 `/sbin/launchd` 本体身份。** 没有读取大镜像、也没有新建镜像审计来补这两项。上游 prepare 曾可把 helper 装成 `/sbin/launchd`，所以仅有路径不能证明那一定是 Apple 原始二进制。当前证据证明上游显式 helper 路径未执行、Data 策略未落地；不进一步声称最终参数已逐字核实。

有限深度文件查询找到的两个源码配置说明了集成差异，但不能替代最终镜像参数：

- `UniXNU-pong/board.toml:136` 有 `pong_init_path=/pong/init`。
- `UniXNU-thor-18/board.toml:78–100` 没有该项，使用了一组 `thor_*` 参数。
- `thor-out/manifest.json` 指向 v73 镜像，但没有实际 kernel args 文本；其旧 `hardware_tested=false` 是打包阶段记录，不能推翻当前真机运行结果。

manifest 中 `pong/etc/AirPlay-local-audio.plist`、`pong/audio-hal18`、`pong/staged-audio18`、`Library/Audio/Plug-Ins/HAL` 均为不存在。**这些是 Data 卷相对路径**，只证明 `/private/var/pong/...` 与 `/private/var/Library/...` 的状态；未单独证明 OS 根 `/pong` 或 `/Library` 的对应目录为空。只有正确的本轮别名 / mount 证据才能把这些路径等同起来。

本轮 Console 另记录 airplayd PID 207 在 raw 141.661475 秒达到约 32128 wakes/s 的告警。它证明该进程曾运行且唤醒异常多，不能证明 services73 的 `DiscoveryMode` 超时重现。当前 Console 中没有 `routediscoverer` / `endpointmanager` 原因行；audiomxd PID 83 后续保持稳定。

### 最小可实施的集成建议（本轮未实施）

若后续要关闭这项历史回归缺口，建议在 **现有 THOR 启动前 Data provisioning 链** 只加入同一确切 feature flag，而不是直接启用整个 `/pong/init`。完整 helper 还会调整 Data seed、路径、Setup bypass、设备身份、HAL mount 与 stdio；当前 THOR 的 shim / mount / seed 顺序及 payload 中 helper 是否存在尚未验证，整段启用无法保持单一变量。

候选目标：`/private/var/preferences/FeatureFlags/Domain/AirPlay.plist`。其语义内容为：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>airplayd</key>
  <dict>
    <key>Enabled</key>
    <false/>
  </dict>
</dict>
</plist>
```

实现条件：在 launchd / CoreMedia 第一次读取 feature flags 之前写入；仅不存在时创建，保留既有 override；目录采用 root 所有、0755，文件 root 所有、0644；写入失败返回可见错误。随后从目标 Data 文件回读解析同一键，并在运行时查询框架所选 provider / 对应 Mach service 与首次请求结果。仅“文件存在”不足以证明框架已选内置 endpoint。

这项建议目前是可复核的配置差异和候选回归实验，**不是要求立即实施的本轮主修复**。本轮先解决 window scene 创建、主线程 / 提交与实际像素之间仍缺的因果证据；只有后续需要复现 v72 音频问题或完成集成时，再对这一个策略做隔离验证。services73 当时仍仅主机构建的验证边界不变。

## 与本轮 scene / pointer 栈的定向上游对照

本轮追加了限定范围查询：本地 `migration/ipados18`、`porting.toml` 与 `userland` 中查找 `SBMultiDisplayUserInteractionCoordinator`、`addPointerInteractionObserver`、`SBSystemPointerInteractionManager`、`SBEmbeddedDisplayWindowSceneDelegate`、`_configureForConnectingWindowScene`、`_connectUISceneFromFBSScene`、`_createSystemShellSceneWithOrientation`、`displayAssertionDidGainControlOfDisplay`，均无命中。官方 Sheng 当前 `porting.toml` 也没有前两个 coordinator / manager 类或 scene 相关记录（网页 refs：turn21view0、turn21view1、turn21view2）。这是明确的有限范围检索结果，不等于所有上游历史没有此问题。

最接近的现有显示修复如下，但均不能据此断言当前 observer 注册位置故障：

| 已知记录 | 修复与证据级别 | 当前 THOR 是否已有 / 是否直接命中 |
|---|---|---|
| 同 22H374 services26–28，`services.md:396–445` | 可见矩形裁剪、缩放缓冲失败回滚、BGRA 必须 1×1 元素 / 合法 stride / 可见末行在分配范围内；有效图层开始前只交接一次 console。原始记录区分源码 / 构建 / 临时启动，未把这些改动单独认定为真机唯一根因。 | 实际编译 `MiniIOMobileFramebuffer.cpp:194–208` 已有像素 / stride / 容量检查，`698–715` 已有单次 console 交接和清屏。它们验证后续显示输入契约，不能证明前面的 scene observer 函数死锁。 |
| 同 22H374 services97→98 | 原厂 readiness 在首帧前调用 SwapWait(0)；旧 BadArgument 是已定位回归。修复查真实待完成队列，空队列成功，高位 readiness 未完成返回 Busy。services98 页仍是构建层证据。 | 当前实际编译源 `MiniIOMobileFramebuffer.cpp:925–945` 已完整包含这段行为；不是一个尚未移入 THOR 的新补丁。 |
| services124 GPU 首帧回归 | services123 的 VM-backed BO / shared pager 是可疑回归；退回曾被用户确认进桌面的映射模型，明确仍缺冻结现场栈与本次修复后结果。 | A730 GPU path；本轮 THOR 的 CPU framebuffer 分支不能直接套用该映射改动。 |
| 官方 Sheng pointer / geometry 项 | WN8030 实体键盘 / 触控板、macOS 相对位移与按钮、iPadOS 27 HID / displayUUID 配对等；文档分别标注真机与 host-only。 | 这是输入设备协议和显示配对，不是 SB `addPointerInteractionObserver:` 的类级修复；OS / 硬件也不同。不能因栈里出现 Pointer 字样就断言缺鼠标、touch 或多显示驱动。 |

**本轮没有查到可直接命中这条 scene 创建栈的上游补丁。** `addPointerInteractionObserver:+0` 位于一份 `run=1` 单点栈，不提供卡住时长、锁对象或等待 owner。合理的下一证据是同一 scene 连接的进入 / 返回、主线程后续进展，以及对应非空图层是否真正到达显示；不能把注册 observer 改成空返回、跳过 scene 配置或伪造完成。Console 清黑和 beginGraphics 的文字也不等同于桌面非黑内容已经完成绘制。
