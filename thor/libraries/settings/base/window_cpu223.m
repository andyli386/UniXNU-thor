#include "window_cpu223.h"
#ifndef THOR_WINDOW_HOST
#import <UIKit/UIKit.h>
#else
#import <Foundation/Foundation.h>
typedef long UIEventType,UITouchPhase;
enum { UIEventTypeTouches=0, UITouchPhaseEnded=3 };
@class UIWindow, UIEvent;
@interface UITouch:NSObject
@property(nonatomic,assign) UIWindow *window;
@property(nonatomic,assign) UITouchPhase phase;
@property(nonatomic,assign) NSTimeInterval timestamp;
@end
@interface UIEvent:NSObject
@property(nonatomic,assign) UIEventType type;
@property(nonatomic,retain) NSSet *allTouches;
@end
@interface UIWindow:NSObject
-(void)sendEvent:(UIEvent *)event;
@end
#endif
#import <objc/runtime.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

enum { WindowBudget=32 };
static const uint64_t MaxAgeUs=500000;
typedef void (*WindowSend)(UIWindow *,SEL,UIEvent *);
static _Atomic(WindowSend) previousWindow;
static unsigned accepted,depth;
static bool stopped;
static uint64_t lastTimestamp;
static struct {
    uint64_t timestamp,start,cpu,end,end_cpu;
} pending;

#ifndef THOR_WINDOW_HOST
static uint64_t cpu_clock(void)
{
    struct timespec ts;
    if(clock_gettime(CLOCK_THREAD_CPUTIME_ID,&ts)!=0 || ts.tv_sec<0 ||
       ts.tv_nsec<0 || ts.tv_nsec>=1000000000L) return UINT64_MAX;
    return (uint64_t)ts.tv_sec*1000000+(uint64_t)ts.tv_nsec/1000;
}
#else
static uint64_t cpu_clock(void);
#endif

static int64_t delta_us(uint64_t a,uint64_t b)
{
    return a==UINT64_MAX || b==UINT64_MAX || b<a || b-a>INT64_MAX ? -1:(int64_t)(b-a);
}

static void observe_window(UIWindow *window,SEL selector,UIEvent *event)
{
    WindowSend send=atomic_load_explicit(&previousWindow,memory_order_acquire);
    if(!pthread_main_np() || stopped || !event || event.type!=UIEventTypeTouches) {
        send(window,selector,event);
        return;
    }
    // Any intervening touch invalidates the previous association, including
    // a nested dispatch, a second finger or delivery to another window.
    pending.start=0;
    if(accepted>=WindowBudget) {
        send(window,selector,event);
        return;
    }
    uint64_t stamp=0;
    if(!depth) {
        NSSet *touches=event.allTouches;
        UITouch *touch=touches.count==1?touches.anyObject:nil;
        if(touch && touch.window==window && touch.phase==UITouchPhaseEnded) {
            double t=touch.timestamp;
            if(t>0 && t<315360000)stamp=(uint64_t)(t*1000000);
            if(stamp && stamp>lastTimestamp) {
                lastTimestamp=stamp;
                ++accepted;
                pending.timestamp=stamp;
                pending.start=settings_trace_uptime223();
                pending.cpu=cpu_clock();
                pending.end=0;
                pending.end_cpu=UINT64_MAX;
            } else stamp=0;
        }
    }
    ++depth;
    bool returned=false;
    @try {
        send(window,selector,event);
        returned=true;
    } @finally {
        --depth;
        if(!returned)pending.start=0;
        if(returned && stamp && pending.start && pending.timestamp==stamp) {
            pending.end_cpu=cpu_clock();
            pending.end=settings_trace_uptime223();
        }
    }
}

void match_window_cpu223(uint64_t push_us,struct window_cpu_receipt *out)
{
    if(!pthread_main_np() || !out || !pending.start) return;
    uint64_t start=pending.start;
    pending.start=0;
    if(depth>1 || push_us<start || push_us-start>MaxAgeUs) return;
    uint64_t now=cpu_clock();
    out->touch_us=pending.timestamp;
    out->window_us=start;
    out->cpu_us=delta_us(pending.cpu,now);
    bool ended=pending.end>=start && pending.end<=push_us && !depth;
    out->send_wall_us=ended?(int64_t)(pending.end-start):-1;
    out->send_cpu_us=ended?delta_us(pending.cpu,pending.end_cpu):-1;
    out->in_window=depth!=0;
}
void stop_window_cpu223(void)
{
    if(pthread_main_np()) {stopped=true;pending.start=0;}
}

bool install_window_cpu223(void)
{
    if(!pthread_main_np()) return false;
    if(atomic_load_explicit(&previousWindow,memory_order_acquire)) return true;
    Class cls=objc_lookUpClass("UIWindow");
    SEL sel=@selector(sendEvent:);
    Method method=cls?class_getInstanceMethod(cls,sel):NULL;
    if(!method || method==class_getInstanceMethod(class_getSuperclass(cls),sel) ||
       method_getNumberOfArguments(method)!=3) return false;
    char ret[8]={0},arg[8]={0};
    method_getReturnType(method,ret,sizeof ret);
    method_getArgumentType(method,2,arg,sizeof arg);
    if(ret[0]!='v' || arg[0]!='@')return false;
    // Installation and qualifying deliveries take place on the main thread.
    // The old Settings observer wraps UIApplication; this hook stays on its window.
    WindowSend previous=(WindowSend)method_getImplementation(method);
    if(!previous || (IMP)previous==(IMP)observe_window)return false;
    atomic_store_explicit(&previousWindow,previous,memory_order_release);
    (void)method_setImplementation(method,(IMP)observe_window);
    return true;
}
