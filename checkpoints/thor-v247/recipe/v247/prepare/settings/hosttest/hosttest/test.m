#import <Foundation/Foundation.h>
#include <stdatomic.h>
#include <objc/message.h>
#include "../source/about237.c"
@class PongBase;
@class PSSystemConfigurationDynamicStoreEthernetWatcher;

static atomic_int reports;
static void capture(char *line, int length)
{
    atomic_fetch_add(&reports, 1);
    printf("  [line %3d] %.*s\n", length, length, line);
}

@interface PongWorker : NSObject
- (void)work;
- (void)nested:(int)n;
@end
@implementation PongWorker
- (void)work { [self nested:3]; }
- (void)nested:(int)n { if (n) [self nested:n - 1]; else sleep(5); }
@end

static BOOL answer(id object)
{
    return ((BOOL (*)(id, SEL))objc_msgSend)(object, sel_registerName("hasEthernetNetworkInterfaces"));
}

int main(void)
{
#if OWN_METHOD
    id watcher = [[PSSystemConfigurationDynamicStoreEthernetWatcher new] autorelease];
    id unrelated = nil;
#else
    id watcher = [[PSSystemConfigurationDynamicStoreEthernetWatcher new] autorelease];
    id unrelated = [[PongBase new] autorelease];
#endif
    printf("before: watcher=%d\n", watcher ? answer(watcher) : -1);
    char note[ABOUT_NOTE] = "";
    bool ok = about_replace_ethernet_answer(note, sizeof(note));
    printf("replace: ok=%d watcher=%d base=%d note=[%s]\n", ok, watcher ? answer(watcher) : -1,
           unrelated ? answer(unrelated) : -1, note);
    char again[ABOUT_NOTE] = "";
    printf("second replace: ok=%d watcher=%d note=[%s]\n", about_replace_ethernet_answer(again, sizeof(again)),
           answer(watcher), again);

    about_queue = dispatch_queue_create("test.report", DISPATCH_QUEUE_SERIAL);
    about_report = capture;
    about_main = mach_thread_self();
    PongWorker *worker = [PongWorker new];
    pthread_setname_np("com.apple.SettingsApp.PrimaryListLoading");
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_DEFAULT, 0), ^{ [worker work]; });
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 500 * NSEC_PER_MSEC), dispatch_get_global_queue(QOS_CLASS_DEFAULT, 0), ^{
        about_hangs = 1;
        about_collect();
        dispatch_async(about_queue, ^{ about_emit(NULL); });
    });
    sleep(3);
    printf("hang snapshot done, %d lines\n", atomic_load(&reports));
    return 0;
}
