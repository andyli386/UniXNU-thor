#import <Foundation/Foundation.h>
typedef NSString *UITransitionContextViewControllerKey;
extern UITransitionContextViewControllerKey const UITransitionContextToViewControllerKey;
extern UITransitionContextViewControllerKey const UITransitionContextFromViewControllerKey;
@protocol UIViewControllerTransitionCoordinatorContext <NSObject>
- (BOOL)isCancelled;
@end
@protocol UIViewControllerTransitionCoordinator <NSObject>
- (BOOL)isAnimated;
- (id)viewControllerForKey:(UITransitionContextViewControllerKey)key;
- (BOOL)animateAlongsideTransition:(void (^)(id<UIViewControllerTransitionCoordinatorContext>))animation
                       completion:(void (^)(id<UIViewControllerTransitionCoordinatorContext>))completion;
@end
@interface UIViewController : NSObject
@property (nonatomic,strong) id<UIViewControllerTransitionCoordinator> transitionCoordinator;
@end
