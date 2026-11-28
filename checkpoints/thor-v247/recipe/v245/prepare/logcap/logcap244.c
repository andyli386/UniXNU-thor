/* SPDX-License-Identifier: MIT */
/* Pong log capture v244: keeps what a few processes write to their error output.
 *
 * Why.  Three questions of 2026-11-27 could not be answered from the kernel's console:
 *   - the wallpaper stays black although the catalog library of v243 is in SpringBoard, and
 *     WallpaperKit never looked for a catalog during the whole boot;
 *   - the battery is published (the audio processor is up at 79 s) and the status bar shows the
 *     icon, but not the percentage number;
 *   - Freeform (无边记) dies as soon as the user draws on its canvas, and no crash report is
 *     written: the five corpse slots of the boot are taken in the first 83 seconds by other
 *     processes, so there is no stack and no reason.
 * What these processes say themselves answers them: WallpaperKit, PosterBoard and PowerUI log
 * through os_log, and an uncaught ObjC exception prints its reason to stderr before it aborts.
 *
 * How.  The kernel puts this library into SpringBoard, backboardd, PosterBoard, CollectionsPoster,
 * PowerUIAgent, Freeform and kbd (DYLD_INSERT_LIBRARIES, by a table of its own).  Its constructor
 * switches libsystem_trace to mirroring os_log to the process's error output (trace_flags.h: the
 * environment variable OS_ACTIVITY_DT_MODE did nothing for wifid on 2026-11-27), connects that
 * output (fd 2) to a pipe and starts one thread that reads it:
 *   - SpringBoard, backboardd, PosterBoard, CollectionsPoster, PowerUIAgent: a line that contains a
 *     wallpaper word (wallpaper, catalog, collection, ...) is passed on, and so is a line with a
 *     secondary word (poster, battery, percent, render, gpu, ...), each group up to a fixed number per
 *     process;
 *   - Freeform, kbd: a line that contains an indicator (exception, assert, metal, ...) is passed on, and
 *     the last 40 lines are always kept; a handler for the fatal signals passes those on when the
 *     process is about to die, so the reason that ObjC prints just before abort() is not lost.
 * Everything goes through the kernel's diagnostic channel (kern.pong_diagnostic) to the console as
 *   "PONG PROBE <process>[pid]: Pong Probe: log v244 <process>: <line>".
 * The reader reads for the whole life of the process and discards what it must not pass on, so a
 * writer never blocks on a full pipe.  In a process that is not in the table nothing happens.
 *
 * The Mac comparison test (hosttest/run.sh) compiles the same source with PONG_LOGCAP_TEST_FILE set:
 * then the lines go to that file instead of the channel.
 */
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <signal.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/sysctl.h>
#include <unistd.h>

#include "trace_flags.h"

#define LINE_BYTES 960           /* the kernel's endpoint takes 1023 bytes, prefix included */
/* Darwin's headers do not always declare it; the kernel knows it (1023). */
#ifndef F_SETPIPE_SZ
#define F_SETPIPE_SZ 1023
#endif
#define RING_LINES 40

enum { MODE_KEYWORDS = 1, MODE_CRASH = 2 };

struct policy {
	const char *name;
	int mode;
	unsigned lines;              /* the lines of the first group (wallpaper words, or indicators) */
	unsigned other_lines;        /* the lines of the second group (poster, battery, ...) */
	unsigned seconds;            /* how long os_log is mirrored to the error output (0: for the life) */
};
static const struct policy policies[] = {
	/*  name, mode, wallpaper-word lines, other-word lines, seconds of os_log mirroring (0: for the life) */
	{ "SpringBoard", MODE_KEYWORDS, 900, 300, 240 },
	{ "backboardd", MODE_KEYWORDS, 200, 200, 180 },
	{ "PosterBoard", MODE_KEYWORDS, 250, 150, 0 },
	{ "CollectionsPoster", MODE_KEYWORDS, 250, 100, 0 },
	{ "PowerUIAgent", MODE_KEYWORDS, 120, 200, 0 },
	{ "Freeform", MODE_CRASH, 500, 0, 0 },
	{ "kbd", MODE_CRASH, 300, 0, 0 },
};
/* In lower case: a line belongs to a group when it contains one of its words (case-insensitive). */
static const char *const wallpaper_words[] = {
	"wallpaper", "wkdefault", "wkwallpaper", "wklog", "collectio", "catalog", "legacy", "lookup",
	"solid black", "defaultwallpaper", "logicalscreenclass", "screen class",
};
static const char *const other_words[] = {
	"poster", "pbf", "pbui", "battery", "powerui", "iopm", "iops", "percent", "charg",
	/* the render server: why it draws the frames in software at another size */
	"software", "render", "composit", "surface", "gpu", "metal", "handoff", "framebuffer", "iosurface",
	"screen", "bounds", "scale",
};
static const char *const indicators[] = {
	"terminat", "exception", "assert", "abort", "fatal", "crash", "signal", "error", "fail",
	"metal", "mtl", "pencil", "pkcanvas", "unsupport", "invalid", "nil", "denied", "not allowed",
};

