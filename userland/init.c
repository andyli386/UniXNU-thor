/*
 * thor PID 1.
 *
 * XNU execs /sbin/launchd as init and never mounts the Data volume: on iOS
 * mountroot gives a read-only System volume plus a shadow fs_root, and
 * /private/var only becomes writable once a separate apfs volume is mounted
 * over it. Whoever does that on a real device, nothing in this kernel or in
 * launchd does it, so this helper takes PID 1, mounts the Data volume at
 * /private/var with the same apfs mount ABI pong uses, and then execs the
 * real launchd (installed as /sbin/launchd.ios).
 *
 * Freestanding arm64, built like pong's init: no dyld, no libSystem.
 */
#include "mini.h"

#define SYS_unmount 159

/* apfs_mount_args as apfs 2142 consumes it: device path, flags, mode, and the
 * uid/gid pair at the tail. Copied from pong's qualified owner hook. */
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
    /* "/dev/disk" + digits + "s" + digits, written without printf. */
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

/* Mount the Data volume over /private/var.
 *
 * The disk number is not stable across boots, so scan: every volume of the
 * mounted container has a device node, and the Data volume is the only one
 * whose device id differs from the root's (mounting the System volume again
 * yields the same apfs fsid). A wrong volume is unmounted and skipped.
 * Returns 0 when a different file system is mounted at /private/var.
 */
static int
prepare_var(void)
{
    struct stat root, var;
    char device[32];
    int disk, slice;

    if (stat("/", &root) != 0) return -1;
    if (stat("/private/var", &var) == 0 && var.st_dev != root.st_dev) {
        puts("thor init: Data volume already separate");
        return 0;
    }
    for (disk = 0; disk <= 9; disk++) {
        for (slice = 1; slice <= 6; slice++) {
            struct stat st;
            device_path(device, sizeof(device), disk, slice);
            if (stat(device, &st) != 0) continue;
            if (mount_data(device) != 0) continue;
            if (stat("/private/var", &var) == 0 && var.st_dev != root.st_dev) {
                printf("thor init: Data volume mounted from %s\n", device);
                return 0;
            }
            /* Same file system as root: not the Data volume. */
            (void)mini_syscall(SYS_unmount, (long)(uintptr_t)"/private/var", 0, 0, 0, 0, 0);
        }
    }
    puts("thor init: no separate Data volume; /private/var stays read-only");
    return -1;
}

static void
prepare_root_skeleton(void)
{
    /* The System image carries a read-only /private/var tree; once the Data
     * volume is on top, the few directories swap and launchd need first must
     * exist there. mkdir on an existing path is harmless. */
    mkdir("/private/var/vm", 0755);
    mkdir("/private/var/run", 0755);
    mkdir("/private/var/log", 0755);
    mkdir("/private/var/tmp", 01777);
    mkdir("/private/var/db", 0755);
}

int
main(int argc, char **argv, char **envp)
{
    (void)argc; (void)argv; (void)envp;
    /* launchd keeps its own name in argv[0]; the iOS launchd refuses to run
     * as PID 1 with an interactive stdout, so stdin/stdout go to /dev/null and
     * diagnostics stay on the console (fd 2). */
    char *next[] = { "/sbin/launchd", NULL };
    char *env[] = {
        "PATH=/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME=/var/root",
        "TMPDIR=/var/tmp/",
        NULL
    };
    int fd;

    fd = open("/dev/console", O_RDWR, 0);
    if (fd >= 0) {
        dup2(fd, 0); dup2(fd, 1); dup2(fd, 2);
        if (fd > 2) close(fd);
    }
    (void)chmod("/dev/console", 0666);
    (void)chdir("/");
    puts("thor init: static arm64 PID 1");

    if (prepare_var() == 0) prepare_root_skeleton();

    puts("thor init: exec /sbin/launchd.ios");
    int nullfd = open("/dev/null", O_RDWR, 0);
    if (nullfd >= 0) {
        dup2(nullfd, 0);
        dup2(nullfd, 1);
        if (nullfd > 2) close(nullfd);
    }
    execve("/sbin/launchd.ios", next, env);
    printf("thor init: launchd exec failed: errno=%d\n", errno);
    return 127;
}
