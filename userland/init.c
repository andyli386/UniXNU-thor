/*
 * thor PID 1.
 *
 * XNU execs /sbin/launchd as init and never mounts the Data volume: mountroot
 * gives a read-only System volume plus a shadow fs_root, and nothing in this
 * kernel or in launchd mounts the Data volume over /private/var.
 *
 * This version calls no stat() at all. proc_taskinfo on v5 showed exactly one
 * completed Unix syscall — chdir("/") — with the next one never returning, and
 * that next one was stat("/"). Since the goal is the mount, the mount is tried
 * directly: the device comes from a boot argument (or a short slice-2 scan),
 * and the mount result is reported through the process name, which the kernel
 * probe prints as pid1 comm=... . Every syscall this binary makes is one the
 * mount actually needs.
 *
 * Freestanding arm64, built like pong's init: no dyld, no libSystem.
 */
#include "mini.h"

#define SYS_unmount 159
#define O_NONBLOCK  0x4

static int gLastError = -1;

/* apfs_mount_args as apfs 2142 consumes it: device path, flags, mode, and the
 * uid/gid pair at the tail (pong's qualified owner hook). */
/* apfs_mount_args as apfs 2142 consumes it: device path, flags, mode, and the
 * uid/gid pair at the tail. */
static int
mount_apfs(const char *device, const char *where)
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
    errno = 0;
    return mount("apfs", where, 0, payload);
}

static int
mount_data(const char *device)
{
    if (mount_apfs(device, "/private/var") == 0) return 0;
    gLastError = errno;
    return -1;
}

/* The dyld shared cache does not live on the root volume in iOS 16: it is on
 * the SystemOS (SystemCryptex) volume, which the UFS driver publishes as its
 * own disk.  The kernel's own exec hook mounts that volume at
 * /private/preboot and hands dyld DYLD_SHARED_CACHE_DIR pointing at it
 * (bsd/kern/kern_exec.c); without it vm_shared_region_start_address() fails
 * and dyld has no cache to map.  Here the same mount is done from PID 1
 * because this kernel runs the hook disabled.
 *
 * Every candidate is tried and unmounted again unless it actually carries
 * System/Library/Caches/com.apple.dyld, so re-mounting the root or Data
 * volume here is harmless. */
static const char kCacheDir[] = "/private/preboot/System/Library/Caches/com.apple.dyld";

static void device_path(char *out, size_t cap, int disk, int slice);

