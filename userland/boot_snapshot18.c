/* SPDX-License-Identifier: MIT
 * Bounded, process-local startup snapshots (22H374 thor integration).
 * Output paths: a persistent file under /private/var/logs (root) or
 * /private/var/mobile/Library/Logs (mobile), a bounded mirror on
 * /dev/console so early batches are visible on the photographed screen,
 * and - since the framebuffer takeover retires the text console - a
 * one-line PREV summary of the previous boot's own file at startup.
 * v62: batches 60/120/240/600/900 s then a full snapshot every 600 s and
 * a one-line tick every 60 s, both unbounded in time with an 8 MiB
 * rotation; files are opened O_APPEND so a restarted process (observed:
 * containermanagerd PID 922) appends instead of destroying the previous
 * instance's batches; up to 32 threads sampled; migration XPC services
 * whitelisted; runningboardd additionally mirrors the kernel console
 * (dmesg buffer) to PongConsole.txt so every boot's full text log can be
 * pulled from Data in Recovery instead of photographed.
 */
#include <dirent.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <mach/mach.h>
#include <mach/arm/thread_status.h>
#include <mach-o/dyld.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/param.h>
#include <sys/stat.h>
#include <sys/sysctl.h>
#include <time.h>
#include <unistd.h>

static const char *process_name;
static int console_fd = -2;   /* -2 not tried, -1 unavailable */

/* Shared with ui_compat18.c: key receipts must also reach the photographed
 * console because spawned services inherit stdio on /dev/null. */
void pong_console_line(const char *line)
{
    if (console_fd == -2)
        console_fd = open("/dev/console", O_WRONLY | O_NOCTTY | O_CLOEXEC);
    if (console_fd >= 0) {
        (void)write(console_fd, line, strlen(line));
        (void)write(console_fd, "\n", 1);
    }
}

static void emit(int fd, const char *line)
{
    dprintf(fd, "%s\n", line);
    pong_console_line(line);
}

static void snapshot_path(char *path, size_t size)
{
    snprintf(path, size, "%s/PongBoot-%s.txt",
             geteuid() == 0 ? "/private/var/logs"
                            : "/private/var/mobile/Library/Logs",
             process_name);
}

static void snapshot(int fd, unsigned elapsed)
{
    char line[1100];
    thread_act_array_t threads = NULL;
    mach_msg_type_number_t count = 0;
    kern_return_t result = task_threads(mach_task_self(), &threads, &count);
    int n = snprintf(line, sizeof(line),
        "Pong boot snapshot BEGIN: %s pid=%d seconds=%u time=%lld threads=%u",
        process_name, getpid(), elapsed, (long long)time(NULL), count);
    if (n > 0) emit(fd, line);
    if (result != KERN_SUCCESS) {
        snprintf(line, sizeof(line),
                 "Pong boot snapshot: %s task_threads=%d", process_name, result);
        emit(fd, line);
        return;
    }
    mach_port_t self = mach_thread_self();
    size_t cache_size = 0;
    const void *(*cache_range)(size_t *) =
        dlsym(RTLD_DEFAULT, "_dyld_get_shared_cache_range");
    const void *cache = cache_range ? cache_range(&cache_size) : NULL;
    const char *image0 = _dyld_get_image_name(0);
    n = snprintf(line, sizeof(line),
        "Pong boot snapshot: %s pid=%d seconds=%u image0=%s image_slide=%llx "
        "cache=%p size=%zx",
        process_name, getpid(), elapsed, image0 ? image0 : "?",
        (unsigned long long)_dyld_get_image_vmaddr_slide(0), cache, cache_size);
    if (n > 0) emit(fd, line);
    mach_msg_type_number_t sampled = 0;
    for (mach_msg_type_number_t i = 0; i < count; i++) {
        if (i < 32 && threads[i] != self) {
            arm_thread_state64_t state = {0};
            mach_msg_type_number_t words = ARM_THREAD_STATE64_COUNT;
            result = thread_get_state(threads[i], ARM_THREAD_STATE64,
                                      (thread_state_t)&state, &words);
            thread_basic_info_data_t info = {0};
            words = THREAD_BASIC_INFO_COUNT;
            kern_return_t ir = thread_info(threads[i], THREAD_BASIC_INFO,
                                           (thread_info_t)&info, &words);
            thread_identifier_info_data_t ident = {0};
            words = THREAD_IDENTIFIER_INFO_COUNT;
            (void)thread_info(threads[i], THREAD_IDENTIFIER_INFO,
                              (thread_info_t)&ident, &words);
            n = snprintf(line, sizeof(line),
                "thread=%u tid=%llu state_result=%d info_result=%d run=%d cpu=%d "
                "pc=%llx lr=%llx fp=%llx sp=%llx frames:",
                i, (unsigned long long)ident.thread_id, result, ir,
                info.run_state, info.cpu_usage,
                (unsigned long long)arm_thread_state64_get_pc(state),
                (unsigned long long)arm_thread_state64_get_lr(state),
                (unsigned long long)arm_thread_state64_get_fp(state),
                (unsigned long long)arm_thread_state64_get_sp(state));
            if (result == KERN_SUCCESS && n > 0 && (size_t)n < sizeof(line)) {
                uint64_t fp = arm_thread_state64_get_fp(state),
                         sp = arm_thread_state64_get_sp(state);
                for (unsigned j = 0;
                     j < 16 && fp >= sp && fp - sp < 8 * 1024 * 1024 && !(fp & 15);
                     j++) {
                    uint64_t pair[2]; vm_size_t got = 0;
                    if (vm_read_overwrite(mach_task_self(), (vm_address_t)fp,
                                          sizeof(pair), (vm_address_t)pair,
                                          &got) != KERN_SUCCESS ||
                        got != sizeof(pair)) break;
                    int used = snprintf(line + n, sizeof(line) - (size_t)n,
                                        " %llx", (unsigned long long)pair[1]);
                    if (used < 0 || (size_t)used >= sizeof(line) - (size_t)n) break;
                    n += used;
                    if (pair[0] <= fp) break;
                    fp = pair[0];
                }
            }
            emit(fd, line);
            sampled++;
        }
        (void)mach_port_deallocate(mach_task_self(), threads[i]);
    }
    (void)mach_port_deallocate(mach_task_self(), self);
    (void)vm_deallocate(mach_task_self(), (vm_address_t)threads,
                        count * sizeof(*threads));
    snprintf(line, sizeof(line),
             "Pong boot snapshot END: %s pid=%d seconds=%u sampled=%u of %u",
             process_name, getpid(), elapsed, sampled, count);
    emit(fd, line);
    (void)fsync(fd);
}

