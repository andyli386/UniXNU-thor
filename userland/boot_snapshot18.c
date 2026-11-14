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
#include <mach/mach_host.h>
#include <mach/arm/thread_status.h>
#include <mach-o/dyld.h>
#include <mach/vm_statistics.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/event.h>
#include <sys/param.h>
#include <sys/proc.h>
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

/* v65 review P1: receipts written only to /dev/console never reach
 * kern.msgbuf (cnwrite forwards to the tty; log_putc is kernel printf
 * only), so PongConsole.txt cannot capture them. The auxiliary workers
 * log through this bounded persistent file instead, timestamped, then
 * mirror to the console for the pre-takeover text phase. One serialized
 * writer; 4 MiB rotation. */
static pthread_mutex_t diag_lock = PTHREAD_MUTEX_INITIALIZER;
static int diag_fd = -2;   /* -2 not tried, -1 unavailable */

/* The device SDK ships no proc_info headers; these are the kernel ABI
 * definitions (xnu-work bsd/sys/proc_info.h, stable layout). */
#define PONG_PROC_PIDTBSDINFO 3
struct pong_proc_bsdinfo {
    uint32_t pbi_flags, pbi_status, pbi_xstatus, pbi_pid, pbi_ppid;
    uint32_t pbi_uid, pbi_gid, pbi_ruid, pbi_rgid, pbi_svuid, pbi_svgid;
    uint32_t rfu_1;
    char pbi_comm[MAXCOMLEN];
    char pbi_name[2 * MAXCOMLEN];
    uint32_t pbi_nfiles, pbi_pgid, pbi_pjobc, e_tdev, e_tpgid;
    int32_t pbi_nice;
    uint64_t pbi_start_tvsec, pbi_start_tvusec;
};
extern int proc_pidinfo(int pid,int flavor,uint64_t arg,void *buffer,int buffersize);

/* Shared with ui_compat18.c (fairplayd.A2 interposers). */
void pong_diag_line(const char *line)
{
    pthread_mutex_lock(&diag_lock);
    if(diag_fd==-2){
        diag_fd=open("/private/var/logs/PongDiag.txt",
                     O_WRONLY|O_CREAT|O_APPEND|O_CLOEXEC,0600);
        if(diag_fd>=0)
            dprintf(diag_fd,"Pong diag: worker start pid=%d time=%lld\n",
                    getpid(),(long long)time(NULL));
    }
    if(diag_fd>=0){
        dprintf(diag_fd,"[%lld] %s\n",(long long)time(NULL),line);
        struct stat st;
        if(fstat(diag_fd,&st)==0&&st.st_size>4*1024*1024){
            (void)fsync(diag_fd);
            close(diag_fd);
            diag_fd=open("/private/var/logs/PongDiag.txt",
                         O_WRONLY|O_CREAT|O_TRUNC|O_CLOEXEC,0600);
        }
    }
    pthread_mutex_unlock(&diag_lock);
    pong_console_line(line);
}

