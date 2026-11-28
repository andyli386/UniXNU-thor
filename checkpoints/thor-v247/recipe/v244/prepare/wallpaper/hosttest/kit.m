/* Stands in for WallpaperKit: asks MobileGestalt and lower-cases the answer, as the catalog code of
   the real framework does (defaultWallpaperLookupURL and _loadSystemWallpaperCollections both use the
   lower-cased name afterwards, so the call keeps its frame; the copy below keeps it here too). */
#import <Foundation/Foundation.h>
extern CFTypeRef MGCopyAnswer(CFStringRef question);
NSString *kitDeviceName(void) {
	CFTypeRef answer = MGCopyAnswer(CFSTR("GSDeviceName"));
	NSString *lower = [(NSString *)answer lowercaseString];
	return [lower copy];
}
/* Another string that WallpaperKit lower-cases: it must come out unchanged. */
NSString *kitLower(NSString *string) {
	NSString *lower = [string lowercaseString];
	return [lower copy];
}
