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

#define TRACE_PREFIX "Pong Settings: Trace "
#define TRACE_TIMEOUT_MS 1500
#define TRACE_NAV_SPANS 32
#define TRACE_DATA_SPANS 64
#define TRACE_SPANS (TRACE_NAV_SPANS + TRACE_DATA_SPANS)

enum trace_method {
    NAV_PUSH, NAV_POP,
    ABOUT_INIT, ABOUT_SPECIFIERS, ABOUT_VALUES, ABOUT_WILL, ABOUT_DID,
    STORAGE_LOAD, STORAGE_MONITOR, STORAGE_SPECIFIERS, STORAGE_WILL, STORAGE_DID,
    STORAGE_UI_LOAD, STORAGE_UI_WILL, STORAGE_UI_DID,
    STORAGE_REMOTE, STORAGE_APPS_SYNC, STORAGE_DISK_SYNC,
    TRACE_METHODS
};
enum signature { RETURNS_OBJECT, RETURNS_VOID, VOID_BOOL, VOID_OBJECT_BOOL, OBJECT_BOOL };
enum image_index { UIKIT_IMAGE, ABOUT_IMAGE, STORAGE_IMAGE, STORAGE_UI_IMAGE, STORAGE_DATA_IMAGE, TRACE_IMAGES };

struct method_spec {
    const char *owner;
    const char *selector;
    enum signature signature;
    enum image_index image;
    bool class_method;
    bool worker;
};
static const struct method_spec methods[] = {
    {"UINavigationController", "pushViewController:animated:", VOID_OBJECT_BOOL, UIKIT_IMAGE, false, false},
    {"UINavigationController", "popViewControllerAnimated:", OBJECT_BOOL, UIKIT_IMAGE, false, false},
    {"PSGAboutDataSource", "init", RETURNS_OBJECT, ABOUT_IMAGE, false, false},
    {"PSGAboutDataSource", "loadSpecifiers", RETURNS_VOID, ABOUT_IMAGE, false, false},
    {"PSGAboutDataSource", "_loadValues", RETURNS_VOID, ABOUT_IMAGE, false, false},
    {"PSGAboutController", "viewWillAppear:", VOID_BOOL, ABOUT_IMAGE, false, false},
    {"PSGAboutController", "viewDidDisappear:", VOID_BOOL, ABOUT_IMAGE, false, false},
    {"STStorageController", "viewDidLoad", RETURNS_VOID, STORAGE_IMAGE, false, false},
    {"STStorageController", "startMonitor", RETURNS_VOID, STORAGE_IMAGE, false, false},
    {"STStorageController", "specifiers", RETURNS_OBJECT, STORAGE_IMAGE, false, false},
    {"STStorageController", "viewWillAppear:", VOID_BOOL, STORAGE_IMAGE, false, false},
    {"STStorageController", "viewDidDisappear:", VOID_BOOL, STORAGE_IMAGE, false, false},
    {"StorageSettingsUIWrapper", "viewDidLoad", RETURNS_VOID, STORAGE_UI_IMAGE, false, false},
    {"StorageSettingsUIWrapper", "viewWillAppear:", VOID_BOOL, STORAGE_UI_IMAGE, false, false},
    {"StorageSettingsUIWrapper", "viewDidDisappear:", VOID_BOOL, STORAGE_UI_IMAGE, false, false},
    {"STStorageDataInterface", "storageInfoDict_Remote", RETURNS_OBJECT, STORAGE_DATA_IMAGE, true, true},
    {"STStorageAppsMonitor", "sync", RETURNS_VOID, STORAGE_DATA_IMAGE, false, true},
    {"STStorageDiskMonitor", "sync", RETURNS_VOID, STORAGE_DATA_IMAGE, false, true}
};
_Static_assert(sizeof(methods) / sizeof(methods[0]) == TRACE_METHODS, "method table");

