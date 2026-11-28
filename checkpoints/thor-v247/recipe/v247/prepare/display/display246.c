/* SPDX-License-Identifier: MIT */
/* Pong display v246: the render server (backboardd) on the panel's own size.
 *
 * What is known (2026-11-27, v239 against v242 to v244).  Since v242 the render server draws every frame
 * at 1206x2622 -- the pixel size of the iPhone 16 Pro, the identity the device tree carries -- and the
 * framebuffer driver enlarges it to the 1440x3200 panel with the nearest neighbour, which is the jagged
 * edge the user sees; it also costs 1.5-2 ms a frame.  In v237 to v239 the render server drew at
 * 1440x3200 and the driver copied 1:1.  The kernel, the adapter, the device tree and the boot arguments
 * of the display path are the same in both; what differs is a value the render server reads at start.
 *
 * How backboardd decides.  -[BKSystemFacade startWindowServer] (0x100034fe4) takes the size of
 * +[CADisplay mainDisplay] (-bounds, 0x100035200) as the "initial main screen size", gives it to its
 * display object (-setSize:) and builds the window server display with it.  In v244 that is
 * {1206, 2622}.  From then on everything follows it: the window server display, the scenes, the swap
 * surfaces.
 *
 * What v245 saw on the phone (2026-11-27 19:48).  The library loaded, and -bounds was called at the start of
 * the window server: the display had no size of its own at that time (native bounds 0x0, no current mode, point
 * scale not a number) and answered {1206, 2622}; the library kept it, because v245 answered only a display
 * whose native bounds were the panel's.  backboardd's own log at that moment: "[WindowServer] Display 1
 * current mode (16383 x 16383), preferred mode (0 x 0)" -- the display has no mode yet.  So {1206, 2622} is
 * a stand-in for a display without information, and the frames stayed at 1206x2622.
 *
 * What this library does.  The kernel puts it into backboardd only.  Its constructor replaces the
 * implementation of -[CADisplay bounds].  The first calls are passed on to the kernel's diagnostic
 * channel with what the display says of itself (native bounds, scales, current mode, class), so the
 * log shows which values the render server saw.  Then, unless the boot arguments hold pong_ca_native=0,
 * a display that reports {1206, 2622} is answered with {0, 0, 1440, 3200} when its native bounds are the
 * panel's ({1440, 3200}) or empty (0x0: v246): the size v239 ran with.  A display with other native
 * bounds, and every other size, gets exactly what QuartzCore returned.
 *
 * Needs libSystem and libobjc, as backboardd itself does.  Host test: hosttest/run.sh (compiled with
 * PONG_DISPLAY_HOSTTEST: the lines go to a file, and the test may put a stand-in under the hook).
 */
#include <fcntl.h>
#include <mach-o/dyld.h>
#include <objc/message.h>
#include <objc/runtime.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/sysctl.h>
#include <unistd.h>

struct pong_rect {
	double x, y, width, height;
};
struct pong_size {
	double width, height;
};

#define IDENTITY_WIDTH 1206.0
#define IDENTITY_HEIGHT 2622.0
#define PANEL_WIDTH 1440.0
#define PANEL_HEIGHT 3200.0
#define FIRST_CALLS 8            /* every one of the first calls is reported */
#define POWER_REPORTS_UNTIL 2048 /* then at every power of two up to this call number */

static struct pong_rect (*original_bounds)(id, SEL);
static bool native_allowed;
static unsigned calls;
static __thread bool inside;     /* nothing of this library calls into the display again from inside */

#ifdef PONG_DISPLAY_HOSTTEST
static const char *test_file;    /* the Mac comparison test collects the lines in a file */
#endif

static void
report(const char *text)
{
#ifdef PONG_DISPLAY_HOSTTEST
	if (test_file) {
		int fd = open(test_file, O_WRONLY | O_APPEND | O_CREAT, 0644);
		if (fd >= 0) {
			ssize_t ignored = write(fd, text, strlen(text));
			ignored = write(fd, "\n", 1);
			(void)ignored;
			close(fd);
		}
		return;
	}
#endif
	(void)sysctlbyname("kern.pong_diagnostic", NULL, NULL, (void *)text, strlen(text) + 1);
}

static void
report_line(const char *format, ...)
__attribute__((format(printf, 1, 2)));

static void
report_line(const char *format, ...)
{
	char line[700];
	va_list arguments;
	va_start(arguments, format);
	int length = vsnprintf(line, sizeof line, format, arguments);
	va_end(arguments);
	if (length > 0 && (size_t)length < sizeof line) {
		report(line);
	}
}

static bool
responds(id object, const char *name)
{
	return object && ((bool(*)(id, SEL, SEL))objc_msgSend)(object, sel_registerName("respondsToSelector:"),
	    sel_registerName(name));
}

/* The getters below are called with "inside" set, so that a getter which asked -bounds again would not
 * come back here. */
static struct pong_rect
rect_of(id object, const char *name)
{
	struct pong_rect rect = {0, 0, 0, 0};
	if (responds(object, name)) {
		rect = ((struct pong_rect(*)(id, SEL))objc_msgSend)(object, sel_registerName(name));
	}
	return rect;
}

