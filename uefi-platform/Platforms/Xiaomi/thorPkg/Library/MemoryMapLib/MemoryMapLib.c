// SPDX-License-Identifier: BSD-2-Clause-Patent
// Source: thor (Xiaomi 12S Ultra / SM8475) stock memory map and 1029 measurements.
// Same SoC as pong; differs in display (1440x3200 vs 1080x2412) and some reserved sizes.
#include <Library/MemoryMapLib.h>

STATIC EFI_MEMORY_REGION_DESCRIPTOR gRegions[] = {
  // DDR Regions
  {"HYP",                0x80000000, 0x00600000, AddMem, MEM_RES, SYS_MEM_CAP, Reserv, NS_DEVICE},
  {"DT BLOB",            0x80600000, 0x00040000, AddMem, MEM_RES, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},
  {"XBL RAMDUMP",        0x80640000, 0x001C0000, AddMem, MEM_RES, UNCACHEABLE, Conv,   UNCACHED_UNBUFFERED_XN},
  {"AOP",                0x80800000, 0x000A0000, AddMem, MEM_RES, UNCACHEABLE, Reserv, UNCACHED_UNBUFFERED_XN},
  {"SMEM",               0x80900000, 0x00200000, AddMem, MEM_RES, UNCACHEABLE, Reserv, UNCACHED_UNBUFFERED_XN},
  {"PIL Reserved",       0x85200000, 0x1C200000, AddMem, MEM_RES, UNCACHEABLE, Reserv, UNCACHED_UNBUFFERED_XN},
  {"FD Reserved",        0xA7000000, 0x00100000, AddMem, SYS_MEM, SYS_MEM_CAP, BsData, WRITE_BACK},
  {"UEFI FD",            0xA7100000, 0x00300000, AddMem, SYS_MEM, SYS_MEM_CAP, BsData, WRITE_BACK},
  {"UEFI FD Reserved",   0xA7400000, 0x00200000, AddMem, SYS_MEM, SYS_MEM_CAP, BsData, WRITE_BACK},
  {"Info Blk",           0xA7601000, 0x00001000, AddMem, SYS_MEM, SYS_MEM_CAP, RtData, WRITE_BACK_XN},
  {"MMU PageTables",     0xA7602000, 0x00003000, AddMem, SYS_MEM, SYS_MEM_CAP, BsData, WRITE_BACK_XN},
  {"Log Buffer",         0xA7605000, 0x00008000, AddMem, SYS_MEM, SYS_MEM_CAP, RtData, WRITE_BACK_XN},
  {"UEFI Stack",         0xA760D000, 0x00040000, AddMem, SYS_MEM, SYS_MEM_CAP, BsData, WRITE_BACK_XN},
  {"SEC Heap",           0xA764D000, 0x0008C000, AddMem, SYS_MEM, SYS_MEM_CAP, BsData, WRITE_BACK_XN},
  {"Sched Heap",         0xA76D9000, 0x00400000, AddMem, SYS_MEM, SYS_MEM_CAP, BsData, WRITE_BACK_XN},
  {"FV Region",          0xA7AD9000, 0x00400000, AddMem, SYS_MEM, SYS_MEM_CAP, BsData, WRITE_BACK_XN},
  {"Display Reserved",   0xB8000000, 0x04B00000, AddMem, MEM_RES, SYS_MEM_CAP, Reserv, WRITE_THROUGH_XN},
  {"OEM VM",             0xBB000000, 0x05000000, AddMem, SYS_MEM, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},
  {"MTE Reserved",       0xC0000000, 0x20000000, AddMem, SYS_MEM, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},
  {"High free RAM",      0x800000000,0x20000000, AddMem, SYS_MEM, SYS_MEM_CAP, Conv,   WRITE_BACK_XN},
  {"DXE Heap",           0xF8000000, 0x07C00000, AddMem, SYS_MEM, SYS_MEM_CAP, Conv,   WRITE_BACK_XN},

  // Other Memory Regions
  {"IMEM Base",          0x14680000, 0x0002A000, NoHob,  MMAP_IO, INITIALIZED, Conv,   NS_DEVICE},
  {"GICD",               0x17100000, 0x00010000, AddDev, MMAP_IO, UNCACHEABLE, MmIO,   NS_DEVICE},
  {"GICR",               0x17180000, 0x00200000, AddDev, MMAP_IO, UNCACHEABLE, MmIO,   NS_DEVICE},
};

VOID GetMemoryMap (
  OUT EFI_MEMORY_REGION_DESCRIPTOR **MemoryDescriptor,
  OUT UINT8 *MemoryDescriptorCount
  )
{
  *MemoryDescriptor = gRegions;
  *MemoryDescriptorCount = (UINT8)ARRAY_SIZE (gRegions);
}
