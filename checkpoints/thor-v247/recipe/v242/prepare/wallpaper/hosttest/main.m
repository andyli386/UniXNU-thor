/* Host test of the wallpaper library: a stand-in for MobileGestalt answers "iPhone" to everyone;
   a stand-in for WallpaperKit asks through the library's interposer; the two getters start at NO. */
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
		CFTypeRef (*ask)(CFStringRef) = dlsym(kit, "kitAsk");
		CFTypeRef (*tail)(CFStringRef) = dlsym(kit, "kitAskTail");
		char own[64], name[64], class[64], product[64], tailName[64];
		CFTypeRef ownAnswer = MGCopyAnswer(CFSTR("GSDeviceName"));
		CFTypeRef a = ask(CFSTR("GSDeviceName")), b = ask(CFSTR("DeviceClass")), c = ask(CFSTR("ProductType")), t = tail(CFSTR("GSDeviceName"));
		CFStringGetCString(ownAnswer, own, sizeof own, kCFStringEncodingUTF8);
		CFStringGetCString(a, name, sizeof name, kCFStringEncodingUTF8);
		CFStringGetCString(b, class, sizeof class, kCFStringEncodingUTF8);
		CFStringGetCString(c, product, sizeof product, kCFStringEncodingUTF8);
		CFStringGetCString(t, tailName, sizeof tailName, kCFStringEncodingUTF8);
		NSUserDefaults *defaults = NSUserDefaults.standardUserDefaults;
		printf("this program's own call=%s | from WallpaperKit: GSDeviceName=%s DeviceClass=%s ProductType=%s, tail call=%s | ignore screen class=%d ignore product types=%d\n",
		    own, name, class, product, tailName,
		    (int)(long)[defaults performSelector:@selector(wk_ignoreLogicalScreenClassForWallpaperBundle)],
		    (int)(long)[defaults performSelector:@selector(wk_ignoreProductTypesForWallpaperBundle)]);
	}
	return 0;
}