static struct pong_size
size_of(id object, const char *name)
{
	struct pong_size size = {0, 0};
	if (responds(object, name)) {
		size = ((struct pong_size(*)(id, SEL))objc_msgSend)(object, sel_registerName(name));
	}
	return size;
}

static double
double_of(id object, const char *name)
{
	return responds(object, name) ? ((double(*)(id, SEL))objc_msgSend)(object, sel_registerName(name)) : -1.0;
}

/* v246: the display of the render server has no size of its own when its window server starts; the panel's
 * size is answered then as well as when the native bounds are the panel's. */
static bool
on_panel(const struct pong_rect *native)
{
	return (native->width == 0 && native->height == 0) ||
	    (native->width == PANEL_WIDTH && native->height == PANEL_HEIGHT);
}

/* What the display says of itself.  Only getters are called. */
static void
describe(id self, const struct pong_rect *bounds, unsigned number)
{
	if (inside) {
		return;
	}
	inside = true;
	struct pong_rect native = rect_of(self, "nativeBounds");
	double scale = double_of(self, "scale"), native_scale = double_of(self, "nativeScale");
	double point_scale = double_of(self, "pointScale");
	struct pong_size mode = {0, 0};
	id current = responds(self, "currentMode") ?
	    ((id(*)(id, SEL))objc_msgSend)(self, sel_registerName("currentMode")) : nil;
	if (current) {
		mode = size_of(current, "size");
	}
	const char *verdict;
	if (bounds->width != IDENTITY_WIDTH || bounds->height != IDENTITY_HEIGHT) {
		verdict = "kept";
	} else if (native.width == 0 && native.height == 0) {
		verdict = "the identity's size while the display has no size of its own: the panel's size is answered";
	} else if (native.width == PANEL_WIDTH && native.height == PANEL_HEIGHT) {
		verdict = "the identity's size on this panel: the panel's size is answered";
	} else {
		verdict = "the identity's size, but the native bounds are those of another display: kept";
	}
	report_line("Pong Probe: display v246: call %u of -bounds on %s %p: %.0fx%.0f at %.0f,%.0f; native %.0fx%.0f; "
	    "scale %g nativeScale %g pointScale %g; current mode %.0fx%.0f; %s", number,
	    class_getName(object_getClass(self)), self, bounds->width, bounds->height, bounds->x, bounds->y,
	    native.width, native.height, scale, native_scale, point_scale, mode.width, mode.height, verdict);
	inside = false;
}

static struct pong_rect
pong_bounds(id self, SEL selector)
{
	struct pong_rect bounds = original_bounds(self, selector);
	if (inside) {
		return bounds;
	}
	unsigned number = __atomic_add_fetch(&calls, 1U, __ATOMIC_RELAXED);   /* the first call is number 1 */
	const bool identity = bounds.x == 0 && bounds.y == 0 && bounds.width == IDENTITY_WIDTH &&
	    bounds.height == IDENTITY_HEIGHT;
	const bool report_now = number <= FIRST_CALLS ||
	    (number <= POWER_REPORTS_UNTIL && (number & (number - 1)) == 0);
	if (report_now) {
		describe(self, &bounds, number);
	}
	if (identity && native_allowed) {
		/* Only the display whose native size is this panel or none yet: another display keeps its own. */
		inside = true;
		struct pong_rect native = rect_of(self, "nativeBounds");
		inside = false;
		if (on_panel(&native)) {
			bounds.width = PANEL_WIDTH;
			bounds.height = PANEL_HEIGHT;
		}
	}
	return bounds;
}

/* The switch is read once, from the boot arguments of this boot. */
static bool
allowed_by_boot_arguments(void)
{
	char arguments[2048];
	size_t size = sizeof arguments;
	static const char denied[] = "pong_ca_native=0";
	if (sysctlbyname("kern.bootargs", arguments, &size, NULL, 0) != 0) {
		return true;
	}
	arguments[sizeof arguments - 1] = 0;
	return !strstr(arguments, denied);
}

#ifdef PONG_DISPLAY_HOSTTEST
/* The Mac comparison test (hosttest/run.sh) puts a stand-in under the hook for its rewrite check. */
void
pong_display_test_origin(struct pong_rect (*origin)(id, SEL));
void
pong_display_test_origin(struct pong_rect (*origin)(id, SEL))
{
	original_bounds = origin;
}
#endif

__attribute__((constructor))
static void
display_size(void)
{
#ifdef PONG_DISPLAY_HOSTTEST
	test_file = getenv("PONG_DISPLAY_TEST_FILE");
#endif
	native_allowed = allowed_by_boot_arguments();
	const char *name = getprogname();
	if (!name || strcmp(name, "backboardd")) {
		return;
	}
	Class display = objc_lookUpClass("CADisplay");
	Method method = display ? class_getInstanceMethod(display, sel_registerName("bounds")) : NULL;
	if (!method) {
		report("Pong Probe: display v246: QuartzCore's CADisplay has no -bounds here; nothing changed");
		return;
	}
	original_bounds = (struct pong_rect(*)(id, SEL))method_setImplementation(method, (IMP)pong_bounds);
	report_line("Pong Probe: display v246: -[CADisplay bounds] of %s watched in %s%s",
	    class_getName(display), name, native_allowed ? "; the panel's size is answered for {1206, 2622}" :
	    " (pong_ca_native=0: only watched)");
}
