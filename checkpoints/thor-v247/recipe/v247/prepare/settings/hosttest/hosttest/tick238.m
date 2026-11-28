#import <Foundation/Foundation.h>
#include <objc/message.h>
#include "/tmp/claude-501/settings238/source/about237.c"
#include <stdatomic.h>
static atomic_int reports;
static void capture(char *line, int length) { atomic_fetch_add(&reports, 1); printf("  [line %3d] %.*s\n", length, length, line); }
@interface PongWorker : NSObject
- (void)work;
@end
@implementation PongWorker
- (void)work { [self waitFor:6]; }
- (void)waitFor:(int)sec { sleep(sec); }
@end
int main(void) {
    // The watchdog source starts its timer 8 s after launch: wait that long with a live main queue,
    // then block the main thread for 4.5 s and expect one hang report with the main thread in it.
    about_queue = dispatch_queue_create("test.report", DISPATCH_QUEUE_SERIAL);
    about_report = capture;
    PongWorker *worker = [PongWorker new];
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_DEFAULT, 0), ^{ [worker work]; });
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 9 * NSEC_PER_SEC), dispatch_get_main_queue(), ^{
        printf("blocking the main thread for 4.5 s at t=9 s\n");
        [worker waitFor:4];
        [worker waitFor:1];
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 1500 * NSEC_PER_MSEC), dispatch_get_main_queue(), ^{
            printf("done: %d report lines, hangs=%u\n", atomic_load(&reports), atomic_load(&about_hangs));
            exit(0);
        });
    });
    about237_start(mach_thread_self(), about_queue, capture);
    dispatch_main();
}
