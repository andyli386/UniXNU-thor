/* Host test of the wallpaper library (compiled for this Mac): a stand-in for MobileGestalt answers
   "iPhone" to everyone; a stand-in for WallpaperKit lower-cases that answer and another string; the two
   getters start at NO.  With the library: WallpaperKit gets "ipad" for the device name and "iphone" stays
   the answer for everyone else; another string is lower-cased as before; the two getters answer yes. */
#import <Foundation/Foundation.h>
#include <dlfcn.h>
#include <CoreFoundation/CoreFoundation.h>
#include <objc/runtime.h>
extern CFTypeRef MGCopyAnswer(CFStringRef question);
@interface NSObject (WKAdditions)
- (BOOL)wk_ignoreLogicalScreenClassForWallpaperBundle;
- (BOOL)wk_ignoreProductTypesForWallpaperBundle;
@end
@implementation NSObject (WKAdditions)
- (BOOL)wk_ignoreLogicalScreenClassForWallpaperBundle { return NO; }
- (BOOL)wk_ignoreProductTypesForWallpaperBundle { return NO; }
@end
int main(int argc, const char **argv) {
	@autoreleasepool {
		void *kit = dlopen(argv[1], RTLD_NOW);
		if (!kit) { fprintf(stderr, "dlopen: %s\n", dlerror()); return 2; }
		NSString *(*kitDeviceName)(void) = dlsym(kit, "kitDeviceName");
		NSString *(*kitLower)(NSString *) = dlsym(kit, "kitLower");
		if (!kitDeviceName || !kitLower) { fprintf(stderr, "dlsym: %s\n", dlerror()); return 2; }
		NSString *own = [(NSString *)MGCopyAnswer(CFSTR("GSDeviceName")) lowercaseString];
		NSString *fromKit = kitDeviceName(), *other = kitLower(@"IPadOS_17");
		NSUserDefaults *defaults = NSUserDefaults.standardUserDefaults;
		printf("this program's own call=%s | from WallpaperKit: device name=%s, another string=%s | ignore screen class=%d ignore product types=%d\n",
		    own.UTF8String, fromKit.UTF8String, other.UTF8String,
		    (int)(long)[defaults performSelector:@selector(wk_ignoreLogicalScreenClassForWallpaperBundle)],
		    (int)(long)[defaults performSelector:@selector(wk_ignoreProductTypesForWallpaperBundle)]);
	}
	return 0;
}
