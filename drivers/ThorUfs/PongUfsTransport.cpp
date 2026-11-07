// SPDX-License-Identifier: BSD-2-Clause-Patent
// Pong/SM8475 UFS transport. Writes are boot-arg gated and limited to the
// two validated SHNGROOT windows. Shared queue/protocol code derives
// from Sheng; board addresses and stream ID come from Pong's live DT.
#include <IOKit/IOService.h>
#include <IOKit/IOBufferMemoryDescriptor.h>
#include <IOKit/IOBSD.h>
#include <IOKit/IODeviceTreeSupport.h>
#include <IOKit/storage/IOBlockStorageDevice.h>
#include <IOKit/storage/IOMedia.h>
#include <IOKit/IOLib.h>
#include <vm/vm_kern.h>
#include <kern/clock.h>
#include <mach/mach_time.h>
#include <libkern/c++/OSBoolean.h>
#include <libkern/c++/OSDictionary.h>
#include <libkern/c++/OSIterator.h>
#include <libkern/c++/OSNumber.h>
#include <libkern/c++/OSSymbol.h>
#include <libkern/c++/OSString.h>
#include <libkern/OSKextLib.h>
#include <libkern/crypto/sha2.h>
#include <pexpert/pexpert.h>
#include <sys/param.h>
#include <sys/types.h>
#include <sys/vnode.h>
#include <sys/vnode_internal.h>
#include <sys/mount.h>
#include <sys/fcntl.h>
#include <sys/uio.h>
#include <sys/vfs_context.h>
#include <sys/proc.h>
#include <sys/proc_internal.h>
extern "C" {
#include <sys/bsdtask_info.h>
}
#include <kern/task.h>
#include <kern/thread.h>
#include <mach/arm/thread_status.h>
#include <mach/thread_act.h>
/* Declared in kern/thread.h, but behind a guard this kext build does not open. */
extern "C" kern_return_t machine_thread_get_state(thread_t, thread_flavor_t,
    thread_state_t, mach_msg_type_number_t *);
// Recorded by osfmk/arm64/sleh.c for the task the heartbeat watches: the last
// user exception and the last syscall.  A zero syscall count with a growing
// exception count separates "blocked in a syscall" from "never got there".
extern "C" {
extern void *thor_watch_task;
extern unsigned long long thor_exc_count, thor_exc_va, thor_exc_pc;
extern unsigned int thor_exc_esr, thor_exc_class;
extern unsigned long long thor_sys_count, thor_sys_num;
extern unsigned long long thor_block_count, thor_block_ra, thor_block_ra2, thor_block_cont;
extern unsigned long long thor_block_proc;
extern void *thor_watch_proc;
extern unsigned long long thor_sys_x0, thor_sys_x1, thor_sys_x2, thor_sys_x3;
extern unsigned int thor_sysctl_name[4];
extern int thor_sysctl_n;
extern unsigned long long thor_exc_all, thor_sys_all;
extern void *thor_last_exc_task;
extern void *thor_last_sys_task;
}
#include "../ThorProbe.h"

