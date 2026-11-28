/* SPDX-License-Identifier: MIT */
/* Pong trace flags v244: make a process's own os_log messages appear on its error output.
 *
 * Read from libsystem_trace of 22H374 (libtrace-1643.120.5, in the shared cache): three one-byte
 * switches in its __DATA_DIRTY.
 *   0x1f28efbbe  mirror every enabled message to the process's error output.  __os_trace_init_slow sets it
 *               when the environment holds ACTIVITY_LOG_STDERR (or CFLOG_FORCE_STDERR) -- but only when
 *               AMFI lets the process use development variables, which it does not for Apple's own
 *               processes on this port, so the environment variable alone did nothing for wifid on
 *               2026-11-27 (its capture file stayed empty for 120 s).
 *   0x1f28efbbf  "development tool" mode (OS_ACTIVITY_DT_MODE): info and debug messages are composed too
 *               (_os_log_type_enabled asks it), and _os_log_impl_mirror_to_stderr passes them on.
 *   0x1f28efbc0  tool privacy (OS_ACTIVITY_TOOLS_PRIVACY): private values are shown, not "<private>".
 * __os_log_impl_mirror_to_stderr reads the first two on every message, so they may be set at any time
 * and cleared again; nothing else needs them in a particular state.
 *
 * pong_trace_flags(tool_mode, privacy) sets them for this process and returns 1; it returns 0 and
 * writes nothing unless the system is 22H374 and the bytes lie inside libsystem_trace's __DATA_DIRTY.
 * pong_trace_flags(0, 0) switches the mirroring off again.
 */
#ifndef PONG_TRACE_FLAGS_H
#define PONG_TRACE_FLAGS_H

#include <dlfcn.h>
#include <mach-o/dyld.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include <sys/sysctl.h>

#ifdef __cplusplus
extern "C" {
#endif

/* In libSystem on every Darwin; the iOS SDK has no header for it. */
extern const uint8_t *getsegmentdata(const struct mach_header *mach_header, const char *segment_name,
    unsigned long *size);

#define PONG_TRACE_MIRROR 0x1f28efbbeULL
#define PONG_TRACE_TOOL_MODE 0x1f28efbbfULL
#define PONG_TRACE_PRIVACY 0x1f28efbc0ULL

static int pong_trace_flags(int tool_mode, int privacy)
{
	char build[40] = {0};
	size_t build_size = sizeof build;
	if (sysctlbyname("kern.osversion", build, &build_size, NULL, 0) != 0 || strcmp(build, "22H374") != 0) {
		return 0;
	}
	static const char suffix[] = "/libsystem_trace.dylib";
	const size_t suffix_bytes = sizeof suffix - 1;
	for (uint32_t i = 0, count = _dyld_image_count(); i < count; ++i) {
		const char *path = _dyld_get_image_name(i);
		const struct mach_header *header = _dyld_get_image_header(i);
		if (!path || !header || strlen(path) < suffix_bytes ||
		    strcmp(path + strlen(path) - suffix_bytes, suffix)) {
			continue;
		}
		unsigned long size = 0;
		const uint8_t *dirty = getsegmentdata(header, "__DATA_DIRTY", &size);
		uint8_t *mirror = (uint8_t *)(_dyld_get_image_vmaddr_slide(i) + (intptr_t)PONG_TRACE_MIRROR);
		if (!dirty || size < 3 || mirror < dirty || mirror + 3 > dirty + size) {
			return 0;
		}
		mirror[0] = 1;
		mirror[1] = tool_mode ? 1 : 0;
		mirror[2] = privacy ? 1 : 0;
		return 1;
	}
	return 0;
}

#ifdef __cplusplus
}
#endif

#endif
