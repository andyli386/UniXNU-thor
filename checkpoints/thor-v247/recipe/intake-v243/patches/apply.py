#!/usr/bin/env python3.13
"""Apply the v243 source changes to the APFS clone of the v242 recipe.  Every replacement must match
exactly once; nothing is written unless all of them do."""
import json, re, subprocess, sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
assert root.name == 'v243-unified', root
edits = []   # (path, old, new)

# ---------------------------------------------------------------- firmware LUN: the primary decides
p = root / 'device/drivers/NativeUfs/ModemFirmwareAccess.h'
s = p.read_text()
a = s.index('// v242: what the last partition-table check of the modem firmware LUN failed on.')
b = s.index('static void modemFirmwareResetReads(ModemFirmwareProbe &probe)')
old_block = s[a:b]
assert 'static int modemFirmwareGptVerifyLocked(ModemFirmwareProbe &probe)' in old_block and old_block.count('static int ') == 1
new_block = r'''// v242: what the last partition-table check of the modem firmware LUN failed on.  v239 and v241, the
// first two boots from flash, each lost Wi-Fi, the modem and with them the battery to one failed
// check two seconds into the boot, and nothing recorded which step had failed or what the device said.
struct ModemFirmwareGptFault {
  uint32_t step = 0;      // 0: none.  1 TEST UNIT READY, 2 READ CAPACITY, 3 geometry, 4 primary header read,
                          // 5 primary header, 6 backup header read, 7 backup header, 8 the two differ,
                          // 9 primary entries read, 10 primary entries, 11 backup entries read, 12 backup entries
  uint32_t result = 0;    // IOReturn of the failing command
  uint32_t scsi = 0, sense = 0, residual = 0, protocol = 0, ocs = 0;
  uint64_t lba = 0;       // the block address of the last read
  uint32_t signature = 0; // the first four bytes of the payload after the failing read
  uint32_t detail = 0;    // step 3: the block size; 5 and 7: the failing header checks; 8: the backup's table CRC; 10: the entries' CRC
  uint32_t first = 0;     // step 3: the last block the device reported; 8: the primary's table CRC
};
static ModemFirmwareGptFault gModemFirmwareGptFault;
constexpr uint64_t kModemFirmwareGptWaitUs = 45000000;   // how long the radio driver waits for the table
static uint64_t modemFirmwareNowUs(void)
{
  uint64_t ns = 0;
  absolutetime_to_nanoseconds(mach_absolute_time(), &ns);
  return ns / 1000;
}

// The checks of modemFirmwareGptHeader, as bits, for the console.
static uint32_t modemFirmwareGptHeaderFault(const uint8_t *p, bool backup)
{
  constexpr uint64_t lastBlock = kModemFirmwareLunBlocks - 1;
  const uint32_t bytes = le32(p + 12);
  uint32_t bits = 0;
  if (memcmp(p, "EFI PART", 8)) bits |= 1u;
  if (le32(p + 8) != 0x10000) bits |= 2u;
  if (bytes < 92 || bytes > kModemFirmwareBlockBytes) bits |= 4u;
  if (le32(p + 20)) bits |= 8u;
  if (!(bits & 4u) && rootCrc32(p, bytes, true) != le32(p + 16)) bits |= 16u;
  if (le64(p + 24) != (backup ? lastBlock : 1)) bits |= 32u;
  if (le64(p + 32) != (backup ? 1 : lastBlock)) bits |= 64u;
  if (le64(p + 40) != kModemFirmwareFirstUsable || le64(p + 48) != kModemFirmwareLastUsable) bits |= 128u;
  if (!rootEntryUsed(p + 56)) bits |= 256u;
  if (le64(p + 72) != (backup ? kModemFirmwareBackupEntries : kModemFirmwarePrimaryEntries)) bits |= 512u;
  if (le32(p + 80) != kModemFirmwareEntryCount || le32(p + 84) != kModemFirmwareEntrySize) bits |= 1024u;
  return bits;
}

// v243: how the backup partition table of the firmware LUN differs from the primary one, examined
// after the primary has been verified.  LUN 4 is the firmware LUN: boot_a, modem_a and the rest of the
// A/B partitions live there.  The bootloader rewrites the primary table on every normal boot of a slot
// that is not marked successful (it counts the slot's retries down there), and the backup lags behind:
// in all boots from flash of v239 to v242 the two named different CRCs (step 8 in v242), so Wi-Fi, the
// modem and the battery never came up, while the same v242 image passed when it was temporary-booted.
struct ModemFirmwareBackupLag {
  uint32_t bits = 0;        // 1 backup header not read, 2 backup header invalid, 4 its disk GUID or its table CRC
                            // differs from the primary's, 8 backup entries not read, 16 backup entries differ from
                            // the CRC their own header names, 32 backup entries differ from the primary ones
  uint32_t headerFault = 0; // the failing checks of the backup header, as above
  uint32_t entriesCrc = 0;  // the CRC of the backup entries as read
  uint32_t bytes = 0;       // how many bytes of the two entry arrays differ
  uint32_t entry = 0, offset = 0, primary = 0, backup = 0;   // the first difference
};
static ModemFirmwareBackupLag gModemFirmwareBackupLag;

static int modemFirmwareGptVerifyLocked(ModemFirmwareProbe &probe)
{
  const uint8_t tur[6] = {}, capacity[10] = {0x25};
  const auto *dma = gHost.virt + kDataOffset;
  gModemFirmwareGptFault = ModemFirmwareGptFault{};
  gModemFirmwareBackupLag = ModemFirmwareBackupLag{};
  const auto note = [&](uint32_t step, IOReturn rc) {
    auto &f = gModemFirmwareGptFault;
    f.step = step;
    f.result = static_cast<uint32_t>(rc);
    f.scsi = gRootDiagnostic.scsi;
    f.sense = gRootDiagnostic.sense;
    f.residual = gRootDiagnostic.residual;
    f.protocol = gRootDiagnostic.protocol;
    f.ocs = gRootDiagnostic.ocs;
    f.lba = gRootDiagnostic.lba;
    f.signature = loadBe32(dma);
    return step <= 3 ? ENXIO : EIO;
  };
  uint32_t scsi = 0;
  IOReturn rc = hostScsi(kModemFirmwareLun, tur, sizeof(tur), false, 0, 200000, &scsi);
  if (rc != kIOReturnSuccess) return note(1, rc);
  rc = hostScsi(kModemFirmwareLun, capacity, sizeof(capacity), true, 8, 200000, &scsi);
  if (rc != kIOReturnSuccess) return note(2, rc);
  if (loadBe32(dma) != kModemFirmwareLunBlocks - 1 || loadBe32(dma + 4) != kModemFirmwareBlockBytes) {
    gModemFirmwareGptFault.first = loadBe32(dma);
    gModemFirmwareGptFault.detail = loadBe32(dma + 4);
    return note(3, kIOReturnSuccess);
  }
  gHost.modemFirmwareBlocks = kModemFirmwareLunBlocks;

  // v243: the primary table decides.  It is pinned: a valid header, entries whose CRC that header
  // names, and exactly the two expected partitions at their expected places.  The backup is examined
  // too, but whatever is amiss with it is only recorded: no read ever depends on it, and the firmware
  // read through it is verified against pinned digests afterwards.  Up to v242 a backup that lagged
  // behind the primary failed the whole check.
  ModemFirmwareGptIdentity primary{}, backup{};
  auto &lag = gModemFirmwareBackupLag;
  if ((rc = hostRead4kLocked(kModemFirmwareLun, 1, 1)) != kIOReturnSuccess) return note(4, rc);
  if (!modemFirmwareGptHeader(dma, false, primary)) {
    gModemFirmwareGptFault.detail = modemFirmwareGptHeaderFault(dma, false);
    return note(5, kIOReturnSuccess);
  }
  if ((rc = hostRead4kLocked(kModemFirmwareLun, kModemFirmwareLunBlocks - 1, 1)) != kIOReturnSuccess) {
    lag.bits |= 1;
  } else if (!modemFirmwareGptHeader(dma, true, backup)) {
    lag.bits |= 2;
    lag.headerFault = modemFirmwareGptHeaderFault(dma, true);
  } else if (primary.tableCrc != backup.tableCrc ||
             memcmp(primary.diskGuid, backup.diskGuid, sizeof(primary.diskGuid))) {
    lag.bits |= 4;
  }
  if ((rc = hostRead4kLocked(kModemFirmwareLun, kModemFirmwarePrimaryEntries,
                             kModemFirmwareEntryBlocks)) != kIOReturnSuccess) return note(9, rc);
  if (!modemFirmwareGptEntries(dma, primary.tableCrc)) {
    gModemFirmwareGptFault.detail = rootCrc32(dma, kModemFirmwareEntryBytes);
    gModemFirmwareGptFault.first = primary.tableCrc;
    return note(10, kIOReturnSuccess);
  }
  static_assert(kModemFirmwareEntryBytes <= sizeof(probe.transferScratch));
  static_assert(sizeof(probe.transferScratch) <= kDataMax);
  memcpy(probe.transferScratch, dma, kModemFirmwareEntryBytes);
  if ((rc = hostRead4kLocked(kModemFirmwareLun, kModemFirmwareBackupEntries,
                             kModemFirmwareEntryBlocks)) != kIOReturnSuccess) {
    lag.bits |= 8;
  } else {
    lag.entriesCrc = rootCrc32(dma, kModemFirmwareEntryBytes);
    if (!(lag.bits & 3) && lag.entriesCrc != backup.tableCrc) lag.bits |= 16;
    for (uint32_t i = 0; i < kModemFirmwareEntryBytes; ++i) {
      if (probe.transferScratch[i] == dma[i]) continue;
      if (!lag.bytes++) {
        lag.entry = i / kModemFirmwareEntrySize;
        lag.offset = i % kModemFirmwareEntrySize;
        lag.primary = probe.transferScratch[i];
        lag.backup = dma[i];
      }
    }
    if (lag.bytes) lag.bits |= 32;
  }
  gHost.modemFirmwareGptVerified = true;
  return 0;
}

'''
edits.append((p, old_block, new_block))

