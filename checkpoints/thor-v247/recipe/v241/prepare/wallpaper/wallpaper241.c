/* SPDX-License-Identifier: MIT */
/* Pong wallpaper v241: the stock wallpaper catalogs of this restore for the identity this port shows.
 *
 * What the 22H374 WallpaperKit does (read from the framework of this restore):
 *   - +[WKDefaultWallpaperManager defaultWallpaperLookupURL] opens
 *     /Library/Wallpaper/DefaultWallpapers~<device name>.plist, the name being MobileGestalt's
 *     "GSDeviceName" in lower case.
 *   - -[WKWallpaperRepresentingCollectionsManager _loadSystemWallpaperCollections] orders the
 *     collections by Collections~<device name>.plist.
 *   - WKLogicalScreenClassString() builds "<w>w-<h>h@<scale>x~<DeviceClass in lower case>" from the
 *     main screen, and +[WKWallpaperBundle shouldLoadWallpaperBundleAtURL:] drops every bundle whose
 *     "logicalScreenClass" differs, unless -wk_ignoreLogicalScreenClassForWallpaperBundle says yes
 *     (that getter answers yes only on internal installs).
 *   - -[WKDefaultWallpaperManager _loadDefaultWallpaperInformation] falls back, when the lookup file
 *     is missing, to a hard-coded iPhone or iPad collection by -[UIDevice sf_isiPad], and finally
 *     builds a "Solid Black" bundle.
 * This port shows the identity of an iPhone (iPhone17,1) on a restore that carries only the iPad
 * catalogs: DefaultWallpapers~ipad.plist, Collections~ipad.plist, iPadOS_17~ipad.wallpaperCollection
 * (810w-1080h@2x~ipad).  So nothing matched and every wallpaper request ended in "Solid Black".
 *
 * This library changes three answers, for WallpaperKit's own calls only:
 *   1. MobileGestalt's "GSDeviceName" and "DeviceClass" are answered with "iPad" when the caller is
 *      inside WallpaperKit (decided from the return address), so the iPad lookup and collection
 *      files are used.  Every other caller keeps the phone identity.
 *   2. The two WallpaperKit getters above answer yes, so the iPad bundles pass the screen-class
 *      check.  They are instance methods of NSUserDefaults in WallpaperKit's category WKAdditions.
 * Nothing is drawn, no file is written, and no preference is changed.  The library does nothing in a
 * process that does not load WallpaperKit.
 *
 * Lines go through the kernel's diagnostic channel (kern.pong_diagnostic) to the console as
 * "PONG PROBE <process>[pid]: Pong Probe: wallpaper v241 ...".
 */
#include <CoreFoundation/CoreFoundation.h>
#include <dlfcn.h>
#include <mach-o/dyld.h>
#include <objc/runtime.h>
#include <objc/objc.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <sys/sysctl.h>
#include <unistd.h>

extern CFTypeRef MGCopyAnswer(CFStringRef question);
/* In libSystem on every Darwin; the iOS SDK has no header for it. */
extern const uint8_t *getsegmentdata(const struct mach_header *mach_header, const char *segment_name,
    unsigned long *size);

static const char version_line[] = "Pong Probe: wallpaper v241: ";

static void
say(const char *format, ...) __attribute__((format(printf, 1, 2)));

static void
say(const char *format, ...)
{
	char line[240];
	va_list list;
	va_start(list, format);
	int length = vsnprintf(line, sizeof line, format, list);
	va_end(list);
	if (length <= 0 || (size_t)length >= sizeof line) {
		return;
	}
	(void)sysctlbyname("kern.pong_diagnostic", NULL, NULL, line, (size_t)length + 1);
}

static uintptr_t kit_start, kit_end;
static bool hooks_installed;
static unsigned ipad_answers;

static bool
from_wallpaper_kit(const void *return_address)
{
	const uintptr_t address = (uintptr_t)return_address;
	return address >= kit_start && address < kit_end;
}

