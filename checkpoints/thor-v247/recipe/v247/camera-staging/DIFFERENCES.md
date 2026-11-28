# Front camera: zizhan (MIX Fold 2) versus thor (12S Ultra)

Both phones are SM8475 ("cape") on the same 22H374 build, so the zizhan probe's clock and CCI steps carry over
unchanged. The wiring differs in almost every other respect. Line numbers are exact unless a range is given.

Paths: `ZIZ` = `thor-out/v209-present/zizhan-latest`, `DTS` = `thor-out/v226-intake/recovery-linux.dts`,
`V246` = `thor-out/v246-unified`, `LIN` = upstream Linux v6.1 (fetched 2026-11-27 with curl, nothing saved).

| Item | zizhan front camera | thor front camera | Source zizhan | Source thor |
|---|---|---|---|---|
| DT node | `cam-sensor1` under CCI0, `cci-master = 1` | `cam-sensor1` under CCI1, `cci-master = 1` | `ZIZ/docs/research/2026-11-19-camera-feasibility.md:89` | `DTS:24807-24830`, `DTS:24926-24959` (24932) |
| CCI controller | CCI0 at 0xac15000, master 1 | CCI1 at 0xac16000, master 1 | `ZIZ/drivers/PongPower/ZizhanCamProbe.h:105` (`cci_base 0xac15000`, `master 1`) | `DTS:24828`, `DTS:24932` |
| CCI pads | GPIO112/113, function `cci_i2c` = 1, pull-up | GPIO208/209, function `cci_i2c` = 1, pull-up, 4 mA; an eGPIO pair: active state `qcom,apps`, suspend state `qcom,remote` | `ZizhanCamProbe.h:105` (`pad0 112, pad1 113`), `:175` | `DTS:4682-4712` (`cci3_active`, `cci3_suspend`), `DTS:24810-24813`; mux numbers `LIN/drivers/pinctrl/qcom/pinctrl-sm8450.c:1632-1633`, claim bit `LIN/drivers/pinctrl/qcom/pinctrl-msm.c` `msm_pinmux_set_mux` |
| CCI root and branch | CCI0: RCG 0x1312c, branch 0x13144 | CCI1: RCG 0x13148, branch 0x13160 | `ZizhanCamProbe.h:105`, `ZizhanCci.h:18` | `DTS:24817-24821`; `LIN/drivers/clk/qcom/camcc-sm8450.c:660-661, 1389-1393` |
| MCLK pin | GPIO105, mux 1 | GPIO104, mux 1 (`cam_front_active_mclk`) | `ZizhanCamProbe.h:105` (`mclk_pin 105`), `:172` | `DTS:5491-5504`; `pinctrl-sm8450.c:1528` |
| Which MCLK | MCLK5: RCG 0x1508c, branch 0x150a4 | **MCLK4**: RCG 0x15070, branch 0x15088 | `ZizhanCamProbe.h:105`, `ZizhanCamCc.h:60` | `DTS:4834` (`cam_sensor_mclk4_active` = gpio104), `DTS:24931` (clock id 0x54); `camcc-sm8450.c:1073-1074, 2325-2330` |
| MCLK rate | 19.2 MHz from BI_TCXO (no PLL) | 19.2 MHz from BI_TCXO (DT asks 0x124f800) | `ZizhanCamCc.h:5` | `DTS:24928`; `camcc-sm8450.c:1006-1007` |
| Reset pin | GPIO127 | GPIO108; suspend state output-low, 2 mA, pull-down | `ZizhanCamProbe.h:105` (`reset_pin 127`) | `DTS:5536-5550`, `DTS:24937`; `pinctrl-sm8450.c:1532` |
| vio 1.8 V | PM8008J L5, set over I2C (QUP wrap0 S5 at 0x994000, GPIO206/207, slave 0x0d, enable GPIO11) | **RPMh VRM `ldoj3`** (PM8010J L3, `qcom,rpmh-vrm-regulator`, pmic5-ldo), no I2C, no enable GPIO | `ZizhanPm8008.h:5-6, 13, 24-29`, `ZizhanCamProbe.h:2-5, 63-87, 115-116, 124, 144-156` | `DTS:6873-6882`, `DTS:24951` |
| vdig | PM8008J L2 at 1152 mV | **RPMh VRM `ldoj2`** (PM8010J L2) at 1104 mV | `ZizhanCamProbe.h:104-105, 156` | `DTS:6855-6864`, `DTS:24944, 24950` |
| vana | GPIO97 gpio-regulator (2.9 V) | **RPMh VRM `ldoj5`** (PM8010J L5) at 2800 mV; min = max = 2.8 V | `ZizhanCamProbe.h:104-105` | `DTS:6909-6918`, `DTS:24944, 24948` |
| Rail control path | byte writes to the PMIC over GENI I2C | `pong_rpmh_vote()` three times per rail: MODE 7, millivolts, ENABLE 1 (same writes as the Wi-Fi driver) | `ZizhanCamProbe.h:74-85` | `V246/device/drivers/PongWifi/PongWifiPlatform.cpp:170-195`; `LIN/drivers/regulator/qcom-rpmh-regulator.c:35-37, 53-55` (`_rpmh_regulator_vrm_set_voltage_sel`, `rpmh_regulator_set_enable_state`) |
| Physical read-back | every PM8008 write is read back over I2C | SPMI observer read of the LDO (STATUS, ENABLE, VSET), PPID slave 9, peripheral 0x42/0x44/0x41 | `ZizhanPm8008.h:7` | `PongWifiPlatform.cpp:98-152`; PM8010J = slave 9: `DTS:11114-11125`, `DTS:9982` |
| Clock domain | `cam_cc_titan_top_gdsc`, GDSCR 0x131dc | same | `ZizhanCamCc.h:4, 13` | `DTS:1002-1012`; `camcc-sm8450.c:2787-2788` |
| GCC camera AHB | 0x136004, read before CAM_CC | same | `ZizhanCamProbe.h:53-56` | `LIN/drivers/clk/qcom/gcc-sm8450.c` probe (`0x36004`, `0x36020` forced on) |
| Sensor part | IMX596 (vendor CamX module `zizhan_sunny_imx596_front`) | **not in any local source** (see below) | `ZIZ/docs/research/2026-11-19-camera-feasibility.md:12` | `DTS:24926-24959` has no `reg`, no part name |
| I2C address | 0x10 answered (list tried: 0x10, 0x1a, 0x37), id 0x0596 | **unknown**; ordered list of 8 attempts | `2026-11-19-camera-feasibility.md:81, 89` | `PongCamCci.h` `kCamIdAttempts` |
| Gate | boot args `zizhan_cam_probe=2` and `zizhan_user_present=1` | `/chosen thor-camera = 2` (the boot command is at 1005 of 1023 bytes) | `ZizhanCamProbe.h:215-227` | `V246/unified-verification.json` (`boot_argument_bytes`), `PongWifiPlatform.cpp:723-742` |
| Start point | `zizhan_cam_probe::start()` in `pong_power_start_optional()` (PongPower.cpp:33), called by ArmDTPlatform::start | `pong_power_start_optional` is **not linked** on thor (see INTEGRATION.md); the call goes into `pong_rpmh_init()` | `ZIZ/drivers/PongPower/PongPower.cpp:24-45` | `V246/device/scripts/build-pong-power-native.py:48`, `nm` of the v246 kernel |

