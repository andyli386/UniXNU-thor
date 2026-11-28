/* SPDX-License-Identifier: MIT */
/* Pong probe v238: a passive look into three processes that had left no trace.
 *
 *   PosterBoard   starts at about 88 s, stays, and never produces a wallpaper
 *   wifid         the system's Wi-Fi policy daemon
 *   airportd      talks to the Wi-Fi driver; it reads three values at about 63 s and stops
 *
 * The probe changes nothing in the process.  It reports the launch, samples every thread five
 * times (image, offset and the exported symbol dladdr knows), looks at the wallpaper folders
 * (PosterBoard only) and reports how the process ends.  Lines go through the kernel's diagnostic
 * channel (kern.pong_diagnostic) to the console, as "PONG PROBE <process>[pid]: Pong Probe: ...". */
#include <dispatch/dispatch.h>
#include <dirent.h>
#include <dlfcn.h>
#include <errno.h>
#include <inttypes.h>
#include <mach-o/dyld.h>
#include <mach/arm/thread_status.h>
#include <mach/mach.h>
#include <pthread.h>
#include <signal.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/sysctl.h>
#include <time.h>
#include <unistd.h>

#define PROBE_PREFIX "Pong Probe: "
#define PROBE_THREADS 12
#define PROBE_FRAMES 10

static uint64_t started_ms;
static unsigned lines;

static uint64_t now_ms(void)
{
    struct timespec now = {0};
    (void)clock_gettime(CLOCK_MONOTONIC, &now);
    return (uint64_t)now.tv_sec * 1000 + (uint64_t)now.tv_nsec / 1000000;
}

/* One line to the kernel console; the channel takes at most 255 bytes. */
static void say(const char *format, ...) __attribute__((format(printf, 1, 2)));
static void vsay(const char *format, va_list list);
static void say(const char *format, ...)
{
    va_list list;
    va_start(list, format);
    vsay(format, list);
    va_end(list);
}
static void vsay(const char *format, va_list list)
{
    if (__atomic_fetch_add(&lines, 1, __ATOMIC_RELAXED) >= 270) {
        return;
    }
    char line[256];
    int length = snprintf(line, sizeof(line), PROBE_PREFIX "t=%" PRIu64 "ms ", (uint64_t)(now_ms() - started_ms));
    if (length > 0 && (size_t)length < sizeof(line)) {
        length += vsnprintf(line + length, sizeof(line) - (size_t)length, format, list);
    }
    if (length <= 0 || length >= 255) {
        return;
    }
#ifdef PROBE_HOST_TEST
    (void)write(2, line, (size_t)length);
    (void)write(2, "\n", 1);
    return;
#endif
    static int mib[CTL_MAXNAME];
    static size_t count;
    if (!count) {
        size_t n = CTL_MAXNAME;
        if (sysctlnametomib("kern.pong_diagnostic", mib, &n) == 0) {
            count = n;
        }
    }
    if (count) {
        (void)sysctl(mib, (u_int)count, NULL, NULL, line, (size_t)length);
    }
}

static const char *base_name(const char *path)
{
    const char *slash = strrchr(path, '/');
    return slash ? slash + 1 : path;
}

/* "Image!symbol+0x1c" when dladdr knows an exported symbol at or below the address, else "Image+0x1234". */
static void describe(uint64_t address, char *out, size_t capacity)
{
    Dl_info info = {0};
    if (dladdr((const void *)(uintptr_t)address, &info) && info.dli_fname && info.dli_fbase) {
        const char *image = base_name(info.dli_fname);
        if (info.dli_sname && (uint64_t)info.dli_saddr <= address && strlen(info.dli_sname) < 96) {
            snprintf(out, capacity, "%.32s!%.96s+0x%" PRIx64, image, info.dli_sname, address - (uint64_t)info.dli_saddr);
            return;
        }
        snprintf(out, capacity, "%.32s+0x%" PRIx64, image, address - (uint64_t)info.dli_fbase);
        return;
    }
    snprintf(out, capacity, "%" PRIx64, address);
}

static uint64_t frame_address(uint64_t value)
{
    /* This arm64 shim also reads the frame records of arm64e guest code. */
    return value & UINT64_C(0x0000ffffffffffff);
}

/* Reads one frame record at address; false when it cannot be read. */
static bool read_pair(uint64_t address, uint64_t pair[2])
{
    vm_size_t size = 0;
    return address && !(address & 15) &&
        vm_read_overwrite(mach_task_self(), (vm_address_t)address, 2 * sizeof(uint64_t),
                          (vm_address_t)(uintptr_t)pair, &size) == KERN_SUCCESS && size == 2 * sizeof(uint64_t);
}

