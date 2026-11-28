# 摄像头在 Thor 上的可行性（2026-11-27 22:40–23:12 的只读调研）

调研范围：Thor 自己的内核和驱动树、这台 Mac 上的安卓设备树、Mi 10（HakiOS）移植的摄像头驱动、Fold 2 移植树里同 SoC 的时钟/CCI 原语、
上游各机型的设备记录、22H374 系统镜像和 dyld 缓存里的摄像头框架、主线 Linux 的 camss 驱动。
没有改任何文件，没有碰手机。调研期间网络搜索时好时坏，公开资料只拿到一部分。

## 结论（中文）

**能做到，但是大活：让“相机”App 能用是以月计的工作；第一张测试图案帧是几天到两周。**
每一步在别的手机上都有先例，而这台手机的安卓设备树就在这台 Mac 上。

### 硬件（本地已知的）

- 这台手机的完整安卓设备树在 `thor-out/v226-intake/recovery-linux.dts`（1.66 MB，`recovery-linux.dtb` 同源）。
  四个传感器节点（`qcom,cam-sensor`），型号不在树里：

| 位置 | MIPI | I2C | 时钟 | 复位 | 供电 | 其他 |
|---|---|---|---|---|---|---|
| sensor0 广角 | CSIPHY1 | CCI0 master 1（引脚 GPIO112/113） | MCLK0 19.2 MHz，GPIO102 | GPIO126 | AVDD GPIO171 2.8 V；VIO 1.8 V；VDIG 1.1 V | 对焦、防抖、EEPROM |
| sensor1 前置 | CSIPHY4 | CCI1（0xac16000）master 1，引脚 GPIO208/209（eGPIO） | MCLK4 19.2 MHz，GPIO104（"MCLK1" 只是表里的标签） | GPIO108 | PM8010J 的 RPMh 供电：`ldoj3` 1.8 V（vio）、`ldoj5` 2.8 V（vana）、`ldoj2` 1.104 V（vdig） | 无对焦/防抖；型号和 I2C 地址不在任何本地来源里 |
| sensor2 超广角 | CSIPHY2（小米定制 `csiphy-v2.1.3-xiaomi-l1`） | CCI0 master 0 | MCLK2，GPIO101 | GPIO60 | — | — |
| sensor3 潜望长焦 | CSIPHY3 | CCI1 master 0 | MCLK3，GPIO100 | GPIO128 | — | 防抖 |

- 采集模块的地址：CSIPHY0–5 在 0xace4000；CCI0 0xac15000、CCI1 0xac16000；摄像头时钟控制器 camcc 0xade0000；
  CSID0 0xacb7000、CSID‑lite0 0xacc6000；IFE‑lite0 0xacc6000、IFE0–2 从 0xac62000 起；测试图案发生器 0xacf6000；有自己的 SMMU。
  车道数和数据速率不在树里。
- Fold 2 移植树（同是 SM8475、同系统版本）里有现成的同 SoC 原语：`thor-out/v209-present/zizhan-latest/drivers/PongPower/`
  下的 `ZizhanCamCc.h`、`ZizhanCci.h`、`ZizhanPm8008.h`、`ZizhanCamProbe.h`（注明“主机测过，还没接进内核”）。camcc 和显示共用 MMCX 电源域。

### 别人做到过什么

- **Mi 10 / HakiOS 的驱动**（`thor-out/reference-umi/hakios-xnu-kernel-umi-fixes/drivers/camera/hakios.camera.c`，4309 行，为 POCO X5 Pro 写的）：
  CSIPHY → CSID‑lite0 RDI0 → IFE‑lite0 写入内存的采集，手写的 CCI 和 PM8008 控制，寄存器表从安卓的
  `com.qti.sensormodule.*.bin` 转换而来；用户态 `userland/src/camera_device.c` 用 `CMDerivedObjectCreate` 造一个 FigCaptureDevice，加软件 ISP。
  他们的笔记：iOS 16.5 下 `cam=5` 时“相机”App 能看到前后摄像头、实时预览、对焦、变焦、拍 HEIC。Mi 10 那台机器本身只验证到读芯片 ID、
  编程模式、通过测试图案出一帧，没有从传感器采到帧。