/* One cheap liveness line between full snapshots. */
static void tick(int fd, unsigned elapsed)
{
    thread_act_array_t threads = NULL;
    mach_msg_type_number_t count = 0;
    if (task_threads(mach_task_self(), &threads, &count) == KERN_SUCCESS) {
        (void)vm_deallocate(mach_task_self(), (vm_address_t)threads,
                            count * sizeof(*threads));
    }
    char line[128];
    int n = snprintf(line, sizeof(line), "Pong tick: %s pid=%d seconds=%u threads=%u",
                     process_name, getpid(), elapsed, count);
    if (n > 0) emit(fd, line);
}

/* Restarted processes must not destroy earlier instances' evidence. */
static void rotate_if_huge(int fd, unsigned elapsed)
{
    struct stat st;
    if (fstat(fd, &st) == 0 && st.st_size > 8 * 1024 * 1024) {
        char line[128];
        (void)ftruncate(fd, 0);
        int n = snprintf(line, sizeof(line),
                         "Pong boot snapshot ROTATE: %s seconds=%u size=%lld",
                         process_name, elapsed, (long long)st.st_size);
        if (n > 0) emit(fd, line);
    }
}

/* Previous boot summary only: one line saying whether this process wrote a
 * file last boot and how far it got. Full retrieval is the Recovery pull. */
static void replay_previous(void)
{
    char path[256], header[192];
    static const char marker[] = "Pong boot snapshot END:";
    snapshot_path(path, sizeof(path));
    int fd = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) {
        int n = snprintf(header, sizeof(header),
            "Pong boot snapshot PREV: %s no previous run file errno=%d",
            process_name, errno);
        if (n > 0) pong_console_line(header);
        return;
    }
    char tail[512];
    off_t size = lseek(fd, 0, SEEK_END);
    size_t want = size > (off_t)(sizeof(tail) - 1) ? sizeof(tail) - 1 : (size_t)size;
    ssize_t got = pread(fd, tail, want, size - (off_t)want);
    close(fd);
    if (got <= 0) return;
    tail[got] = 0;
    char *last = NULL;
    for (char *scan = tail;
         (size_t)(tail + got - scan) >= sizeof(marker) - 1; scan++)
        if (!strncmp(scan, marker, sizeof(marker) - 1)) last = scan;
    if (last) {
        char *newline = strchr(last, '\n');
        if (newline) *newline = 0;
        char *spurious = strchr(last, '\r');
        if (spurious) *spurious = 0;
        int n = snprintf(header, sizeof(header),
            "Pong boot snapshot PREV: %s file=%lldB last=[%s]",
            process_name, (long long)size, last);
        if (n > 0) pong_console_line(header);
    } else {
        int n = snprintf(header, sizeof(header),
            "Pong boot snapshot PREV: %s file=%lldB no END marker (incomplete)",
            process_name, (long long)size);
        if (n > 0) pong_console_line(header);
    }
}

