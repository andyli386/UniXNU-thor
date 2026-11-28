# 蜂窝语音通话在 Thor 上的可行性（2026-11-27 22:40–23:08 的只读调研）

调研范围：上游同 SoC 的基带桥 `thor-out/v161-rmtfs-staging/pong-baseband`、Mi 10（umi）移植的源码和笔记、
22H374 系统镜像里 CommCenter / 音频栈的二进制、Thor 自己的内核和驱动树、公开的 SM8475 音频内核源码。
没有改任何文件，没有碰手机。调研时网络搜索不可用（503），公开资料只取到 NothingOSS 的 SM8475 内核树一份。

## 结论（中文）

| 目标 | 现状 | 工作量估计 | 能不能承诺 |
|---|---|---|---|
| 基带起来并保持（PAS、GLINK/QRTR、rmtfs/EFS） | 上游有；Thor 上只做了 24 秒的一次性试验 | 1–2 周 | 可以，这是第一步 |
| 读 SIM 并注册 | 上游有 | 之后几天 | 可以（Thor 上目前 SIM 状态是 `fault=2`，206 次采样都是） |
| CommCenter 服务、数据、短信 | 上游有、在 Pong 手机上验证过数据；Thor 上没有 | 2–4 周 | 数据可以；短信可能 |
| IMS | 上游只是**观察**调制解调器自己的 IMS，自己不注册；Apple 的 SIP 从未注册成功 | 几天到几周 | 不确定 |
| 拨号 / 接听 / 挂断 | 上游桥里**没有任何通话控制代码**；QMI VOICE 只是透传，没测过 | 几天 | “一拨就断”的原因都没查过，不能承诺 |
| 通话音频 | **要新写** | 数月 | 不能承诺，见下 |
| 电话界面 | iPad 系统里有通话中的界面；iPhone 的“电话”App 是 arm64e 的，这个用户态跑不了 | 几天 | App 本身无解 |

**为什么通话音频难（事实）：**

- iOS 18 的 CommCenter 用一组 QMI AUDIO 命令控制通话音频（`kAudioSetAudioPath`、`kAudioSetI2SFormat`、
  `kAudioEnablePCMChannel`、`kAudioEnableEchoCancelationNoiseReduction`、`kAudioQueryAudioVocoderInfo` …），
  并要求基带驱动提供 Apple 私有的 AUDIO 服务（0xe1）。上游的桥对这些私有服务一律回“不支持”。
- 系统里的 `BasebandVoice.driver` 需要 Apple 自己的基带（ICE）的用户客户端
  `IOAppleConvergedIPCBBUserClient` 和一条 PCIe 共享内存的 PCM 通道；Apple 芯片的内核缓存里只有通用音频 kext，
  没有通话专用的。Thor 预链接的 19 个 Apple 组件里没有音频、没有基带。
- 这颗 SoC 上语音不经过应用处理器：调制解调器和音频 DSP（ADSP）的语音域在片内协商编解码、交换语音帧，
  ADSP 做声码器和回声消除并驱动 codec。公开的 SM8475 音频内核里没有 AP 承载语音的接口。
  Mi 10 那个移植要自己做一条 AP 承载的语音通道，只是因为它的基带是外挂的——那条通道写完了，也从来没听到过声音。
- Thor 现在的音频是只有扬声器的 AudioReach 图，**没有麦克风采集**。

所以：不是物理上不可能（可以做一个 AUDIO 0xe1 的替身加一个 ADSP 语音会话驱动，或者走 AP 媒体栈加软件声码器），
但它是一个以月计的研究项目。**紧急通话在这个移植上必须明确视为不支持。**

## 建议的顺序

1. 基带起来并保持、读到 SIM、注册到网络（上游代码在，Thor 上要接进来并解决 EFS 持久化）。
2. 数据（IPA），然后短信。
3. 把上游“一拨就断”的原因查清楚（本地没有任何日志，只有一句 README），再谈拨通。
4. 通话音频作为单独的长期课题。

## 调研原文（英文，未改动）

Roots: `T=thor-out`, `P=T/v161-rmtfs-staging/pong-baseband`, `U=T/reference-umi/hakios-xnu-kernel-umi-fixes/devices/xiaomi-mi-10`, `V=T/v246-unified`.

### 1. What the bridge does
- Registration, SIM identity, IMS-bearer, data: yes (`P/PongModemStatus.h`, `PongModemSim.h`, `PongModemIpa.h`, `PongModemIpaDma.h`, `PongPdpInterfaces.h`). README: cellular-only HTTP 200 on the Pong phone.
- SMS: no WMS code; forwarded as on umi, delivery never shown.
- IMS: it only *observes* the modem's IMS (IMSS 18, IMSA 33: bind, registration, service status, `PongModemIms.h`) and serves the modem's IMS bearer requests (`PongImsDcm.h`, QRTR 0x302 → WDS). It registers nothing itself.
- Call control: none. No dial/answer/hangup code in 5.6k lines. Apple private services 0xe1–0xea (AUDIO, MAVIMS, ELQM) get QMI NOT_SUPPORTED except Vinyl 0xe5 and BSP 0xe2 (`P/PongBasebandService.cpp:312-369`). Standard QMI VOICE (0x09) passes through unmodified, untested.
- "Calls end immediately": the only evidence is `P/README.md:7` and `T/upstream-refresh-20261123/UniXNU-upstream/docs/devices/pong.md:51,61`. No log or cause anywhere locally. Candidate causes (inference): AUDIO 0xe1 rejection breaking CommCenter's call-audio driver; Apple SIP never registered; or a CS dial on LTE rejected as on umi.

