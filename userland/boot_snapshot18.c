/* SPDX-License-Identifier: MIT
 * Bounded, process-local startup snapshots (22H374 thor integration).
 * Two output paths: a persistent file under /private/var/logs and a bounded
 * mirror on /dev/console so a photograph of the screen captures the batch
 * even when the file cannot be retrieved from the device. No service
 * answers, render gates, thread suspension or persistent polling here.
 * GLM-review additions: batch BEGIN/END markers, stable thread ids
 * (THREAD_IDENTIFIER_INFO), total thread count always printed, three
 * batches (35/90/240s), logs directory self-provisioned.
 */
#include <dlfcn.h>
#include <fcntl.h>
#include <mach/mach.h>
#include <mach/arm/thread_status.h>
#include <mach-o/dyld.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

static const char *process_name;
static int console_fd = -2;   /* -2 not tried, -1 unavailable */

static void say_console(const char *line)
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
    say_console(line);
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
    n = snprintf(line, sizeof(line),
        "Pong boot snapshot: %s pid=%d seconds=%u image_slide=%llx cache=%p size=%zx",
        process_name, getpid(), elapsed,
        (unsigned long long)_dyld_get_image_vmaddr_slide(0), cache, cache_size);
    if (n > 0) emit(fd, line);
    mach_msg_type_number_t sampled = 0;
    for (mach_msg_type_number_t i = 0; i < count; i++) {
        if (i < 16 && threads[i] != self) {
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
             "Pong boot snapshot END: %s seconds=%u sampled=%u of %u",
             process_name, elapsed, sampled, count);
    emit(fd, line);
    (void)fsync(fd);
}

static void *worker(void *unused)
{
    (void)unused;
    char path[256];
    (void)mkdir("/private/var/logs", 0755);
    snprintf(path, sizeof(path), "%s/PongBoot-%s.txt",
             geteuid() == 0 ? "/private/var/logs"
                            : "/private/var/mobile/Library/Logs",
             process_name);
    int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC | O_NOFOLLOW, 0600);
    if (fd >= 0) (void)fchmod(fd, 0600);
    if (fd < 0) fd = dup(STDERR_FILENO);
    if (fd < 0) return NULL;
    sleep(35);  snapshot(fd, 35);
    sleep(55);  snapshot(fd, 90);
    sleep(150); snapshot(fd, 240);
    close(fd);
    if (console_fd >= 0) close(console_fd);
    return NULL;
}

void pong_boot_snapshot_start(const char *name)
{
    if (strcmp(name, "SpringBoard") && strcmp(name, "backboardd") &&
        strcmp(name, "lsd") && strcmp(name, "containermanagerd") &&
        strcmp(name, "containermanagerd_system") &&
        strcmp(name, "runningboardd") && strcmp(name, "usermanagerd") &&
        strcmp(name, "PosterBoard") && strcmp(name, "wifid")) return;
    process_name = name;
    pthread_attr_t attr;
    if (pthread_attr_init(&attr)) return;
    pthread_t thread;
    if (pthread_attr_setdetachstate(&attr, PTHREAD_CREATE_DETACHED) == 0)
        (void)pthread_create(&thread, &attr, worker, NULL);
    pthread_attr_destroy(&attr);
}
