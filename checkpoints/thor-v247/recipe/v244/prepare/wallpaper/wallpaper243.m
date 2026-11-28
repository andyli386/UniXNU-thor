/* SPDX-License-Identifier: MIT */
/* Pong wallpaper v243: the stock wallpaper catalogs of this restore for the identity this port shows.
 *
 * What the 22H374 WallpaperKit does (read from the framework of this restore):
 *   - +[WKDefaultWallpaperManager defaultWallpaperLookupURL] opens
 *     /Library/Wallpaper/DefaultWallpapers~<device name>.plist, and
 *     -[WKWallpaperRepresentingCollectionsManager _loadSystemWallpaperCollections] reads
 *     Collections/Collections~<device name>.plist.  The device name is the answer of MobileGestalt's
 *     GSDeviceName in lower case.
 *   - +[WKWallpaperBundle shouldLoadWallpaperBundleAtURL:] drops every bundle whose logicalScreenClass
 *     differs from this device's unless -[NSUserDefaults(WKAdditions) wk_ignoreLogicalScreenClassForWallpaperBundle]
 *     says yes (it does on internal installs only); the same holds for product types.
 *   - With no lookup file, -[WKDefaultWallpaperManager _loadDefaultWallpaperInformation] falls back to a
 *     hard-coded iPhone or iPad collection and finally builds a "Solid Black" bundle.
 *
 * This restore carries only the iPad catalogs (DefaultWallpapers~ipad.plist, iPadOS_17~ipad, Lake~ipad,
 * Stripe), while this port shows the identity of an iPhone (iPhone17,1): the device name is "iPhone",
 * WallpaperKit looked for DefaultWallpapers~iphone.plist, found nothing, and every request ended in
 * "Solid Black".
 *
 * What this library does, in the processes that load WallpaperKit (SpringBoard through
 * SpringBoard.framework, CollectionsPoster):
 *   1. It replaces -[NSString lowercaseString] on the class that implements it.  WallpaperKit lower-cases
 *      the device name in both places that build a catalog file name.  When the call comes from
 *      WallpaperKit and the string is "iPhone", the answer is "ipad"; every other caller and every other
 *      string gets the original.
 *   2. It replaces the two wk_ignore*ForWallpaperBundle getters of NSUserDefaults with a function that
 *      answers yes, so the iPad bundles pass the screen-class and the product-type check.
 *   3. It writes no file, changes no preference and draws nothing; in a process that does not load
 *      WallpaperKit it does nothing.
 *
 * v241 and v242 tried to answer MobileGestalt's questions through an interpose entry.  That cannot work
 * here: WallpaperKit and libMobileGestalt are both in the shared cache, and the call between them is a
 * direct jump fixed when the cache was built (all ten call sites of WallpaperKit go to one stub that
 * computes the address of MGCopyAnswer), so there is no binding for dyld to redirect.  The v242 console
 * shows the library loaded and the two getters replaced, and not one intercepted question.
 *
 * Lines go through the kernel's diagnostic channel (kern.pong_diagnostic) to the console as
 * "PONG PROBE <process>[pid]: Pong Probe: wallpaper v243 ...".
 */
#include <Foundation/Foundation.h>
#include <dlfcn.h>
#include <mach-o/dyld.h>
#include <objc/runtime.h>
#include <objc/objc.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/sysctl.h>
#include <unistd.h>

/* In libSystem on every Darwin; the iOS SDK has no header for it. */
extern const uint8_t *getsegmentdata(const struct mach_header *mach_header, const char *segment_name,
    unsigned long *size);

static const char version_line[] = "Pong Probe: wallpaper v243: ";

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
	if (getenv("PONG_WALLPAPER_STDERR")) {
		/* The Mac comparison test has no diagnostic channel; it reads the lines from here. */
		fputs(line, stderr);
		fputc('\n', stderr);
		return;
	}
	(void)sysctlbyname("kern.pong_diagnostic", NULL, NULL, line, (size_t)length + 1);
}

static uintptr_t kit_start, kit_end;
static bool getters_replaced;
static unsigned ipad_names, other_names, owners;

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
replace_getters(void)
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

