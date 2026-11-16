#include <dispatch/dispatch.h>
#include <mach/mach_time.h>
#include <objc/message.h>
#include <objc/runtime.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/sysctl.h>

enum { ReceiptLimit = 32 };
static const uint64_t MaxAssociationUs = 500000;

struct NavigationReceipt {
    uint64_t received_us;
    uint64_t ui_touch_us;
    uint64_t push_us;
    unsigned touch_sequence;
    bool during_send_event;
    char controller[56];
};

static mach_timebase_info_data_t timebase;
static dispatch_queue_t receipt_queue;
static struct NavigationReceipt receipts[ReceiptLimit];
static unsigned receipt_count;
static unsigned touch_sequence;
static unsigned event_depth;
static uint64_t last_touch_us;
static uint64_t last_touch_timestamp_us;

static SEL type_sel, touches_sel, count_sel, any_sel, phase_sel, timestamp_sel;
static void (*native_send_event)(id, SEL, id);
static void (*native_push)(id, SEL, id, BOOL);

static uint64_t
current_time_us(void)
{
    const __uint128_t nanos = (__uint128_t)mach_absolute_time() * timebase.numer / timebase.denom;
    return (uint64_t)(nanos / 1000);
}

static void
emit_receipt(void *context)
{
    const struct NavigationReceipt *record = context;
    char line[240];
    int length = snprintf(line, sizeof(line),
        "Pong Settings: nav touch=%u recv_us=%llu ui_touch_us=%llu push_us=%llu "
        "recv_to_push_us=%llu in_send=%u target=%s",
        record->touch_sequence, (unsigned long long)record->received_us,
        (unsigned long long)record->ui_touch_us, (unsigned long long)record->push_us,
        (unsigned long long)(record->push_us - record->received_us),
        (unsigned)record->during_send_event, record->controller);
    if (length > 0 && (size_t)length < sizeof(line))
        (void)sysctlbyname("kern.pong_diagnostic", NULL, NULL, line, (size_t)length);
}

static void
observe_terminal_touch(id event)
{
    id touches = ((id (*)(id, SEL))objc_msgSend)(event, touches_sel);
    if (!touches || ((unsigned long (*)(id, SEL))objc_msgSend)(touches, count_sel) != 1)
        return;
    id touch = ((id (*)(id, SEL))objc_msgSend)(touches, any_sel);
    // UIEventTypeTouches is 0; UITouchPhaseEnded is 3.
    if (!touch || ((long (*)(id, SEL))objc_msgSend)(touch, phase_sel) != 3)
        return;
    const double timestamp = ((double (*)(id, SEL))objc_msgSend)(touch, timestamp_sel);
    last_touch_timestamp_us = timestamp > 0 && timestamp < 315360000 ?
        (uint64_t)(timestamp * 1000000) : 0;
    last_touch_us = current_time_us();
    ++touch_sequence;
}

static void
observe_send_event(id self, SEL selector, id event)
{
    if (!pthread_main_np() || receipt_count == ReceiptLimit) {
        native_send_event(self, selector, event);
        return;
    }
    if (event && ((long (*)(id, SEL))objc_msgSend)(event, type_sel) == 0)
        observe_terminal_touch(event);
    ++event_depth;
    native_send_event(self, selector, event);
    --event_depth;
}

static void
observe_push(id self, SEL selector, id controller, BOOL animated)
{
    struct NavigationReceipt *record = NULL;
    if (pthread_main_np() && receipt_count < ReceiptLimit && last_touch_us) {
        uint64_t now = current_time_us();
        if (now >= last_touch_us && now - last_touch_us <= MaxAssociationUs) {
            record = &receipts[receipt_count++];
            record->received_us = last_touch_us;
            record->ui_touch_us = last_touch_timestamp_us;
            record->push_us = now;
            record->touch_sequence = touch_sequence;
            record->during_send_event = event_depth != 0;
            snprintf(record->controller, sizeof(record->controller), "%s",
                controller ? object_getClassName(controller) : "-");
            // Keep the first matching push for a touch, including nested calls.
            last_touch_us = 0;
        }
    }
    native_push(self, selector, controller, animated);
    if (record)
        dispatch_async_f(receipt_queue, record, emit_receipt);
}

static bool
check_method(Method method, const char *result, const char *first, const char *second)
{
    unsigned count = (first != NULL) + (second != NULL);
    if (!method || method_getNumberOfArguments(method) != count + 2)
        return false;
    char *type = method_copyReturnType(method);
    bool ok = type && type[0] && strchr(result, type[0]);
    free(type);
    for (unsigned index = 0; index < count && ok; ++index) {
        type = method_copyArgumentType(method, index + 2);
        ok = type && type[0] && strchr(index == 0 ? first : second, type[0]);
        free(type);
    }
    return ok;
}

static void
emit_status(void *context)
{
    const char *line = context;
    (void)sysctlbyname("kern.pong_diagnostic", NULL, NULL, (void *)line, strlen(line));
}

static void
install_navigation_timing(void *context)
{
    (void)context;
    receipt_queue = dispatch_get_global_queue(QOS_CLASS_UTILITY, 0);
    Class app = objc_lookUpClass("UIApplication");
    Class navigation = objc_lookUpClass("UINavigationController");
    Class touch = objc_lookUpClass("UITouch");
    Class event = objc_lookUpClass("UIEvent");
    SEL send_sel = sel_registerName("sendEvent:");
    SEL push_sel = sel_registerName("pushViewController:animated:");
    type_sel = sel_registerName("type");
    touches_sel = sel_registerName("allTouches");
    count_sel = sel_registerName("count");
    any_sel = sel_registerName("anyObject");
    phase_sel = sel_registerName("phase");
    timestamp_sel = sel_registerName("timestamp");
    Method send = app ? class_getInstanceMethod(app, send_sel) : NULL;
    Method push = navigation ? class_getInstanceMethod(navigation, push_sel) : NULL;
    if (mach_timebase_info(&timebase) != KERN_SUCCESS || timebase.denom == 0 ||
        !check_method(send, "v", "@", NULL) || !check_method(push, "v", "@", "Bc") ||
        !event || !touch ||
        !check_method(class_getInstanceMethod(event, type_sel), "q", NULL, NULL) ||
        !check_method(class_getInstanceMethod(event, touches_sel), "@", NULL, NULL) ||
        !check_method(class_getInstanceMethod(touch, phase_sel), "q", NULL, NULL) ||
        !check_method(class_getInstanceMethod(touch, timestamp_sel), "d", NULL, NULL)) {
        dispatch_async_f(receipt_queue, "Pong Settings: nav timing unavailable", emit_status);
        return;
    }
    native_send_event = (void (*)(id, SEL, id))method_setImplementation(send, (IMP)observe_send_event);
    native_push = (void (*)(id, SEL, id, BOOL))method_setImplementation(push, (IMP)observe_push);
    dispatch_async_f(receipt_queue, "Pong Settings: nav timing armed", emit_status);
}

__attribute__((visibility("hidden"))) void
thor_settings_navigation_timing_start(void)
{
    dispatch_async_f(dispatch_get_main_queue(), NULL, install_navigation_timing);
}
