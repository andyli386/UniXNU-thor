/* SPDX-License-Identifier: MIT */
#include <dispatch/dispatch.h>
#include <dlfcn.h>
#include <errno.h>
#include <inttypes.h>
#include <mach/mach.h>
#include <mach/arm/thread_status.h>
#include <ptrauth.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/sysctl.h>
#include <time.h>
#include <unistd.h>

#define SETTINGS_STACK_PREFIX "Pong Settings: Preferences watchdog stack "

static mach_port_t main_thread;
static uint64_t started_ms;
static unsigned samples_pending;
static bool active;

static uint64_t monotonic_ms(void)
{
    struct timespec now = {0};
    (void)clock_gettime(CLOCK_MONOTONIC, &now);
    return (uint64_t)now.tv_sec * 1000 + (uint64_t)now.tv_nsec / 1000000;
}

static void report(char *line, size_t len)
{
    if (!len || len >= 255)
        return;
    int mib[CTL_MAXNAME];
    size_t count = CTL_MAXNAME;
    if (sysctlnametomib("kern.pong_diagnostic", mib, &count) == 0)
        (void)sysctl(mib, (u_int)count, NULL, NULL, line, len);
    line[len] = '\n';
    (void)write(STDERR_FILENO, line, len + 1);
}

static void report_install(void *context)
{
    (void)context;
    char line[256] = SETTINGS_STACK_PREFIX "armed";
    report(line, strlen(line));

    /* Probe the same libSystem entry as 22H374 timed with a short wait. */
    int (*get_uuid)(unsigned char *, const struct timespec *) = dlsym(RTLD_DEFAULT, "gethostuuid");
    const struct timespec limit = {0, 250 * NSEC_PER_MSEC};
    unsigned char identity[16];
    const uint64_t started = monotonic_ms();
    const int status = get_uuid ? get_uuid(identity, &limit) : -1;
    const int error = get_uuid ? (status ? errno : 0) : ENOSYS;
    int result_size = snprintf(line, sizeof(line), SETTINGS_STACK_PREFIX
                               "gethostuuid status=%d errno=%d elapsed=%" PRIu64 "ms",
                               status, error, monotonic_ms() - started);
    if (result_size > 0 && result_size < (int)sizeof(line))
        report(line, (size_t)result_size);

    /* Do not make sampling wait for the dyld loader lock. */
    const void *(*cache_range)(size_t *) =
        dlsym(RTLD_DEFAULT, "_dyld_get_shared_cache_range");
    size_t size = 0;
    const void *base = cache_range ? cache_range(&size) : NULL;
    int len = snprintf(line, sizeof(line), SETTINGS_STACK_PREFIX "cache=%p size=%zx", base, size);
    if (len > 0 && len < (int)sizeof(line))
        report(line, (size_t)len);
}

static uint64_t frame_return_address(uint64_t value)
{
#if __has_feature(ptrauth_calls)
    return (uintptr_t)ptrauth_strip((void *)(uintptr_t)value, ptrauth_key_return_address);
#else
    /* This arm64 shim can still unwind arm64e guest code's frame records. */
    return value & UINT64_C(0x0000ffffffffffff);
#endif
}

static void read_main_stack(unsigned delay)
{
    arm_thread_state64_t state = {0};
    mach_msg_type_number_t words = ARM_THREAD_STATE64_COUNT;
    kern_return_t result = thread_get_state(main_thread, ARM_THREAD_STATE64,
                                           (thread_state_t)&state, &words);
    thread_basic_info_data_t info = {0};
    words = THREAD_BASIC_INFO_COUNT;
    (void)thread_info(main_thread, THREAD_BASIC_INFO, (thread_info_t)&info, &words);
    uint64_t pc = arm_thread_state64_get_pc(state);
    uint64_t lr = arm_thread_state64_get_lr(state);
    uint64_t fp = arm_thread_state64_get_fp(state);
    uint64_t sp = arm_thread_state64_get_sp(state);

    /* Latch the lifecycle phase at the same sample, not after the stack walk. */
    bool was_active = __atomic_load_n(&active, __ATOMIC_ACQUIRE);
    uint64_t elapsed = monotonic_ms() - started_ms;
    uint64_t frames[24];
    unsigned frame_count = 0;
    uint64_t next_fp = fp;
    while (result == KERN_SUCCESS && frame_count < sizeof(frames) / sizeof(frames[0]) &&
           sp && next_fp >= sp && next_fp - sp < 8 * 1024 * 1024 && !(next_fp & 15)) {
        uint64_t pair[2];
        vm_size_t got = 0;
        if (vm_read_overwrite(mach_task_self(), (vm_address_t)next_fp, sizeof(pair),
                              (vm_address_t)(uintptr_t)pair, &got) != KERN_SUCCESS ||
            got != sizeof(pair))
            break;
        frames[frame_count++] = frame_return_address(pair[1]);
        if (pair[0] <= next_fp)
            break;
        next_fp = pair[0];
    }

    char line[256];
    int len = snprintf(line, sizeof(line), SETTINGS_STACK_PREFIX
                       "at=%us elapsed=%" PRIu64 "ms err=%d run=%d cpu=%d "
                       "pc=%" PRIx64 " lr=%" PRIx64 " fp=%" PRIx64 " sp=%" PRIx64
                       " active=%d frames:",
                       delay, elapsed, result, info.run_state, info.cpu_usage,
                       pc, lr, fp, sp, was_active);
    if (len <= 0 || len >= (int)sizeof(line) - 1)
        return;

    for (unsigned i = 0; i < frame_count; ++i) {
        char address[18];
        int size = snprintf(address, sizeof(address), " %" PRIx64, frames[i]);
        if (size <= 0 || size >= (int)sizeof(address))
            break;
        if ((size_t)len + (size_t)size >= sizeof(line) - 1) {
            report(line, (size_t)len);
            len = snprintf(line, sizeof(line), SETTINGS_STACK_PREFIX
                           "at=%us elapsed=%" PRIu64 "ms active=%d frames+offset=%u:",
                           delay, elapsed, was_active, i);
            if (len <= 0 || (size_t)len + (size_t)size >= sizeof(line) - 1)
                return;
        }
        memcpy(line + len, address, (size_t)size);
        len += size;
    }
    report(line, (size_t)len);
}

static void sample(void *context)
{
    read_main_stack(*(const unsigned *)context);
    if (__atomic_sub_fetch(&samples_pending, 1, __ATOMIC_ACQ_REL) == 0)
        (void)mach_port_deallocate(mach_task_self(), main_thread);
}

void pong_settings_watch_stack_start(void)
{
    const char *process = getprogname();
    if (!process || strcmp(process, "Preferences") != 0 || !pthread_main_np() || main_thread)
        return;
    main_thread = mach_thread_self();
    if (!MACH_PORT_VALID(main_thread))
        return;

    static unsigned delays[] = {5, 12, 18, 35};
    samples_pending = sizeof(delays) / sizeof(delays[0]);
    started_ms = monotonic_ms();
    dispatch_queue_t queue = dispatch_get_global_queue(QOS_CLASS_UTILITY, 0);
    dispatch_async_f(queue, NULL, report_install);
    for (unsigned i = 0; i < sizeof(delays) / sizeof(delays[0]); ++i)
        dispatch_after_f(dispatch_time(DISPATCH_TIME_NOW, (int64_t)delays[i] * NSEC_PER_SEC),
                         queue, &delays[i], sample);
}

void pong_settings_watch_stack_mark_active(void)
{
    __atomic_store_n(&active, true, __ATOMIC_RELEASE);
}
