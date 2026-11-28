#import <UIKit/UIKit.h>
#include "navigation_completion185.h"

void observe_navigation_completion(id owner, id target, bool pushing, bool animated,
                                  void *context, navigation_observer observer)
{
    if (!animated) {
        observer(context, NAV_NOT_ANIMATED);
        return;
    }
    id<UIViewControllerTransitionCoordinator> coordinator = [(UIViewController *)owner transitionCoordinator];
    if (!coordinator || ![coordinator isAnimated]) {
        observer(context, NAV_NO_COORDINATOR);
        return;
    }
    UITransitionContextViewControllerKey key = pushing ? UITransitionContextToViewControllerKey
                                                      : UITransitionContextFromViewControllerKey;
    if (!target || [coordinator viewControllerForKey:key] != target) {
        observer(context, NAV_OTHER_TARGET);
        return;
    }
    BOOL registered = [coordinator animateAlongsideTransition:nil
        completion:^(id<UIViewControllerTransitionCoordinatorContext> result) {
            observer(context, [result isCancelled] ? NAV_CANCELLED : NAV_FINISHED);
        }];
    if (!registered)
        observer(context, NAV_DECLINED);
}