- **Galaxy Tab S9 的移植**：三颗传感器的芯片 ID、一整帧 3280×2464 的 RAW10（CSID‑lite0 RDI0 + IFE‑lite0 WM0）。
  它做的 CoreMediaIO 摄像头扩展被 mediaserverd 当成“外接摄像头”，而 App 只认内置摄像头（H9 ISP 路径），所以 App 还用不上。

### iPadOS 18.7.10 需要什么

- 系统卷里没有摄像头 kext（`/System/Library/MediaCapture` 是空的）；ISP 固件在 `/System/Library/Isp/afpp/j171/ispane.afpp`。
  相关框架：AVFoundation、CoreMedia、CoreMediaIO、AVFCapture、CMCapture、CMCaptureCore、CMCaptureDevice、H9ISPServices、H10ISPServices、CameraKit。
- App 的调用链：相机/FaceTime → AVFoundation → AVFCapture → CMCapture 里的设备供应商 `BWFigCaptureDeviceVendor`，
  它可以接“内置设备创建函数”（Apple 的 ISP 插件 `H9ISPCaptureDeviceCreate` 等），也可以接一个 CMIO 扩展摄像头。
  `CMDerivedObjectCreate`、`FigCaptureDeviceGetClassID`、`_CMIOExtensionFigCaptureDeviceCreate` 都是导出的。
- 所以第三方驱动要做的是：**给这个供应商一个 FigCaptureDevice 和源源不断的像素缓冲**（HakiOS 的做法，这些入口在 22H374 里还在，
  但没有在手机上重新验证过），并在设备树里放 `/arm-io/isp`、`/product/camera` 节点。没有人重写过 Apple 的 kext 家族，不建议走那条路。

### 主线 Linux 能借的

主线 camss 没有 SM8450/SM8475 的条目，这一代的 CSID/IFE（680）只有 X1E80100 在用，而且只有 RAW 直通（RDI），没有 ISP 处理；
`drivers/media/i2c` 里没有 IMX989、IMX586、IMX707 的驱动。所以寄存器表只能从手机上的安卓
`/vendor/lib64/camera/com.qti.sensormodule.thor_*` 里取。型号方面：广角 IMX989 有多处公开资料印证；超广角和长焦的型号没有查实。

### 路线和大小

| 路线 | 要做什么 | 主要风险 | 大小 |
|---|---|---|---|
| (c) 测试图案帧 | camcc 的供电和时钟、CSID‑lite0 测试图案、IFE‑lite0 WM0、一个 SMMU 上下文、缓冲区 0x9f500000 | camcc 和正在工作的显示共用 MMCX；SMMU 写入必须按上下文 | 小到中：几天到两周；三台别的手机验证过 |
| (a) CSI 驱动 + FigCapture 垫片 | (c) 加 CCI、上电时序、从安卓取的传感器表、CSIPHY 配置、流式缓冲环、供应商宿主里的垫片、软件 ISP | 寄存器表不在本地；供应商的接口自 iOS 16.5 起有变动；软件 ISP 的 CPU 开销 | 到第一帧真实图像是中；到“相机”App 能用是大：数月 |
| (b) CMIO 扩展摄像头 | (a) 的内核采集加扩展二进制 | 被当成外接摄像头，App 可能不认；签名和权限 | 大；逆向比 (a) 少 |
| (d) 重写 Apple 的 kext 家族 | AppleH10CamIn、ISP 用户客户端、固件协议 | 没有先例，协议未知 | 不建议 |

### 第一个里程碑（由启动参数开关控制，默认不开）

1. 只读盘点：GCC 的摄像头时钟、camcc 的供电开关、引脚。
2. 一帧 CSID‑lite0 的测试图案 RAW10，经 IFE‑lite0 写到 0x9f500000，再拉到 Mac 上——不碰传感器就证明时钟、DMA、SMMU 都通了。
3. 给前置摄像头上电（VIO、AVDD、VDIG、MCLK1、复位），通过 CCI1 master 1 读到芯片 ID。

这三步成了，再去拉安卓的传感器表、从广角传感器采一帧原始数据。


> 更正（2026-11-28，见 `v247-intake/ANALYSIS.md` 和 `v247-camera-staging/DIFFERENCES.md`）：前置摄像表的 I2C 引脚是 GPIO208/209、主时钟是 MCLK4（GPIO104）、供电来自 PM8010J 的三路 LDO；上面表里已改正。
> 调研原文第 1 节里 "sensor1, front: CCI1 master 1 (GPIO112/113), MCLK1 GPIO104" 一句同样有误，GPIO112/113 是广角（CCI0 master 1）的引脚。