old = '''  } else {
    IOLog("THOR MODEM FW V163: state=gpt-verified lun=4 slots=2 selected=none\\n");
    if (attempt > 1)'''
assert s.count(old) == 1
new = '''  } else {
    IOLog("THOR MODEM FW V163: state=gpt-verified lun=4 slots=2 selected=none\\n");
    const auto &lag = gModemFirmwareBackupLag;
    if (lag.bits)
      IOLog("THOR MODEM FW V243: the backup table of the firmware LUN differs from the primary one: what=%#x "
            "header-fault=%#x entries-crc=%08x differing-bytes=%u first=entry %u offset %u primary=%02x "
            "backup=%02x; the primary decides\\n", lag.bits, lag.headerFault, lag.entriesCrc, lag.bytes,
            lag.entry, lag.offset, lag.primary, lag.backup);
    if (attempt > 1)'''
edits.append((p, old, new))

# ---------------------------------------------------------------- kernel: wallpaper v243
p = root / 'build-tree/.cache/xnu-work/bsd/kern/kern_exec.c'
s = p.read_text()
for old, new in (('THOR_WALLPAPER_V242', 'THOR_WALLPAPER_V243'), ('thor_wallpaper_v242', 'thor_wallpaper_v243'),
                 ('"THOR WALLPAPER V242:', '"THOR WALLPAPER V243:')):
    assert s.count(old) >= 1, old
    edits.append((p, old, new, 'all'))
