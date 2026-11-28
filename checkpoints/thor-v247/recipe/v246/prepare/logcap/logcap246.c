/* SPDX-License-Identifier: MIT */
/* Pong log capture v246: v245 with PosterBoard added to the processes whose death is reported, and the
 * fatal signal handlers armed again after the other injected libraries ran.
 *
 * What v245 saw on the phone (2026-11-27 19:48).  With the UI adapter in PosterBoard (v245) the process
 * aborted with SIGABRT three times as it initialised its data store, and from the fourth start it ran
 * without the adapter.  The adapter installs its own handler for SIGABRT and SIGTRAP after this library's
 * constructor (it is loaded later), so its handler ran: it wrote the program counter and the frame records
 * to /private/var/mobile/Library/Logs/Pong-Poster18.log and the process died.  The reason was not seen:
 * this library kept the last lines but was not asked for them in PosterBoard (keyword mode only), and the
 * line an uncaught exception prints has no keyword.  The frame records showed the dispatch frames of the
 * thread only, not the code that aborted.
 *
 * Changes:
 *   1. PosterBoard is in crash mode as well as keyword mode: its last 40 lines are kept and passed on when
 *      it dies, with the signal, the registers and the frames, and lines with an indicator word
 *      (terminat, exception, assert, ...) are passed on at once;
 *   2. in crash mode the fatal signal handlers are armed again two seconds after the start and again when
 *      the first line arrives, so that a handler installed by a library that loads later is replaced.
 * Everything else is v245's.  See the heads of logcap244.c and logcap245.c for the design.
 */
#include <errno.h>
#include <fcntl.h>
#include <mach/mach.h>          /* vm_read_overwrite is declared in mach/vm_map.h; the iOS SDK has no mach_vm.h */
#include <pthread.h>
#include <signal.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/sysctl.h>
#include <sys/time.h>
#include <sys/ucontext.h>
#include <unistd.h>

#include "trace_flags.h"

#define LINE_BYTES 960           /* the kernel's endpoint takes 1023 bytes, prefix included */
/* Darwin's headers do not always declare it; the kernel knows it (1023). */
#ifndef F_SETPIPE_SZ
#define F_SETPIPE_SZ 1023
#endif
#define RING_LINES 40
#define FRAME_LINES 8            /* six frame records to a line */
#define ALT_STACK_BYTES 65536

enum { MODE_KEYWORDS = 1, MODE_CRASH = 2 };

struct policy {
	const char *name;
	int mode;
	unsigned lines;              /* the lines of the first group (wallpaper words, or indicators) */
	unsigned other_lines;        /* the lines of the second group (poster, battery, ...) */
	unsigned seconds;            /* how long os_log is mirrored to the error output (0: for the life) */
	unsigned verbose_lines;      /* every line, from the first one seen, up to so many ... */
	unsigned verbose_seconds;    /* ... for at most so many seconds (0: no such window) */
};
static const struct policy policies[] = {
	/*  name, mode, wallpaper-word lines, other-word lines, seconds of os_log mirroring (0: for the life),
	    all lines, seconds of that window */
	{ "SpringBoard", MODE_KEYWORDS, 900, 300, 240, 0, 0 },
	/* The render server starts its window server about one second after its first line reaches this
	 * library (2026-11-27: 46.4 s and 47.1 s); the first frame comes at 83 s. */
	{ "backboardd", MODE_KEYWORDS, 200, 200, 180, 2600, 10 },
	{ "PosterBoard", MODE_KEYWORDS | MODE_CRASH, 250, 150, 0, 0, 0 },
	{ "CollectionsPoster", MODE_KEYWORDS, 250, 100, 0, 0, 0 },
	{ "PowerUIAgent", MODE_KEYWORDS, 120, 200, 0, 0, 0 },
	{ "Freeform", MODE_CRASH, 500, 0, 0, 0, 0 },
	{ "kbd", MODE_CRASH, 300, 0, 0, 0, 0 },
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
	"screen", "bounds", "scale", "canvas", "display", "mobilegestalt", "product id", "iokit is still busy",
};
static const char *const indicators[] = {
	"terminat", "exception", "assert", "abort", "fatal", "crash", "signal", "error", "fail",
	"metal", "mtl", "pencil", "pkcanvas", "unsupport", "invalid", "nil", "denied", "not allowed",
	/* v245: the keyboard asks for a layout the system does not have ("... resulted in (null)") */
	"(null)", "layoutstar", "keyboardname", "mtlcompute", "pipeline", "function", "library",
};

static const struct policy *me;
static unsigned passed, other_passed, verbose_passed;
static uint64_t verbose_from;
static bool verbose_open;
static volatile unsigned busy;
static int pipe_read = -1;
static const char *test_file;
static char ring[RING_LINES][LINE_BYTES];
static unsigned ring_count, ring_next;
static uint64_t cache_slide;
static void *alt_stack;