static BOOL
ignore_for_wallpaper_bundle(id self, SEL selector)
{
	(void)self;
	(void)selector;
	return YES;
}

/* The category methods are on NSUserDefaults; their names are unique to WallpaperKit. */
static bool
install_hooks(void)
{
	Method screen = class_getInstanceMethod(objc_getClass("NSUserDefaults"),
	    sel_registerName("wk_ignoreLogicalScreenClassForWallpaperBundle"));
	Method product = class_getInstanceMethod(objc_getClass("NSUserDefaults"),
	    sel_registerName("wk_ignoreProductTypesForWallpaperBundle"));
	if (!screen || !product) {
		return false;
	}
	const char *screen_type = method_getTypeEncoding(screen);
	const char *product_type = method_getTypeEncoding(product);
	/* BOOL return, self and the selector only: "B16@0:8". */
	if (!screen_type || !product_type || screen_type[0] != 'B' || product_type[0] != 'B' ||
	    method_getNumberOfArguments(screen) != 2 || method_getNumberOfArguments(product) != 2) {
		return false;
	}
	method_setImplementation(screen, (IMP)ignore_for_wallpaper_bundle);
	method_setImplementation(product, (IMP)ignore_for_wallpaper_bundle);
	return true;
}

static void
note_image(const struct mach_header *header, intptr_t slide)
{
	(void)slide;
	static const char suffix[] = "/WallpaperKit.framework/WallpaperKit";
	if (kit_start || !header) {
		return;
	}
	Dl_info info;
	if (!dladdr(header, &info) || !info.dli_fname) {
		return;
	}
	const size_t length = strlen(info.dli_fname);
	if (length < sizeof(suffix) - 1 || strcmp(info.dli_fname + length - (sizeof(suffix) - 1), suffix)) {
		return;
	}
	unsigned long size = 0;
	const uint8_t *base = getsegmentdata(header, "__TEXT", &size);
	if (!base || !size) {
		return;
	}
	kit_start = (uintptr_t)base;
	kit_end = kit_start + size;
	hooks_installed = install_hooks();
	say("%sWallpaperKit at %p-%p, catalog hooks %s", version_line, (const void *)kit_start,
	    (const void *)kit_end, hooks_installed ? "installed" : "NOT installed (methods differ)");
}

static CFTypeRef
pong_MGCopyAnswer(CFStringRef question)
{
	if (question && from_wallpaper_kit(__builtin_return_address(0)) &&
	    (CFEqual(question, CFSTR("GSDeviceName")) || CFEqual(question, CFSTR("DeviceClass")))) {
		if (__atomic_fetch_add(&ipad_answers, 1, __ATOMIC_RELAXED) < 2) {
			say("%s\"%s\" answered with iPad for a call from WallpaperKit+%lx", version_line,
			    CFEqual(question, CFSTR("GSDeviceName")) ? "GSDeviceName" : "DeviceClass",
			    (unsigned long)((uintptr_t)__builtin_return_address(0) - kit_start));
		}
		return (CFTypeRef)CFSTR("iPad");
	}
	return MGCopyAnswer(question);
}

__attribute__((used)) static const void *const pong_interpose_mg_copy_answer[2]
    __attribute__((section("__DATA,__interpose"))) = {
	(const void *)&pong_MGCopyAnswer, (const void *)&MGCopyAnswer
};

__attribute__((constructor))
static void
wallpaper_catalog(void)
{
	uint32_t count = _dyld_image_count();
	for (uint32_t i = 0; i < count; i++) {
		note_image(_dyld_get_image_header(i), _dyld_get_image_vmaddr_slide(i));
	}
	_dyld_register_func_for_add_image(note_image);
	if (!kit_start) {
		/* A process that may load WallpaperKit later reports then; one line says it was here. */
		say("%sloaded; WallpaperKit is not among the %u images of this process", version_line,
		    (unsigned)count);
	}
}