a = s.index('\t/* v241: the wallpaper catalog library goes into the processes that link WallpaperKit: SpringBoard')
b = s.index('\tstatic const char wallpaper_library[] = THOR_WALLPAPER_V243_STAGE_PATH;') if 'THOR_WALLPAPER_V243_STAGE_PATH' in s[a:] else None
b = s.index('\tstatic const char wallpaper_library[] = THOR_WALLPAPER_V242_STAGE_PATH;')
edits.append((p, s[a:b], '''\t/* v243: the wallpaper catalog library goes into the processes that load WallpaperKit: SpringBoard
\t * (through SpringBoard.framework) and CollectionsPoster (the extension that renders the stock
\t * collections).  chronod and PosterBoard, which v241 and v242 listed as well, do not load it (the
\t * v242 console shows the library in both and WallpaperKit in neither), so they no longer get it.
\t * The restore carries only the iPad catalogs while this port shows the identity of an iPhone, so
\t * WallpaperKit ended every request in its "Solid Black" bundle.  The library gives WallpaperKit the
\t * iPad catalog names and lets the iPad bundles pass the screen-class check; it changes nothing else. */
\tstatic const struct { const char *name, *path; } wallpaper_targets[] = {
\t\t{ "SpringBoard", "/System/Library/CoreServices/SpringBoard.app/SpringBoard" },
\t\t{ "CollectionsPoster",
\t\t    "/System/Library/PrivateFrameworks/WallpaperKit.framework/PlugIns/CollectionsPoster.appex/CollectionsPoster" },
\t};
'''))