struct image_spec { const char *path; unsigned char uuid[16]; };
/* Exact 22H374 UUIDs and paths are filled from native metadata before build. */
static const struct image_spec images[TRACE_IMAGES] = {
    {"/System/Library/PrivateFrameworks/UIKitCore.framework/UIKitCore", {0x15,0x0b,0x91,0xd9,0xf3,0x7e,0x35,0x9f,0xad,0x9d,0x95,0x26,0xa4,0x3b,0x81,0xaa}},
    {"/System/Library/PrivateFrameworks/Settings/GeneralSettingsUI.framework/GeneralSettingsUI",
      {0x24,0x82,0x62,0xd0,0x70,0xed,0x3e,0xd5,0xbf,0x6f,0x4c,0x4c,0x8f,0x8d,0xec,0xfb}},
    {"/System/Library/PreferenceBundles/StorageSettings.bundle/StorageSettings", {0x70,0x3a,0x55,0xa7,0xc3,0xa8,0x38,0x0a,0x9c,0x03,0x1a,0x82,0x07,0x28,0xcf,0x34}},
    {"/System/Library/PreferenceBundles/StorageSettingsUI.bundle/StorageSettingsUI", {0x49,0xc0,0x99,0x9d,0x07,0xe6,0x30,0x4b,0x84,0xf1,0x0a,0xf3,0xc2,0x8e,0x70,0xc1}},
    {"/System/Library/PrivateFrameworks/StorageData.framework/StorageData", {0x62,0x45,0xe8,0xce,0xa7,0x53,0x32,0x77,0xac,0xf8,0xeb,0x78,0xd4,0x8c,0xf2,0x10}}
};

struct trace_span {
    unsigned sequence;
    enum trace_method method;
    uintptr_t object;
    const char *object_class;
    const char *target_class;
    uint64_t thread_id;
    uint64_t started_ms;
    bool main;
    bool owns_thread;
    thread_t source_thread;
    _Atomic(bool) completed;
    _Atomic(bool) main_ack;
    _Atomic(uint64_t) finished_ms;
};

static thread_t main_thread;
static dispatch_queue_t trace_queue;
static struct trace_span spans[TRACE_SPANS];
static _Atomic(unsigned) nav_used, data_used;
static _Atomic(IMP) originals[TRACE_METHODS];
static _Atomic(bool) qualified[TRACE_IMAGES];
static atomic_flag installing = ATOMIC_FLAG_INIT;

static uint64_t monotonic_ms(void)
{
    struct timespec now = {0};
    (void)clock_gettime(CLOCK_MONOTONIC, &now);
    return (uint64_t)now.tv_sec * 1000 + (uint64_t)now.tv_nsec / 1000000;
}

/* Callers run only on the diagnostic queue. */
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
    const struct trace_span *span = context;
    char line[256];
    int n = snprintf(line, sizeof(line), TRACE_PREFIX
        "begin id=%u op=%s cls=%.40s target=%.40s object=%" PRIxPTR
        " tid=%" PRIu64 " main=%d t_ms=%" PRIu64,
        span->sequence, methods[span->method].selector, span->object_class,
        span->target_class, span->object, span->thread_id, span->main, span->started_ms);
    report(line, n);
}

static void end_report(void *context)
{
    const struct trace_span *span = context;
    uint64_t ended = atomic_load_explicit(&span->finished_ms, memory_order_acquire);
    char line[256];
    int n = snprintf(line, sizeof(line), TRACE_PREFIX
        "end id=%u op=%s t_ms=%" PRIu64 " duration_ms=%" PRIu64,
        span->sequence, methods[span->method].selector, ended, ended - span->started_ms);
    report(line, n);
}

static bool span_pending(const struct trace_span *span)
{
    return span->main ? !atomic_load_explicit(&span->main_ack, memory_order_acquire) :
                       !atomic_load_explicit(&span->completed, memory_order_acquire);
}

