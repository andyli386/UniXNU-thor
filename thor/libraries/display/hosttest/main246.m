/* Host test of the display library (this Mac, process name backboardd).  QuartzCore's CADisplay class of
   this Mac carries the hook; the objects asked are instances without QuartzCore's own state, so the
   subclass answers every getter the library calls.  The stand-in reports the identity's size and the
   panel's native bounds (the hook answers the panel's size); a second subclass reports other native
   bounds (the hook keeps the size). */
#import <Foundation/Foundation.h>
#import <QuartzCore/QuartzCore.h>
#import <dlfcn.h>
#import <objc/runtime.h>

@interface CADisplay : NSObject
- (CGRect)bounds;
- (CGRect)nativeBounds;
@end

@interface PongStandIn : CADisplay
@property (nonatomic) CGRect panel;
@end
@implementation PongStandIn
- (CGRect)nativeBounds { return self.panel; }
- (double)scale { return 3; }
- (double)nativeScale { return 3; }
- (double)pointScale { return 3; }
- (id)currentMode { return nil; }
@end

struct pong_rect { double x, y, width, height; };

static struct pong_rect stand_in_bounds(id self, SEL selector)
{
	(void)self; (void)selector;
	struct pong_rect rect = {0, 0, 1206, 2622};
	return rect;
}

typedef void (*pong_test_origin_t)(struct pong_rect (*origin)(id, SEL));

static void show(const char *what, CGRect rect)
{
	printf("%s: %.0fx%.0f\n", what, rect.size.width, rect.size.height);
}

int main(void)
{
	@autoreleasepool {
		pong_test_origin_t put = (pong_test_origin_t)dlsym(RTLD_DEFAULT, "pong_display_test_origin");
		if (!put) { printf("the library is not loaded\n"); return 1; }
		if (strcmp(getprogname(), "backboardd")) { printf("Other: -bounds is still QuartzCore's own %p\n", method_getImplementation(class_getInstanceMethod(objc_lookUpClass("CADisplay"), sel_registerName("bounds")))); return 0; }
		put(stand_in_bounds);
		PongStandIn *on_panel = class_createInstance(objc_lookUpClass("PongStandIn"), 0);
		on_panel.panel = CGRectMake(0, 0, 1440, 3200);
		show("stand-in on the panel, call 1", [on_panel bounds]);
		show("stand-in on the panel, call 2", [on_panel bounds]);
		PongStandIn *elsewhere = class_createInstance(objc_lookUpClass("PongStandIn"), 0);
		elsewhere.panel = CGRectMake(0, 0, 1920, 1080);
		show("the same size on other native bounds", [elsewhere bounds]);
		PongStandIn *no_size = class_createInstance(objc_lookUpClass("PongStandIn"), 0);
		show("the identity's size while the display has no size of its own", [no_size bounds]);
	}
	return 0;
}
