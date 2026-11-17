/* SPDX-License-Identifier: MIT */
#include <dispatch/dispatch.h>
#include <inttypes.h>
#include <mach/arm/thread_status.h>
#include <mach/mach.h>
#include <mach-o/dyld.h>
#include <mach-o/loader.h>
#include <objc/runtime.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/sysctl.h>
#include <time.h>
#include <unistd.h>

#define ABOUT_PREFIX "Pong Settings: About "
#define ABOUT_TIMEOUT_MS 1500
#define ABOUT_MAX_SPANS 48

enum about_method {
    ABOUT_INIT,
    ABOUT_SPECIFIERS,
    ABOUT_VALUES,
    ABOUT_WILL_APPEAR,
    ABOUT_DID_DISAPPEAR,
    ABOUT_METHOD_COUNT
};

static const char *const method_names[] = {
    "init",
    "loadSpecifiers",
    "_loadValues",
    "viewWillAppear:",
    "viewDidDisappear:"
};

struct about_span {
    unsigned sequence;
    enum about_method method;
    uintptr_t object;
    uint64_t started_ms;
    _Atomic(uint64_t) finished_ms;
    _Atomic(uint64_t) main_ack_ms;
};

static thread_t preferences_main_thread;
static dispatch_queue_t about_queue;
static struct about_span spans[ABOUT_MAX_SPANS];
static unsigned spans_used; /* Main thread owns this counter. */
static _Atomic(IMP) originals[ABOUT_METHOD_COUNT];
static atomic_flag installing = ATOMIC_FLAG_INIT;
static _Atomic(const struct mach_header *) qualified_image;

static uint64_t monotonic_ms(void)
{
    struct timespec now = {0};
    (void)clock_gettime(CLOCK_MONOTONIC, &now);
    return (uint64_t)now.tv_sec * 1000 + (uint64_t)now.tv_nsec / 1000000;
}

/* Only diagnostics-queue callers can reach the kernel or stderr. */
static void report(char *line, int length)
{
    if (length <= 0 || length >= 255)
        return;

    int mib[CTL_MAXNAME];
    size_t count = CTL_MAXNAME;
    if (sysctlnametomib("kern.pong_diagnostic", mib, &count) == 0 &&
        sysctl(mib, (u_int)count, NULL, NULL, line, (size_t)length) == 0)
        return;

    line[length] = '\n';
    (void)write(STDERR_FILENO, line, (size_t)length + 1);
}

static void begin_report(void *context)
{
    const struct about_span *span = context;
    char line[256];
    int length = snprintf(line, sizeof(line), ABOUT_PREFIX
        "begin id=%u op=%s object=%" PRIxPTR " t_ms=%" PRIu64,
        span->sequence, method_names[span->method], span->object, span->started_ms);
    report(line, length);
}

static void end_report(void *context)
{
    const struct about_span *span = context;
    uint64_t ended_ms = atomic_load_explicit(&span->finished_ms, memory_order_acquire);
    char line[256];
    int length = snprintf(line, sizeof(line), ABOUT_PREFIX
        "end id=%u op=%s t_ms=%" PRIu64 " duration_ms=%" PRIu64,
        span->sequence, method_names[span->method], ended_ms, ended_ms - span->started_ms);
    report(line, length);
}

