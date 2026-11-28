// Host harness for ThorMssServe.inc's QRTR/QMI layer: kernel types and transports are stubs,
// the outgoing GLINK send is captured, the EFS partitions are RAM patterns.
#pragma once
#include <cstdint>
#include <cstdio>
#include <cstdarg>
#include <cstdlib>
#include <cstring>
#include <cerrno>
#include <string>
#include <vector>
#include <cassert>
using namespace std;
static uint64_t gNow = 1000000;
extern "C" uint64_t mach_absolute_time() { return gNow; }
static void absolutetime_to_nanoseconds(uint64_t t, uint64_t *ns) { *ns = t; }
static vector<string> gLogs;
static void IOLog(const char *f, ...)
{
    char line[2048]; va_list a; va_start(a, f); vsnprintf(line, sizeof line, f, a); va_end(a); gLogs.emplace_back(line);
}
static void IOSleep(unsigned ms) { gNow += ms * 1000; }
static void *IOMallocData(size_t n) { return calloc(1, n); }
static void IOFreeData(void *p, size_t) { free(p); }
constexpr unsigned kIOMapAnywhere = 1, kIOMapInhibitCache = 2;
struct IOMemoryMap {
    uintptr_t va = 0; size_t len = 0;
    uintptr_t getVirtualAddress() { return va; }
    size_t getLength() { return len; }
    void release() {}
};
struct IODeviceMemory {
    size_t bytes = 0;
    static IODeviceMemory *withRange(uint64_t, size_t n) {
        static IODeviceMemory d[4]; int i = 0; while (i < 4 && d[i].bytes) ++i; assert(i < 4); d[i].bytes = n; return d + i;
    }
    IOMemoryMap *map(unsigned) {
        static IOMemoryMap m[4]; int i = 0; while (i < 4 && m[i].len) ++i; assert(i < 4);
        m[i].len = bytes; m[i].va = reinterpret_cast<uintptr_t>(calloc(1, bytes)); return m + i;
    }
    void release() {}
};
namespace PongLowDma {
constexpr unsigned ModemMetadataBytes = 0x10000, RmtfsBytes = 0x280000;
static inline bool modemMetadataAddress(uint64_t &r) { r = 0xfed14000; return true; }
static inline bool rmtfsAddress(uint64_t &r) { r = 0xfed24000; return true; }
}
// The four LUN-5 partitions, 8 MiB / 8 MiB / 8 MiB / 1 MiB, each word holding its sector and slot.
static const uint64_t kPartBytes[4] = {8ull << 20, 8ull << 20, 8ull << 20, 1ull << 20};
static vector<uint8_t> gDisk[4];
static unsigned gNvCalls;
extern "C" int pong_ufs_factory_nv(unsigned slot, uint64_t offset, void *dst, uint32_t count, uint64_t *bytes)
{
    ++gNvCalls;
    if (!bytes) return EINVAL;
    *bytes = 0;
    if (slot >= 4) return EINVAL;
    if (!gDisk[slot].size()) {
        gDisk[slot].resize(kPartBytes[slot]);
        for (uint64_t s = 0; s < kPartBytes[slot] / 512; ++s)
            for (unsigned w = 0; w < 128; ++w) {
                const uint32_t v = uint32_t(s * 128 + w) ^ (slot << 28);
                memcpy(&gDisk[slot][s * 512 + w * 4], &v, 4);
            }
    }
    if (!count) { if (dst || offset) return EINVAL; *bytes = kPartBytes[slot]; return 0; }
    if (count > 128 * 1024 || offset + count > kPartBytes[slot]) return EINVAL;
    memcpy(dst, &gDisk[slot][offset], count); *bytes = kPartBytes[slot]; return 0;
}
extern "C" bool pong_scm_call_lock() { return true; }
extern "C" void pong_scm_call_unlock() {}
extern "C" bool pong_smem_lock(volatile uint32_t *) { return false; }
extern "C" void pong_smem_unlock(volatile uint32_t *) {}