## Corrections to the task brief and to the earlier research note

The brief and `thor-out/v246-intake/research/CAMERA-22H374.md` (lines 20, 83) put thor's front camera on
"GPIO112/113" and "MCLK1". The DT says otherwise:

- `cam_cci0` (0xac15000) has `pinctrl-names = "m0_active", "m0_suspend", "m1_active", "m1_suspend"` with
  phandles `0x4c9..0x4cc` (`DTS:24520-24524`). `0x4cb` is `cci1_active`, GPIO112/113 (`DTS:4622-4635`). So
  **GPIO112/113 are CCI0 master 1**, which carries `cam-sensor0`, the wide camera (`DTS:24685-24722`, `cci-master = 1` at 24691).
- The front camera is a child of `cam_cci1` with `cci-master = 1` (`DTS:24926-24959`). CCI1's `m1_active` is
  `pinctrl-2 = <0x4cf>` = `cci3_active` = GPIO208/209 (`DTS:24813`, `DTS:4682-4696`).
- "MCLK1" is the label `CAMIF_MCLK1` in Xiaomi's `gpio-req-tbl-label` (`DTS:24933`), a free text. The pin is
  gpio104 (`DTS:24937`, `DTS:5491-5504`), and the SoC function on gpio104 is `cam_mclk4` (`cam_sensor_mclk4_active`,
  `DTS:4834-4848`; `cam_sensor_mclk1_active` is gpio101, `DTS:4744`). The DT clock id 0x54 agrees: ids 0x4c, 0x4e,
  0x50, 0x54 are the sensors on gpio100, 101, 102, 104, two ids per MCLK. So the probe programs **MCLK4**
  (RCG 0x15070 / branch 0x15088, confirmed in `camcc-sm8450.c`).