static void report_thread_stack(const struct trace_span *span, thread_t thread, const char *scope)
{
    arm_thread_state64_t state = {0};
    mach_msg_type_number_t count = ARM_THREAD_STATE64_COUNT;
    kern_return_t result = thread_get_state(thread, ARM_THREAD_STATE64, (thread_state_t)&state, &count);
    thread_basic_info_data_t info = {0};
    count = THREAD_BASIC_INFO_COUNT;
    (void)thread_info(thread, THREAD_BASIC_INFO, (thread_info_t)&info, &count);
    if (!span_pending(span))
        return;

    uint64_t pc = arm_thread_state64_get_pc(state);
    uint64_t lr = arm_thread_state64_get_lr(state);
    uint64_t fp = arm_thread_state64_get_fp(state);
    uint64_t sp = arm_thread_state64_get_sp(state);
    char line[256];
    int n = snprintf(line, sizeof(line), TRACE_PREFIX
        "timeout id=%u op=%s scope=%s phase=%s elapsed_ms=%" PRIu64
        " kr=%d run=%d pc=%" PRIx64 " lr=%" PRIx64 " fp=%" PRIx64 " sp=%" PRIx64,
        span->sequence, methods[span->method].selector, scope,
        atomic_load_explicit(&span->completed, memory_order_acquire) ? "main-queue" : "native",
        monotonic_ms() - span->started_ms, result, info.run_state, pc, lr, fp, sp);
    report(line, n);

    uint64_t frames[18], cursor = fp;
    unsigned used = 0;
    while (result == KERN_SUCCESS && used < 18 && sp && cursor >= sp &&
           cursor - sp < 8 * 1024 * 1024 && !(cursor & 15)) {
        uint64_t pair[2];
        vm_size_t size = 0;
        if (vm_read_overwrite(mach_task_self(), (vm_address_t)cursor, sizeof(pair),
                              (vm_address_t)(uintptr_t)pair, &size) != KERN_SUCCESS || size != sizeof(pair))
            break;
        frames[used++] = pair[1] & UINT64_C(0x0000ffffffffffff);
        if (pair[0] <= cursor)
            break;
        cursor = pair[0];
    }
    for (unsigned offset = 0; offset < used; offset += 6) {
        n = snprintf(line, sizeof(line), TRACE_PREFIX "stack id=%u scope=%s offset=%u",
                     span->sequence, scope, offset);
        for (unsigned i = offset; i < used && i - offset < 6 && n > 0; ++i)
            n += snprintf(line + n, sizeof(line) - (size_t)n, " %" PRIx64, frames[i]);
        report(line, n);
    }
}

static void capture_timeout(void *context)
{
    struct trace_span *span = context;
    if (span_pending(span)) {
        report_thread_stack(span, main_thread, "main");
        if (!span->main && MACH_PORT_VALID(span->source_thread))
            report_thread_stack(span, span->source_thread, "request");
    }
    if (span->owns_thread) {
        (void)mach_port_deallocate(mach_task_self(), span->source_thread);
        span->owns_thread = false;
    }
}

static unsigned reserve_slot(_Atomic(unsigned) *counter, unsigned limit)
{
    unsigned current = atomic_load_explicit(counter, memory_order_relaxed);
    while (current < limit) {
        if (atomic_compare_exchange_weak_explicit(counter, &current, current + 1,
                                                  memory_order_relaxed, memory_order_relaxed))
            return current;
    }
    return limit;
}

static struct trace_span *begin_span(id object, enum trace_method method, id target)
{
    bool on_main = pthread_main_np();
    if (!on_main && !methods[method].worker)
        return NULL;
    bool navigation = method <= NAV_POP;
    unsigned limit = navigation ? TRACE_NAV_SPANS : TRACE_DATA_SPANS;
    unsigned slot = reserve_slot(navigation ? &nav_used : &data_used, limit);
    if (slot == limit)
        return NULL;
    unsigned index = navigation ? slot : TRACE_NAV_SPANS + slot;
    struct trace_span *span = &spans[index];
    span->sequence = index + 1;
    span->method = method;
    span->object = (uintptr_t)object;
    span->object_class = object ? class_getName(object_getClass(object)) : "nil";
    span->target_class = target ? class_getName(object_getClass(target)) : "-";
    (void)pthread_threadid_np(NULL, &span->thread_id);
    span->started_ms = monotonic_ms();
    span->main = on_main;
    span->source_thread = on_main ? main_thread : mach_thread_self();
    span->owns_thread = !on_main && MACH_PORT_VALID(span->source_thread);
    dispatch_async_f(trace_queue, span, begin_report);
    dispatch_after_f(dispatch_time(DISPATCH_TIME_NOW, TRACE_TIMEOUT_MS * NSEC_PER_MSEC),
                     trace_queue, span, capture_timeout);
    return span;
}

static void main_queue_ack(void *context)
{
    struct trace_span *span = context;
    atomic_store_explicit(&span->main_ack, true, memory_order_release);
}