/* The classes on which -lowercaseString was replaced, and what they answered before. */
static Class name_owners[4];
static IMP name_originals[4];

static id
pong_lowercase_string(id self, SEL selector)
{
	const void *caller = __builtin_return_address(0);
	if (from_wallpaper_kit(caller)) {
		if ([self isEqualToString:@"iPhone"]) {
			if (__atomic_fetch_add(&ipad_names, 1, __ATOMIC_RELAXED) < 3) {
				say("%s\"iPhone\" lower-cased to \"ipad\" for WallpaperKit+0x%lx", version_line,
				    (unsigned long)((uintptr_t)caller - kit_start));
			}
			return @"ipad";
		}
		/* A short string that is not the device name: left as it is, but the first ones are named,
		 * so a device name spelled differently would show. */
		if ([self length] <= 16 && __atomic_fetch_add(&other_names, 1, __ATOMIC_RELAXED) < 3) {
			say("%sWallpaperKit+0x%lx lower-cases \"%s\" (left as it is)", version_line,
			    (unsigned long)((uintptr_t)caller - kit_start), [self UTF8String]);
		}
	}
	for (Class cls = object_getClass(self); cls; cls = class_getSuperclass(cls)) {
		for (unsigned i = 0; i < owners; ++i) {
			if (name_owners[i] == cls) {
				return ((id (*)(id, SEL))name_originals[i])(self, selector);
			}
		}
	}
	/* Not reachable: the replacement was only ever installed on a class of such an object. */
	return self;
}

/* Replace -lowercaseString on the class that implements it for this object, once per class.  The
 * constant strings MobileGestalt answers with and the strings made at run time may have different
 * classes; each is followed up its hierarchy to the class that owns the method. */
static bool
replace_name_method(id instance, IMP *original)
{
	for (Class cls = object_getClass(instance); cls; cls = class_getSuperclass(cls)) {
		unsigned count = 0;
		Method *list = class_copyMethodList(cls, &count);
		Method found = NULL;
		for (unsigned i = 0; i < count && !found; ++i) {
			if (method_getName(list[i]) == @selector(lowercaseString)) {
				found = list[i];
			}
		}
		bool replaced = false;
		if (found) {
			for (unsigned i = 0; i < owners; ++i) {
				if (name_owners[i] == cls) {
					*original = name_originals[i];
					replaced = true;
				}
			}
			if (!replaced && owners < sizeof name_owners / sizeof name_owners[0]) {
				IMP before = method_getImplementation(found);
				if (class_replaceMethod(cls, @selector(lowercaseString), (IMP)pong_lowercase_string,
				    method_getTypeEncoding(found))) {
					name_owners[owners] = cls;
					name_originals[owners] = before;
					++owners;
					*original = before;
					replaced = true;
				}
			}
		}
		free(list);
		if (found) {
			return replaced;
		}
	}
	return false;
}

static void
install_hooks(void)
{
	getters_replaced = replace_getters();
	IMP original = NULL;
	NSString *made = [[NSString alloc] initWithUTF8String:"iPhone"];
	bool constant = made ? replace_name_method(@"iPhone", &original) : false;
	bool runtime = made ? replace_name_method(made, &original) : false;
	char names[4][40];
	for (unsigned i = 0; i < owners; ++i) {
		const char *name = class_getName(name_owners[i]);
		snprintf(names[i], sizeof names[i], "%s", name ? name : "?");
	}
	say("%sWallpaperKit at %p-%p: getters %s; the device-name hook is on %u class%s (%s%s%s)", version_line,
	    (const void *)kit_start, (const void *)kit_end, getters_replaced ? "replaced" : "NOT replaced (methods differ)",
	    owners, owners == 1 ? "" : "es", owners > 0 ? names[0] : "none", owners > 1 ? ", " : "",
	    owners > 1 ? names[1] : "");
	if (!constant || !runtime) {
		say("%sdevice-name hook not complete: constant-string class %s, run-time class %s", version_line,
		    constant ? "replaced" : "NOT replaced", runtime ? "replaced" : "NOT replaced");
	}
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
	install_hooks();
}

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
