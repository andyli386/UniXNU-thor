/* Stands in for MobileGestalt: answers "iPhone" to everyone. */
#include <CoreFoundation/CoreFoundation.h>
CFTypeRef MGCopyAnswer(CFStringRef question) {
	if (CFEqual(question, CFSTR("GSDeviceName"))) return CFSTR("iPhone");
	if (CFEqual(question, CFSTR("DeviceClass"))) return CFSTR("iPhone");
	return CFSTR("iPhone17,1");
}