static const struct policy *me;
static unsigned passed, other_passed;
static volatile unsigned busy;
static int pipe_read = -1;
static const char *test_file;
static char ring[RING_LINES][LINE_BYTES];
static unsigned ring_count, ring_next;

/* One line, with its prefix, to the console.  The one lock is a flag: a second caller waits briefly
 * (the signal handler must not lose its lines) or, when it does not get in, drops the line.  Safe
 * inside a signal handler (no allocation, no stdio, no long lock). */
static void
emit(const char *prefix, size_t prefix_bytes, const char *line, size_t bytes, bool wait)
{
	char text[LINE_BYTES + 80];
	unsigned spins = 0;
	while (__atomic_exchange_n(&busy, 1U, __ATOMIC_ACQ_REL)) {
		if (!wait || ++spins > 20000000U) {
			return;
		}
	}
	if (bytes > LINE_BYTES) {
		bytes = LINE_BYTES;
	}
	while (bytes && (line[bytes - 1] == '\n' || line[bytes - 1] == '\r')) {
		bytes--;
	}
	size_t used = 0;
	static const char lead[] = "Pong Probe: log v244 ";
	memcpy(text, lead, sizeof lead - 1);
	used = sizeof lead - 1;
	size_t name = strlen(me->name);
	if (used + name + 2 < sizeof text) {
		memcpy(text + used, me->name, name);
		used += name;
		text[used++] = ':';
		text[used++] = ' ';
	}
	if (used + prefix_bytes + 1 < sizeof text) {
		memcpy(text + used, prefix, prefix_bytes);
		used += prefix_bytes;
	}
	if (used + bytes + 1 > sizeof text) {
		bytes = sizeof text - used - 1;
	}
	for (size_t i = 0; i < bytes; ++i) {
		unsigned char c = (unsigned char)line[i];
		text[used++] = (c < 0x20 || c > 0x7e) ? '?' : (char)c;
	}
	text[used] = 0;
	if (test_file) {
		int fd = open(test_file, O_WRONLY | O_APPEND | O_CREAT, 0644);
		if (fd >= 0) {
			ssize_t ignored = write(fd, text, used);
			ignored = write(fd, "\n", 1);
			(void)ignored;
			close(fd);
		}
	} else {
		(void)sysctlbyname("kern.pong_diagnostic", NULL, NULL, text, used + 1);
	}
	__atomic_store_n(&busy, 0U, __ATOMIC_RELEASE);
}

static bool
contains(const char *line, size_t bytes, const char *const *table, size_t count)
{
	char lower[LINE_BYTES];
	if (bytes >= sizeof lower) {
		bytes = sizeof lower - 1;
	}
	for (size_t i = 0; i < bytes; ++i) {
		unsigned char c = (unsigned char)line[i];
		lower[i] = (char)(c >= 'A' && c <= 'Z' ? c + 32 : c);
	}
	lower[bytes] = 0;
	for (size_t i = 0; i < count; ++i) {
		const char *k = table[i];
		size_t length = strlen(k);
		if (length <= bytes && memmem(lower, bytes, k, length)) {
			return true;
		}
	}
	return false;
}

static void
take_line(const char *line, size_t bytes)
{
	bool keep = false;
	if (me->mode & MODE_CRASH) {
		/* Always remember the last lines: they tell what led to the crash. */
		unsigned slot = ring_next;
		size_t n = bytes < LINE_BYTES - 1 ? bytes : LINE_BYTES - 1;
		memcpy(ring[slot], line, n);
		ring[slot][n] = 0;
		ring_next = (ring_next + 1) % RING_LINES;
		if (ring_count < RING_LINES) {
			ring_count++;
		}
		keep = contains(line, bytes, indicators, sizeof indicators / sizeof indicators[0]) &&
		    __atomic_load_n(&passed, __ATOMIC_RELAXED) < me->lines;
		if (keep) {
			__atomic_add_fetch(&passed, 1U, __ATOMIC_RELAXED);
		}
	} else {
		size_t count = sizeof wallpaper_words / sizeof wallpaper_words[0];
		if (contains(line, bytes, wallpaper_words, count) &&
		    __atomic_load_n(&passed, __ATOMIC_RELAXED) < me->lines) {
			__atomic_add_fetch(&passed, 1U, __ATOMIC_RELAXED);
			keep = true;
		} else {
			count = sizeof other_words / sizeof other_words[0];
			if (contains(line, bytes, other_words, count) &&
			    __atomic_load_n(&other_passed, __ATOMIC_RELAXED) < me->other_lines) {
				__atomic_add_fetch(&other_passed, 1U, __ATOMIC_RELAXED);
				keep = true;
			}
		}
	}
	if (keep) {
		emit("", 0, line, bytes, false);
	}
}

/* The mirroring in two phases: the first 60 seconds with info and debug messages (they are many),
 * then default-level messages only; after the policy's time it is switched off, so the process
 * stops paying for it. */
static void *
mirror_phases(void *argument)
{
	const struct policy *policy = (const struct policy *)argument;
	sleep(60);
	(void)pong_trace_flags(1, 1);
	if (policy->seconds > 60) {
		sleep(policy->seconds - 60);
		(void)pong_trace_flags(0, 0);
	}
	return NULL;
}