/* Prints frame records from a frame pointer, newest first, under a heading that names the thread. */
static void print_frames(uint64_t fp, const char *heading, const char *continuation)
{
    char line[256], frame[136];
    int length = snprintf(line, sizeof(line), "%s:", heading);
    uint64_t cursor = fp;
    for (unsigned i = 0; i < PROBE_FRAMES; ++i) {
        uint64_t pair[2] = {0, 0};
        if (!read_pair(cursor, pair)) {
            break;
        }
        describe(frame_address(pair[1]), frame, sizeof(frame));
        if (length > 0 && (size_t)length + strlen(frame) + 1 >= sizeof(line) - 1) {
            say("%s", line);
            length = snprintf(line, sizeof(line), "%s +%u:", continuation, i);
        }
        if (length > 0 && (size_t)length < sizeof(line)) {
            length += snprintf(line + length, sizeof(line) - (size_t)length, " %s", frame);
        }
        if (pair[0] <= cursor) {
            break;
        }
        cursor = pair[0];
    }
    say("%s", line);
}

struct thread_sample {
    uint64_t id;
    int run_state;
    unsigned frames;
    uint64_t fp;
    char name[24];
    uint64_t stack[PROBE_FRAMES];
};
static struct thread_sample samples[PROBE_THREADS];
static unsigned sample_count;

/* Watchdog queue: every thread is stopped for its state and frame records only; names and symbols
 * are resolved on the probe queue once they all run again. */
static void collect(void)
{
    thread_act_array_t list = NULL;
    mach_msg_type_number_t count = 0;
    sample_count = 0;
    if (task_threads(mach_task_self(), &list, &count) != KERN_SUCCESS) {
        return;
    }
    const thread_t self = mach_thread_self();
    for (mach_msg_type_number_t i = 0; i < count && sample_count < PROBE_THREADS; ++i) {
        const thread_t thread = list[i];
        if (thread == self || !MACH_PORT_VALID(thread)) {
            continue;
        }
        struct thread_sample *slot = &samples[sample_count];
        memset(slot, 0, sizeof(*slot));
        if (thread_suspend(thread) != KERN_SUCCESS) {
            continue;
        }
        arm_thread_state64_t state = {0};
        mach_msg_type_number_t words = ARM_THREAD_STATE64_COUNT;
        const kern_return_t result = thread_get_state(thread, ARM_THREAD_STATE64, (thread_state_t)&state, &words);
        thread_basic_info_data_t info = {0};
        words = THREAD_BASIC_INFO_COUNT;
        (void)thread_info(thread, THREAD_BASIC_INFO, (thread_info_t)&info, &words);
        if (result == KERN_SUCCESS) {
            const uint64_t sp = arm_thread_state64_get_sp(state);
            uint64_t cursor = arm_thread_state64_get_fp(state);
            slot->stack[slot->frames++] = frame_address(arm_thread_state64_get_pc(state));
            slot->fp = cursor;
            for (unsigned guard = 0; slot->frames < PROBE_FRAMES && guard < PROBE_FRAMES; ++guard) {
                uint64_t pair[2] = {0, 0};
                if (cursor < sp || cursor - sp >= 8 * 1024 * 1024 || !read_pair(cursor, pair)) {
                    break;
                }
                slot->stack[slot->frames++] = frame_address(pair[1]);
                if (pair[0] <= cursor) {
                    break;
                }
                cursor = pair[0];
            }
            slot->run_state = info.run_state;
        }
        thread_resume(thread);
        if (result != KERN_SUCCESS) {
            continue;
        }
        thread_identifier_info_data_t identity = {0};
        mach_msg_type_number_t id_words = THREAD_IDENTIFIER_INFO_COUNT;
        if (thread_info(thread, THREAD_IDENTIFIER_INFO, (thread_info_t)&identity, &id_words) == KERN_SUCCESS) {
            slot->id = identity.thread_id;
        }
        pthread_t pthread = pthread_from_mach_thread_np(thread);
        if (pthread) {
            (void)pthread_getname_np(pthread, slot->name, sizeof(slot->name));
        }
        ++sample_count;
    }
    if (MACH_PORT_VALID(self)) {
        (void)mach_port_deallocate(mach_task_self(), self);
    }
    for (mach_msg_type_number_t i = 0; i < count; ++i) {
        (void)mach_port_deallocate(mach_task_self(), list[i]);
    }
    vm_deallocate(mach_task_self(), (vm_address_t)list, count * sizeof(*list));
}

/* Probe queue. */
static void print_threads(const char *why)
{
    char line[256], frame[136], heading[96], continuation[96];
    say("sample %s threads=%u", why, sample_count);
    for (unsigned t = 0; t < sample_count; ++t) {
        const struct thread_sample *thread = &samples[t];
        if (thread->frames == 1 && thread->stack[0] == 0) {
            continue;                       /* a thread that is just starting or leaving */
        }
        snprintf(heading, sizeof(heading), "sample %s tid=%" PRIu64 " name=%.20s run=%d", why, thread->id,
                 thread->name, thread->run_state);
        snprintf(continuation, sizeof(continuation), "sample %s tid=%" PRIu64, why, thread->id);
        int length = snprintf(line, sizeof(line), "%s:", heading);
        for (unsigned i = 0; i < thread->frames; ++i) {
            describe(thread->stack[i], frame, sizeof(frame));
            if (length > 0 && (size_t)length + strlen(frame) + 1 >= sizeof(line) - 1) {
                say("%s", line);
                length = snprintf(line, sizeof(line), "%s +%u:", continuation, i);
            }
            if (length > 0 && (size_t)length < sizeof(line)) {
                length += snprintf(line + length, sizeof(line) - (size_t)length, " %s", frame);
            }
        }
        say("%s", line);
    }
}

