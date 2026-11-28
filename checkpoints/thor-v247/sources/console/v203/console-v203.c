#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <sys/sysctl.h>
#include <time.h>
#include <unistd.h>

enum {
    console_read_bytes = 16 * 1024,
    console_cap_bytes = 4 * 1024 * 1024,
    console_sync_ms = 2000,
    console_status_ms = 10000,
    console_burst_reads = 8,
};
static const char log_path[] = "/private/var/logs/PongConsole-v172.txt";
static const char previous_path[] = "/private/var/logs/PongConsole-v172.previous.txt";

extern int __proc_info(int32_t call, int32_t pid, uint32_t flavor, uint64_t arg,
                       void *buffer, int32_t size);

struct console_packet {
    uint64_t oldest, start, end;
    char bytes[console_read_bytes];
};

static uint64_t monotonic_ms(void)
{
    struct timespec tick = {0};
    (void)clock_gettime(CLOCK_MONOTONIC, &tick);
    return (uint64_t)tick.tv_sec * 1000 + (uint64_t)tick.tv_nsec / 1000000;
}

#include "swipe_runtime.h"
#include "motion_log_rotation.h"

static bool append_bytes(int file, const char *bytes, size_t count)
{
    while (count) {
        ssize_t written = write(file, bytes, count);
        if (written < 0 && errno == EINTR) continue;
        if (written <= 0) return false;
        bytes += written;
        count -= (size_t)written;
    }
    return true;
}

static int open_console(void)
{
    int file = open(log_path, O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC | O_NOFOLLOW, 0600);
    if (file < 0) return -1;
    struct stat attributes;
    if (fstat(file, &attributes) != 0 || !S_ISREG(attributes.st_mode) ||
        attributes.st_uid != 0 || attributes.st_nlink != 1 ||
        (attributes.st_mode & 0777) != 0600 || flock(file, LOCK_EX | LOCK_NB) != 0) {
        close(file);
        return -1;
    }
    return file;
}

static bool sync_console(int file)
{
    for (;;) {
        if (fsync(file) == 0) return true;
        if (errno != EINTR) return false;
    }
}

static void *read_console(void *unused)
{
    (void)unused;
    int file = open_console();
    if (file < 0) return NULL;

    struct swipe_channel channel;
    struct swipe_stream stream = {0};
    bool monitor = swipe_channel_init(&channel, "runningboardd");
    if (monitor) swipe_start_record(&channel);
    struct console_packet packet;
    uint64_t cursor = 0;
    bool first = true;
    bool dirty = false;
    bool at_line_boundary = true;
    size_t length;
    char boot[64] = "unavailable";
    length = sizeof(boot);
    if (sysctlbyname("kern.bootsessionuuid", boot, &length, NULL, 0) != 0) {
        strlcpy(boot, "unavailable", sizeof(boot));
    }
    boot[sizeof(boot) - 1] = 0;
    uint64_t now = monotonic_ms();
    uint64_t next_sync = now;
    uint64_t next_status = now + console_status_ms;

    for (;;) {
        bool caught_up = false;
        bool read_failed = false;
        for (unsigned attempt = 0; attempt < console_burst_reads && !caught_up; ++attempt) {
            int size = __proc_info(4, 0, 0x54484f52U, cursor, &packet,
                                   (int32_t)sizeof(packet));
            const int header = (int)(sizeof(packet) - sizeof(packet.bytes));
            if (size < header || size > (int)sizeof(packet)) {
                read_failed = true;
                break;
            }
            const size_t count = (size_t)(size - header);
            if (packet.oldest > packet.start || packet.start > packet.end ||
                count > packet.end - packet.start ||
                packet.start != (cursor < packet.oldest || cursor > packet.end ?
                                packet.oldest : cursor)) {
                read_failed = true;
                break;
            }
            if (first) {
                if (dprintf(file, "\nPong console capture BEGIN boot=%s pid=%d first=%llu\n"
                            "Pong console capture V172 poll_ms=1000 active_poll_ms=125 sync_ms=%u status_ms=%u\n",
                            boot, getpid(), (unsigned long long)packet.start,
                            console_sync_ms, console_status_ms) < 0) goto done;
                first = false;
                dirty = true;
            }
            if (packet.start != cursor) {
                if (monitor) swipe_discontinuity(&stream);
                if (dprintf(file, "\nPong console capture GAP expected=%llu start=%llu\n",
                            (unsigned long long)cursor,
                            (unsigned long long)packet.start) < 0) goto done;
                at_line_boundary = true;
                dirty = true;
            }
            if (count) {
                if (monitor) swipe_bytes(&stream,packet.bytes,count,swipe_publish,&channel);
                if (!append_bytes(file, packet.bytes, count)) goto done;
                at_line_boundary = packet.bytes[count - 1] == '\n';
                dirty = true;
            }
            cursor = packet.start + count;
            caught_up = cursor == packet.end;
        }

        now = monotonic_ms();
        if (monitor && caught_up && !read_failed)
            swipe_tick(&stream.watch,capture_event_us(),swipe_publish,&channel);
        if (caught_up && at_line_boundary && now >= next_status) {
            if (dprintf(file, "Pong console capture STATUS boot=%s uptime_ms=%llu kernel_clock_us=%llu cursor=%llu\n",
                        boot, (unsigned long long)now, (unsigned long long)capture_event_us(), (unsigned long long)cursor) < 0) goto done;
            next_status = now + console_status_ms;
            dirty = true;
        }
        if (dirty && now >= next_sync) {
            const bool synced = sync_console(file);
            next_sync = monotonic_ms() + console_sync_ms;
            dirty = !synced;

            struct stat attributes;
            if (fstat(file, &attributes) != 0) goto done;
            if (attributes.st_size >= console_cap_bytes) {
                if (!synced || rename(log_path, previous_path) != 0) goto done;
                close(file);
                file = open_console();
                if (file < 0) return NULL;
                first = true;
            }
        }
        // Follow an ongoing gesture at 8 Hz so its early receipt can reach disk
        // before the next foreground scheduler or compositor stall.
        uint64_t us=capture_event_us();
        if (monitor && stream.watch.last_input && us>=stream.watch.last_input &&
            us-stream.watch.last_input<=4000000) usleep(125000);
        else sleep(read_failed && !dirty ? 10 : 1);
    }

done:
    close(file);
    return NULL;
}

__attribute__((constructor))
static void start_console_capture(void)
{
    const char *program = getprogname();
    if (!program) return;
    const char *role = NULL;
    if (!strcmp(program,"SpringBoard")) role = "SpringBoard";
    else if (!strcmp(program,"backboardd")) role = "backboardd";
    else if (geteuid()!=0 || strcmp(program,"runningboardd")) return;
    uid_t uid=geteuid();
    if (uid!=0 && uid!=501) return;
    if (role && strcmp(role,"SpringBoard")==0)
        motion_prepare_receipt_log("/private/var/mobile/Library/Logs", 501);
    pthread_attr_t attributes;
    if (pthread_attr_init(&attributes) != 0) return;
    pthread_t thread;
    if (pthread_attr_setdetachstate(&attributes, PTHREAD_CREATE_DETACHED) == 0) {
        (void)pthread_create(&thread, &attributes, role ? swipe_consume : read_console, (void *)role);
    }
    (void)pthread_attr_destroy(&attributes);
}
