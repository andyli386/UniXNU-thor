#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <time.h>
#include <fcntl.h>
#include <unistd.h>

int fake_open(const char *, int, ...);
int fake_fstat(int, struct stat *);
ssize_t fake_write(int, const void *, size_t);
int fake_rename(const char *, const char *);
int fake_fsync(int);
unsigned int fake_sleep(unsigned int);
int fake_clock_gettime(clockid_t, struct timespec *);

#define __proc_info kernel_mock
#define open fake_open
#define fstat fake_fstat
#define write fake_write
#define rename fake_rename
#define fsync fake_fsync
#define sleep fake_sleep
#define clock_gettime fake_clock_gettime
#include "console-v172.c"
#undef clock_gettime
#undef sleep
#undef fsync
#undef rename
#undef write
#undef fstat
#undef open
#undef __proc_info

static unsigned period, syncs, opens, rotations, checked_writes, retried_writes;
static unsigned sync_at[20], reads[20], sleep_seconds[20];
static const char *capture, *previous;
static unsigned scenario;
static bool stop_after_rotation;
static bool resent_range;

int fake_open(const char *path, int flags, ...)
{
    assert(!strcmp(path, log_path));
    assert((flags & O_NOFOLLOW) && (flags & O_APPEND) && (flags & O_CLOEXEC));
    ++opens;
    return open(capture, flags, 0600);
}

int fake_fstat(int file, struct stat *attributes)
{
    const int status = fstat(file, attributes);
    if (!status) {
        attributes->st_uid = 0;
        if (scenario == 4) attributes->st_nlink = 2;
        if (scenario == 1 && syncs && !rotations) attributes->st_size = console_cap_bytes;
    }
    return status;
}

int fake_rename(const char *path, const char *prior)
{
    assert(!strcmp(path, log_path) && !strcmp(prior, previous_path));
    assert(syncs >= 1 && scenario == 1);
    ++rotations;
    return rename(capture, previous);
}

int fake_fsync(int file)
{
    if (scenario == 2 && !retried_writes) {
        ++retried_writes;
        errno = EINTR;
        return -1;
    }
    assert(syncs < sizeof(sync_at) / sizeof(sync_at[0]));
    sync_at[syncs++] = period;
    if (scenario == 5 && syncs == 1) {
        errno = EIO;
        return -1;
    }
    return fsync(file);
}

int fake_clock_gettime(clockid_t clock_id, struct timespec *value)
{
    assert(clock_id == CLOCK_MONOTONIC);
    value->tv_sec = period;
    value->tv_nsec = 0;
    return 0;
}

unsigned int fake_sleep(unsigned int type)
{
    assert(period < 16);
    sleep_seconds[period] = type;
    period += type;
    return 0;
}

ssize_t fake_write(int file, const void *bytes, size_t length)
{
    ++checked_writes;
    if (scenario == 2 && checked_writes == 1) {
        errno = EINTR;
        return -1;
    }
    if (stop_after_rotation || period >= 12) {
        errno = EIO;
        return -1;
    }
    if (scenario == 2 && length > 1) return write(file, bytes, length - 1);
    return write(file, bytes, length);
}

int kernel_mock(int32_t call, int32_t pid, uint32_t flavor, uint64_t cursor,
                void *buffer, int32_t size)
{
    assert(call == 4 && pid == 0 && flavor == 0x54484f52U);
    assert(size == (int32_t)sizeof(struct console_packet));
    assert(period < 20);
    ++reads[period];
    struct console_packet *p = buffer;
    const char *bytes = "";
    uint64_t start = cursor;
    if (scenario == 3 && !period) {
        resent_range = true;
        return -1;
    }
    if (scenario == 1 && rotations) {
        bytes = "new rotation\n";
        stop_after_rotation = true;
        assert(cursor == strlen("boot sample\n"));
    } else if (cursor == 0) {
        bytes = "boot sample\n";
        if (scenario == 3) start = 8;
    } else if (period == 1 && cursor == strlen("boot sample\n")) {
        bytes = "second sample\n";
    } else if (period >= 12) {
        bytes = "terminate mock\n";
    }
    size_t n = strlen(bytes);
    assert(n <= sizeof(p->bytes));
    p->oldest = scenario == 3 ? 8 : 0;
    p->start = start;
    p->end = start + n;
    memcpy(p->bytes, bytes, n);
    return (int)(offsetof(struct console_packet, bytes) + n);
}

static void reset(unsigned index, const char *dir)
{
    static char file[256], prior[256];
    snprintf(file, sizeof(file), "%s/output%u", dir, index);
    snprintf(prior, sizeof(prior), "%s/previous%u", dir, index);
    capture = file;
    previous = prior;
    scenario = index;
    period = syncs = opens = rotations = checked_writes = retried_writes = 0;
    resent_range = stop_after_rotation = false;
    memset(reads, 0, sizeof(reads));
    memset(sleep_seconds, 0, sizeof(sleep_seconds));
    memset(sync_at, 0, sizeof(sync_at));
}

static void verify_file(const char *path, const char *needle)
{
    FILE *file = fopen(path, "r");
    assert(file);
    char data[4096] = {0};
    fread(data, 1, sizeof(data) - 1, file);
    fclose(file);
    assert(strstr(data, needle));
}

int main(void)
{
    char work[] = "worker-smoke.XXXXXX";
    assert(mkdtemp(work));

    reset(0, work);
    read_console(NULL);
    assert(opens == 1 && syncs == 3 && sync_at[0] == 0 && sync_at[1] == 2 && sync_at[2] == 10);
    assert(sleep_seconds[0] == 1 && reads[11] == 1);
    verify_file(capture, "boot sample\nsecond sample\nPong console capture STATUS");

    period = 0;
    read_console(NULL);
    assert(opens == 2 && syncs == 6 && sync_at[3] == 0);
    verify_file(capture, "cursor=26\n\nPong console capture BEGIN");

    reset(1, work);
    read_console(NULL);
    assert(rotations == 1 && opens == 2 && syncs == 1);
    verify_file(previous, "V172 poll_ms=1000 sync_ms=2000 status_ms=10000\nboot sample\n");
    verify_file(capture, "first=12\n");

    reset(2, work);
    read_console(NULL);
    assert(retried_writes == 1 && checked_writes >= 6);
    verify_file(capture, "boot sample\nsecond sample\n");

    reset(3, work);
    read_console(NULL);
    assert(resent_range && sleep_seconds[0] == 10 && sync_at[0] == 10);
    verify_file(capture, "GAP expected=0 start=8\nboot sample\n");

    reset(4, work);
    read_console(NULL);
    assert(opens == 1 && syncs == 0 && checked_writes == 0);

    reset(5, work);
    read_console(NULL);
    assert(sync_at[0] == 0 && sync_at[1] == 2);
    puts("v172 console exact worker passed: cadence, high-link rejection, gaps, partial writes, EINTR, EIO, rotation, restart");
}
