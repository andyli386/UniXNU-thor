##
# SPDX-License-Identifier: BSD-2-Clause-Patent
##

[Defines]
  PLATFORM_NAME                  = thor
  PLATFORM_GUID                  = C3A75F42-E18B-4D29-9A5D-7E4F3B1C6D02
  PLATFORM_VERSION               = 0.1
  DSC_SPECIFICATION              = 0x00010005
  OUTPUT_DIRECTORY               = Build/thorPkg
  SUPPORTED_ARCHITECTURES        = AARCH64
  BUILD_TARGETS                  = RELEASE|DEBUG
  SKUID_IDENTIFIER               = DEFAULT
  FLASH_DEFINITION               = thorPkg/thor.fdf

!include PalimaPkg/PalimaPkg.dsc.inc

[PcdsFixedAtBuild]
  gArmTokenSpaceGuid.PcdSystemMemoryBase|0x80000000

  gArmPlatformTokenSpaceGuid.PcdCPUCoresStackBase|0xA760D000
  gArmPlatformTokenSpaceGuid.PcdCPUCorePrimaryStackSize|0x40000

  gSiliciumPkgTokenSpaceGuid.PcdSmbiosSystemManufacturer|"Xiaomi"
  gSiliciumPkgTokenSpaceGuid.PcdSmbiosSystemModel|"12S Ultra"
  gSiliciumPkgTokenSpaceGuid.PcdSmbiosSystemRetailModel|"thor"
  gSiliciumPkgTokenSpaceGuid.PcdSmbiosSystemRetailSku|"12S_Ultra_thor"

  # Xiaomi 12S Ultra: 1440x3200 AMOLED
  gSiliciumPkgTokenSpaceGuid.PcdFrameBufferWidth|1440
  gSiliciumPkgTokenSpaceGuid.PcdFrameBufferHeight|3200
  gSiliciumPkgTokenSpaceGuid.PcdFrameBufferColorDepth|32

  gQcomPkgTokenSpaceGuid.PcdPlatformType|"LA"

[LibraryClasses]
  UniXNULib|UniXNUPkg/Library/UniXNULib/UniXNULib.inf
  MemoryMapLib|thorPkg/Library/MemoryMapLib/MemoryMapLib.inf

[Components]
  UniXNUPkg/Application/UniXNU/UniXNU.inf
  UniXNUPkg/Driver/UniXNUDxe/UniXNUDxe.inf
  DarwinBootPkg/Driver/DarwinBoot.inf