static void capture_stack(void *context)
{
    const struct about_span *span = context;
    if (atomic_load_explicit(&span->main_ack_ms, memory_order_acquire))
        return;

    uint64_t elapsed_ms = monotonic_ms() - span->started_ms;
    uint64_t end = atomic_load_explicit(&span->finished_ms, memory_order_acquire);
    arm_thread_state64_t state = {0};
    mach_msg_type_number_t words = ARM_THREAD_STATE64_COUNT;
    kern_return_t result = thread_get_state(preferences_main_thread, ARM_THREAD_STATE64,
                                           (thread_state_t)&state, &words);
    thread_basic_info_data_t info = {0};
    words = THREAD_BASIC_INFO_COUNT;
    (void)thread_info(preferences_main_thread, THREAD_BASIC_INFO, (thread_info_t)&info, &words);
    if (atomic_load_explicit(&span->main_ack_ms, memory_order_acquire))
        return;

    char line[256];
    uint64_t pc = arm_thread_state64_get_pc(state);
    uint64_t lr = arm_thread_state64_get_lr(state);
    uint64_t fp = arm_thread_state64_get_fp(state);
    uint64_t sp = arm_thread_state64_get_sp(state);
    int length = snprintf(line, sizeof(line), ABOUT_PREFIX
        "timeout id=%u op=%s phase=%s elapsed_ms=%" PRIu64
        " kr=%d run=%d pc=%" PRIx64 " lr=%" PRIx64 " fp=%" PRIx64 " sp=%" PRIx64,
        span->sequence, method_names[span->method], end ? "main-queue" : "native",
        elapsed_ms, result, info.run_state, pc, lr, fp, sp);
    report(line, length);

    uint64_t cursor = fp;
    uint64_t frames[18];
    unsigned used = 0;
    while (result == KERN_SUCCESS && used < sizeof(frames) / sizeof(frames[0]) &&
           sp && cursor >= sp && cursor - sp < 8 * 1024 * 1024 && !(cursor & 15)) {
        uint64_t pair[2];
        vm_size_t size = 0;
        if (vm_read_overwrite(mach_task_self(), (vm_address_t)cursor, sizeof(pair),
                              (vm_address_t)(uintptr_t)pair, &size) != KERN_SUCCESS ||
            size != sizeof(pair))
            break;

        /* The arm64 diagnostic also samples arm64e guest frame records. */
        frames[used++] = pair[1] & UINT64_C(0x0000ffffffffffff);
        if (pair[0] <= cursor)
            break;
        cursor = pair[0];
    }
    for (unsigned offset = 0; offset < used; offset += 6) {
        length = snprintf(line, sizeof(line), ABOUT_PREFIX
            "stack id=%u offset=%u", span->sequence, offset);
        for (unsigned i = offset; i < used && i - offset < 6 && length > 0; ++i)
            length += snprintf(line + length, sizeof(line) - (size_t)length, " %" PRIx64, frames[i]);
        report(line, length);
    }
}

static struct about_span *begin_span(id self, enum about_method method)
{
    if (!pthread_main_np() || spans_used == ABOUT_MAX_SPANS)
        return NULL;

    struct about_span *span = &spans[spans_used++];
    span->sequence = spans_used;
    span->method = method;
    span->object = (uintptr_t)self;
    span->started_ms = monotonic_ms();
    dispatch_async_f(about_queue, span, begin_report);
    dispatch_after_f(dispatch_time(DISPATCH_TIME_NOW, ABOUT_TIMEOUT_MS * NSEC_PER_MSEC),
                     about_queue, span, capture_stack);
    return span;
}

static void main_queue_ack(void *context)
{
    struct about_span *span = context;
    atomic_store_explicit(&span->main_ack_ms, monotonic_ms(), memory_order_release);
}

static void end_span(struct about_span *span)
{
    if (!span)
        return;

    atomic_store_explicit(&span->finished_ms, monotonic_ms(), memory_order_release);
    dispatch_async_f(about_queue, span, end_report);
    dispatch_async_f(dispatch_get_main_queue(), span, main_queue_ack);
}

static id observe_init(id self, SEL selector)
{
    struct about_span *span = begin_span(self, ABOUT_INIT);
    IMP implementation = atomic_load_explicit(&originals[ABOUT_INIT], memory_order_acquire);
    id result = ((id (*)(id, SEL))implementation)(self, selector);
    end_span(span);
    return result;
}

static void observe_specifiers(id self, SEL selector)
{
    struct about_span *span = begin_span(self, ABOUT_SPECIFIERS);
    IMP implementation = atomic_load_explicit(&originals[ABOUT_SPECIFIERS], memory_order_acquire);
    ((void (*)(id, SEL))implementation)(self, selector);
    end_span(span);
}

static void observe_values(id self, SEL selector)
{
    struct about_span *span = begin_span(self, ABOUT_VALUES);
    IMP implementation = atomic_load_explicit(&originals[ABOUT_VALUES], memory_order_acquire);
    ((void (*)(id, SEL))implementation)(self, selector);
    end_span(span);
}

static void observe_will_appear(id self, SEL selector, BOOL animated)
{
    struct about_span *span = begin_span(self, ABOUT_WILL_APPEAR);
    IMP implementation = atomic_load_explicit(&originals[ABOUT_WILL_APPEAR], memory_order_acquire);
    ((void (*)(id, SEL, BOOL))implementation)(self, selector, animated);
    end_span(span);
}

static void observe_did_disappear(id self, SEL selector, BOOL animated)
{
    struct about_span *span = begin_span(self, ABOUT_DID_DISAPPEAR);
    IMP implementation = atomic_load_explicit(&originals[ABOUT_DID_DISAPPEAR], memory_order_acquire);
    ((void (*)(id, SEL, BOOL))implementation)(self, selector, animated);
    end_span(span);
}

static bool verify_abi(Method method, char result_type, bool animated)
{
    if (!method || method_getNumberOfArguments(method) != (animated ? 3u : 2u))
        return false;

    char encoding[16] = {0};
    method_getReturnType(method, encoding, sizeof(encoding));
    if (encoding[0] != result_type)
        return false;

    if (animated) {
        method_getArgumentType(method, 2, encoding, sizeof(encoding));
        if (encoding[0] != 'B' && encoding[0] != 'c')
            return false;
    }
    return true;
}