namespace {
#include "RootTransportLayout.h"
#include "RootDiagnostics.h"
#include "TransferPoll.h"
#include "SmmuTakeover.h"
static UfsRootDiagnostics gRootDiagnostic;
// Total successful reads, printed by the heartbeat: a stalled PID 1 exec and a
// busy loop look identical on screen, but only one of them keeps this moving.
static uint64_t gReadBytes;
static uint64_t gReadCount;
struct UfsHost {
  OSSharedPtr<IOMemoryMap> hcMap, smmuMap;
  uintptr_t hc = 0, smmu = 0, cb = 0;
  IOBufferMemoryDescriptor *dma = nullptr;
  uint8_t *virt = nullptr;
  uint64_t phys = 0, blocks = 0;
  IOLock *lock = nullptr;
  bool ready = false, poisoned = false;
  bool writable = false;
};
static UfsHost gHost;
static bool gTransportAttempted;
enum : uint64_t {
  // Pong LUN0 GPT entry "userdata", captured from this phone in recovery.
  kPongUserdataFirst4k = 0x413248ULL,
  kPongUserdataLast4k = 0x75a40eaULL,
  kRootLogicalBlock = 512,
};
struct RootDiskWindow {
  const char *name;
  const char *product;
  const char *signature;
  unsigned signatureOffset;
  uint64_t baseByte;
  uint64_t size;
};
static RootDiskWindow gRootWindows[2];
static unsigned gRootWindowCount;
static uint32_t read32(uintptr_t base, unsigned offset) {
  return *reinterpret_cast<volatile uint32_t *>(base + offset);
}
static void write32(uintptr_t base, unsigned offset, uint32_t value) {
  __asm__ volatile("dsb sy" ::: "memory");
  *reinterpret_cast<volatile uint32_t *>(base + offset) = value;
  __asm__ volatile("dsb sy" ::: "memory");
}
static uintptr_t mapPhys(uint64_t base, uint64_t size, OSSharedPtr<IOMemoryMap> &keep) {
  OSSharedPtr<IOMemoryDescriptor> memory(IOMemoryDescriptor::withPhysicalAddress(base, size, kIODirectionInOut), OSNoRetain);
  if (memory) keep = OSSharedPtr<IOMemoryMap>(memory->map(kIOMapAnywhere | kIOMapInhibitCache), OSNoRetain);
  return keep ? keep->getVirtualAddress() : 0;
}
static IOReturn recordTransport(IOReturn result, uint32_t ocs) {
  gRootDiagnostic.result = static_cast<uint32_t>(result); gRootDiagnostic.ocs = ocs;
  gRootDiagnostic.hcs = read32(gHost.hc, 0x30);
  gRootDiagnostic.hce = read32(gHost.hc, 0x34);
  gRootDiagnostic.doorbell = read32(gHost.hc, 0x58);
  gRootDiagnostic.interrupt = read32(gHost.hc, 0x20);
  gRootDiagnostic.list = uint64_t(read32(gHost.hc, 0x50)) | (uint64_t(read32(gHost.hc, 0x54)) << 32);
  gRootDiagnostic.runStop = read32(gHost.hc, 0x60);
  gRootDiagnostic.ahit = read32(gHost.hc, 0x18);
  for (unsigned i = 0; i < 5; ++i) gRootDiagnostic.uic[i] = read32(gHost.hc, 0x38 + i * 4);
  if (gHost.smmu && gHost.cb) {
    gRootDiagnostic.gfsr = read32(gHost.smmu, 0x48);
    gRootDiagnostic.gsyn = read32(gHost.smmu, 0x54);
    gRootDiagnostic.fsr = read32(gHost.cb, 0x58);
    gRootDiagnostic.fsyn = read32(gHost.cb, 0x68);
    gRootDiagnostic.far = uint64_t(read32(gHost.cb, 0x60)) | (uint64_t(read32(gHost.cb, 0x64)) << 32);
  }
  return result;
}
static void cacheCivac(const void *start, size_t size) {
  uintptr_t end = reinterpret_cast<uintptr_t>(start) + size;
  for (uintptr_t at = reinterpret_cast<uintptr_t>(start) & ~uintptr_t(63); at < end; at += 64)
    __asm__ volatile("dc civac, %0" :: "r"(at) : "memory");
  __asm__ volatile("dsb sy" ::: "memory");
}
static void cacheInvalidate(const void *start, size_t size) {
  __asm__ volatile("dsb sy" ::: "memory");
  uintptr_t end = reinterpret_cast<uintptr_t>(start) + size;
  for (uintptr_t at = reinterpret_cast<uintptr_t>(start); at < end; at += 64)
    __asm__ volatile("dc ivac, %0" :: "r"(at) : "memory");
  __asm__ volatile("dsb sy" ::: "memory");
}
static void hostWrite(unsigned offset, uint32_t value) { write32(gHost.hc, offset, value); }
#include "RootQueue.h"
#include "RootCommands.h"
#include "RootLayout.h"

// Late-readable copy of the board picture: the root-mount panic is the one
// place every failed boot reaches, so the census prints from here.
static RootPartition gPartitions[64];
static unsigned gPartitionCount;
static bool gGptRead;
static const char *gTransportWhy = "not started";
static unsigned gStorageAbiMismatch;

static bool transportFailure(const char *why) {
  gHost.ready = false;
  gTransportWhy = why;
  // No panic, reset, retry or DMA release after a timeout. The controller
  // may still own the allocation. Preserve evidence and let BSD continue.
  char text[2400];
  formatUfsRootDiagnostics(text, sizeof(text), gRootDiagnostic, why, "PONG_UFS TRANSPORT");
  IOLog("%s\nPONG_UFS: no media published; continue boot\n", text);
  PongProbeRecord(4, "ufs-transport", PongProbeFail, why, gRootDiagnostic.result);
  snprintf(text, sizeof(text), "stage=%s op=%x ocs=%x scsi=%x protocol=%u", gRootDiagnostic.stage,
           gRootDiagnostic.opcode, gRootDiagnostic.ocs, gRootDiagnostic.scsi, gRootDiagnostic.protocol);
  PongProbeRecord(5, "ufs-command", PongProbeFail, text, gRootDiagnostic.dma);
  snprintf(text, sizeof(text), "hcs=%x db=%x is=%x fsr=%x syn=%x", gRootDiagnostic.hcs,
           gRootDiagnostic.doorbell, gRootDiagnostic.interrupt, gRootDiagnostic.fsr, gRootDiagnostic.fsyn);
  PongProbeRecord(6, "ufs-hardware", PongProbeFail, text, gRootDiagnostic.far);
  return false;
}

static bool hostBringUp() {
  uint32_t requestWrites = 0;
  gHost.writable = PE_parse_boot_argn("pong_ufs_readwrite", &requestWrites, sizeof(requestWrites)) &&
      requestWrites == 1;
  IOLog("PONG_UFS: storage write path %s by pong_ufs_readwrite boot-arg\n",
        gHost.writable ? "enabled" : "disabled");
  gRootDiagnostic.stage = "controller map";
  gHost.hc = mapPhys(0x01d84000, 0x3000, gHost.hcMap);
  if (!gHost.hc) return transportFailure("controller map");
  gRootDiagnostic.cap = read32(gHost.hc, 0);
  gRootDiagnostic.version = read32(gHost.hc, 8);
  recordTransport(kIOReturnNotReady, 15);
  gRootDiagnostic.stage = "DMA allocation";
  gHost.dma = IOBufferMemoryDescriptor::inTaskWithOptions(kernel_task,
    IOOptionBits(kIODirectionInOut) | IOOptionBits(kIOMemoryPhysicallyContiguous) | IOOptionBits(kIOMemoryMapperNone), kDmaBytes, 4096);
  gHost.lock = IOLockAlloc();
  if (!gHost.dma || !gHost.lock) return transportFailure("DMA allocation");
  gHost.virt = static_cast<uint8_t *>(gHost.dma->getBytesNoCopy());
  IOByteCount contiguous = 0;
  gHost.phys = gHost.dma->getPhysicalSegment(0, &contiguous, kIOMemoryMapperNone);
  gRootDiagnostic.dma = gHost.phys; gRootDiagnostic.contiguous = contiguous;
  if (!gHost.virt || !gHost.phys || (gHost.phys & 4095) || contiguous < kDmaBytes ||
      (!(gRootDiagnostic.cap & 0x01000000) && gHost.phys + kDmaBytes > 0x100000000ULL))
    return transportFailure("DMA address/contiguity");
  bzero(gHost.virt, kDmaBytes); cacheCivac(gHost.virt, kDmaBytes);
  gRootDiagnostic.stage = "quiesce firmware queue";
  const char *why = nullptr;
  if (!hostStopQueue(&why)) return transportFailure(why);
  gRootDiagnostic.stage = "SMMU plan";
  gHost.smmu = mapPhys(0x15000000, 0x100000, gHost.smmuMap);
  if (!gHost.smmu) return transportFailure("SMMU map");
  auto read = [](unsigned offset) { return read32(gHost.smmu, offset); };
  UfsSmmuPlan plan = planUfsSmmu(0xe0, gHost.phys, kDmaBytes, read, 78, 120);
  gRootDiagnostic.smmuId1 = read(0x24); gRootDiagnostic.smmuId2 = read(0x28);
  gRootDiagnostic.smmuSlot = plan.slot; gRootDiagnostic.smr = plan.smr; gRootDiagnostic.s2cr = plan.s2cr;
  unsigned rawBanks = gRootDiagnostic.smmuId1 & 255, rawGroups = read(0x20) & 255;
  IOLog("PONG_UFS: SMMU id0=%08x id1=%08x id2=%08x HW banks=%u groups=%u effective banks=%u groups=%u\n",
        read(0x20), gRootDiagnostic.smmuId1, gRootDiagnostic.smmuId2, rawBanks, rawGroups,
        rawBanks < 78 ? rawBanks : 78, rawGroups < 120 ? rawGroups : 120);
  if (plan.error) return transportFailure(plan.error);
  IOLog("PONG_UFS: SID e0 slot=%u firmware-bank=%u target-bank=%u action=%s\n",
        plan.slot, plan.previousBank, plan.bank,
        plan.migrate ? "migrate exact firmware mapping" : plan.reuse ? "reuse bypass" : "install exact mapping");
  gHost.cb = gHost.smmu + (plan.pages + plan.bank) * 4096;
  gRootDiagnostic.stage = "SMMU stream configuration";
  why = configureUfsSmmu(plan, 0xe0, read, [](unsigned offset, uint32_t value) { write32(gHost.smmu, offset, value); });
  gRootDiagnostic.smr = read(0x800 + 4 * plan.slot); gRootDiagnostic.s2cr = read(0xc00 + 4 * plan.slot);
  gRootDiagnostic.cbar = read(0x1000 + 4 * plan.bank); gRootDiagnostic.cba2r = read(0x1800 + 4 * plan.bank);
  gRootDiagnostic.sctlr = read32(gHost.cb, 0);
  if (why) return transportFailure(why);
  gRootDiagnostic.stage = "request list start";
  if (!hostStartQueue(&why)) return transportFailure(why);
  gRootDiagnostic.stage = "NOP OUT";
  if (hostNop() != kIOReturnSuccess) return transportFailure("NOP OUT");
  uint32_t power = 0, scsi = 0;
  gRootDiagnostic.stage = "power mode query";
  if (hostQueryRead(3, 2, &power) != kIOReturnSuccess) return transportFailure("power mode query");
  gRootDiagnostic.power = power;
  if ((power & 0xff) != 0x11) {
    gRootDiagnostic.stage = "wake device";
    uint8_t ssu[6] = {0x1b, 0, 0, 0, 0x10, 0};
    if (hostScsi(0xd0, ssu, 6, false, 0, 500000, &scsi) != kIOReturnSuccess) return transportFailure("START STOP UNIT");
    IOSleep(50);
  }
  gRootDiagnostic.stage = "LUN 0 ready";
  for (unsigned attempt = 0; attempt < 20 && !gHost.poisoned; ++attempt) {
    uint8_t tur[6] = {};
    if (hostScsi(0, tur, 6, false, 0, 200000, &scsi) == kIOReturnSuccess) { gHost.ready = true; break; }
    IOSleep(10);
  }
  if (!gHost.ready) return transportFailure("LUN 0 not ready");
  gRootDiagnostic.stage = "READ CAPACITY";
  uint8_t capacity[10] = {0x25};
  if (hostScsi(0, capacity, 10, true, 8, 200000, &scsi) != kIOReturnSuccess) return transportFailure("READ CAPACITY");
  uint32_t last = loadBe32(gHost.virt + kDataOffset), blockSize = loadBe32(gHost.virt + kDataOffset + 4);
  if (!last || last == 0xffffffff || blockSize != 4096) return transportFailure("unsupported LUN geometry");
  gHost.blocks = uint64_t(last) + 1;
  IOLog("PONG_UFS: transport ready LUN=0 block=4096 blocks=%llu; WRITE(10) FUA=%s; writes stay inside OS/SystemOS windows\n",
        gHost.blocks, gHost.writable ? "enabled" : "disabled");
  return true;
}
}

