#import <Foundation/Foundation.h>
@interface PongBase : NSObject @end
@implementation PongBase
- (BOOL)hasEthernetNetworkInterfaces { return YES; }
@end
#if OWN_METHOD
@interface PSSystemConfigurationDynamicStoreEthernetWatcher : NSObject @end
@implementation PSSystemConfigurationDynamicStoreEthernetWatcher
- (BOOL)hasEthernetNetworkInterfaces { return YES; }
@end
#else
@interface PSSystemConfigurationDynamicStoreEthernetWatcher : PongBase @end
@implementation PSSystemConfigurationDynamicStoreEthernetWatcher
@end
#endif