static void installed_report(void *context)
{
    uintptr_t enabled = (uintptr_t)context;
    char line[256];
    int length = snprintf(line, sizeof(line), ABOUT_PREFIX
        "armed methods=0x%" PRIxPTR " timeout_ms=%d span_limit=%d",
        enabled, ABOUT_TIMEOUT_MS, ABOUT_MAX_SPANS);
    report(line, length);
}

static void install_methods(void *unused)
{
    (void)unused;
    if (!atomic_load_explicit(&qualified_image, memory_order_acquire) ||
        atomic_flag_test_and_set_explicit(&installing, memory_order_acquire))
        return;

    Class source = objc_lookUpClass("PSGAboutDataSource");
    Class controller = objc_lookUpClass("PSGAboutController");
    const char *image = "/System/Library/PrivateFrameworks/Settings/GeneralSettingsUI.framework/GeneralSettingsUI";
    const char *source_image = source ? class_getImageName(source) : NULL;
    const char *controller_image = controller ? class_getImageName(controller) : NULL;
    if (!source_image || !controller_image ||
        strcmp(source_image, image) || strcmp(controller_image, image)) {
        atomic_flag_clear_explicit(&installing, memory_order_release);
        return;
    }

    static const IMP observers[] = {
        (IMP)observe_init, (IMP)observe_specifiers, (IMP)observe_values,
        (IMP)observe_will_appear, (IMP)observe_did_disappear
    };
    uintptr_t enabled = 0;
    for (unsigned i = 0; i < ABOUT_METHOD_COUNT; ++i) {
        SEL selector = sel_registerName(method_names[i]);
        Class owner = i >= ABOUT_WILL_APPEAR ? controller : source;
        Method method = class_getInstanceMethod(owner, selector);
        if (method && method == class_getInstanceMethod(class_getSuperclass(owner), selector))
            continue;
        if (!verify_abi(method, i == ABOUT_INIT ? '@' : 'v', i >= ABOUT_WILL_APPEAR))
            continue;
        atomic_store_explicit(&originals[i], method_getImplementation(method), memory_order_release);
        method_setImplementation(method, observers[i]);
        enabled |= (uintptr_t)1 << i;
    }

    /* Leave the flag set once the class is available, including ABI refusal. */
    dispatch_async_f(about_queue, (void *)enabled, installed_report);
}

static void image_added(const struct mach_header *header, intptr_t slide)
{
    (void)slide;
    static const unsigned char uuid[16] = {
        0x24, 0x82, 0x62, 0xd0, 0x70, 0xed, 0x3e, 0xd5,
        0xbf, 0x6f, 0x4c, 0x4c, 0x8f, 0x8d, 0xec, 0xfb
    };
    if (!header || header->magic != MH_MAGIC_64)
        return;

    const struct mach_header_64 *image = (const struct mach_header_64 *)header;
    const unsigned char *cursor = (const unsigned char *)(image + 1);
    size_t remaining = image->sizeofcmds;
    for (unsigned i = 0; i < image->ncmds && remaining >= sizeof(struct load_command); ++i) {
        const struct load_command *command = (const struct load_command *)cursor;
        if (command->cmdsize < sizeof(*command) || command->cmdsize > remaining)
            return;
        if (command->cmd == LC_UUID && command->cmdsize == sizeof(struct uuid_command)) {
            const struct uuid_command *identity = (const struct uuid_command *)cursor;
            if (!memcmp(identity->uuid, uuid, sizeof(uuid))) {
                atomic_store_explicit(&qualified_image, header, memory_order_release);
                install_methods(NULL);
                /* Also try after dyld has published the Objective-C class. */
                dispatch_async_f(dispatch_get_main_queue(), NULL, install_methods);
            }
            return;
        }
        remaining -= command->cmdsize;
        cursor += command->cmdsize;
    }
}

__attribute__((constructor))
static void start_about_trace(void)
{
    const char *process = getprogname();
    if (!process || strcmp(process, "Preferences") || !pthread_main_np())
        return;

    preferences_main_thread = mach_thread_self();
    if (!MACH_PORT_VALID(preferences_main_thread))
        return;
    dispatch_queue_attr_t qos =
        dispatch_queue_attr_make_with_qos_class(DISPATCH_QUEUE_SERIAL, QOS_CLASS_USER_INITIATED, 0);
    about_queue = dispatch_queue_create("org.unixnu.pong.about-trace", qos);
    if (!about_queue) {
        (void)mach_port_deallocate(mach_task_self(), preferences_main_thread);
        return;
    }
    _dyld_register_func_for_add_image(image_added);
}