class PongUfsBlock : public IOBlockStorageDevice {
  OSDeclareDefaultStructors(PongUfsBlock)
  uint8_t *bounce = nullptr;
  IOLock *mutex = nullptr;
  bool online = false;
  RootDiskWindow window = {};
public:
  bool init(OSDictionary *properties, const RootDiskWindow &selected);
  bool start(IOService *provider) override;
  void free() override;
  IOReturn doEjectMedia() override { return kIOReturnUnsupported; }
  IOReturn doFormatMedia(UInt64) override { return kIOReturnNotWritable; }
  UInt32 doGetFormatCapacities(UInt64 *, UInt32) const override { return 0; }
  char *getVendorString() override { return const_cast<char *>("Pong"); }
  char *getProductString() override { return const_cast<char *>(window.product); }
  char *getRevisionString() override { return const_cast<char *>("1"); }
  char *getAdditionalDeviceInfoString() override { return const_cast<char *>(window.name); }
  IOReturn reportBlockSize(UInt64 *v) override { *v = kRootLogicalBlock; return kIOReturnSuccess; }
  IOReturn reportMaxValidBlock(UInt64 *v) override { *v = window.size / kRootLogicalBlock - 1; return kIOReturnSuccess; }
  IOReturn reportEjectability(bool *v) override { *v = false; return kIOReturnSuccess; }
  IOReturn reportRemovability(bool *v) override { *v = false; return kIOReturnSuccess; }
  IOReturn reportWriteProtection(bool *v) override {
    *v = !gHost.writable || (window.baseByte & 4095) || (window.size & 4095);
    return kIOReturnSuccess;
  }
  IOReturn reportMediaState(bool *v, bool *changed) override { *v = online && gHost.ready; if (changed) *changed = false; return kIOReturnSuccess; }
  IOReturn doSynchronizeCache() override { return gHost.ready ? kIOReturnSuccess : kIOReturnNotReady; }
  IOReturn doSynchronize(UInt64, UInt64, IOOptionBits) override { return doSynchronizeCache(); }
  IOReturn getWriteCacheState(bool *v) override { *v = false; return kIOReturnSuccess; }
  IOReturn setWriteCacheState(bool) override { return kIOReturnNotWritable; }
  IOReturn doAsyncReadWrite(IOMemoryDescriptor *, UInt64, UInt64, IOStorageAttributes *, IOStorageCompletion *) override;
};
OSDefineMetaClassAndStructors(PongUfsBlock, IOBlockStorageDevice)

// If an expected APFS volume is absent, list only IOMedia objects beneath the
// selected UFS window. This keeps the failure report specific to the
// SystemOS image and reveals whether the UUID, the volume, or its ancestry is
// the mismatch.
static unsigned native_pong_log_ufs_window_media(const char *windowKey) {
  OSDictionary *matching = IOService::serviceMatching("IOMedia");
  OSIterator *iterator = matching ? IOService::getMatchingServices(matching) : nullptr;
  OSSafeReleaseNULL(matching);
  if (!iterator) {
    IOLog("PONG APFS: window media enumeration unavailable window=%s\n", windowKey);
    return 0;
  }

  unsigned candidates = 0;
  while (OSObject *object = iterator->getNextObject()) {
    IOService *media = OSDynamicCast(IOService, object);
    if (!media) continue;

    bool targetWindow = false;
    bool readOnlyWindow = false;
    bool writableWindow = false;
    unsigned depth = 0;
    for (IOService *provider = media; provider && depth < 64;
         provider = provider->getProvider(), ++depth) {
      PongUfsBlock *block = OSDynamicCast(PongUfsBlock, provider);
      if (!block) continue;
      targetWindow = block->getProperty(windowKey) == kOSBooleanTrue;
      readOnlyWindow = block->getProperty("PongReadOnly") == kOSBooleanTrue;
      writableWindow = block->getProperty("PongWritable") == kOSBooleanTrue;
      break;
    }
    if (!targetWindow) continue;

    ++candidates;
    OSString *uuid = OSDynamicCast(OSString, media->getProperty("UUID"));
    OSString *name = OSDynamicCast(OSString, media->getProperty(kIOBSDNameKey));
    IOLog("PONG APFS: window media uuid=%s bsd=%s readonly=%d writable=%d depth=%u\n",
          uuid ? uuid->getCStringNoCopy() : "?",
          name ? name->getCStringNoCopy() : "?", readOnlyWindow, writableWindow, depth);
  }
  iterator->release();
  IOLog("PONG APFS: window media count=%u window=%s\n", candidates, windowKey);
  return candidates;
}

