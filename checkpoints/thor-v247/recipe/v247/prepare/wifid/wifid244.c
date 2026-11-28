/* SPDX-License-Identifier: MIT */
/* Pong wifid hold v244: the system Wi-Fi daemon starts when configd and the power source are ready,
 * and its own log is kept for the next analysis.
 *
 * Why: with a wlan node in the device tree launchd starts wifid in the first second.  On this port
 * that left the Wi-Fi pane of Settings empty and the status bar without its battery number (v239).
 * Upstream's helper prepares the interface first: the interface-namer marker, en0 in
 * State:/Network/Interface and a network service for it.  The battery level is published about a
 * minute into the boot.  So wifid may run only after both.
 *
 * How: the kernel puts this library into wifid (DYLD_INSERT_LIBRARIES, by a table of its own).
 * Its constructor waits until the kernel's read-only switch kern.pong_wifi_gate is 1 (configd
 * says en0 is prepared, a power source is on the registry, or 150 seconds have gone by), and only
 * then lets wifid start.  Nothing of wifid is changed.
 *
 * v244, after the first run with wifid up (v243 with the wlan node, 2026-11-27): the daemon listed
 * 48 networks, but Settings hung on it as soon as its Wi-Fi pane opened, an association request
 * was refused by the access point, and the daemon then answered nothing more.  Its capture file
 * held 129 bytes: OS_ACTIVITY_DT_MODE in the environment did not make wifid's os_log appear
 * (libsystem_trace honours development variables only when AMFI allows them).  Now the library
 * sets libsystem_trace's own switch (../logcap/trace_flags.h: info and debug messages too, private
 * values shown) when it releases wifid, and keeps the output in /private/var/logs/PongWifid-v244.txt
 * for the life of the process, up to 24 MiB.  libpong-probe-v244 in the same process samples its
 * threads.
 */
#include <stdarg.h>
#include <sys/stat.h>
#include <sys/sysctl.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include "../logcap/trace_flags.h"

#define LOG_PATH "/private/var/logs/PongWifid-v244.txt"
#define LOG_LIMIT (24u * 1024u * 1024u)

static uint64_t
now_us(void)
{
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return (uint64_t)ts.tv_sec * 1000000u + (uint64_t)ts.tv_nsec / 1000u;
}

static void
note(const char *format, ...) __attribute__((format(printf, 1, 2)));

static void
note(const char *format, ...)
{
	char line[240];
	va_list list;
	va_start(list, format);
	int length = vsnprintf(line, sizeof line, format, list);
	va_end(list);
	if (length > 0 && (size_t)length < sizeof line) {
		(void)sysctlbyname("kern.pong_diagnostic", NULL, NULL, line, (size_t)length + 1);
	}
}

static int
gate(void)
{
	int value = -1;
	size_t size = sizeof value;
	if (sysctlbyname("kern.pong_wifi_gate", &value, &size, NULL, 0) != 0 || size != sizeof value) {
		return -1;
	}
	return value;
}

/* The capture ends with the process, or when the file has grown past LOG_LIMIT. */
static void *
capture_end(void *argument)
{
	(void)argument;
	for (;;) {
		sleep(30);
		struct stat status;
		if (fstat(STDERR_FILENO, &status) != 0 || status.st_size > (off_t)LOG_LIMIT) {
			break;
		}
	}
	int null = open("/dev/null", O_WRONLY | O_CLOEXEC);
	if (null >= 0) {
		(void)dup2(null, STDOUT_FILENO);
		(void)dup2(null, STDERR_FILENO);
		close(null);
	}
	return NULL;
}

static void
capture_start(unsigned waited_ms)
{
	int log = open(LOG_PATH, O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0600);
	if (log < 0) {
		return;
	}
	off_t size = lseek(log, 0, SEEK_END);
	if (size > (off_t)LOG_LIMIT) {
		(void)ftruncate(log, 0);
	}
	char boot[40] = "?";
	size_t boot_size = sizeof boot;
	if (sysctlbyname("kern.bootsessionuuid", boot, &boot_size, NULL, 0) != 0 || boot_size == 0) {
		boot[0] = '?';
		boot[1] = 0;
	}
	char head[160];
	int length = snprintf(head, sizeof head, "\n==== wifid pid %d, boot session %.8s: waited %.1f s for kern.pong_wifi_gate; "
	    "the output of this process follows (os_log mirrored, at most %u MiB)\n", getpid(), boot,
	    waited_ms / 1000.0, LOG_LIMIT >> 20);
	if (length > 0) {
		(void)write(log, head, (size_t)length);
	}
	(void)dup2(log, STDOUT_FILENO);
	(void)dup2(log, STDERR_FILENO);
	if (log != STDOUT_FILENO && log != STDERR_FILENO) {
		close(log);
	}
	pthread_t thread;
	if (pthread_create(&thread, NULL, capture_end, NULL) == 0) {
		(void)pthread_detach(thread);
	}
}

__attribute__((constructor))
static void
hold_until_prepared(void)
{
	const uint64_t began = now_us();
	unsigned waited_ms = 0;
	for (;;) {
		const int value = gate();
		if (value != 0) {
			break;              /* 1: prepared; -1: this kernel has no such switch
			                       (the library then belongs to no table): run at once */
		}
		usleep(100000);
		waited_ms = (unsigned)((now_us() - began) / 1000u);
	}
	const int mirrored = pong_trace_flags(1, 1);
	capture_start(waited_ms);
	note("Pong Probe: wifid hold v244: released after %u ms; its output goes to %s (os_log %s)",
	    waited_ms, LOG_PATH, mirrored ? "mirrored" : "NOT mirrored");
}
