
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <setjmp.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#define PONG_KERNMSGBUF_CURSOR 0x54484f52u
#define PONG_CONSOLE_READ_BYTES (16u * 1024u)
#define PONG_CONSOLE_MAX_BYTES (16 * 1024 * 1024)
#define PONG_CONSOLE_SYNC_INTERVAL_MS 2000u
#define PONG_CONSOLE_STATUS_INTERVAL_MS 10000u

struct pong_console_range {
    uint64_t oldest, start, end;
};


static jmp_buf finished;
static enum { Idle, Rotate, ReadError } mode;
static uint64_t now, synced_at, last_appended;
static unsigned polls, syncs, errors, statuses, rotations, open_count;
static uint64_t first_status;
static int fake_open(const char *name, int flags, unsigned permissions) {
    (void)name; (void)flags; (void)permissions;
    return 3 + ++open_count;
}
static int fake_close(int fd) { assert(fd >= 4); return 0; }
static int fake_sysctl(const char *n, void *text, size_t *length, void *new_data, size_t new_len) {
    (void)n; (void)length; (void)new_data; (void)new_len;
    strcpy(text, "unit-test-boot");
    return 0;
}
static void diag_say(const char *format, ...) {
    if (strstr(format, "fsync failed")) ++errors;
}
static int fake_print(int fd, const char *format, ...) {
    assert(fd >= 4);
    last_appended = now;
    if (strstr(format, "POLICY")) assert(now == 0);
    if (strstr(format, "STATUS")) {
        if (!statuses) first_status = now;
        ++statuses;
    }
    return 1;
}
static int console_write_memory(int fd, const char *boot, uint64_t time) {
    (void)boot; (void)time;
    return fake_print(fd, "memory");
}
static bool console_append(int fd, const char *bytes, size_t count) {
    assert(fd >= 4 && bytes && count);
    last_appended = now;
    return true;
}
static void snapshot_observe_console_bytes(const char *bytes, size_t count, bool continuous) {
    assert(bytes && count && continuous);
}
static uint64_t snapshot_clock_ms(void) { return now; }
static unsigned fake_sleep(unsigned seconds) {
    assert(seconds == 1 || (mode == ReadError && seconds == 30));
    now += 1000 * seconds;
    if (now >= 33000) longjmp(finished, 1);
    return 0;
}
static int fake_sync(int fd) {
    assert(fd >= 4 && now >= synced_at + 2000);
    synced_at = now;
    ++syncs;
    if (mode == Rotate && syncs < 3) {
        errno = EIO;
        return -1;
    }
    return 0;
}
static int fake_stat(int fd, struct stat *result) {
    assert(fd >= 4);
    result->st_size = mode == Rotate && now >= 5000 && now < 7000 ? PONG_CONSOLE_MAX_BYTES : 0;
    return 0;
}
static int fake_rename(const char *from, const char *to) {
    (void)from; (void)to;
    assert(now == 6000 && synced_at == last_appended && synced_at == now);
    ++rotations;
    return 0;
}
static int fake_cursor(int call, int pid, uint32_t key, uint64_t cursor, void *packet, int size) {
    assert(call == 4 && pid == 0 && key == PONG_KERNMSGBUF_CURSOR);
    assert(size == (int)(sizeof(struct pong_console_range) + PONG_CONSOLE_READ_BYTES));
    ++polls;
    if (mode == ReadError && now >= 1000 && now <= 2000) {
        errno = EIO;
        return -1;
    }
    struct pong_console_range *range = packet;
    char *bytes = (char *)(range + 1);
    const char *line = "";
    if (mode == Rotate) line = "line\n";
    if (mode == Idle && now == 10000) line = "half line";
    if (mode == Idle && now == 12000) line = " completes\n";
    size_t count = strlen(line);
    range->oldest = 0;
    range->start = cursor;
    range->end = cursor + count;
    memcpy(bytes, line, count);
    return (int)(sizeof(*range) + count);
}
#define open fake_open
#define close fake_close
#define sysctlbyname fake_sysctl
#define dprintf fake_print
#define sleep fake_sleep
#define fsync fake_sync
#define fstat fake_stat
#define rename fake_rename
#define __proc_info fake_cursor
static bool console_sync_if_due(int out, uint64_t now, uint64_t *next_sync,
                               bool *dirty, bool *complained)
{
    if (!*dirty || now < *next_sync) return false;
    *next_sync = now + PONG_CONSOLE_SYNC_INTERVAL_MS;
    if (fsync(out) == 0) {
        *dirty = false;
        *complained = false;
    } else if (!*complained) {
        *complained = true;
        diag_say("Pong console capture: fsync failed errno=%d", errno);
    }
    return true;
}