// Development APFS handoff: resolve a unique APFS IOMedia by its observed
// volume UUID and prove that it descends from the matching SHNGROOT UFS window.
// This is device ancestry, not cryptographic image authentication.
extern "C" int native_pong_ufs_volume_device(const char *uuid, int systemOS,
                                               dev_t *device, char *path, size_t pathSize) {
  if (!uuid || strlen(uuid) != 36 || !device || !path || pathSize < 8) return 0;
  *device = NODEV;
  path[0] = 0;

  OSDictionary *matching = IOService::serviceMatching("IOMedia");
  OSDictionary *properties = OSDictionary::withCapacity(1);
  OSString *wanted = OSString::withCString(uuid);
  if (!matching || !properties || !wanted) {
    OSSafeReleaseNULL(wanted);
    OSSafeReleaseNULL(properties);
    OSSafeReleaseNULL(matching);
    return 0;
  }
  bool ready = properties->setObject("UUID", wanted) &&
      matching->setObject("IOPropertyMatch", properties);
  wanted->release();
  properties->release();
  OSIterator *iterator = ready ? IOService::getMatchingServices(matching) : nullptr;
  if (!iterator && ready && systemOS) {
    IOLog("PONG APFS: wait for SystemOS UUID %.36s, timeout=5s\n", uuid);
    auto published = IOService::waitForMatchingService(matching, 5ULL * 1000000000ULL);
    if (published) {
      IOLog("PONG APFS: SystemOS UUID appeared; rechecking IOMedia\n");
      iterator = IOService::getMatchingServices(matching);
    } else {
      IOLog("PONG APFS: SystemOS UUID wait timed out\n");
    }
  }
  matching->release();
  if (!iterator) {
    IOLog("PONG APFS: UUID lookup unavailable uuid=%.36s window=%s\n", uuid,
          systemOS ? "SystemOS" : "OS");
    if (systemOS) native_pong_log_ufs_window_media("PongSystemOSWindow");
    return 0;
  }

  unsigned matches = 0;
  bool accepted = false;
  dev_t selected = NODEV;
  char selectedPath[64] = {};
  while (OSObject *object = iterator->getNextObject()) {
    IOService *media = OSDynamicCast(IOService, object);
    if (!media) continue;
    ++matches;
    if (matches != 1) { accepted = false; break; }
    OSString *identity = OSDynamicCast(OSString, media->getProperty("UUID"));
    OSString *name = OSDynamicCast(OSString, media->getProperty(kIOBSDNameKey));
    OSNumber *major = OSDynamicCast(OSNumber, media->getProperty(kIOBSDMajorKey));
    OSNumber *minor = OSDynamicCast(OSNumber, media->getProperty(kIOBSDMinorKey));
    if (!identity || strcmp(identity->getCStringNoCopy(), uuid) || !name || !major || !minor ||
        !name->getLength() || name->getLength() >= sizeof(selectedPath) - 6 ||
        name->getLength() >= pathSize - 6 || major->unsigned64BitValue() > 255 ||
        minor->unsigned64BitValue() > 0xffffff) {
      IOLog("PONG APFS: UUID candidate malformed uuid=%.36s identity=%d bsd=%d major=%d minor=%d\n",
            uuid, identity != nullptr, name != nullptr, major != nullptr, minor != nullptr);
      continue;
    }

    bool validWindow = false;
    bool foundUfsWindow = false;
    unsigned depth = 0;
    for (IOService *provider = media; provider && depth < 64;
         provider = provider->getProvider(), ++depth) {
      PongUfsBlock *block = OSDynamicCast(PongUfsBlock, provider);
      if (!block) continue;
      foundUfsWindow = true;
      const char *windowKey = systemOS ? "PongSystemOSWindow" : "PongRootWindow";
      validWindow = block->getProperty(windowKey) == kOSBooleanTrue &&
          block->getProperty("PongUfsWindow") == kOSBooleanTrue;
      break;
    }
    if (!validWindow) {
      IOLog("PONG APFS: UUID candidate rejected uuid=%.36s bsd=%s ufs-window=%d depth=%u window=%s\n",
            uuid, name->getCStringNoCopy(), foundUfsWindow, depth,
            systemOS ? "SystemOS" : "OS");
      continue;
    }

    selected = static_cast<dev_t>(makedev(major->unsigned32BitValue(), minor->unsigned32BitValue()));
    snprintf(selectedPath, sizeof(selectedPath), "/dev/%s", name->getCStringNoCopy());
    accepted = true;
  }
  iterator->release();
  if (!accepted || matches != 1) {
    IOLog("PONG APFS: UUID lookup miss wanted=%.36s matches=%u accepted=%d window=%s\n",
          uuid, matches, accepted, systemOS ? "SystemOS" : "OS");
    if (systemOS) native_pong_log_ufs_window_media("PongSystemOSWindow");
    return 0;
  }
  *device = selected;
  strlcpy(path, selectedPath, pathSize);
  IOLog("PONG APFS: UUID %.36s bound to %s UFS window %s\n", uuid,
        systemOS ? "SystemOS" : "OS", selectedPath);
  return 1;
}

