/* Stands in for WallpaperKit: asks MobileGestalt through the same call the real framework makes.
   kitAsk keeps its frame, as the catalog code of the real framework does (it uses the answer);
   kitAskTail ends in a tail call, so the return address is its caller's, not WallpaperKit's. */
#include <CoreFoundation/CoreFoundation.h>
extern CFTypeRef MGCopyAnswer(CFStringRef question);
CFTypeRef kitAsk(CFStringRef question) { CFTypeRef answer = MGCopyAnswer(question); return answer ? answer : CFSTR("none"); }
CFTypeRef kitAskTail(CFStringRef question) { return MGCopyAnswer(question); }
