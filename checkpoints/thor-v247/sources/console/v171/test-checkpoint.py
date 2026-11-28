from pathlib import Path
import subprocess

folder = Path(__file__).resolve().parent
source = (folder / "userland/boot_snapshot18.c").read_text()
constants = source[source.index("#define PONG_KERNMSGBUF_CURSOR"):source.index("extern int __proc_info")]
struct = source[source.index("struct pong_console_range {"):source.index("static bool console_append")]
worker = source[source.index("static bool console_sync_if_due"):source.index("/* Warm the dyld")]
fixture = r'''
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
@CONSTANTS@
@STRUCT@
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
@WORKER@
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
'''
test = folder / "test-checkpoint.c"
test.write_text(fixture.replace("@CONSTANTS@", constants).replace("@STRUCT@", struct).replace("@WORKER@", worker))
binary = folder / "test-checkpoint"
subprocess.run(["/usr/bin/clang", "-std=c11", "-Wall", "-Wextra", "-Werror", "-O2", str(test), "-o", str(binary)], check=True)
subprocess.run([str(binary)], check=True)