bool PongUfsBlock::init(OSDictionary *properties, const RootDiskWindow &selected) {
  if (!selected.name || !selected.product || !selected.signature || !selected.size ||
      (selected.baseByte & 4095) || (selected.size & 511) ||
      !IOBlockStorageDevice::init(properties)) return false;
  window = selected;
  return true;
}
bool PongUfsBlock::start(IOService *provider) {
  bounce = static_cast<uint8_t *>(IOMalloc(4096)); mutex = IOLockAlloc();
  if (!bounce || !mutex || !gHost.ready || !window.size || !IOBlockStorageDevice::start(provider)) return false;
  gRootDiagnostic.stage = "first root read";
  if (hostRead4k(0, window.baseByte / 4096, 1, bounce) != kIOReturnSuccess)
    return transportFailure("first root read");
  // A stored image carries the configured signature; a stock iOS partition
  // is usually a bare APFS container (NXSB at block offset 32).
  if (memcmp(bounce + window.signatureOffset, window.signature, strlen(window.signature)) &&
      memcmp(bounce + 32, "NXSB", 4) && memcmp(bounce + 512, "EFI PART", 8))
    return transportFailure("root window signature mismatch");
  gRootDiagnostic.first = window.baseByte / 4096;
  gRootDiagnostic.last = (window.baseByte + window.size) / 4096 - 1;
  gRootDiagnostic.windowBytes = window.size;
  const bool writableWindow = gHost.writable && !(window.baseByte & 4095) && !(window.size & 4095);
  IOLog("PONG_UFS: SHNGROOT %s first read complete signature=1 base=%llx bytes=%llx; publish 512-byte %s disk\n",
        window.name, window.baseByte, window.size, writableWindow ? "read-write" : "read-only");
  setProperty("PongReadOnly", !writableWindow);
  setProperty("PongWritable", writableWindow);
  setProperty("PongUfsWindow", true);
  setProperty("PongRootWindow", !strcmp(window.name, "OS"));
  setProperty("PongSystemOSWindow", !strcmp(window.name, "SystemOS"));
  setProperty("Physical Interconnect", "UFS");
  online = true; registerService();
  char detail[96];
  snprintf(detail, sizeof(detail), "SHNGROOT %s disk published", window.name);
  PongProbeRecord(4, "ufs-transport", PongProbePass, detail, window.size);
  return true;
}
void PongUfsBlock::free() {
  if (bounce) IOFree(bounce, 4096);
  if (mutex) IOLockFree(mutex);
  IOBlockStorageDevice::free();
}
IOReturn PongUfsBlock::doAsyncReadWrite(IOMemoryDescriptor *buffer, UInt64 block, UInt64 count,
                                      IOStorageAttributes *, IOStorageCompletion *completion) {
  const uint64_t rootBlocks = window.size / kRootLogicalBlock;
  const bool write = buffer && buffer->getDirection() == kIODirectionOut;
  // Entry marker: proves the mount path reaches this device at all, even when
  // every later stage fails. Printed once per boot.
  {
    static bool entered;
    if (!entered) {
      entered = true;
      printf("THOR UFS-RD: entry block=%llu count=%llu write=%u\n", block, count, write ? 1u : 0u);
    }
  }
  if (!buffer || !completion || !completion->action || !count || block >= rootBlocks ||
      count > rootBlocks - block || count > UINT64_MAX / kRootLogicalBlock ||
      buffer->getLength() < count * kRootLogicalBlock ||
      (buffer->getDirection() != kIODirectionIn && !write)) {
    // Rejecting here skips transportFailure() and the read counters, so a
    // page-in turned away as a bad argument used to be completely invisible.
    static bool badArgLogged;
    if (!badArgLogged) {
      badArgLogged = true;
      printf("THOR UFS-BA: block=%llu count=%llu root=%llu write=%u len=%llu dir=%u buf=%d comp=%d act=%d\n",
             (unsigned long long)block, (unsigned long long)count,
             (unsigned long long)rootBlocks, write ? 1u : 0u,
             buffer ? (unsigned long long)buffer->getLength() : 0ull,
             buffer ? static_cast<unsigned>(buffer->getDirection()) : 0u,
             buffer != nullptr, completion != nullptr,
             completion && completion->action ? 1 : 0);
    }
    return kIOReturnBadArgument;
  }
  if (write && (!gHost.writable || (window.baseByte & 4095) || (window.size & 4095)))
    return kIOReturnNotWritable;
  if (!online || !gHost.ready) {
    static bool notReadyLogged;
    if (!notReadyLogged) {
      notReadyLogged = true;
      printf("THOR UFS-NR: online=%d ready=%u block=%llu count=%llu\n",
             online ? 1 : 0, gHost.ready ? 1u : 0u,
             (unsigned long long)block, (unsigned long long)count);
    }
    return kIOReturnNotReady;
  }
  uint64_t done = 0, bytes = count * kRootLogicalBlock;
  IOReturn result = kIOReturnSuccess;
  IOLockLock(mutex);
  while (done < bytes) {
    const uint64_t absolute = window.baseByte + block * kRootLogicalBlock + done;
    const uint32_t inside = static_cast<uint32_t>(absolute & 4095);
    uint64_t wanted = bytes - done;
    if (wanted > kDataMax - inside) wanted = kDataMax - inside;
    uint32_t physicalBlocks = static_cast<uint32_t>((inside + wanted + 4095) / 4096);
    uint64_t available = uint64_t(physicalBlocks) * 4096 - inside;
    uint64_t take = wanted < available ? wanted : available;
    if (write) {
      uint64_t writeStart = absolute & ~UINT64_C(4095);
      uint64_t writeEnd = writeStart + uint64_t(physicalBlocks) * 4096;
      uint64_t windowEnd = window.baseByte + window.size;
      if (writeStart < window.baseByte || writeEnd < writeStart || writeEnd > windowEnd) {
        result = kIOReturnNotWritable;
        break;
      }
    }
    IOLockLock(gHost.lock);
    if (!gHost.ready || gHost.poisoned) {
      result = kIOReturnNotReady;
    } else {
      // Prepared storage descriptors can copy directly to/from the shared
      // payload. Keep ownership until the copy and any RMW write complete.
      // Full physical-block writes need no read of the old contents.
      if (!write || inside || take != uint64_t(physicalBlocks) * 4096)
        result = hostRead4kLocked(0, absolute / 4096, physicalBlocks);
      if (result == kIOReturnSuccess && write && !hostIdle()) result = kIOReturnNotReady;
      if (result == kIOReturnSuccess) {
        uint8_t *data = gHost.virt + kDataOffset + inside;
        uint64_t copied = write ? buffer->readBytes(done, data, take) : buffer->writeBytes(done, data, take);
        if (copied != take) result = kIOReturnUnderrun;
        else if (write) result = hostWrite4kLocked(0, absolute / 4096, physicalBlocks);
      }
    }
    IOLockUnlock(gHost.lock);
    if (result != kIOReturnSuccess) break;
    done += take;
  }
  IOLockUnlock(mutex);
  if (result != kIOReturnSuccess && result != kIOReturnNotWritable)
    transportFailure(write ? "block write failed" : "block read failed");
  // Heartbeat: one line per 128 MiB proves a long apfs root-hash validation is
  // still making progress instead of deadlocking on a single command.
  if (result == kIOReturnSuccess && !write) {
    static bool first = true;
    uint64_t before = gReadBytes;
    gReadBytes += done;
    ++gReadCount;
    {
      // Page-sized and smaller reads are the page-in path; log the first few
      // so a cluster_io that never reaches the device is easy to tell apart
      // from one that does.
      static int smallN;
      if (count <= 128 && smallN < 12) {
        ++smallN;
        printf("THOR UFS-PG: #%d block=%llu count=%llu bytes=%llu\n", smallN,
               (unsigned long long)block, (unsigned long long)count,
               (unsigned long long)done);
      }
    }
    if (first) {
      first = false;
      printf("THOR UFS-RD: first read %llu bytes at block %llu\n", done, block);
    } else if ((before >> 27) != (gReadBytes >> 27)) {
      printf("THOR UFS-RD: %llu MiB read\n", gReadBytes >> 20);
    }
  }
  completion->action(completion->target, completion->parameter, result, done);
  return kIOReturnSuccess;
}
// Ask VFS directly where /private/var lives. The apfs mountroot log only proves
// the volume group was accepted; it says nothing about whether the Data volume
// is mounted, and "Failed to open swap file 30" (EROFS) could mean either that
// the data volume is missing or that this path still resolves to the read-only
// root. One line, once, after the mount has had time to settle.
static void pongVarMountProbe(void) {
  // Is userspace alive at all? The kernel prints nothing when load_init_program
  // succeeds, so "no error" in the log does not mean launchd is running. PID 1
  // existing is the only cheap proof, and it decides whether /private/var is
  // meant to be mounted by launchd or by the kernel.
  proc_t init = proc_find(1);
  if (init) {
    printf("THOR VARMNT: pid1 comm=%s stat=%d\n", init->p_comm, init->p_stat);
    proc_rele(init);
  } else {
    printf("THOR VARMNT: pid1 absent\n");
  }
  // Did PID 1 ever execute a user instruction? proc_taskinfo counts Unix
  // syscalls and faults without touching thread internals — the thread-state
  // APIs panicked in zalloc, and a fault loop at the entry point looks exactly
  // like "alive and stuck" from the outside.
  if (init) {
    struct proc_taskinfo_internal info;
    memset(&info, 0, sizeof(info));
    fill_taskprocinfo(proc_task(init), &info);
    printf("THOR PID1TASK: unix=%d faults=%d pageins=%d csw=%d threads=%d usr=%llu sys=%llu\n",
           info.pti_syscalls_unix, info.pti_faults, info.pti_pageins, info.pti_csw,
           info.pti_threadnum,
           (unsigned long long)info.pti_total_user,
           (unsigned long long)info.pti_total_system);
  }
  vfs_context_t ctx = vfs_context_kernel();
  const char *paths[] = { "/", "/private/var", "/private/var/vm",
                          "/private/preboot", "/private/preboot/System" };
  for (unsigned i = 0; i < sizeof(paths) / sizeof(paths[0]); ++i) {
    vnode_t vp = NULLVP;
    int err = vnode_lookup(paths[i], 0, &vp, ctx);
    if (err) {
      printf("THOR VARMNT: %s lookup err=%d\n", paths[i], err);
      continue;
    }
    mount_t mp = vnode_mount(vp);
    struct vfsstatfs *sb = mp ? vfs_statfs(mp) : nullptr;
    printf("THOR VARMNT: %s from=%s on=%s rdonly=%d\n", paths[i],
           sb ? sb->f_mntfromname : "?", sb ? sb->f_mntonname : "?",
           (mp && vfs_isrdonly(mp)) ? 1 : 0);
    vnode_put(vp);
  }
  // Reproduce the init exec step by step. PID 1 spins inside exec with no I/O
  // at all by 60 s, so the failing call cannot be a plain read; each step gets
  // its own line first, because the one that hangs never prints its result.
  struct { const char *path; } steps[] = {
    { "/sbin" }, { "/sbin/launchd" }, { "/usr/lib/dyld" }, { "/etc/passwd" },
    // What launchd needs right after libignition: the job cache, the daemon
    // directory and the dyld shared cache it failed to start a region for.
    { "/System/Library/xpc/launchd.plist" },
    { "/System/Library/LaunchDaemons" },
    { "/System/Library/Caches/com.apple.dyld" },
    { "/private/preboot" },
    // Did PID 1 manage to mount the cryptex, and is the shared cache actually
    // reachable through the path the kernel and dyld look at?
    { "/private/preboot/System/Library/Caches/com.apple.dyld" },
    { "/private/preboot/System/Library/Caches/com.apple.dyld/dyld_shared_cache_arm64" },
  };
  for (unsigned i = 0; i < sizeof(steps) / sizeof(steps[0]); ++i) {
    vnode_t vp = NULLVP;
    printf("THOR VARMNT: step lookup %s\n", steps[i].path);
    int err = vnode_lookup(steps[i].path, 0, &vp, ctx);
    printf("THOR VARMNT: step %s err=%d\n", steps[i].path, err);
    if (err || !vp) continue;
    vnode_put(vp);
  }
}
// Reproduce PID 1's second syscall in a thread of our own. proc_taskinfo says
// PID 1 completed exactly one Unix syscall (chdir) and then burned kernel time
// on the next one, which for this init is stat("/"); a getattr that has to be
// observed from a thread that cannot take the heartbeat down with it.
static void pongGetattrProbe(void *, wait_result_t) {
  IOSleep(50000);
  vfs_context_t ctx = vfs_context_kernel();
  vnode_t vp = NULLVP;
  printf("THOR GATTR: begin\n");
  if (vnode_lookup("/", 0, &vp, ctx) != 0 || !vp) {
    printf("THOR GATTR: lookup failed\n");
    return;
  }
  struct vnode_attr va;
  VATTR_INIT(&va);
  VATTR_WANTED(&va, va_mode);
  VATTR_WANTED(&va, va_data_size);
  printf("THOR GATTR: calling vnode_getattr on /\n");
  int err = vnode_getattr(vp, &va, ctx);
  printf("THOR GATTR: err=%d mode=%o size=%lld\n", err, (unsigned)va.va_mode,
         (long long)va.va_data_size);
  vnode_put(vp);
}