static void *
reader(void *argument)
{
	int fd = (int)(intptr_t)argument;
	char chunk[8192], line[LINE_BYTES];
	size_t used = 0;
	bool dropping = false;
	for (;;) {
		ssize_t n = read(fd, chunk, sizeof chunk);
		if (n < 0) {
			if (errno == EINTR) {
				continue;
			}
			break;
		}
		if (n == 0) {
			break;            /* every writer closed the pipe */
		}
		for (ssize_t i = 0; i < n; ++i) {
			char c = chunk[i];
			if (c == '\n') {
				if (!dropping) {
					take_line(line, used);
				}
				used = 0;
				dropping = false;
			} else if (used < sizeof line - 1) {
				line[used++] = c;
			} else {
				dropping = true;   /* a line longer than the budget: skipped to its end */
			}
		}
	}
	return NULL;
}

/* The fatal signals: pass on what was written last, then die as before. */
static void
on_fatal_signal(int number)
{
	static const char before[] = "before the crash: ";
	static const char last[] = "at the crash: ";
	/* The oldest line first: ring_next is where the next line goes. */
	for (unsigned i = 0; i < ring_count; ++i) {
		unsigned slot = (ring_next + RING_LINES - ring_count + i) % RING_LINES;
		emit(before, sizeof before - 1, ring[slot], strlen(ring[slot]), true);
	}
	char chunk[4096], line[LINE_BYTES];
	size_t used = 0;
	/* Read only what is already in the pipe: a blocking read here would wait for ever. */
	(void)fcntl(pipe_read, F_SETFL, fcntl(pipe_read, F_GETFL) | O_NONBLOCK);
	for (unsigned rounds = 0; rounds < 1024; ++rounds) {
		ssize_t n = read(pipe_read, chunk, sizeof chunk);
		if (n <= 0) {
			break;
		}
		for (ssize_t i = 0; i < n; ++i) {
			char c = chunk[i];
			if (c == '\n') {
				line[used] = 0;
				emit(last, sizeof last - 1, line, used, true);
				used = 0;
			} else if (used < sizeof line - 1) {
				line[used++] = c;
			}
		}
	}
	if (used) {
		line[used] = 0;
		emit(last, sizeof last - 1, line, used, true);
	}
	signal(number, SIG_DFL);
	raise(number);
	_exit(128 + number);
}

__attribute__((constructor))
static void
log_capture(void)
{
	test_file = getenv("PONG_LOGCAP_TEST_FILE");
	const char *name = getprogname();
	for (unsigned i = 0; i < sizeof policies / sizeof policies[0]; ++i) {
		if (name && !strcmp(name, policies[i].name)) {
			me = &policies[i];
		}
	}
	if (!me) {
		return;
	}
	int fds[2];
	if (pipe(fds) != 0) {
		return;
	}
	(void)fcntl(fds[1], F_SETPIPE_SZ, 65536);      /* as much room as the system allows */
	pipe_read = fds[0];
	/* From here on everything the process writes to its error output goes to the pipe. */
	if (dup2(fds[1], 2) != 2) {
		close(fds[1]);
		close(fds[0]);
		pipe_read = -1;
		return;
	}
	close(fds[1]);
	pthread_t thread;
	int error = pthread_create(&thread, NULL, reader, (void *)(intptr_t)pipe_read);
	if (error == 0) {
		error = pthread_detach(thread);
	}
	if (error != 0) {
		/* Nobody would read the pipe: send the output to /dev/null again, so no writer blocks. */
		int nul = open("/dev/null", O_WRONLY);
		if (nul >= 0) {
			dup2(nul, 2);
			close(nul);
		}
		return;
	}
	if (me->mode & MODE_CRASH) {
		const int signals[] = { SIGABRT, SIGSEGV, SIGBUS, SIGILL, SIGTRAP, SIGFPE };
		for (unsigned i = 0; i < sizeof signals / sizeof signals[0]; ++i) {
			(void)signal(signals[i], on_fatal_signal);
		}
	}
	/* os_log to the error output (trace_flags.h): default-level messages for the policy's time, info and
	 * debug for the first 60 seconds of it.  Returns 0 on a system that is not 22H374; then only what
	 * a process writes itself (an uncaught exception's reason, for one) is seen. */
	const int mirrored = pong_trace_flags(1, 1);
	pthread_t phases;
	if (pthread_create(&phases, NULL, mirror_phases, (void *)me) == 0) {
		(void)pthread_detach(phases);
	}
	char line[160];
	int length = snprintf(line, sizeof line, "Pong Probe: log v244: capturing the error output of %s (mode %d, up to %u and %u lines, %u s, os_log %s)",
	    me->name, me->mode, me->lines, me->other_lines, me->seconds, mirrored ? "mirrored" : "not mirrored");
	if (length > 0 && (size_t)length < sizeof line && !test_file) {
		(void)sysctlbyname("kern.pong_diagnostic", NULL, NULL, line, (size_t)length + 1);
	}
}