static uint64_t
now_us(void)
{
	struct timeval tv;
	(void)gettimeofday(&tv, NULL);
	return (uint64_t)tv.tv_sec * 1000000ull + (uint64_t)tv.tv_usec;
}

/* Text building without stdio: safe inside the signal handler. */
static void
put_text(char *out, size_t cap, size_t *used, const char *s)
{
	while (*s && *used + 1 < cap) {
		out[(*used)++] = *s++;
	}
	out[*used] = 0;
}

static void
put_unsigned(char *out, size_t cap, size_t *used, uint64_t value)
{
	char digits[24];
	unsigned n = 0;
	do {
		digits[n++] = (char)('0' + value % 10);
		value /= 10;
	} while (value);
	while (n && *used + 1 < cap) {
		out[(*used)++] = digits[--n];
	}
	out[*used] = 0;
}

static void
put_hex(char *out, size_t cap, size_t *used, uint64_t value)
{
	static const char digits[] = "0123456789abcdef";
	char text[18];
	unsigned n = 0;
	do {
		text[n++] = digits[value & 15];
		value >>= 4;
	} while (value);
	put_text(out, cap, used, "0x");
	while (n && *used + 1 < cap) {
		out[(*used)++] = text[--n];
	}
	out[*used] = 0;
}

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
	static const char lead[] = "Pong Probe: log v246 ";
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
	if (me->verbose_lines) {
		/* The window opens with the first line and closes with its time or its number of lines. */
		if (!verbose_open) {
			verbose_open = true;
			verbose_from = now_us();
		}
		if (now_us() - verbose_from < (uint64_t)me->verbose_seconds * 1000000ull &&
		    __atomic_load_n(&verbose_passed, __ATOMIC_RELAXED) < me->verbose_lines) {
			__atomic_add_fetch(&verbose_passed, 1U, __ATOMIC_RELAXED);
			keep = true;
		}
	}
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
		if (!keep && contains(line, bytes, indicators, sizeof indicators / sizeof indicators[0]) &&
		    __atomic_load_n(&passed, __ATOMIC_RELAXED) < me->lines) {
			__atomic_add_fetch(&passed, 1U, __ATOMIC_RELAXED);
			keep = true;
		}
	}
	if ((me->mode & MODE_KEYWORDS) && !keep) {
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

static void arm_fatal_handlers(void);

/* The mirroring in two phases: the first 60 seconds with info and debug messages (they are many),
 * then default-level messages only; after the policy's time it is switched off, so the process
 * stops paying for it. */
static void *
mirror_phases(void *argument)
{
	const struct policy *policy = (const struct policy *)argument;
	/* v246: by now every library of the process has run its initializers. */
	sleep(2);
	arm_fatal_handlers();
	sleep(58);
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
	bool dropping = false, seen_line = false;
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
		if (!seen_line) {
			seen_line = true; /* the first output: once more, in case a library armed its handler after this one */
			arm_fatal_handlers();
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

/* The stack of the thread that faulted: frame records are pairs (previous frame pointer, return
 * address) with the frame pointer in the first word.  Nothing is dereferenced directly: the read goes
 * through the kernel, so a bad pointer ends the walk instead of faulting again. */
static void
emit_frames(const char *prefix, uint64_t fp)
{
	static const char none[] = "(none readable)";
	char line[LINE_BYTES];
	size_t used = 0, count = 0;
	for (unsigned i = 0; i < FRAME_LINES; ++i) {
		used = 0;
		put_text(line, sizeof line, &used, "frames");
		for (unsigned k = 0; k < 6; ++k) {
			uint64_t frame[2] = {0, 0};
			vm_size_t bytes = 0;
			if (fp < 0x1000 || (fp & 15) ||
			    vm_read_overwrite(mach_task_self(), (vm_address_t)fp, sizeof frame, (vm_address_t)frame,
			    &bytes) != KERN_SUCCESS || bytes != sizeof frame) {
				fp = 0;
			}
			if (!fp) {
				break;
			}
			put_text(line, sizeof line, &used, " ");
			put_hex(line, sizeof line, &used, frame[1]);
			count++;
			if (frame[0] <= fp || frame[0] - fp > 0x100000) {
				fp = 0;
			} else {
				fp = frame[0];
			}
		}
		if (used == (sizeof "frames" - 1)) {
			break;
		}
		emit(prefix, strlen(prefix), line, used, true);
		if (!fp) {
			break;
		}
	}
	if (!count) {
		emit(prefix, strlen(prefix), none, sizeof none - 1, true);
	}
}

/* The fatal signals: say which signal, where and from which stack; pass on what was written last;
 * then die as before. */
static void
on_fatal_signal(int number, siginfo_t *info, void *context)
{
	static const char before[] = "before the crash: ";
	static const char last[] = "at the crash: ";
	static const char crash[] = "crash: ";
	char line[LINE_BYTES];
	size_t used = 0;
	ucontext_t *saved = context;

	put_text(line, sizeof line, &used, "fatal signal ");
	put_unsigned(line, sizeof line, &used, (uint64_t)(number < 0 ? 0 : number));
	if (info) {
		put_text(line, sizeof line, &used, " code ");
		put_unsigned(line, sizeof line, &used, (uint64_t)(int64_t)info->si_code);
		put_text(line, sizeof line, &used, " at ");
		put_hex(line, sizeof line, &used, (uint64_t)(uintptr_t)info->si_addr);
	}
	emit(crash, sizeof crash - 1, line, used, true);

	uint64_t fp = 0;
	if (saved && saved->uc_mcontext) {
		struct __darwin_arm_thread_state64 *state = &saved->uc_mcontext->__ss;
		used = 0;
		put_text(line, sizeof line, &used, "pc ");
		put_hex(line, sizeof line, &used, (uint64_t)state->__pc);
		put_text(line, sizeof line, &used, "  lr ");
		put_hex(line, sizeof line, &used, (uint64_t)state->__lr);
		put_text(line, sizeof line, &used, "  sp ");
		put_hex(line, sizeof line, &used, (uint64_t)state->__sp);
		put_text(line, sizeof line, &used, "  fp ");
		put_hex(line, sizeof line, &used, (uint64_t)state->__fp);
		put_text(line, sizeof line, &used, "  shared cache slide ");
		put_hex(line, sizeof line, &used, cache_slide);
		emit(crash, sizeof crash - 1, line, used, true);
		/* The frame records start at the frame pointer; the link register above is the caller when
		 * the faulting function had not made its own record yet. */
		fp = (uint64_t)state->__fp;
	}
	emit_frames(crash, fp);

	/* The oldest line first: ring_next is where the next line goes. */
	for (unsigned i = 0; i < ring_count; ++i) {
		unsigned slot = (ring_next + RING_LINES - ring_count + i) % RING_LINES;
		emit(before, sizeof before - 1, ring[slot], strlen(ring[slot]), true);
	}
	char chunk[4096];
	used = 0;
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

/* The slide of the shared cache in this process, read while nothing else runs: the first image that
 * lives in the cache is enough (they all share it). */
static uint64_t
shared_cache_slide(void)
{
	/* On iOS and on the Mac libSystem is in the shared cache, and every image of a cache has its slide. */
	static const char libsystem[] = "/usr/lib/libSystem.B.dylib";
	for (uint32_t i = 0, count = _dyld_image_count(); i < count; ++i) {
		const char *path = _dyld_get_image_name(i);
		if (path && !strcmp(path, libsystem)) {
			return (uint64_t)_dyld_get_image_vmaddr_slide(i);
		}
	}
	return 0;
}

/* The fatal signals of crash mode.  Armed here, and again by the two threads below: a library that this
 * process loads later may install its own handler, and then the last lines would never be passed on. */
static void
arm_fatal_handlers(void)
{
	if (!me || !(me->mode & MODE_CRASH)) {
		return;
	}
	const int signals[] = { SIGABRT, SIGSEGV, SIGBUS, SIGILL, SIGTRAP, SIGFPE };
	for (unsigned i = 0; i < sizeof signals / sizeof signals[0]; ++i) {
		struct sigaction action;
		memset(&action, 0, sizeof action);
		action.sa_sigaction = on_fatal_signal;
		action.sa_flags = SA_SIGINFO | SA_ONSTACK;
		sigemptyset(&action.sa_mask);
		(void)sigaction(signals[i], &action, NULL);
	}
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
		cache_slide = shared_cache_slide();
		alt_stack = mmap(NULL, ALT_STACK_BYTES, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
		if (alt_stack != MAP_FAILED) {
			stack_t alternate;
			memset(&alternate, 0, sizeof alternate);
			alternate.ss_sp = alt_stack;
			alternate.ss_size = ALT_STACK_BYTES;
			(void)sigaltstack(&alternate, NULL);
		}
		arm_fatal_handlers();
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
	int length = snprintf(line, sizeof line, "Pong Probe: log v246: capturing the error output of %s (mode %d, up to %u and %u lines, %u s, os_log %s%s)",
	    me->name, me->mode, me->lines, me->other_lines, me->seconds, mirrored ? "mirrored" : "not mirrored",
	    me->verbose_lines ? "; every line at first" : "");
	if (length > 0 && (size_t)length < sizeof line && !test_file) {
		(void)sysctlbyname("kern.pong_diagnostic", NULL, NULL, line, (size_t)length + 1);
	}
}