// Pre-load the init's own pages into the UBC before PID 1 faults on them.
// Its first missing page never completes (the data request reaches the pager,
// returns success, and no I/O is ever issued), so the only way past it without
// fixing the pager is to make the page resident first.
static void pongPrewarm(void) {
  vfs_context_t ctx = vfs_context_kernel();
  vnode_t vp = NULLVP;
  int err = vnode_open("/sbin/launchd", O_RDONLY, 0, 0, &vp, ctx);
  if (err || !vp) {
    printf("THOR WARM: open err=%d\n", err);
    return;
  }
  char buf[4096];
  int total = 0;
  err = 0;
  for (int i = 0; i < 16; i++) {
    int resid = 0;
    err = vn_rdwr(UIO_READ, vp, buf, (int)sizeof(buf), (off_t)(i * sizeof(buf)),
                  UIO_SYSSPACE, IO_NODELOCKED, vfs_context_ucred(ctx), &resid, NULL);
    if (err) break;
    total += (int)sizeof(buf) - resid;
    if (resid) break;
  }
  printf("THOR WARM: read=%d err=%d\n", total, err);
  vnode_close(vp, FREAD, ctx);
}

// The kernel now reaches launchd and keeps running, so the failure census that
// only printed on a root-mount panic never shows. A slow heartbeat makes the
// live state visible in any photograph.
static void pongStatusLoop(void *, wait_result_t) {
  unsigned ticks = 0;
  for (;;) {
    IOSleep(15000);
    ++ticks;
    // Re-arm the sleh recorder every tick from PID 1's own proc: the first
    // arm point (inside load_init_program) still sees the kernel thread's
    // task, and exec can only be trusted once the proc owns it.
    {
      proc_t p = proc_find(1);
      if (p) {
        thor_watch_task = (void *)proc_task(p);
        thor_watch_proc = (void *)p;
        proc_rele(p);
      }
    }
    // ~60 s in: bsd_init, the swap-file attempt and launchd's first exec have
    // all happened by then, so the mount picture is final.
    if (ticks == 2) pongPrewarm();
    if (ticks == 4) pongVarMountProbe();
    // Every fourth tick (~60 s): does PID 1 keep faulting, or is it parked?
    // A frozen fault/pagein/unix triple means blocked; a moving one means loop.
    if (ticks > 4 && (ticks % 4) == 0) {
      proc_t init = proc_find(1);
      if (init) {
        struct proc_taskinfo_internal info;
        memset(&info, 0, sizeof(info));
        fill_taskprocinfo(proc_task(init), &info);
        thor_watch_task = (void *)proc_task(init);
        thor_watch_proc = (void *)init;
        printf("THOR PID1TASK: unix=%d mach=%d faults=%d pageins=%d csw=%d thr=%d stat=%d\n",
               info.pti_syscalls_unix, info.pti_syscalls_mach, info.pti_faults,
               info.pti_pageins, info.pti_csw, info.pti_threadnum, init->p_stat);
        printf("THOR PID1EXC: exc=%llu va=%llx pc=%llx esr=%x class=%u sys=%llu num=%llx\n",
               thor_exc_count, thor_exc_va, thor_exc_pc, thor_exc_esr, thor_exc_class,
               thor_sys_count, thor_sys_num);
        printf("THOR PID1BLK: n=%llu ra=%llx ra2=%llx cont=%llx bproc=%llx pid1proc=%llx\n",
               thor_block_count, thor_block_ra, thor_block_ra2, thor_block_cont,
               thor_block_proc, (unsigned long long)(uintptr_t)init);
        printf("THOR PID1SYS: num=%llx x0=%llx x1=%llx x2=%llx x3=%llx ctl_n=%d ctl=[%u,%u,%u,%u]\n",
               thor_sys_num, thor_sys_x0, thor_sys_x1, thor_sys_x2, thor_sys_x3,
               thor_sysctl_n, thor_sysctl_name[0], thor_sysctl_name[1],
               thor_sysctl_name[2], thor_sysctl_name[3]);
        // Totals across every task: if these move while the filtered counters
        // stay at zero, the watch pointer is wrong, not the system idle.
        printf("THOR PID1ALL: exc=%llu sys=%llu watch=%p kern=%p lastexc=%p lastsys=%p\n",
               thor_exc_all, thor_sys_all, thor_watch_task, (void *)kernel_task,
               thor_last_exc_task, thor_last_sys_task);
        proc_rele(init);
      }
    }
    uint64_t ramdisk_pa = 0, ramdisk_bytes = 0;
    IORegistryEntry *map = IORegistryEntry::fromPath("/chosen/memory-map", gIODTPlane);
    OSData *rd = map ? OSDynamicCast(OSData, map->getProperty("RAMDisk")) : nullptr;
    if (rd && rd->getLength() == 16) {
      const uint32_t *v = static_cast<const uint32_t *>(rd->getBytesNoCopy());
      ramdisk_pa = uint64_t(v[0]) | (uint64_t(v[1]) << 32);
      ramdisk_bytes = uint64_t(v[2]) | (uint64_t(v[3]) << 32);
    }
    OSSafeReleaseNULL(map);
    unsigned media = 0, mnt = 0;
    OSDictionary *matching = IOService::serviceMatching("IOMedia");
    OSIterator *iterator = matching ? IOService::getMatchingServices(matching) : nullptr;
    OSSafeReleaseNULL(matching);
    while (iterator && iterator->getNextObject()) ++media;
    OSSafeReleaseNULL(iterator);
    matching = IOService::serviceMatching("IOMedia");
    iterator = matching ? IOService::getMatchingServices(matching) : nullptr;
    OSSafeReleaseNULL(matching);
    while (iterator) {
      IOService *svc = OSDynamicCast(IOService, iterator->getNextObject());
      if (!svc) break;
      if (OSDynamicCast(OSString, svc->getProperty("BSD Name")) &&
          svc->getProperty("Content Hint")) ++mnt;
    }
    OSSafeReleaseNULL(iterator);
    proc_t init = proc_find(1);
    int init_stat = init ? init->p_stat : -1;
    if (init) proc_rele(init);
    printf("THOR UFS-T: ramdisk=%llu media=%u ready=%u rd=%llu/%llu pid1=%d\n",
           ramdisk_bytes, media, gHost.ready ? 1u : 0u, gReadCount, gReadBytes >> 20, init_stat);
  }
}