p = root / 'build-tree/.cache/xnu-work/osfmk/kern/thor_witness_v235.h'
s = p.read_text()
a = s.index('\tprintf("THOR WITNESS V242: the wallpaper catalog library loads again')
b = s.index('\n', s.index('shared-memory host id (v237)\\n");', a))
edits.append((p, s[a:b + 1], '''\tprintf("THOR WITNESS V243: the primary partition table of the firmware LUN decides (the bootloader "
\t    "rewrites it on every normal boot of a slot that is not marked successful and the backup lags behind; "
\t    "v239 to v242 lost Wi-Fi, the modem and the battery to that in every boot from flash while the same "
\t    "v242 image passed when temporary-booted); the wallpaper library gives WallpaperKit the iPad catalog "
\t    "names through its own lower-casing call (the MobileGestalt interception could not reach a call that "
\t    "the shared cache fixed, and chronod and PosterBoard no longer get the library); carried: the retries "
\t    "and the record of a failed check (v242), the interface-namer marker, the en0 listing, the panic "
\t    "record slot (v238), data volume classes A and B open for every process, the modem server's "
\t    "shared-memory host id (v237)\\n");
'''))

p = root / 'build-kernel.sh'
edits.append((p, 'THOR_WALLPAPER_RUNTIME="$out/runtime/libpong-wallpaper-v242.dylib"', 'THOR_WALLPAPER_RUNTIME="$out/runtime/libpong-wallpaper-v243.dylib"'))

p = root / 'device/scripts/build-xnu-ipados18.sh'
for old, new in (('THOR_WALLPAPER_V242_STAGE_PATH', 'THOR_WALLPAPER_V243_STAGE_PATH'),
                 ('/private/var/pong/libpong-wallpaper-v242.dylib', '/private/var/pong/libpong-wallpaper-v243.dylib'),
                 ("c_array('thor_wallpaper_v242_payload'", "c_array('thor_wallpaper_v243_payload'")):
    edits.append((p, old, new))

# ---------------------------------------------------------------- inputs record
p = root / 'runtime/final-inputs.json'
d = json.loads(p.read_text())
assert 'libpong-wallpaper-v242.dylib' in d['payloads'] and 'libpong-wallpaper-v243.dylib' not in d['payloads']
lib = root / 'runtime/libpong-wallpaper-v243.dylib'
sha = subprocess.check_output(['shasum', '-a', '256', str(lib)]).decode().split()[0]
uuid = subprocess.check_output(['dwarfdump', '--uuid', str(lib)]).decode().split()[1]
d['payloads']['libpong-wallpaper-v243.dylib'] = {'sha256': sha, 'uuid': uuid, 'bytes': lib.stat().st_size}
del d['payloads']['libpong-wallpaper-v242.dylib']
old = p.read_text()
new = json.dumps(d, indent=2, ensure_ascii=False) + '\n'
if old.rstrip('\n').endswith('}') and not old.endswith('}\n'):
    new = new.rstrip('\n')
edits.append((p, old, new))

# ---------------------------------------------------------------- apply: all or nothing
pending = {}
for e in edits:
    path, old, new = e[0], e[1], e[2]
    text = pending.get(path, path.read_text())
    n = text.count(old)
    if len(e) > 3 and e[3] == 'all':
        assert n >= 1, (path, old)
    else:
        assert n == 1, (path, n, old[:80])
    pending[path] = text.replace(old, new)
for path, text in pending.items():
    path.write_text(text)
    print('patched', path.relative_to(root))