- CCI is not a QUP/GENI serial engine. There is no "QUP engine state" to report for the CCI buses; the probe
  reports the CCI controller (hardware version word, when it can be read safely) and its two pads instead.
  No thor driver uses a QUP engine for anything camera related.

## What the code does about the unknown sensor address

`cam_cci_identify()` (PongCamCci.h) resets CCI1 master 1 and tries, in this fixed order, one read per table entry:

| # | 7-bit address | register | bytes | family |
|---|---|---|---|---|
| 0 | 0x10 | 0x0016 | 2 | Sony (zizhan's front answered at 0x10) |
| 1 | 0x1a | 0x0016 | 2 | Sony |
| 2 | 0x37 | 0x0016 | 2 | Sony (third entry of the zizhan front list) |
| 3 | 0x36 | 0x300a | 3 | OmniVision |
| 4 | 0x10 | 0x300a | 3 | OmniVision |
| 5 | 0x20 | 0x300a | 3 | OmniVision |
| 6 | 0x2a | 0x0000 | 2 | Samsung |
| 7 | 0x5a | 0x0000 | 2 | Samsung |

An answer counts only when it is not all zeros or all ones and, for the Sony model id register, the high byte is
below 0x10 (an OmniVision part at 0x10 reads 0x0016 as zeros and is not mistaken for a Sony). A NACK halts the CCI
queue, so the controller is reset before the next entry; a reset that is not acknowledged ends the search. At most
eight reads and nine resets run, each bounded by 2000 polls of 10 us. The log says which entry answered and the raw
bytes; the part number is then read off the bytes by hand. Nothing is written to the sensor.

## Assumed, not verified

- **PM8010J LDO base addresses** `0x40 + n - 1` (PongCamBoard.h) come from the PM8008 layout in zizhan's
  `ZizhanPm8008.h:22`; the thor DT lists no PM8010 LDO peripherals. A wrong guess only costs the physical
  read-back (level 2 then stops before switching anything on, see INTEGRATION.md).
- **Regulator order** vio, vana, vdig is the common Qualcomm Sony table and zizhan's order; thor's own vendor
  sensor driver (which holds the real table) is not on this Mac.
- **LDO mode HPM** on all three rails; stock Android would use LPM for the two low-current rails.
- The upstream Linux files were read at tag v6.1; the phone runs a 5.10 vendor kernel, but these are SoC
  register tables, which do not change between the two.