extern "C" bool pong_ufs_transport_start(IOService *provider) {
  if (gTransportAttempted) {
    IOLog("PONG_UFS: transport attempt already completed; skip duplicate match\n");
    return false;
  }
  gTransportAttempted = true;
  // A built-in transport has no separate kext dependency record. Explicitly
  // start the genuine family and its personalities before using its classes.
  // Thor links IOStorageFamily and the APFS classes into the kernel itself, so
  // the kext identifiers may not resolve even though every class is present.
  // The metaclass check below is the real gate; a failed load is only recorded.
  OSReturn loaded = OSKextLoadKextWithIdentifier("com.apple.iokit.IOStorageFamily");
  gRootDiagnostic.result = static_cast<uint32_t>(loaded);
  IOLog("PONG_UFS: IOStorageFamily load returned %d (statically linked builds ignore this)\n",
        static_cast<int>(loaded));
  loaded = OSKextLoadKextWithIdentifier("com.apple.filesystems.apfs");
  gRootDiagnostic.far = static_cast<uint32_t>(loaded);
  IOLog("PONG_UFS: apfs load returned %d\n", static_cast<int>(loaded));
  // Original prelinked storage metadata must be initialized before allocation.
  OSSharedPtr<const OSSymbol> name(OSSymbol::withCStringNoCopy("IOBlockStorageDevice"), OSNoRetain);
  const OSMetaClass *meta = name ? OSMetaClass::getMetaClassWithName(name.get()) : nullptr;
  if (meta != &IOBlockStorageDevice::gMetaClass) {
    printf("THOR UFS7: meta=%p ours=%p name=%s size=%u super=%s\n", (void *)meta,
           (void *)&IOBlockStorageDevice::gMetaClass,
           meta && meta->getClassName() ? (const char *)meta->getClassName() : "-",
           meta ? (unsigned)meta->getClassSize() : 0u,
           meta && meta->getSuperClass() && meta->getSuperClass()->getClassName()
               ? (const char *)meta->getSuperClass()->getClassName() : "-");
    // Thor links a source copy of the class while the prelinked IOStorageFamily
    // is the one that owns the registry entry. Keep the numbers, keep going:
    // the failure this check guards against is a wrong vtable, which the first
    // real command would show anyway.
    gStorageAbiMismatch = 1;
  }
  if (meta && (meta->getClassSize() != sizeof(IOBlockStorageDevice) ||
      meta->getSuperClass() != &IOService::gMetaClass)) {
    printf("THOR UFS8: class-size=%u/%u super=%p/%p\n", meta ? (unsigned)meta->getClassSize() : 0u,
           (unsigned)sizeof(IOBlockStorageDevice), (void *)meta->getSuperClass(),
           (void *)&IOService::gMetaClass);
    gStorageAbiMismatch = 2;
  }
  if (!hostBringUp()) return false;
  RootLayout layout;
  const char *why = nullptr;
  if (!findRootLayout(&layout, &why)) { gTransportWhy = why; return transportFailure(why); }
  gGptRead = true;
  gPartitionCount = layout.partitions < 64 ? layout.partitions : 64;
  for (unsigned i = 0; i < gPartitionCount; ++i) gPartitions[i] = layout.partition[i];
  // Two carriers are supported. Thor keeps the unmodified iOS images in the
  // stock GPT partitions ("OS" holds the root container), so they are used
  // directly when present: no copy, no manifest, no partition is written.
  // Pong instead stores them in userdata windows behind a SHNGROOT manifest.
  static const struct { const char *name, *product, *signature; unsigned offset; } wanted[] = {
    {"OS", "iOS 16 OS root", "EFI PART", 512},
    {"SystemOS", "iOS 16 SystemOS", "NXSB", 32},
  };
  gRootWindowCount = 0;
  bool partitionCarrier = false;
  for (const auto &item : wanted) {
    for (uint32_t index = 0; index < layout.partitions; ++index) {
      const RootPartition &part = layout.partition[index];
      if (strcmp(part.name, item.name) || part.last < part.first || part.last >= gHost.blocks) continue;
      if (gRootWindowCount >= 2) return transportFailure("too many root partitions");
      gRootWindows[gRootWindowCount++] = {item.name, item.product, item.signature, item.offset,
        part.first * 4096ULL, (part.last - part.first + 1) * 4096ULL};
      partitionCarrier = true;
      break;
    }
  }
  if (!partitionCarrier) {
    // Thor derives the partition from its own GPT; only the bounds matter.
    if (!layout.userdataFirst || layout.userdataLast <= layout.userdataFirst ||
        layout.userdataLast >= gHost.blocks)
      return transportFailure("userdata geometry unusable");
    if (layout.manifestStatus) return transportFailure("invalid SHNGROOT manifest/hash/bounds");
    for (const auto &item : wanted) {
      bool found = false;
      for (uint32_t index = 0; index < layout.windows; ++index) {
        if (strcmp(layout.window[index].name, item.name)) continue;
        if (gRootWindowCount >= 2) return transportFailure("too many SHNGROOT disks");
        gRootWindows[gRootWindowCount++] = {item.name, item.product, item.signature, item.offset,
          layout.userdataFirst * 4096ULL + layout.window[index].offset, layout.window[index].size};
        found = true;
        break;
      }
      if (!found) return transportFailure("required SHNGROOT window missing");
    }
  } else {
    IOLog("PONG_UFS: root carrier = stock GPT partitions (%u); no userdata manifest required\n",
          gRootWindowCount);
  }
  for (unsigned index = 0; index < gRootWindowCount; ++index) {
    OSSharedPtr<PongUfsBlock> block(new PongUfsBlock, OSNoRetain);
    if (!block || !block->init(nullptr, gRootWindows[index]) || !block->attach(provider))
      return transportFailure("block allocation/attachment");
    if (!block->start(provider)) { block->detach(provider); return transportFailure("block start"); }
  }
  {
    thread_t thread = nullptr;
    if (kernel_thread_start(pongStatusLoop, nullptr, &thread) == KERN_SUCCESS) thread_deallocate(thread);
    if (kernel_thread_start(pongGetattrProbe, nullptr, &thread) == KERN_SUCCESS) thread_deallocate(thread);
  }
  return true;
}