static void sample(void *why)
{
    collect();
    print_threads((const char *)why);
}

static void look_at_folders(void *unused)
{
    (void)unused;
    static const char *const paths[] = {
        "/private/var/mobile/Library/com.apple.wallpaper",
        "/private/var/mobile/Library/com.apple.wallpaper/Store",
        "/private/var/mobile/Library/com.apple.wallpaper/Store/Index.plist",
        "/private/var/mobile/Library/PosterBoard",
        "/Library/Wallpaper/Collections",
        "/System/Library/Wallpaper",
    };
    for (unsigned i = 0; i < sizeof(paths) / sizeof(paths[0]); ++i) {
        struct stat st;
        if (stat(paths[i], &st) == 0) {
            say("path %s exists type=%s uid=%u mode=%o size=%lld", paths[i],
                S_ISDIR(st.st_mode) ? "dir" : S_ISREG(st.st_mode) ? "file" : "other",
                st.st_uid, st.st_mode & 0777, (long long)st.st_size);
        } else {
            say("path %s stat errno=%d", paths[i], errno);
        }
    }
    DIR *dir = opendir("/Library/Wallpaper/Collections");
    if (dir) {
        unsigned n = 0;
        char line[256] = "wallpaper collections:";
        const struct dirent *entry;
        while (n < 14 && (entry = readdir(dir)) != NULL) {
            if (entry->d_name[0] == '.') {
                continue;
            }
            size_t used = strlen(line);
            snprintf(line + used, sizeof(line) - used, " %.28s", entry->d_name);
            ++n;
        }
        closedir(dir);
        say("%s (%u)", line, n);
    } else {
        say("wallpaper collections opendir errno=%d", errno);
    }
}

static void report_exit(void)
{
    say("exit");
}

/* A fatal signal: the faulting place with its symbol, the frames, then the default action. */
static void fatal(int number, siginfo_t *info, void *context)
{
    char where[136], lr[136], heading[96];
    const ucontext_t *uc = context;
    const arm_thread_state64_t *state = &uc->uc_mcontext->__ss;
    describe(frame_address(arm_thread_state64_get_pc(*state)), where, sizeof(where));
    describe(frame_address(arm_thread_state64_get_lr(*state)), lr, sizeof(lr));
    say("signal=%d code=%d pc=%s lr=%s", number, info ? info->si_code : 0, where, lr);
    snprintf(heading, sizeof(heading), "signal %d frames", number);
    print_frames(frame_address(arm_thread_state64_get_fp(*state)), heading, heading);
    /* Let the process end the way it would have without the probe. */
    struct sigaction action;
    memset(&action, 0, sizeof(action));
    action.sa_handler = SIG_DFL;
    (void)sigaction(number, &action, NULL);
    signal(number, SIG_DFL);
    raise(number);
}

static void install_handlers(void)
{
    struct sigaction action;
    memset(&action, 0, sizeof(action));
    action.sa_flags = SA_SIGINFO;
    action.sa_sigaction = fatal;
    static const int list[] = {SIGABRT, SIGSEGV, SIGBUS, SIGILL, SIGFPE, SIGTRAP};
    for (unsigned i = 0; i < sizeof(list) / sizeof(list[0]); ++i) {
        (void)sigaction(list[i], &action, NULL);
    }
}

__attribute__((constructor))
static void probe_start(void)
{
    const char *process = getprogname();
    if (!process) {
        return;
    }
    const bool poster = !strcmp(process, "PosterBoard");
    const bool wifi = !strcmp(process, "wifid") || !strcmp(process, "airportd");
    if (!poster && !wifi) {
        return;
    }
    started_ms = now_ms();
    char path[512] = "?";
    uint32_t path_length = sizeof(path);
    if (_NSGetExecutablePath(path, &path_length) != 0) {
        strlcpy(path, "(too long)", sizeof(path));
    }
    const char *service = getenv("XPC_SERVICE_NAME");
    const char *home = getenv("HOME");
    say("loaded %s pid=%d path=%.72s home=%.40s service=%.56s", process, getpid(), base_name(path),
        home ? home : "-", service ? service : "-");
    dispatch_queue_t queue = dispatch_get_global_queue(QOS_CLASS_UTILITY, 0);
    static const unsigned delays[] = {300, 1500, 5000, 20000, 60000};
    static const char *const whys[] = {"0.3s", "1.5s", "5s", "20s", "60s"};
    for (unsigned i = 0; i < sizeof(delays) / sizeof(delays[0]); ++i) {
        dispatch_after_f(dispatch_time(DISPATCH_TIME_NOW, (int64_t)delays[i] * NSEC_PER_MSEC), queue,
                         (void *)(uintptr_t)whys[i], sample);
    }
    if (poster) {
        dispatch_after_f(dispatch_time(DISPATCH_TIME_NOW, (int64_t)4000 * NSEC_PER_MSEC), queue, NULL,
                         look_at_folders);
    }
    install_handlers();
    (void)atexit(report_exit);
}