static void *console_worker(void *unused)
{
    (void)unused;
    const char *path = "/private/var/logs/PongConsole.txt";
    const char *previous = "/private/var/logs/PongConsole.previous.txt";
    int out = open(path, O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0600);
    if (out < 0) {
        diag_say("Pong console capture: open failed errno=%d", errno);
        return NULL;
    }
    struct {
        struct pong_console_range range;
        char bytes[PONG_CONSOLE_READ_BYTES];
    } packet = {0};
    uint64_t cursor = 0;
    bool first = true, complained = false, sync_complained = false;
    bool dirty = false, rotation_due = false, at_line_boundary = true;
    char boot[64] = "unavailable";
    size_t length = sizeof(boot);
    if (sysctlbyname("kern.bootsessionuuid", boot, &length, NULL, 0) != 0)
        strlcpy(boot, "unavailable", sizeof(boot));
    boot[sizeof(boot) - 1] = '\0';

    static const unsigned memory_sample_ms[] = {0, 30000, 120000};
    const uint64_t start_ms = snapshot_clock_ms();
    uint64_t next_sync_ms = start_ms + PONG_CONSOLE_SYNC_INTERVAL_MS;
    uint64_t next_status_ms = start_ms + PONG_CONSOLE_STATUS_INTERVAL_MS;
    size_t sample = 0;
    for (;;) {
        const uint64_t now = snapshot_clock_ms();
        if (sample < sizeof(memory_sample_ms) / sizeof(memory_sample_ms[0]) &&
            now - start_ms >= memory_sample_ms[sample]) {
            if (console_write_memory(out, boot, now) < 0) break;
            dirty = true;
            sample++;
        }
        int got = __proc_info(0x4, 0, PONG_KERNMSGBUF_CURSOR, cursor,
                              &packet, (int32_t)sizeof(packet));
        size_t count = got >= (int)sizeof(packet.range)
            ? (size_t)got - sizeof(packet.range) : 0;
        const struct pong_console_range range = packet.range;
        if (got < (int)sizeof(range) || got > (int)sizeof(packet) ||
            range.oldest > range.start || range.start > range.end ||
            count > range.end - range.start ||
            range.start != (cursor < range.oldest || cursor > range.end ? range.oldest : cursor)) {
            if (!complained) {
                complained = true;
                diag_say("Pong console capture: cursor read failed result=%d errno=%d", got, errno);
            }
            (void)console_sync_if_due(out, now, &next_sync_ms, &dirty, &sync_complained);
            sleep(dirty ? 1 : 30);
            continue;
        }
        complained = false;
        if (first) {
            first = false;
            if (dprintf(out, "\nPong console capture BEGIN boot=%s pid=%d first=%llu\n",
                        boot, getpid(), (unsigned long long)range.start) < 0) break;
            if (dprintf(out, "Pong console capture POLICY poll_ms=1000 sync_ms=%u status_ms=%u\n",
                        PONG_CONSOLE_SYNC_INTERVAL_MS, PONG_CONSOLE_STATUS_INTERVAL_MS) < 0) break;
            dirty = true;
        }
        if (range.start != cursor) {
            if (dprintf(out, cursor < range.start
                        ? "\nPong console capture WRAP lost bytes cursor=%llu oldest=%llu\n"
                        : "\nPong console capture RESET cursor=%llu oldest=%llu\n",
                        (unsigned long long)cursor, (unsigned long long)range.oldest) < 0) break;
            dirty = true;
        }
        if (count) {
            if (!console_append(out, packet.bytes, count)) break;
            dirty = true;
            snapshot_observe_console_bytes(packet.bytes, count, range.start == cursor);
            at_line_boundary = packet.bytes[count - 1] == '\n';
        }
        cursor = range.start + count;
        if (now >= next_status_ms && cursor == range.end && at_line_boundary) {
            if (dprintf(out, "\nPong console capture STATUS boot=%s uptime_ms=%llu cursor=%llu end=%llu\n",
                        boot, (unsigned long long)now, (unsigned long long)cursor,
                        (unsigned long long)range.end) < 0) break;
            dirty = true;
            next_status_ms = now + PONG_CONSOLE_STATUS_INTERVAL_MS;
        }

        struct stat st;
        if (count && fstat(out, &st) == 0 && st.st_size >= PONG_CONSOLE_MAX_BYTES)
            rotation_due = true;
        const bool sync_attempted = console_sync_if_due(out, now, &next_sync_ms, &dirty,
                                                        &sync_complained);
        if (rotation_due && (!dirty || sync_attempted)) {
            if (rename(path, previous) != 0) {
                diag_say("Pong console capture: rotate failed errno=%d", errno);
                break;
            }
            close(out);
            out = open(path, O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0600);
            if (out < 0) {
                diag_say("Pong console capture: reopen failed errno=%d", errno);
                return NULL;
            }
            if (dprintf(out, "\nPong console capture ROTATE next=%llu\n",
                        (unsigned long long)cursor) < 0) break;
            dirty = true;
            rotation_due = false;
        }
        sleep(1);
    }
    diag_say("Pong console capture: output failed errno=%d cursor=%llu",
             errno, (unsigned long long)cursor);
    close(out);
    return NULL;
}


int main(void) {
    for (mode = Idle; mode <= ReadError; ++mode) {
        now = synced_at = last_appended = 0;
        polls = syncs = errors = statuses = rotations = open_count = 0;
        first_status = 0;
        if (!setjmp(finished)) console_worker(NULL);
        if (mode == Idle) {
            assert(polls == 33 && errors == 0 && rotations == 0);
            assert(syncs == 6 && statuses == 3 && first_status == 12000);
        } else if (mode == Rotate) {
            assert(polls == 33 && errors == 1 && rotations == 1 && open_count == 2);
            assert(syncs == 16 && statuses == 3 && first_status == 10000);
        } else {
            assert(polls == 4 && syncs == 2 && statuses == 1 && first_status == 32000);
        }
    }
    puts("PASS exact console worker: idle and partial receipt, retry plus rate cap, rotation after sync, cursor failure backoff");
}