### 2. umi (external SDX55 on PCIe)
- Calls never worked. Only attempt: VOICE Dial 0x20 → error 0x0d `NO_NETWORK_FOUND`; stock Android without VoLTE cannot call there either (`U/VOICE-CS-umi.md:35-40,237`). VoLTE: IMS PDN starts, `kDADMissing`, synthesised 0x5561, no SIP registration ever, RTP service 0x28 absent (`U/VOLTE-umi.md:294-394`).
- Voice audio is a shim, implemented but never heard: MHI channel 80 offload, 128 KiB mailbox mapped to modem and ADSP, APR MVM/CVS/CVP passive sessions, AFE routes (`bsd/dev/umi_voice_audio_*.inc`). Apple's AUDIO interface and PhoneCall route were not used (`VOICE-CS-umi.md:163-171,324,378-394`).

### 3. How 22H374 sets up call audio
Facts:
- CommCenter (`T/session-tools/osimg.py`, OS volume): `AudioToolboxCallAudioService`, `/cc/requests/start_media|stop_media`, `AVAudioSessionCategoryPhoneCall`, `provider:didActivateAudioSession:`; QMI AUDIO commands `kAudioSetAudioPath`, `kAudioSetI2SFormat`, `kAudioEnablePCMChannel`, `kAudioSetMixState`, `kAudioEnableEchoCancelationNoiseReduction`, `kAudioQueryAudioVocoderInfo`, `kAudioI2SInterface`, `kAudioPcmDirection`; log "Vocoder info: type, sample rate, I2S Clock Rate, EVS UL/DL Bandwidth"; "AUDIO STREAM TOKENS … uplink/downlink"; `IMSSubscriberConfigInterface::shouldUseAPMediaStack`.
- Dyld cache: `libCommCenterMCommandDrivers` has `QMIAudioCommandDriver`, `QMICallAudioDriver`, "Dialing Normal Voice Call", "Device does not support CS voice calls"; `libIPTelephony` has AP SIP plus `QMIRTPStackController`, `AVCRTPStackController` (AP media), `IBIRTPStackController`, `ImsPrefs::getUseAPMediaStackKey`, "start only after VSID is retrieved".
- HAL: `/System/Library/Audio/Plug-Ins/HAL/BasebandVoice.driver` needs `IOAppleConvergedIPCBBUserClient` and a PCIe shared-memory PCM link (Apple's ICE baseband) and logs "NO (I2S device present)". The Qualcomm path is the built-in IOAudio2 device (transport "Baseband I2S").
- Apple's ipad7c kernelcache: only generic audio kexts (IOAudio2Family, AppleEmbeddedAudio, AppleARMIISAudio), no voice kext. Thor prelinks 19 Apple components, none audio or baseband (`V/kernel/prelink-thor.json`); its own audio is a speaker-only AudioReach graph with no capture ring (`V/device/drivers/PongPower/README.md`).

Inference: on Qualcomm iPhones the baseband runs the vocoder and IMS RTP and hands PCM to the AP over an I2S link into Apple's codec; the AP is in the sample path (mixing, phone-call session). An AP media stack exists but its switch is Internal-only (16.5 evidence only).

### 4. How this SoC carries voice
- Integrated modem, no MHI, no voice node in the Android DT; the AP reaches the ADSP over GPR (`adsp_apps`) and APR (`apr_apps2`) (`T/v218-cell-work/live.dts:11426-11470,11563-11585,12210`).
- The modem's own registry lists ADSP `audio_pd` providing `avs/audio` (`V/device/drivers/PongPower/ThorPdLocator.h:11-13`), and Thor's modem queries it at boot (206 log hits, e.g. `T/v242-intake/logs/console-v242temp-kernel.txt:1330`).
- The public SM8475 audio kernel has no q6voice/voice_mhi and no voice DAI link (NothingOSS msm-5.10, `vendor/qcom/opensource/audio-kernel`). umi needed an AP-carried mailbox only because its modem is external.
- Conclusion: the modem negotiates the codec and exchanges frames with the ADSP voice domain inside the SoC; the ADSP does vocoder/EC and drives the codec; the stock AP never sees the samples.
- Thor today: the modem is up for 15 s, advertises VOICE (service 9) but no IMS services (18/33/40/82), and SIM status is `fault=2` in all 206 captures.

### 5. Gap

| # | Piece | Exists | Size | Main risk |
|---|---|---|---|---|
| 1 | Modem up and kept up (PAS, GLINK/QRTR, rmtfs/EFS) | upstream yes; Thor 24 s trial | 1–2 wk | EFS persistence |
| 2 | SIM read and registration | upstream yes | days after 1 | card fault today |
| 3 | CommCenter service, data (IPA), SMS | upstream yes, proven on Pong; absent on Thor | 2–4 wk | SM8475 IPA facts only on device |
| 4 | IMS owner | modem IMS upstream (observed); Apple SIP never shown | days vs weeks | two owners on one PDN |
| 5 | Call control | QMI VOICE passthrough untested | days | undiagnosed hang-up |
| 6 | Call audio | must be written | months | see above |
| 7 | Phone UI | iPad has in-call UI; MobilePhone is arm64e-only and absent | days, app unsolved | arm64e userland |

Piece 6 is blocked by facts as far as Apple's model goes: there is no modem-to-codec I2S link the AP owns, the SM8475 voice interface in the ADSP is in no tree, and Thor has no microphone path. It is not physically blocked: an AUDIO 0xe1 shim plus an ADSP voice-session driver, or the AP media stack with software codecs, remain possible.

### 6. Recommendation
- Worth promising: modem up and registered, then data (weeks, upstream code exists).
- Possible: SMS.
- A call that connects silently: plausible only with modem IMS; do not promise until the Pong hang-up is diagnosed.
- Two-way audio: a multi-month research project. Emergency calls must be stated as unsupported.