static void end_span(struct trace_span *span)
{
    if (!span)
        return;
    atomic_store_explicit(&span->finished_ms, monotonic_ms(), memory_order_release);
    atomic_store_explicit(&span->completed, true, memory_order_release);
    dispatch_async_f(trace_queue, span, end_report);
    if (span->main)
        dispatch_async_f(dispatch_get_main_queue(), span, main_queue_ack);
}

#define OBJECT_OBSERVER(NAME, INDEX) \
static id NAME(id self, SEL selector) { \
    struct trace_span *span = begin_span(self, INDEX, nil); \
    IMP original = atomic_load_explicit(&originals[INDEX], memory_order_acquire); \
    id result = ((id (*)(id, SEL))original)(self, selector); \
    end_span(span); return result; \
}
#define VOID_OBSERVER(NAME, INDEX) \
static void NAME(id self, SEL selector) { \
    struct trace_span *span = begin_span(self, INDEX, nil); \
    IMP original = atomic_load_explicit(&originals[INDEX], memory_order_acquire); \
    ((void (*)(id, SEL))original)(self, selector); end_span(span); \
}
#define BOOL_OBSERVER(NAME, INDEX) \
static void NAME(id self, SEL selector, BOOL animated) { \
    struct trace_span *span = begin_span(self, INDEX, nil); \
    IMP original = atomic_load_explicit(&originals[INDEX], memory_order_acquire); \
    ((void (*)(id, SEL, BOOL))original)(self, selector, animated); end_span(span); \
}

static void observe_push(id self, SEL selector, id controller, BOOL animated)
{
    struct trace_span *span = begin_span(self, NAV_PUSH, controller);
    IMP original = atomic_load_explicit(&originals[NAV_PUSH], memory_order_acquire);
    ((void (*)(id, SEL, id, BOOL))original)(self, selector, controller, animated);
    end_span(span);
}
static id observe_pop(id self, SEL selector, BOOL animated)
{
    struct trace_span *span = begin_span(self, NAV_POP, nil);
    IMP original = atomic_load_explicit(&originals[NAV_POP], memory_order_acquire);
    id result = ((id (*)(id, SEL, BOOL))original)(self, selector, animated);
    end_span(span);
    return result;
}
OBJECT_OBSERVER(observe_about_init, ABOUT_INIT)
VOID_OBSERVER(observe_about_specifiers, ABOUT_SPECIFIERS)
VOID_OBSERVER(observe_about_values, ABOUT_VALUES)
BOOL_OBSERVER(observe_about_will, ABOUT_WILL)
BOOL_OBSERVER(observe_about_did, ABOUT_DID)
VOID_OBSERVER(observe_storage_load, STORAGE_LOAD)
VOID_OBSERVER(observe_storage_monitor, STORAGE_MONITOR)
OBJECT_OBSERVER(observe_storage_specifiers, STORAGE_SPECIFIERS)
BOOL_OBSERVER(observe_storage_will, STORAGE_WILL)
BOOL_OBSERVER(observe_storage_did, STORAGE_DID)
VOID_OBSERVER(observe_storage_ui_load, STORAGE_UI_LOAD)
BOOL_OBSERVER(observe_storage_ui_will, STORAGE_UI_WILL)
BOOL_OBSERVER(observe_storage_ui_did, STORAGE_UI_DID)
OBJECT_OBSERVER(observe_storage_remote, STORAGE_REMOTE)
VOID_OBSERVER(observe_storage_apps_sync, STORAGE_APPS_SYNC)
VOID_OBSERVER(observe_storage_disk_sync, STORAGE_DISK_SYNC)

static const IMP observers[] = {
    (IMP)observe_push, (IMP)observe_pop, (IMP)observe_about_init, (IMP)observe_about_specifiers,
    (IMP)observe_about_values, (IMP)observe_about_will, (IMP)observe_about_did,
    (IMP)observe_storage_load, (IMP)observe_storage_monitor, (IMP)observe_storage_specifiers,
    (IMP)observe_storage_will, (IMP)observe_storage_did, (IMP)observe_storage_ui_load,
    (IMP)observe_storage_ui_will, (IMP)observe_storage_ui_did, (IMP)observe_storage_remote,
    (IMP)observe_storage_apps_sync, (IMP)observe_storage_disk_sync
};