// RamBlock declares these as weak imports and keeps its volatile disk when the
// board supplies no persistent store. Thor has no pong/sheng Data window, so
// the honest answer is "absent": mode 0 means persistence stays off and every
// call reports no storage rather than silently accepting writes.
extern "C" unsigned native_ufs_store_mode(void) { return 0; }
extern "C" int native_ufs_store_open(uint64_t, uint64_t *, const char **why) {
  if (why) *why = "thor: no persistent store carrier";
  return 0;
}
extern "C" int native_ufs_store_read(uint64_t, uint32_t, void *) { return 0; }
extern "C" int native_ufs_store_write(uint64_t, uint32_t, const void *) { return 0; }
extern "C" int native_ufs_store_sync(void) { return 0; }
extern "C" unsigned native_ufs_store_gpt_check(void) { return 0; }

// Called from the late boot census: everything a failed root mount needs to
// explain itself, printed where a photograph cannot miss it.
extern "C" void pong_ufs_dump_state(void) {
  printf("THOR UFS2: gpt=%u parts=%u windows=%u ready=%u blocks=%llu\n",
         gGptRead, gPartitionCount, gRootWindowCount, gHost.ready ? 1u : 0u,
         gHost.blocks);
  printf("THOR UFS2c: storage-abi-mismatch=%u\n", gStorageAbiMismatch);
  printf("THOR UFS2b: stage=%s why=%s result=%08x\n",
         gRootDiagnostic.stage ? gRootDiagnostic.stage : "-",
         gTransportWhy ? gTransportWhy : "-", gRootDiagnostic.result);
  // Only sizeable partitions can hold a root image; keep the dump short because
  // every printed line costs about a second of video-console scrolling.
  {
    char line[160];
    unsigned used = 0;
    printf("THOR UFS3: %u partitions, those >=16 MiB follow\n", gPartitionCount);
    line[0] = 0;
    for (unsigned i = 0; i < gPartitionCount; ++i) {
      unsigned long long blocks = gPartitions[i].last >= gPartitions[i].first
          ? gPartitions[i].last - gPartitions[i].first + 1 : 0;
      unsigned mib = (unsigned)((blocks * 4096ULL) >> 20);
      if (mib < 16) continue;
      char item[64];
      snprintf(item, sizeof(item), "%s=%uM ", gPartitions[i].name, mib);
      if (strlen(line) + strlen(item) > 140) {
        printf("THOR UFS3: %s\n", line);
        line[0] = 0;
      }
      strlcat(line, item, sizeof(line));
      ++used;
    }
    if (line[0]) printf("THOR UFS3: %s\n", line);
    printf("THOR UFS3b: sizeable=%u\n", used);
  }
  for (unsigned i = 0; i < gRootWindowCount; ++i)
    printf("THOR UFS4: win %u %s base=%llx bytes=%llx %s\n", i, gRootWindows[i].name,
           gRootWindows[i].baseByte, gRootWindows[i].size,
           gRootWindows[i].signature ? gRootWindows[i].signature : "-");
  {
    // Did the loader's root-media payload actually reach the kernel? The ADT
    // carries it as /chosen/memory-map RAMDisk = {pa, bytes}.
    IORegistryEntry *map = IORegistryEntry::fromPath("/chosen/memory-map", gIODTPlane);
    OSData *rd = map ? OSDynamicCast(OSData, map->getProperty("RAMDisk")) : nullptr;
    if (rd && rd->getLength() == 16) {
      const uint32_t *v = static_cast<const uint32_t *>(rd->getBytesNoCopy());
      uint64_t pa = uint64_t(v[0]) | (uint64_t(v[1]) << 32);
      uint64_t len = uint64_t(v[2]) | (uint64_t(v[3]) << 32);
      printf("THOR UFS9: chosen RAMDisk pa=%llx bytes=%llu\n", pa, len);
    } else {
      printf("THOR UFS9: chosen RAMDisk absent\n");
    }
    OSSafeReleaseNULL(map);
  }
  // Published volumes: the APFS volume UUIDs are what the ADT root-match needs.
  OSDictionary *matching = IOService::serviceMatching("IOMedia");
  OSIterator *iterator = matching ? IOService::getMatchingServices(matching) : nullptr;
  OSSafeReleaseNULL(matching);
  unsigned media = 0;
  while (iterator && media < 4) {
    IOService *service = OSDynamicCast(IOService, iterator->getNextObject());
    if (!service) continue;
    OSString *uuid = OSDynamicCast(OSString, service->getProperty("UUID"));
    OSString *name = OSDynamicCast(OSString, service->getProperty(kIOBSDNameKey));
    if (!uuid) continue;
    printf("THOR UFS5: media %s uuid=%s\n", name ? name->getCStringNoCopy() : "?",
           uuid->getCStringNoCopy());
    ++media;
  }
  OSSafeReleaseNULL(iterator);
  printf("THOR UFS6: media-with-uuid=%u\n", media);
}
