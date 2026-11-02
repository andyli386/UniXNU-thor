/*
 * thor PID 1.
 *
 * XNU execs /sbin/launchd as init and never mounts the Data volume: mountroot
 * gives a read-only System volume plus a shadow fs_root, and nothing in this
 * kernel or in launchd mounts the Data volume over /private/var. Pong solves
 * it by replacing init; this does the same and then execs the real launchd
 * (installed as /sbin/launchd.ios).
 *
 * Two things this version does differently from the first attempt, both
 * because the previous one was observed alive as PID 1 but produced no output
 * and never mounted anything:
 *
 *  - The mount happens before any console work. Opening /dev/console as PID 1
 *    must not be able to delay or block the only step that matters.
 *  - The result is reported through the process name, which the kernel-side
 *    probe prints as pid1 comm=...: success execs /sbin/launchd.ios, failure
 *    execs /sbin/thor-e<errno> (symlinks to launchd.ios), so one boot reports
 *    the exact errno without a working console.
 *
 * Freestanding arm64, built like pong's init: no dyld, no libSystem.
 */
#include "mini.h"

#define SYS_unmount 159
#define O_NONBLOCK  0x4

static int gLastError = -1;

/* apfs_mount_args as apfs 2142 consumes it: device path, flags, mode, and the
 * uid/gid pair at the tail (pong's qualified owner hook). */
static int
mount_data(const char *device)
{
    uint8_t payload[0x150];
    uint64_t fspec = (uint64_t)(uintptr_t)device, flags = 0;
    uint16_t mode = 1;
    uint32_t owner = 99;

    memset(payload, 0, sizeof(payload));
    memcpy(payload, &fspec, 8);
    memcpy(payload + 8, &flags, 8);
    memcpy(payload + 16, &mode, 2);
    memcpy(payload + 0x144, &owner, 4);
    memcpy(payload + 0x148, &owner, 4);
    return mount("apfs", "/private/var", 0, payload);
}

static void
device_path(char *out, size_t cap, int disk, int slice)
{
    const char *base = "/dev/disk";
    size_t n = 0;
    while (base[n] && n + 1 < cap) { out[n] = base[n]; n++; }
    if (disk >= 10) { out[n++] = (char)('0' + disk / 10); }
    out[n++] = (char)('0' + disk % 10);
    out[n++] = 's';
    if (slice >= 10) { out[n++] = (char)('0' + slice / 10); }
    out[n++] = (char)('0' + slice % 10);
    out[n] = 0;
}

/* Returns 0 when /private/var is a different file system than /. */
static int
data_separate(void)
{
    struct stat root, var;
    if (stat("/", &root) != 0) return 0;
    return stat("/private/var", &var) == 0 && var.st_dev != root.st_dev;
}

static int
try_device(const char *device)
{
    struct stat st, root;
    if (stat(device, &st) != 0) return -1;
    if (stat("/", &root) != 0) return -1;
    errno = 0;
    if (mount_data(device) != 0) {
        gLastError = errno;
        return -1;
    }
    /* Mounting the System volume again lands on the same apfs fsid; only a
     * different volume can make st_dev differ. */
    if (data_separate()) return 0;
    (void)mini_syscall(SYS_unmount, (long)(uintptr_t)"/private/var", 0, 0, 0, 0, 0);
    gLastError = -2;
    return -1;
}

static int
bootarg_device(char *out, size_t cap)
{
    char args[1024];
    size_t len = sizeof(args);
    const char *key = "thor_data_device=";
    size_t klen = 0;

    if (sysctlbyname("kern.bootargs", args, &len, NULL, 0) != 0) return 0;
    args[sizeof(args) - 1] = 0;
    while (key[klen]) klen++;
    for (char *p = args; *p;) {
        while (*p == ' ') p++;
        size_t i = 0;
        while (i < klen && p[i] == key[i]) i++;
        if (i == klen) {
            p += klen;
            size_t n = 0;
            while (*p && *p != ' ' && n + 1 < cap) out[n++] = *p++;
            out[n] = 0;
            return n != 0;
        }
        while (*p && *p != ' ') p++;
    }
    return 0;
}

/* Mount the Data volume over /private/var. Returns 0 on success. */
static int
prepare_var(void)
{
    char device[64];

    if (data_separate()) return 0;
    /* Explicit device first: the caller knows which slice carries the Data
     * volume, so the common case costs one mount instead of a scan. */
    if (bootarg_device(device, sizeof(device)) && try_device(device) == 0) return 0;
    /* Fall back to slice 2 of every disk: the container's second volume is the
     * Data volume, and the disk number is not stable across boots. */
    for (int disk = 0; disk <= 9; disk++) {
        device_path(device, sizeof(device), disk, 2);
        if (try_device(device) == 0) return 0;
    }
    return -1;
}

static void
report_failure(void)
{
    /* /sbin/thor-e<errno> is a symlink to launchd.ios; p_comm becomes the last
     * path component, which the kernel probe prints. */
    char path[64];
    const char *base = "/sbin/thor-e";
    size_t n = 0;
    int value = gLastError < 0 ? 99 : gLastError;
    while (base[n]) { path[n] = base[n]; n++; }
    if (value >= 100) { path[n++] = (char)('0' + value / 100); value %= 100; }
    if (n > 11) { path[n++] = (char)('0' + value / 10); value %= 10; }
    path[n++] = (char)('0' + value);
    path[n] = 0;
    char *argv[] = { "/sbin/launchd", NULL };
    char *env[] = { "PATH=/usr/bin:/bin:/usr/sbin:/sbin", NULL };
    execve(path, argv, env);
}

int
main(int argc, char **argv, char **envp)
{
    (void)argc; (void)argv; (void)envp;
    char *next[] = { "/sbin/launchd", NULL };
    char *env[] = {
        "PATH=/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME=/var/root",
        "TMPDIR=/var/tmp/",
        NULL
    };
    int mounted;

    (void)chdir("/");

    /* Mount first: this is the only step that cannot wait on anything. */
    mounted = prepare_var();

    /* Console is best effort and must never block the hand-off. */
    int fd = open("/dev/console", O_RDWR | O_NONBLOCK, 0);
    if (fd >= 0) {
        dup2(fd, 0); dup2(fd, 1); dup2(fd, 2);
        if (fd > 2) close(fd);
    }
    if (mounted == 0) {
        puts("thor init: Data volume mounted; exec launchd.ios");
    } else {
        puts("thor init: no separate Data volume; exec launchd.ios anyway");
    }

    if (mounted != 0) report_failure();
    execve("/sbin/launchd.ios", next, env);
    /* launchd.ios missing: leave the errno visible rather than spinning. */
    report_failure();
    for (;;) { usleep(1000000); }
}