static bool verify_abi(Method method, enum signature signature)
{
    unsigned parameters = signature == VOID_OBJECT_BOOL ? 4 :
        (signature == VOID_BOOL || signature == OBJECT_BOOL ? 3 : 2);
    if (!method || method_getNumberOfArguments(method) != parameters)
        return false;
    char encoding[32] = {0};
    method_getReturnType(method, encoding, sizeof(encoding));
    char result = signature == RETURNS_OBJECT || signature == OBJECT_BOOL ? '@' : 'v';
    if (encoding[0] != result)
        return false;
    if (signature == VOID_OBJECT_BOOL) {
        method_getArgumentType(method, 2, encoding, sizeof(encoding));
        if (encoding[0] != '@')
            return false;
    }
    if (parameters >= 3) {
        method_getArgumentType(method, parameters - 1, encoding, sizeof(encoding));
        if (encoding[0] != 'B' && encoding[0] != 'c')
            return false;
    }
    return true;
}

static void installed_report(void *context)
{
    uintptr_t mask = (uintptr_t)context;
    char line[256];
    int n = snprintf(line, sizeof(line), TRACE_PREFIX
        "armed added=0x%" PRIxPTR " timeout_ms=%d nav_limit=%d data_limit=%d",
        mask, TRACE_TIMEOUT_MS, TRACE_NAV_SPANS, TRACE_DATA_SPANS);
    report(line, n);
}

static void install_methods(void *unused)
{
    (void)unused;
    if (atomic_flag_test_and_set_explicit(&installing, memory_order_acquire))
        return;
    uintptr_t added = 0;
    for (unsigned i = 0; i < TRACE_METHODS; ++i) {
        const struct method_spec *spec = &methods[i];
        if (atomic_load_explicit(&originals[i], memory_order_acquire) ||
            !atomic_load_explicit(&qualified[spec->image], memory_order_acquire))
            continue;
        Class cls = objc_lookUpClass(spec->owner);
        const char *path = cls ? class_getImageName(cls) : NULL;
        if (!path || strcmp(path, images[spec->image].path))
            continue;
        Class owner = spec->class_method ? object_getClass((id)cls) : cls;
        Method method = class_getInstanceMethod(owner, sel_registerName(spec->selector));
        if (method && method == class_getInstanceMethod(class_getSuperclass(owner), sel_registerName(spec->selector)))
            continue;
        if (!verify_abi(method, spec->signature))
            continue;
        atomic_store_explicit(&originals[i], method_getImplementation(method), memory_order_release);
        method_setImplementation(method, observers[i]);
        added |= (uintptr_t)1 << i;
    }
    atomic_flag_clear_explicit(&installing, memory_order_release);
    if (added)
        dispatch_async_f(trace_queue, (void *)added, installed_report);
}

static void image_added(const struct mach_header *header, intptr_t slide)
{
    (void)slide;
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
            for (unsigned j = 0; j < TRACE_IMAGES; ++j) {
                if (memcmp(identity->uuid, images[j].uuid, sizeof(identity->uuid)))
                    continue;
                atomic_store_explicit(&qualified[j], true, memory_order_release);
                install_methods(NULL);
                dispatch_async_f(dispatch_get_main_queue(), NULL, install_methods);
            }
            return;
        }
        remaining -= command->cmdsize;
        cursor += command->cmdsize;
    }
}

static void started_report(void *unused)
{
    (void)unused;
    char line[256];
    int n = snprintf(line, sizeof(line), TRACE_PREFIX
        "loaded v180 timeout_ms=%d nav_limit=%d data_limit=%d",
        TRACE_TIMEOUT_MS, TRACE_NAV_SPANS, TRACE_DATA_SPANS);
    report(line, n);
}

__attribute__((constructor))
static void start_trace(void)
{
    const char *process = getprogname();
    if (!process || strcmp(process, "Preferences") || !pthread_main_np())
        return;
    main_thread = mach_thread_self();
    if (!MACH_PORT_VALID(main_thread))
        return;
    dispatch_queue_attr_t attr =
        dispatch_queue_attr_make_with_qos_class(DISPATCH_QUEUE_SERIAL, QOS_CLASS_USER_INITIATED, 0);
    trace_queue = dispatch_queue_create("org.unixnu.pong.settings-trace", attr);
    if (!trace_queue) {
        (void)mach_port_deallocate(mach_task_self(), main_thread);
        return;
    }
    dispatch_async_f(trace_queue, NULL, started_report);
    _dyld_register_func_for_add_image(image_added);
}