/* Continuous kernel-console capture (root only, runningboardd only).
 * v62 field evidence: on this XNU the kern.msgbuf sysctl is size-only
 * (CTLTYPE_INT - it returned the 131072-byte capacity, which is why
 * PongConsole.txt filled with 0x00020000), and /dev/klog refuses to open
 * unless the ATM legacy-logging diagnostic flag is set. The real dmesg
 * contents path is the private proc_info call 0x4 (PROC_INFO_CALL_
 * KERNMSGBUF -> log_dmesg), root-only - exactly what runningboardd is.
 * Each call returns the whole current buffer oldest-first; poll and
 * append only the unseen tail. */
extern int __proc_info(int32_t call_num, int32_t pid, uint32_t flavor,
                       uint64_t arg, void *buffer, int32_t buffersize);

static void *console_worker(void *unused)
{
    (void)unused;
    const char *path = "/private/var/logs/PongConsole.txt";
    int out = open(path, O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0600);
    char notice[128];
    int n;
    if (out < 0) {
        n = snprintf(notice, sizeof(notice),
                     "Pong console capture: open failed errno=%d", errno);
        if (n > 0) pong_console_line(notice);
        return NULL;
    }
    static char buffer[160 * 1024];
    static char previous[8192];
    size_t previous_size = 0;
    bool announced = false, complained = false;
    for (;;) {
        int got = __proc_info(0x4, 0, 0, 0, buffer, (int32_t)sizeof(buffer));
        if (got < 0) {
            if (!complained) {
                complained = true;
                n = snprintf(notice, sizeof(notice),
                             "Pong console capture: proc_info dmesg errno=%d", errno);
                if (n > 0) pong_console_line(notice);
            }
            sleep(60);
            continue;
        }
        if (!announced) {
            announced = true;
            n = snprintf(notice, sizeof(notice),
                         "Pong console capture: proc_info dmesg pid=%d first=%dB",
                         getpid(), got);
            if (n > 0) { pong_console_line(notice); dprintf(out, "%s\n", notice); }
        }
        if (got > 0) {
            /* append only the part not seen last round; a missing overlap
             * means the ring wrapped and some text was lost. */
            char *overlap = NULL;
            if (previous_size && (size_t)got > previous_size) {
                for (size_t skip = 0; skip + previous_size <= (size_t)got; skip++)
                    if (!memcmp(buffer + skip, previous, previous_size)) {
                        overlap = buffer + skip + previous_size;
                        break;
                    }
            }
            if (overlap) {
                (void)write(out, overlap, (size_t)got - (size_t)(overlap - buffer));
            } else if (previous_size) {
                dprintf(out, "\nPong console capture WRAP lost text\n");
                (void)write(out, buffer, (size_t)got);
            } else {
                (void)write(out, buffer, (size_t)got);
            }
            size_t keep = (size_t)got < sizeof(previous) ? (size_t)got : sizeof(previous);
            memcpy(previous, buffer + (size_t)got - keep, keep);
            previous_size = keep;
            (void)fsync(out);
            struct stat st;
            if (fstat(out, &st) == 0 && st.st_size > 16 * 1024 * 1024) {
                dprintf(out, "\nPong console capture ROTATE size=%lld\n",
                        (long long)st.st_size);
                (void)fsync(out);
                close(out);
                out = open(path, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
                if (out < 0) return NULL;
                previous_size = 0;
            }
        }
        sleep(30);
    }
    return NULL;
}

/* Warm the dyld shared cache into the unified buffer cache. v64 field
 * evidence: every 4 KiB page-in is a synchronous single-slot UFS command
 * (~2-4 MiB/s effective), so daemons spend minutes in dyld/ObjC init
 * (fairplayd.A2 never finished paging in, PosterBoard hit the 30 s
 * 0x8badf00d watchdog) while the system thrashes (46k memorystatus
 * lines). Sequential read() of the hottest cache extents populates the
 * same UBC the page-ins then hit - the proven ufs116 recipe from the
 * iOS16 line: 60 s after start, first 64 MiB of the four largest
 * low-numbered subcaches. Disable with pong_cache_prefetch=0. */
static bool cache_prefetch_enabled(void)
{
    char args[2048];
    size_t length = sizeof(args);
    if (sysctlbyname("kern.bootargs", args, &length, NULL, 0) != 0) return true;
    args[length < sizeof(args) ? length : sizeof(args) - 1] = 0;
    return strstr(args, "pong_cache_prefetch=0") == NULL;
}

static void *cache_worker(void *unused)
{
    (void)unused;
    if (!cache_prefetch_enabled()) {
        pong_console_line("Pong cache pre-read: disabled by boot arg");
        return NULL;
    }
    sleep(60);
    static const char *candidates[] = {
        "/System/Library/dyld",
        "/System/Volumes/Preboot/Cryptexes/OS/System/Library/dyld",
        "/System/Library/Caches/com.apple.dyld",
        NULL};
    static const char prefix[] = "dyld_shared_cache_arm64.";
    char path[512], line[256];
    /* subfiles: numeric suffix, big ones carry the hot text; take the
     * four smallest numeric suffixes among files >= 16 MiB. */
    struct { char name[64]; long long size; } pick[4];
    unsigned picked = 0;
    DIR *dir = NULL;
    for (unsigned i = 0; candidates[i] && !dir; i++) {
        dir = opendir(candidates[i]);
        if (!dir) continue;
        struct dirent *entry;
        while ((entry = readdir(dir)) != NULL) {
            const char *suffix = entry->d_name + sizeof(prefix) - 1;
            size_t plen = sizeof(prefix) - 1;
            if (strncmp(entry->d_name, prefix, plen) != 0 || !*suffix) continue;
            bool numeric = true;
            for (const char *p = suffix; *p; p++)
                if (*p < '0' || *p > '9') { numeric = false; break; }
            if (!numeric) continue;   /* skip .symbols/.dylddata/.maps */
            snprintf(path, sizeof(path), "%s/%s", candidates[i], entry->d_name);
            struct stat st;
            if (stat(path, &st) != 0 || st.st_size < 16 * 1024 * 1024) continue;
            long long number = atoll(suffix);
            unsigned slot = picked;
            for (unsigned j = 0; j < picked; j++)
                if (number < atoll(pick[j].name + sizeof(prefix) - 1)) { slot = j; break; }
            if (slot >= sizeof(pick) / sizeof(pick[0])) continue;
            if (picked < sizeof(pick) / sizeof(pick[0])) picked++;
            for (unsigned j = sizeof(pick) / sizeof(pick[0]) - 1; j > slot; j--)
                pick[j] = pick[j - 1];
            snprintf(pick[slot].name, sizeof(pick[slot].name), "%s", entry->d_name);
            pick[slot].size = st.st_size;
        }
        closedir(dir);
    }
    if (!picked) {
        int n = snprintf(line, sizeof(line), "Pong cache pre-read: no cache subfiles found");
        if (n > 0) pong_console_line(line);
        return NULL;
    }
    void *chunk = malloc(1024 * 1024);
    if (!chunk) return NULL;
    for (unsigned i = 0; i < picked; i++) {
        int fd = -1;
        for (unsigned d = 0; candidates[d] && fd < 0; d++) {
            char full[512];
            snprintf(full, sizeof(full), "%s/%s", candidates[d], pick[i].name);
            fd = open(full, O_RDONLY | O_CLOEXEC);
        }
        if (fd < 0) continue;
        long long want = pick[i].size < 64 * 1024 * 1024 ? pick[i].size : 64 * 1024 * 1024;
        long long done = 0;
        struct timespec a, b;
        clock_gettime(CLOCK_MONOTONIC, &a);
        while (done < want) {
            ssize_t got = read(fd, chunk, (size_t)(want - done) < 1024 * 1024
                               ? (size_t)(want - done) : 1024 * 1024);
            if (got <= 0) break;
            done += got;
        }
        clock_gettime(CLOCK_MONOTONIC, &b);
        close(fd);
        long ms = (b.tv_sec - a.tv_sec) * 1000 + (b.tv_nsec - a.tv_nsec) / 1000000;
        int n = snprintf(line, sizeof(line),
                         "Pong cache pre-read: %s MiB=%lld ms=%ld", pick[i].name, done / (1024 * 1024), ms);
        if (n > 0) pong_console_line(line);
    }
    free(chunk);
    return NULL;
}

/* External process watcher (runningboardd only): fairplayd.A2 kept
 * spawning and disappearing without a recorded exit reason (v64). This
 * polls kern.proc from OUTSIDE the observed services - the usermanagerd
 * regression taught us not to add threads inside them - and reports
 * appearance/disappearance/PID changes for the boot-critical set. */
static void *watch_worker(void *unused)
{
    (void)unused;
    static const char *names[] = {
        "fairplayd.A2", "fairplaydeviceidentityd", "locationd", "timed",
        "lockdownd", "usermanagerd", "containermanagerd", "PosterBoard",
        "SpringBoard", "backboardd", "runningboardd", "lsd",
        "com.apple.datamig", "com.apple.migrationplug", "datamigratorhelper",
        "securityd", "mobileassetd", NULL};
    enum { watched = sizeof(names) / sizeof(names[0]) - 1 };
    int previous[watched];
    for (unsigned i = 0; i < watched; i++) previous[i] = -1;
    int mib[3] = {CTL_KERN, KERN_PROC, KERN_PROC_ALL};
    for (;;) {
        sleep(5);
        size_t length = 0;
        if (sysctl(mib, 3, NULL, &length, NULL, 0) != 0 || length == 0) continue;
        struct kinfo_proc *table = malloc(length);
        if (!table) continue;
        if (sysctl(mib, 3, table, &length, NULL, 0) != 0) { free(table); continue; }
        unsigned count = (unsigned)(length / sizeof(*table));
        char line[192];
        for (unsigned i = 0; i < watched; i++) {
            int pid = 0;
            size_t match = strlen(names[i]);
            if (match > MAXCOMLEN - 1) match = MAXCOMLEN - 1;
            for (unsigned j = 0; j < count; j++)
                if (strncmp(table[j].kp_proc.p_comm, names[i], match) == 0) {
                    pid = table[j].kp_proc.p_pid;
                    break;
                }
            if (pid != previous[i]) {
                int n = snprintf(line, sizeof(line), "Pong watch: %s pid %d -> %d",
                                 names[i], previous[i], pid);
                if (n > 0) pong_console_line(line);
                previous[i] = pid;
            }
        }
        free(table);
    }
    return NULL;
}

static void *worker(void *unused)
{
    (void)unused;
    char path[256];
    if (geteuid() == 0) {
        (void)mkdir("/private/var/logs", 0755);
    } else {
        /* The mobile worker provisions its own directory tree: a root-owned
         * 0755 Logs directory would not be writable by this uid, and the
         * graphics takeover makes a stderr fallback unreadable. */
        (void)mkdir("/private/var/mobile", 0755);
        (void)mkdir("/private/var/mobile/Library", 0755);
        (void)mkdir("/private/var/mobile/Library/Logs", 0755);
    }
    snapshot_path(path, sizeof(path));
    int fd = open(path, O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC | O_NOFOLLOW, 0600);
    if (fd >= 0) (void)fchmod(fd, 0600);
    if (fd < 0) fd = dup(STDERR_FILENO);
    if (fd < 0) return NULL;
    unsigned elapsed = 0;
    /* The 60-second tick grid means batches land on 60/120/240/600/900. */
    const unsigned early[] = {60, 120, 240, 600, 900};
    unsigned early_index = 0;
    unsigned next_snapshot = early[0];
    for (;;) {
        sleep(60);
        elapsed += 60;
        rotate_if_huge(fd, elapsed);
        tick(fd, elapsed);
        if (elapsed >= next_snapshot) {
            snapshot(fd, elapsed);
            if (early_index < sizeof(early) / sizeof(early[0]) - 1)
                next_snapshot = early[++early_index];
            else
                next_snapshot += 600;
        }
    }
    return NULL;
}

void pong_boot_snapshot_start(const char *name)
{
    if (strcmp(name, "SpringBoard") && strcmp(name, "backboardd") &&
        strcmp(name, "lsd") && strcmp(name, "containermanagerd") &&
        strcmp(name, "containermanagerd_system") &&
        strcmp(name, "runningboardd") && strcmp(name, "usermanagerd") &&
        strcmp(name, "PosterBoard") && strcmp(name, "wifid") &&
        strcmp(name, "com.apple.datamigrator") &&
        strcmp(name, "com.apple.migrationpluginwrapper") &&
        strcmp(name, "datamigratorhelper")) return;
    process_name = name;
    replay_previous();
    pthread_attr_t attr;
    if (pthread_attr_init(&attr)) return;
    pthread_t thread;
    if (pthread_attr_setdetachstate(&attr, PTHREAD_CREATE_DETACHED) == 0)
        (void)pthread_create(&thread, &attr, worker, NULL);
    /* Only one root, early, stable process owns the auxiliary workers. */
    if (geteuid() == 0 && !strcmp(name, "runningboardd")) {
        (void)pthread_create(&thread, &attr, console_worker, NULL);
        (void)pthread_create(&thread, &attr, cache_worker, NULL);
        (void)pthread_create(&thread, &attr, watch_worker, NULL);
    }
    pthread_attr_destroy(&attr);
}
