# Thor v156 复现手册

目标设备是小米 12S Ultra（Thor，SM8475）。系统输入固定为第七代 iPad Wi-Fi（`iPad7,11` / `J171AP`）的 **iPadOS 18.7.10，build `22H374`**。构建时以 `BuildManifest.plist` 和 [板级版本配置](https://github.com/andyli386/UniXNU-thor/blob/e9c7f81a38dbca2a1ceba89ad38895d74d8ba5a9/board.toml#L14-L23) 为准。

这份手册适用于两种情况。

- **已有 Thor v156 开发输入**。可以按本文重新验证预链接、准备 ADT 和打包。准备好的 Thor 可以用输出镜像做临时启动测试。
- **从干净的 Mac 和一台新 Thor 开始**。可准备依赖、编译固件并核对各源码提交。继续整机构建前，需要完成本文列出的源码集成、模板生成、卷初始化和签名联动。目前这些步骤还没有在干净环境联跑到桌面。

功能复现的验收目标是用自己的 Thor 和本地输入启动到桌面，通过末尾的功能检查。整张启动镜像的逐字节复现另外需要原模板、卷与平台标识、签名产物及工具链等完全一致。新的 APFS 根 UUID 会改变打包输出。

## 1. 获取手册和构建脚本

```sh
git clone --branch docs/thor-v156-reproduction \
  https://github.com/andyli386/UniXNU-thor.git thor-v156-docs
cd thor-v156-docs
git rev-parse HEAD
```

本分支基于已发布的 v156 构建脚本提交。脚本直接使用其所在 checkout 的 `drivers/`、`kits/`、`package/` 和 `patches/xnu-ipados18/`。整编内核时，应先按选定版本装配该 checkout 中的依赖和补丁。

## 2. 核对源码版本

下表用于准备和核对依赖。各行提交分别发布，完整的补丁顺序和组件 lock 文件还待在集成分支验证。

| 模块 | 固定提交与用途 |
| --- | --- |
| 公开设备基线 | [`e9c7f81`](https://github.com/andyli386/UniXNU-thor/commit/e9c7f81a38dbca2a1ceba89ad38895d74d8ba5a9)，含七条公开设备补丁和板级配置。 |
| Apple XNU | [`43a9088`](https://github.com/apple-oss-distributions/xnu/commit/43a90889846e00bfb5cf1d255cdc0a701a1e05a4)，XNU `11417.140.69`。其余 Apple 开源源码由构建 checkout 的 `feeds.toml` 固定。 |
| Thor v156 构建与打包 | [`21890d7`](https://github.com/andyli386/UniXNU-thor/commit/21890d7a3cb7effc07995dcac85357a2c7b1286b)。 |
| v156 显示和触摸内核快照 | [`f6ef48f`](https://github.com/andyli386/UniXNU-thor/commit/f6ef48f8cdf62fed712aaa0c991cd5c623dbc8b7)，基于七条公开设备补丁重放的七个文件。 |
| CPU、蜂窝拓扑和设置首启采样 | CPU [`aa52c79`](https://github.com/andyli386/UniXNU-thor/commit/aa52c795f67168346f3b6269bc96a957ff808c81)、蜂窝 [`b293a49`](https://github.com/andyli386/UniXNU-thor/commit/b293a4980c120611afaaae5e17cf617158fca0cd)、设置 [`d144a78`](https://github.com/andyli386/UniXNU-thor/commit/d144a78bec8d316b09dd082ef006392a9f4890fd)，各自验证的补丁。 |
| UI 运行时和应用安装器 | [`da64942`](https://github.com/andyli386/UniXNU-thor/commit/da64942f25b21952c341acb6a0efbbd5cd7217ae) 和 [`a52db4e`](https://github.com/andyli386/UniXNU-thor/commit/a52db4e266458ac378f68ba51b2175df4943fe90)，用于核对源码与依赖。 |
| `pong-adreno` | [`be9437c`](https://github.com/andyli386/pong-adreno/commit/be9437c383b4295b85c9a0d14b356ccfafc42151)，包含 Thor 启动策略的 v156 源码快照。 |
| `pong-metal` | [`8c0a9ff`](https://github.com/andyli386/pong-metal/commit/8c0a9fff0151ff82d121adc55758a77c52272183)，v156 使用的 v147 前端源码候选。 |
| `pong-ufs` | [`8462a16`](https://github.com/andyli386/pong-ufs/commit/8462a165573882009fac70ded195d82fb4841a33)，与原 v156 NativeUfs 源码树一致。上游评审中的参数化驱动和板级改动有独立验证。 |
| `pong-power` | [`c37179e`](https://github.com/Chengdong-Yu/pong-power/commit/c37179ed12ba7f981b2b088aea482b969085eb84)，核对当前归档实际链接的版本。 |
| Thor DarwinBoot24 和 UEFI | [`831afc4`](https://github.com/andyli386/pong-darwin-boot24/commit/831afc41df166491813aac26251ecd2ab21fa182) 和 [`29f6216`](https://github.com/andyli386/uefi-pong/commit/29f6216318c96989aabe98c10ba690f03092c72e)，按 [UEFI 构建说明](https://github.com/andyli386/uefi-pong/blob/29f6216318c96989aabe98c10ba690f03092c72e/Platforms/Xiaomi/thorPkg/README.md) 生成 Thor 固件。 |

选择原 v156 显示快照时，按 [快照说明](https://github.com/andyli386/UniXNU-thor/blob/f6ef48f8cdf62fed712aaa0c991cd5c623dbc8b7/docs/thor-v156-display-checkpoint.md) 从公开基线重放。它已经包含过期触摸事件处理和卡片采样。原版快照与 [公开卡片采样移植](https://github.com/SFZ97/UniXNU-thor/pull/4) 应用在不同的工作树。使用包含 Thor 启动策略的 Adreno 快照时，保留单独的 Metal 翻译器 `0027` 池补丁，并确认 Thor 平台补丁只生效一次。见 [板级 GPU 提交](https://github.com/andyli386/UniXNU-thor/commit/47fd921c3f5343d076c0cbe99a3a36c8697566f3)。

使用通用 Format 2 工具时，如果 SM8475 外部源无法拉取，可按迁移说明从仓库自带的 `migration/format2/soc-seed` 准备本地源覆盖。该步骤适用于通用 Format 2 配置解析，v156 Mac 构建脚本会另外核对 `feeds.toml` 的 `refer` 源。 [Format 2 迁移说明](../migration/format2/README.zh.md)

## 3. 准备本地输入

核对 Mac 上的完整 Xcode、iPhoneOS SDK、Python 3.11 或更新版本、`mtools` 和 `dtc` 的 `fdtget`。固件构建还用到 EDK II BaseTools。后续命令在仓库根目录运行，使用调用者自己的绝对路径。

```sh
export XCODE_DEVELOPER_DIR="$(xcode-select -p)"
xcrun --sdk iphoneos --show-sdk-path
python3 --version
command -v mcopy fdtget
```

| 输入变量 | 准备方式 |
| --- | --- |
| `BUILD_TREE`、`REFERENCES`、`OUTPUTS` | 本地工作树、Apple 开源 Git checkout 目录、输出目录。`REFERENCES` 下的目录按 `feeds.toml` 中 `kind = "refer"` 条目的 `ref` 命名，脚本会校验每个提交。 |
| `VOLUME_LAYOUT`、`DEFCONFIG` | 目标安装的 APFS 布局 JSON，含 `os.uuid`、`data.uuid`、`systemos.uuid`，以及已解析的 Thor GPU defconfig。卷布局需要和目标机实际安装一致。 |
| `ADRENO_FIRMWARE`、`THOR_FD` | 本地 Thor Adreno 固件目录与编译出的 3 MiB Thor 固件。 |
| `DECOMPRESSED_22H374_KERNELCACHE`、`LOCAL_22H374_STORAGE_BINDINGS` | 从匹配恢复镜像准备的解压 kernelcache 和匹配 `22H374` 的存储绑定。 |
| `SIGNED_UI_RUNTIME`、`SIGNED_APP_HELPER` | 已签名且相互匹配的本地构建产物。见 [运行时依赖](https://github.com/andyli386/UniXNU-thor/blob/da64942f25b21952c341acb6a0efbbd5cd7217ae/docs/thor-v156-runtime-source.md) 和 [应用安装器构建说明](https://github.com/andyli386/UniXNU-thor/blob/a52db4e266458ac378f68ba51b2175df4943fe90/docs/thor-apps-helper.md)。重建安装器后，同步更新 UI 使用的文件尺寸与 SHA-256，以及内核安装器角色使用的 CodeDirectory 哈希。 |
| `V126_TEMPLATE`、`TEMPLATE_ADT`、`V156_EXEC_INJECT_ARG` | v126 低 DMA Thor Android boot v4 模板（160 MiB FAT ramdisk）、与目标 APFS 根匹配的模板 ADT、模板中的注入启动参数（按 v156 配置移除 location daemon）。保留模板中的 `memlog=1`。 |
| `THOR_RECOVERY_DTB`、`MEMORY_CONFIG`、`TOUCH_CONFIG` | 目标 Thor 的原厂 Recovery DTB，以及匹配 Thor 的内存和 FTS 触摸配置 JSON。 |

原厂镜像、固件输入、签名材料和卷布局保存在本地工作目录。公开仓库只保存源码、配置格式和验证说明。

**首次安装停点**。公开的 UFS 安装器仍锁定 Pong 的 `/dev/block/sda13` 和分区几何。Thor 板级 UFS 分支提供布局参数，仍需另行验证适用于 Thor 的首次安装流程。先准备目标机的 OS、SystemOS 和 Data，再考虑临时启动。不要去掉 Pong 安装器的设备或几何校验来写入 Thor。生产数据的备份和目标分区核验应放在后续 Thor 首次安装文档中。 [查看现有安装器校验](https://github.com/andyli386/UniXNU-thor/blob/dbda074d03e0d80ec0c3c930737e3afc649cf7ef/scripts/install-ipados18-ufs.py#L58-L66)

## 4. 构建和打包

### 准备检查和内核构建

在装配好配套源码的设备 checkout 中，第一次调用先留着 `--prepare-only`。它会校验开源依赖提交、签名二进制及本地卷配置，并生成准备报告。通过后去掉 `--prepare-only`，再开始内核编译。全新编译目前仍待独立验证。若已经持有保留的 `mach.development.pong`，可直接从预链接开始。

```sh
python3 -I scripts/build-thor-v156-macos.py --prepare-only \
  --tree "$BUILD_TREE" --references "$REFERENCES" \
  --developer-dir "$XCODE_DEVELOPER_DIR" --artifact-dir "$OUTPUTS" \
  --volume-layout "$VOLUME_LAYOUT" --config-path "$DEFCONFIG" \
  --adreno-firmware "$ADRENO_FIRMWARE" --jobs 8 \
  --thor-ui-runtime "$SIGNED_UI_RUNTIME" --thor-app-installer "$SIGNED_APP_HELPER"
```

### 使用已知内核重新打包

将 `NATIVE_KERNEL` 指到已知匹配的本地内核。如已获得匹配的预链接内核，将 `PRELINKED_KERNEL` 指向它并从 ADT 准备开始。`OUTPUTS` 和 `CELL_TOOLS` 均须使用绝对路径，`CELL_TOOLS` 应为尚不存在的工作树路径。蜂窝 ADT 工具取自单独的提交，不必为重打包合并该分支的内核补丁。

```sh
mkdir -p "$OUTPUTS"
python3 scripts/verify-thor-v156-kernel.py --kernel "$NATIVE_KERNEL"
PRELINKED_KERNEL="$OUTPUTS/kernel.macho"
python3 -I scripts/prelink-thor-kernel.py --gpu \
  --native "$NATIVE_KERNEL" --original "$DECOMPRESSED_22H374_KERNELCACHE" \
  --bindings "$LOCAL_22H374_STORAGE_BINDINGS" --output "$PRELINKED_KERNEL" \
  --plan-json "$OUTPUTS/prelink-plan.json"

git fetch origin refs/heads/thor/cellular-topology
git worktree add --detach "$CELL_TOOLS" b293a4980c120611afaaae5e17cf617158fca0cd
python3 -I scripts/prepare-thor-v156-adt.py --input "$TEMPLATE_ADT" --output "$OUTPUTS/amp.adt"
python3 "$CELL_TOOLS/scripts/prepare-thor-cellular-adt.py" \
  --fdt "$THOR_RECOVERY_DTB" --adt "$OUTPUTS/amp.adt" --output "$OUTPUTS/v156.adt"

python3 scripts/pack-thor-boot.py \
  --template "$V126_TEMPLATE" --firmware "$THOR_FD" --kernel "$PRELINKED_KERNEL" \
  --volume-layout "$VOLUME_LAYOUT" --memory-config "$MEMORY_CONFIG" \
  --touch-config "$TOUCH_CONFIG" --retained-adt "$OUTPUTS/v156.adt" \
  --boot-arg cpus=8 --boot-arg fb_hz=120 --boot-arg fb_copy=0 \
  --omit-boot-arg fbscale --boot-arg "$V156_EXEC_INJECT_ARG" \
  --software-platform-identity --menu-title 'Thor v156 Switcher and Cellular' \
  --output "$OUTPUTS/boot-thor-v156.img"
```

打包器校验 1023 字节启动参数上限、保留 ADT 的八核 E/P 描述、蜂窝拓扑和 FAT 回读。若提示 `Retained ADT belongs to a different APFS root`，应根据目标安装的 `os.uuid` 使用匹配的基础 ADT，从头生成保留 ADT。 [详细构建说明](thor-v156-build-recipe.md)

## 5. 真机验收和复现状态

目标 Thor 已准备匹配系统卷并进入 Fastboot 后，先查询产品名称。

```sh
fastboot getvar product
```

确认输出为 `product: thor` 后执行临时启动。请分别记录 Fastboot 命令结果、到达桌面的结果及功能日志。

```sh
fastboot boot "$OUTPUTS/boot-thor-v156.img"
```

| 验收项 | 应采集的证据 |
| --- | --- |
| 固件和系统卷 | UEFI / DarwinBoot 日志、OS 和 SystemOS 挂载、Data 可写、SpringBoard 到桌面。 |
| CPU 与显示 | 核对启动参数 `cpus=8`、CPU 拓扑及 E/P；采集 DPU 扫描频率和卡片回写事件。原 v156 的 DPU 实测 119.8 Hz。 |
| 运行时与应用 | 确认 UI 运行时已加载，触摸输入、卡片采样、应用安装器和系统应用实际工作。 |
| 蜂窝拓扑 | `THOR CELL` 日志中 `invalid_mask=0x0`、`vm_overlap_mask=0x0`，四个保留区通过范围检查。此项验收覆盖拓扑与内存隔离。 |

**已有证据**。macmini 完成了独立 UEFI 编译、`--prepare-only` 检查、使用保留内核的稀疏预链接和真实模板打包。预链接结果与保留的 v156 内核逐字节一致，FAT 回读的 ADT 也一致。设备日志来自原 v156 整合镜像。

**第三方从零复现的剩余工作**。发布固定所有组件与补丁顺序的集成提交；提供从本地输入生成 v126 等效模板、匹配 ADT 和 Thor 系统卷的步骤；把 UI 与应用安装器的签名校验、内核启动和信任规则一起接入；在干净 Mac 上全量构建并用另一台 Thor 完成上述验收。
