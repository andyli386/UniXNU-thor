#import <UIKit/UIKit.h>
#include "../../navigation_completion185.h"
#include <assert.h>
UITransitionContextViewControllerKey const UITransitionContextToViewControllerKey=@"to";
UITransitionContextViewControllerKey const UITransitionContextFromViewControllerKey=@"from";
@implementation UIViewController
@end
@interface Coordinator : NSObject <UIViewControllerTransitionCoordinator,UIViewControllerTransitionCoordinatorContext>
@property(nonatomic,strong) id from,to;
@property(nonatomic) BOOL isAnimated,isCancelled,decline,inlineCompletion;
@property(nonatomic,copy) void (^pending)(id<UIViewControllerTransitionCoordinatorContext>);
@end
@implementation Coordinator
- (id)viewControllerForKey:(UITransitionContextViewControllerKey)key{return [key isEqual:@"to"] ? self.to : self.from;}
- (BOOL)animateAlongsideTransition:(void (^)(id<UIViewControllerTransitionCoordinatorContext>))animation
                       completion:(void (^)(id<UIViewControllerTransitionCoordinatorContext>))completion
{
    assert(animation==nil && completion);
    if(self.inlineCompletion)completion(self);
    if(self.decline)return NO;
    self.pending=completion;
    return YES;
}
@end
static void record(void *p,enum navigation_outcome x){*(enum navigation_outcome*)p=x;}
int main(void)
{
    @autoreleasepool {
        UIViewController *nav=[UIViewController new]; id a=[UIViewController new], b=[UIViewController new];
        Coordinator *c=[Coordinator new]; c.from=a;c.to=b;c.isAnimated=YES;
        enum navigation_outcome status=NAV_WAITING;
        observe_navigation_completion(nav,b,true,false,&status,record);assert(status==NAV_NOT_ANIMATED);
        observe_navigation_completion(nav,b,true,true,&status,record);assert(status==NAV_NO_COORDINATOR);
        nav.transitionCoordinator=c;
        observe_navigation_completion(nav,a,true,true,&status,record);assert(status==NAV_OTHER_TARGET);
        observe_navigation_completion(nav,nil,true,true,&status,record);assert(status==NAV_OTHER_TARGET);
        c.decline=YES;
        observe_navigation_completion(nav,b,true,true,&status,record);assert(status==NAV_DECLINED);
        c.decline=NO;status=NAV_WAITING;
        observe_navigation_completion(nav,b,true,true,&status,record);assert(status==NAV_WAITING && c.pending);
        c.pending(c);assert(status==NAV_FINISHED);
        c.isCancelled=YES;c.inlineCompletion=YES;
        observe_navigation_completion(nav,a,false,true,&status,record);assert(status==NAV_CANCELLED);
        c.isAnimated=NO;
        observe_navigation_completion(nav,b,true,true,&status,record);assert(status==NAV_NO_COORDINATOR);
    }
    puts("PASS production observer: matching actual push/pop target, asynchronous completion, cancel, disabled or missing animation, other/nil target, registration refusal, no alongside animation.");
}