## 调研原文（英文，未改动）

Path roots: `OUT` = `thor-out`; `V246` = `OUT/v246-unified`; `ADT` = `OUT/v226-intake/recovery-linux.dts`; `UMI` = `OUT/reference-umi/hakios-xnu-kernel-umi-fixes`; `ZIZ` = `OUT/v209-present/zizhan-latest`; `DREC` = `OUT/upstream-20261125/UniXNU/docs/.vitepress/device-records`.

Bottom line: a working Camera.app is a large job, but every piece has a precedent on another phone, and the Android tree for thor is on this Mac.

### 1. Hardware as known locally
- **Port tree has nothing.** `V246/device/dts/pong.dts` holds only touch, buttons, UFS and PCIe (it already reserves Android's `camera_mem` as `region24@9f500000`; `ADT:185`). `V246/kernel/prelink-thor.json` has 19 components, none camera; `strings` on `mach.development.pong.unstripped` has no AppleISP/CamIn/FigCapture names.
- **Android device tree is local:** `ADT` (1.66 MB, full live tree; also `OUT/v226-intake/recovery-linux.dtb`, `OUT/v221-intake/recovery-live.dtb`). Four sensors, all `compatible = "qcom,cam-sensor"`. Part numbers are not in the tree, and no sensor-module file or Android camera log is on this Mac.
  - sensor0, wide (`camera_wide_avdd_ldo`, `ADT:33046`): CSIPHY1, CCI0 master 1 (GPIO110/111), MCLK0 19.2 MHz on GPIO102, reset GPIO126, AVDD GPIO171 2.8 V, VIO 1.8 V, VDIG 1.1 V, AF, OIS, EEPROM (`ADT:24685`).
  - sensor1, front: CSIPHY4, CCI1 master 1 (GPIO112/113), MCLK1 GPIO104, reset GPIO108, AVDD 2.8 V, VDIG 1.104 V, VIO 1.8 V, no AF/OIS (`ADT:24926`).
  - sensor2, ultra-wide: CSIPHY2, a Xiaomi variant `qcom,csiphy-v2.1.3-xiaomi-l1` (`ADT:24432`), CCI0 master 0, MCLK2 GPIO101, reset GPIO60 (`ADT:24769`).
  - sensor3, tele with OIS: CSIPHY3, CCI1 master 0 (GPIO114/115), MCLK3 GPIO100, reset GPIO128 (`ADT:25035`).
  - Blocks: CSIPHY0-5 at 0xace4000; CCI0 0xac15000, CCI1 0xac16000; camcc 0xade0000 (`ADT:815`); CSID0 0xacb7000 and CSID-lite0 0xacc6000 (`csid680_110`); IFE-lite0 0xacc6000, IFE0-2 from 0xac62000 (`vfe680_110`); test-pattern generators at 0xacf6000; camera SMMU. Lane counts and data rates are not in the tree.
- `ZIZ/drivers/PongPower/ZizhanCamCc.h`, `ZizhanCci.h`, `ZizhanPm8008.h`, `ZizhanCamProbe.h` are same-SoC (SM8475, same OS build) clock offsets and CCI/PM8008 primitives: "host tested, not wired into the kernel yet". They note camcc shares MMCX with the display.

### 2. Existing camera work
- **HakiOS / Mi 10:** `UMI/drivers/camera/hakios.camera.c` (4309 lines, written for the POCO X5 Pro) captures CSIPHY through CSID-lite0 RDI0 and the IFE-lite0 write master into memory, with hand-driven CCI and PM8008 and register tables converted from Android's `com.qti.sensormodule.*.bin`. `UMI/userland/src/camera_device.c` builds a FigCaptureDevice with `CMDerivedObjectCreate` plus a software ISP. `UMI/docs/ru/notes.md:34`: with `cam=5`, Camera.app shows rear and front, live preview, AF, zoom, HEIC shots (iOS 16.5).
- The Mi 10 overlay itself did not capture from a sensor: `UMI/devices/xiaomi-mi-10/CAMERA.md:70` says "Physical sensor frame capture remains disabled". On the phone it verified chip-ID read, mode programming and one hardware test-pattern frame through CSID480/IFE-lite48x.
- **Galaxy Tab S9 port,** `DREC/gts7xlwifi.toml:237`: chip IDs of three sensors on device, one full 3280x2464 RAW10 frame through CSID-lite0 RDI0 and IFE-lite0 WM0 with a recognizable thumbnail. Its CoreMediaIO camera extension "is discovered by mediaserverd as an external camera; built-in cameras come only from the H9 ISP path, so apps do not use it yet".
- **OnePlus 13T,** `DREC/oneplus13t-shad0w23333.toml:454`: test-pattern camera only, host-only, never booted.

### 3. What iPadOS 18.7.10 expects
- **OS volume:** no camera kext on disk; ISP firmware at `/System/Library/Isp/afpp/j171/ispane.afpp`; `/System/Library/MediaCapture` is empty. Frameworks: AVFoundation, CoreMedia, CoreMediaIO, AVFCapture, CMCapture, CMCaptureCore, CMCaptureDevice, H9ISPServices, H10ISPServices, CameraKit.
- `CMCapture.framework/J171/CameraSetup.plist` names back sensor 0x0229 (3272x2456) and front 0x9762; `AVCaptureSession.plist` lists "Back Camera" and "Front Camera".
- **Dyld cache:** `/System/Library/MediaCapture/H9ISP.mediacapture` is in the cache. `CMCapture` holds `BWFigCaptureDeviceVendor` with `initWithDefaultDeviceCreateFunction:cmioExtensionDeviceCreateFunction:cmioDiscoverySession:` and strings for `H9ISPCaptureDeviceCreate`, `H10ISPCaptureDeviceCreate`, `H16ISPCaptureDeviceCreate`. `CMDerivedObjectCreate`, `FigCaptureDeviceGetClassID` and `_CMIOExtensionFigCaptureDeviceCreate` are exported.
- `/usr/libexec/dietapplecamerad` opens `AppleH10CamInUserClient` and loads the afpp firmware. `libMobileGestalt` contains `IODeviceTree:/arm-io/isp`; SpringBoard, UIKitCore, FTServices and ARKitCore key on `still-camera`, `video-camera`, `rear-facing-camera`.
- **Chain:** Camera.app or FaceTime, AVFoundation, AVFCapture, then CMCapture's vendor, which takes either a built-in device creator (the ISP plug-in) or a CMIO extension camera.
- **What a third-party driver must provide:** a FigCaptureDevice and streams that deliver pixel buffers to that vendor (HakiOS's hook, still present in 22H374; the hosting process was not re-verified), plus `/arm-io/isp` and `/product/camera` device-tree nodes; or a CMIO camera extension. Nobody has reimplemented Apple's kext families.
- Not determined: Apple's own camera kext list. `OUT/ipsw-components/components/kernelcache.release.ipad7c` is still an LZFSE container and was not decoded.

### 4. Public upstream
- Mainline `camss.c` has no sm8450 or sm8475 entry; `sm8450.dtsi` and `sm8475.dtsi` contain zero camera nodes. `csid_ops_680`/`vfe_ops_680`, this phone's generation, exist only for X1E80100. In `camss-vfe.c`, `vfe_formats_pix_845` is a "TODO: Replace with pix formats" alias of the RDI table, so capture is RAW through RDI only, D-PHY only.
- Mainline `drivers/media/i2c` has no imx989, imx586 or imx707.
- **Phone sensors:** IMX989 wide is confirmed by several public pages: high confidence. IMX586 ultra-wide: no public confirmation found. IMX707: public pages tie it to the Xiaomi 12S and 12S Pro main camera, not to this periscope; treat both as unverified. The authority is Android's `/vendor/lib64/camera/com.qti.sensormodule.thor_*` on the phone.

### 5. Options — see the table above.

### First milestone
A boot-arg gated probe:
1. A read-only inventory of GCC camera AHB/XO, the camcc GDSC and the pins.
2. One CSID-lite0 test-pattern RAW10 frame through IFE-lite0 into 0x9f500000, pulled to the Mac. This proves clocks, DMA and SMMU without touching a sensor.
3. The front camera powered (VIO, AVDD, VDIG, MCLK1, reset) and its chip ID read over CCI1 master 1.

Only then is it worth pulling the Android sensor table and dumping one raw frame from the wide sensor.