static void diag_say(const char *format,...)
{
    char line[384];
    va_list arguments;
    va_start(arguments,format);
    int n=vsnprintf(line,sizeof(line),format,arguments);
    va_end(arguments);
    if(n<=0)return;
    pong_diag_line(line);
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
    char line[1600];
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
                     j < 24 && fp >= sp && fp - sp < 8 * 1024 * 1024 && !(fp & 15);
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
    if (out < 0) {
        diag_say("Pong console capture: open failed errno=%d", errno);
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
                diag_say("Pong console capture: proc_info dmesg errno=%d", errno);
            }
            sleep(60);
            continue;
        }
        if (!announced) {
            announced = true;
            diag_say("Pong console capture: proc_info dmesg pid=%d first=%dB",
                     getpid(), got);
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
 * evidence (hypothesis, to be measured): each 4 KiB page-in is a
 * synchronous single-slot UFS command, so daemons spend a long time in
 * dyld/ObjC init (fairplayd.A2 instances seen mid-page-in and later
 * absent; PosterBoard hit the 30 s 0x8badf00d watchdog) while
 * memorystatus lines accumulate by the thousand per run (the 46,879
 * figure in the earlier commit mixed two boots' consoles). Sequential
 * read() of hot cache extents populates the same UBC the page-ins then
 * hit - the ufs116 recipe from the iOS16 line.
 * The extents are COMPUTED for the 22H374 cache, not copied from the
 * iOS16 "first four subfiles" layout: every image observed in the
 * v61-v64 stack symbolication was mapped through the subfile headers to
 * (subfile, file offset) - libobjc/Foundation/CoreServices/
 * CoreFoundation/.01 0-40 MB, QuartzCore/.01+66.7, dispatch+
 * BackBoardServices+BaseBoard/.03+51-53, RunningBoardServices/.05+53,
 * ChronoServices/.05+36, BoardServices/.09+19, libsystem_kernel/.25+23,
 * SpringBoard/.25+89, DataMigration/.27+26, FrontBoard/.11+51, libxpc+
 * libsystem_pthread/.31+11, RunningBoard/.40+81, GeoServices+.03+25,
 * DataDetection/.31+83, dyld/.11+90, ContainerManagerCommon/.25+75,
 * CoreLocation/.05+30, libsystem_trace/.09+38 - and windows budgeted
 * around them. Validated against the frame addresses of the three
 * pulled runs (11,373 cache-resident stack samples): 97.6% fall inside
 * these windows - a statement about sampled stack frames, not about the
 * full cold-fault working set, and not about fairplayd.A2's own file.
 * 327 MiB requested (~318 MiB reachable given file sizes). A memory-
 * pressure gate defers each window while free+inactive+speculative is
 * thin (16,000 pages; v65 field data showed the pool sits permanently
 * below the previous 28,000 threshold, stalling every window 150 s).
 * Disable with pong_cache_prefetch=0. */
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
        "/System/Library/Caches/com.apple.dyld",
        "/System/Volumes/Preboot/Cryptexes/OS/System/Library/dyld",
        NULL};
    static const char prefix[] = "dyld_shared_cache_arm64.";
    static const struct { unsigned sub, start_mib, length_mib; } extents[] = {
        { 1,  0, 68}, { 3, 21, 19}, { 3, 44, 30}, { 5, 30, 26},
        { 9, 10, 30}, {11, 46, 12}, {11, 88, 10}, {25, 16, 24},
        {25, 72, 26}, {27, 14, 28}, {31,  0, 28}, {31, 82, 10},
        {40, 78, 16},
    };
    char path[512], full[512];
    void *chunk = malloc(1024 * 1024);
    if (!chunk) {
        diag_say("Pong cache pre-read: buffer alloc failed");
        return NULL;
    }
    int fd = -1;
    unsigned opened_sub = 0;
    for (unsigned i = 0; i < sizeof(extents) / sizeof(extents[0]); i++) {
        if (fd < 0 || extents[i].sub != opened_sub) {
            if (fd >= 0) close(fd);
            fd = -1;
            opened_sub = extents[i].sub;
            snprintf(path, sizeof(path), "%s%02u", prefix, extents[i].sub);
            int saved = 0;
            for (unsigned d = 0; candidates[d] && fd < 0; d++) {
                snprintf(full, sizeof(full), "%s/%s", candidates[d], path);
                fd = open(full, O_RDONLY | O_CLOEXEC);
                if (fd < 0) saved = errno;
            }
            if (fd < 0) {
                diag_say("Pong cache pre-read: %s open errno=%d", path, saved);
                continue;
            }
            diag_say("Pong cache pre-read: opened %s", full);
        }
        struct stat st;
        if (fstat(fd, &st) != 0) {
            diag_say("Pong cache pre-read: .%02u fstat errno=%d", extents[i].sub, errno);
            continue;
        }
        off_t begin = (off_t)extents[i].start_mib << 20;
        off_t want = (off_t)extents[i].length_mib << 20;
        const char *note = "complete";
        if (begin >= st.st_size) {
            diag_say("Pong cache pre-read: .%02u [%u+%uMiB] skipped, past EOF %lldB",
                     extents[i].sub, extents[i].start_mib, extents[i].length_mib,
                     (long long)st.st_size);
            continue;
        }
        if (begin + want > st.st_size) {
            want = st.st_size - begin;
            note = "clamped to EOF";
        }
        /* v67 review: this XNU's vm_statistics64 free_count ALREADY
         * includes speculative pages, so the pool must not add them a
         * second time. Log the raw values; the gate releases either on
         * the threshold, after the wait budget (max-waits), or when the
         * statistics call itself fails (stats-failed) - the receipt
         * says which, so "low memory defers reads" is never assumed. */
        const char *release = "stats-failed";
        unsigned waits = 0;
        for (;;) {
            vm_statistics64_data_t vm;
            memset(&vm, 0, sizeof(vm));
            mach_msg_type_number_t count = HOST_VM_INFO64_COUNT;
            if (host_statistics64(mach_host_self(), HOST_VM_INFO64,
                                  (host_info64_t)&vm, &count) != KERN_SUCCESS)
                break;
            uint64_t pool = (uint64_t)vm.free_count + vm.inactive_count;
            diag_say("Pong cache pre-read: pool free=%u(incl speculative) inactive=%u",
                     vm.free_count, vm.inactive_count);
            if (pool >= 16000) { release = "gate-ok"; break; }
            if (waits >= 5) { release = "max-waits"; break; }
            diag_say("Pong cache pre-read: deferred, pool=%llu pages, wait %u of 5",
                     (unsigned long long)pool, waits + 1);
            sleep(30);
            waits++;
        }
        if (lseek(fd, begin, SEEK_SET) < 0) {
            diag_say("Pong cache pre-read: .%02u lseek errno=%d", extents[i].sub, errno);
            continue;
        }
        off_t done = 0;
        int read_errno = 0;
        struct timespec a, b;
        clock_gettime(CLOCK_MONOTONIC, &a);
        while (done < want) {
            size_t take = (size_t)(want - done) < 1024 * 1024
                              ? (size_t)(want - done) : 1024 * 1024;
            ssize_t got = read(fd, chunk, take);
            if (got < 0 && errno == EINTR) continue;
            if (got <= 0) { if (got < 0) read_errno = errno; break; }
            done += got;
        }
        clock_gettime(CLOCK_MONOTONIC, &b);
        long ms = (b.tv_sec - a.tv_sec) * 1000 + (b.tv_nsec - a.tv_nsec) / 1000000;
        const char *status = done == want ? "complete"
                           : (read_errno ? "read-error" : "eof-partial");
        diag_say("Pong cache pre-read: .%02u [%u+%uMiB] read=%lld of %lld bytes ms=%ld %s %s %s",
                 extents[i].sub, extents[i].start_mib, extents[i].length_mib,
                 (long long)done, (long long)want, ms, note, status, release);
    }
    if (fd >= 0) close(fd);
    free(chunk);
    diag_say("Pong cache pre-read: worker finished");
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
    /* v65 review: match PID SETS, not the first hit - p_comm is 16 chars
     * so containermanagerd and containermanagerd_system are
     * indistinguishable in kinfo (they even share pbi_name, being the
     * same binary); the pid set makes that ambiguity explicit instead of
     * silently tracking the wrong service. pbi_name (32 chars) refines
     * every candidate that a p_comm prefix caught. Zombies are marked
     * 'z' - a zombie is itself signal of an unreaped exit. For the
     * exit-tracked services the kernel reports the exit status through
     * EVFILT_PROC NOTE_EXITSTATUS, closing the gap between polls for
     * processes that die inside 5 s (the reason field is still not
     * captured - this remains an observation aid, not a crash log). */
    static const char *names[] = {
        "fairplayd.A2", "fairplaydeviceidentityd", "locationd", "timed",
        "lockdownd", "usermanagerd", "containermanagerd", "PosterBoard",
        "SpringBoard", "backboardd", "runningboardd", "lsd",
        "com.apple.datamig", "com.apple.migrationplug", "datamigratorhelper",
        "securityd", "mobileassetd", NULL};
    static const bool exit_tracked[] = {
        true, false, false, true, false, true, false, true,
        false, false, false, false, false, false, false, false, false};
    enum { watched = sizeof(names) / sizeof(names[0]) - 1 };
    char previous[watched][96];
    for (unsigned i = 0; i < watched; i++) previous[i][0] = 0;
    int mib[3] = {CTL_KERN, KERN_PROC, KERN_PROC_ALL};
    int kq = kqueue();
    if (kq < 0) diag_say("Pong watch: kqueue errno=%d", errno);
    for (;;) {
        sleep(5);
        size_t length = 0;
        if (sysctl(mib, 3, NULL, &length, NULL, 0) != 0 || length == 0) continue;
        struct kinfo_proc *table = malloc(length);
        if (!table) { diag_say("Pong watch: table alloc failed"); continue; }
        if (sysctl(mib, 3, table, &length, NULL, 0) != 0) { free(table); continue; }
        unsigned count = (unsigned)(length / sizeof(*table));
        for (unsigned i = 0; i < watched; i++) {
            size_t match = strlen(names[i]);
            if (match > MAXCOMLEN - 1) match = MAXCOMLEN - 1;
            int pids[8];
            bool zombies[8];
            unsigned found = 0;
            for (unsigned j = 0; j < count && found < 8; j++) {
                if (strncmp(table[j].kp_proc.p_comm, names[i], match) != 0) continue;
                struct pong_proc_bsdinfo info;
                memset(&info, 0, sizeof(info));
                bool named = proc_pidinfo(table[j].kp_proc.p_pid, PONG_PROC_PIDTBSDINFO,
                                          0, &info, (int)sizeof(info)) == (int)sizeof(info);
                if (named && strncmp(info.pbi_name, names[i], match) != 0) {
                    bool other = false;
                    for (unsigned k = 0; k < watched; k++)
                        if (k != i && strncmp(info.pbi_name, names[k], strlen(names[k])) == 0)
                            { other = true; break; }
                    if (other) continue;   /* counted under its own name */
                }
                pids[found] = table[j].kp_proc.p_pid;
                zombies[found] = table[j].kp_proc.p_stat == SZOMB;
                found++;
                if (exit_tracked[i] && kq >= 0) {
                    struct kevent event;
                    EV_SET(&event, table[j].kp_proc.p_pid, EVFILT_PROC,
                           EV_ADD | EV_ONESHOT, NOTE_EXIT | NOTE_EXITSTATUS, 0, 0);
                    (void)kevent(kq, &event, 1, NULL, 0, NULL);
                }
            }
            char current[96];
            int used = snprintf(current, sizeof(current), "[");
            for (unsigned p = 0; p < found && used > 0
                 && (size_t)used < sizeof(current); p++)
                used += snprintf(current + used, sizeof(current) - used,
                                 "%s%d%s", p ? "," : "", pids[p], zombies[p] ? "z" : "");
            if (used > 0 && (size_t)used < sizeof(current))
                used += snprintf(current + used, sizeof(current) - used, "]");
            if (used <= 0 || (size_t)used >= sizeof(current)) continue;
            if (strcmp(current, previous[i]) != 0) {
                diag_say("Pong watch: %s %s -> %s", names[i],
                         previous[i][0] ? previous[i] : "(none)", current);
                snprintf(previous[i], sizeof(previous[i]), "%s", current);
            }
        }
        free(table);
        if (kq >= 0) {
            struct kevent events[16];
            struct timespec zero = {0, 0};
            int n = kevent(kq, NULL, 0, events, 16, &zero);
            for (int e = 0; e < n; e++)
                diag_say("Pong watch: pid %lu exited data=0x%x",
                         (unsigned long)events[e].ident,
                         (unsigned)(events[e].data & 0xffffffffu));
        }
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
    /* v71: schedule against CLOCK_MONOTONIC deadlines - the v70 grid
     * still derived elapsed from the sleep count, so batches carried
     * scheduling and sampling time with them. Ticks stay 60 s apart;
     * wrappers get a true 30 s first batch. */
    unsigned early[] = {60, 120, 240, 600, 900, 1050, 1200, 1500};
    if (!strcmp(process_name, "com.apple.migrationpluginwrapper"))
        early[0] = 30;
    unsigned early_index = 0;
    struct timespec boot;
    clock_gettime(CLOCK_MONOTONIC, &boot);
    uint64_t start_ms = (uint64_t)boot.tv_sec * 1000 + boot.tv_nsec / 1000000;
    uint64_t next_tick = start_ms + 60000;
    uint64_t next_snapshot = start_ms + (uint64_t)early[0] * 1000;
    for (;;) {
        sleep(5);
        struct timespec now;
        clock_gettime(CLOCK_MONOTONIC, &now);
        uint64_t ms = (uint64_t)now.tv_sec * 1000 + now.tv_nsec / 1000000;
        rotate_if_huge(fd, (unsigned)((ms - start_ms) / 1000));
        if ((int64_t)(ms - next_tick) >= 0) {
            tick(fd, (unsigned)((ms - start_ms) / 1000));
            next_tick += 60000;
        }
        if ((int64_t)(ms - next_snapshot) >= 0) {
            snapshot(fd, (unsigned)((ms - start_ms) / 1000));
            if (early_index < sizeof(early) / sizeof(early[0]) - 1)
                next_snapshot = start_ms + (uint64_t)early[++early_index] * 1000;
            else
                next_snapshot += 600000;
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
        strcmp(name, "locationd") && strcmp(name, "filecoordinationd") &&
        strcmp(name, "mediaremoted") && strcmp(name, "audiomxd") &&
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