static int
mount_cryptex(void)
{
    char dev[64];
    for (int disk = 0; disk <= 9; disk++) {
        for (int slice = 1; slice <= 2; slice++) {
            device_path(dev, sizeof(dev), disk, slice);
            if (mount_apfs(dev, "/private/preboot") != 0) continue;
            int fd = open(kCacheDir, O_RDONLY | O_DIRECTORY, 0);
            if (fd >= 0) {
                close(fd);
                return 0;
            }
            (void)mini_syscall(SYS_unmount, (long)(uintptr_t)"/private/preboot", 0, 0, 0, 0, 0);
        }
    }
    return -1;
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
    out[n++] = (char)('0' + slice);
    out[n] = 0;
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

static void
report(const char *tag)
{
    (void)tag;
    /* /sbin/thor-e<errno> is a symlink to launchd.ios; the kernel takes p_comm
     * from the last path component, so one boot reports the exact errno. */
    char path[64];
    const char *base = "/sbin/thor-e";
    size_t n = 0;
    int value = gLastError < 0 ? 99 : gLastError;
    while (base[n]) { path[n] = base[n]; n++; }
    if (value >= 100) { path[n++] = (char)('0' + value / 100); value %= 100; }
    if (value >= 10 || n > 11) { path[n++] = (char)('0' + value / 10); value %= 10; }
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
    /* DYLD_SHARED_CACHE_DIR is what bsd/kern/kern_exec.c would have set from
     * the owner-cache hook; dyld needs it to find the cache on the cryptex
     * volume once that volume is mounted at /private/preboot. */
    char *env[] = {
        "PATH=/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME=/var/root",
        "TMPDIR=/var/tmp/",
        "DYLD_SHARED_CACHE_DIR=/private/preboot/System/Library/Caches/com.apple.dyld",
        NULL
    };
    char device[64];
    int mounted = 0;

    /* Ladder, one syscall per rung, so `unix=` names the rung that stops:
     *   1..10  getpid        - no VFS at all
     *   11     open("/")     - namei + vnode_authorize(SEARCH) + copyin of path
     *   12     chdir("/")    - the above plus proc_dirs_lock and vnode_ref
     *   13..   mount(...)    - the work we actually need
     * Twenty getpids proved syscalls return at all; the question left is
     * which VFS entry point blocks.
     */
    int probe_fd = -1;
    char args[1024];
    size_t argsLen = sizeof(args);
    for (int spin = 0; spin < 10; spin++) (void)getpid();   /* 1..10  no pointers */
    (void)getppid();                                          /* 11     no pointers */
    (void)sysctlbyname("kern.bootargs", args, &argsLen, NULL, 0); /* 12 copyin + copyout */
    probe_fd = open("/", O_RDONLY, 0);                        /* 13     + namei + fd alloc */
    (void)chdir("/");                                         /* 14     + dirs lock */
    if (probe_fd >= 0) close(probe_fd);

    /* The device to try: whatever the loader was told, else slice 2 of each
     * disk (the container's second volume is the Data volume). */
    if (bootarg_device(device, sizeof(device)) && mount_data(device) == 0) mounted = 1;
    for (int disk = 0; !mounted && disk <= 9; disk++) {
        device_path(device, sizeof(device), disk, 2);
        if (mount_data(device) == 0) mounted = 1;
    }

    /* Must happen before the exec: dyld reads the cache directory from the
     * environment and maps the shared cache before launchd gets to run. */
    int cryptex = mount_cryptex();

    /* pong's software UI adapter has to be loaded into the processes that
     * draw; the kernel appends DYLD_INSERT_LIBRARIES from kern.exec_inject.
     * Only arm it when the library really exists: a DYLD_INSERT_LIBRARIES
     * entry that dyld cannot load aborts the process it was injected into. */
    int ui = 0;
    {
        const char *lib = "/private/var/pong/libpong-ui.dylib";
        int fd = open(lib, O_RDONLY, 0);
        if (fd >= 0) {
            close(fd);
            static const char conf[] =
                "/private/var/pong/libpong-ui.dylib SpringBoard backboardd "
                "containermanagerd containermanagerd_system Preferences";
            ui = sysctlbyname("kern.exec_inject", NULL, NULL, conf, sizeof(conf)) == 0;
        }
    }

    /* XNU's low-priority I/O throttle is on by default and our storage never
     * reports itself as an SSD, so page-in of the dyld cache runs with HDD
     * tier periods and SpringBoard's startup crawls (measured: single-digit
     * MB/min). The ADT carries 1 ms tier overrides; turn the whole mechanism
     * off here too, before anything launches, because the flag is read live
     * on every window. */
    {
        int zero = 0;
        int ok = sysctlbyname("debug.lowpri_throttle_enabled", NULL, NULL,
                              &zero, sizeof(zero)) == 0;
        (void)write(2, ok ? "thor init: lowpri I/O throttle disabled\n"
                          : "thor init: lowpri throttle sysctl failed\n",
                    ok ? 40 : 41);
    }

    /* Descriptor hand-off.  The 20G81 launchd rejects a terminal on stdout at
     * entry -- it calls isatty(1) and exits with "cannot be run directly
     * (stdout: 1)" -- so stdin and stdout go to /dev/null while stderr keeps
     * the console, which is also where our own milestones must go now. */
    int con = open("/dev/console", O_RDWR | O_NONBLOCK, 0);
    if (con >= 0) {
        dup2(con, 2);
        if (con > 2) close(con);
    }
    int nul = open("/dev/null", O_RDWR, 0);
    if (nul >= 0) {
        dup2(nul, 0);
        dup2(nul, 1);
        if (nul > 2) close(nul);
    }
    if (mounted) {
        (void)write(2, "thor init: Data volume mounted\n", 31);
    } else {
        (void)write(2, "thor init: Data mount failed\n", 29);
    }
    if (cryptex == 0) {
        (void)write(2, "thor init: cryptex mounted at /private/preboot\n", 47);
    } else {
        (void)write(2, "thor init: cryptex mount failed (no dyld cache)\n", 48);
    }
    if (ui) {
        (void)write(2, "thor init: UI library injection armed\n", 38);
    } else {
        (void)write(2, "thor init: no UI library, injection skipped\n", 43);
    }

    if (!mounted) report("fail");
    execve("/sbin/launchd.ios", next, env);
    report("no-launchd");
    for (;;) { usleep(1000000); }
}
