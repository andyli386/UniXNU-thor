/* SPDX-License-Identifier: MIT */
/* 22H374 FrontBoard watchdog budget for the Settings bring-up path. */
#include <CoreFoundation/CoreFoundation.h>
#include <dlfcn.h>
#include <mach-o/dyld.h>
#include <mach-o/loader.h>
#include <objc/message.h>
#include <objc/runtime.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/sysctl.h>

enum watchdog_event {
    PROCESS_LAUNCH = 1,
    SCENE_CREATE = 2,
    SCENE_UPDATE = 3,
};

/* The native factory, getter and setter use this AArch64 aggregate ABI. */
struct resource_allowance {
    int64_t type;
    uint64_t value;
    uint64_t reserved;
};
_Static_assert(sizeof(struct resource_allowance) == 24, "22H374 allowance ABI");
typedef struct resource_allowance (*make_realtime_allowance)(double);
typedef id (*policy_method)(id, SEL, id, id);
typedef void (*watchdog_note)(const char *, ...);

static IMP launch_original;
static IMP scene_original;
static watchdog_note note;
static unsigned receipts;
static unsigned installed;

static bool type_matches(const char *actual, const char *expected)
{
    if (!actual) return false;
    if (!strcmp(expected, "@")) return actual[0] == '@' && actual[1] != '?';
    if (!strcmp(expected, "qQ")) return !strcmp(actual, "q") || !strcmp(actual, "Q");
    return !strcmp(actual, expected);
}

static bool method_matches(Class cls, const char *selector, const char *result,
                           unsigned count, const char *const *parameters)
{
    Method method = cls ? class_getInstanceMethod(cls, sel_registerName(selector)) : NULL;
    if (!method || method_getNumberOfArguments(method) != count + 2) return false;
    char *type = method_copyReturnType(method);
    bool matches = type_matches(type, result);
    free(type);
    for (unsigned i = 0; matches && i < count; i++) {
        type = method_copyArgumentType(method, i + 2);
        matches = type_matches(type, parameters[i]);
        free(type);
    }
    return matches;
}

static bool inherits(id object, const char *name)
{
    Class wanted = objc_lookUpClass(name);
    if (!wanted) return false;
    for (Class cls = object_getClass(object); cls; cls = class_getSuperclass(cls)) {
        if (cls == wanted) return true;
    }
    return false;
}

static id get_object(id object, const char *selector)
{
    return ((id (*)(id, SEL))objc_msgSend)(object, sel_registerName(selector));
}

static void release_object(id object)
{
    ((void (*)(id, SEL))objc_msgSend)(object, sel_registerName("release"));
}

static uint64_t settings_event(id process, id context)
{
    if (!inherits(process, "FBProcess") || !inherits(context, "FBProcessWatchdogEventContext")) {
        return 0;
    }
    const uint64_t event = ((uint64_t (*)(id, SEL))objc_msgSend)(context, sel_registerName("event"));
    if (event != PROCESS_LAUNCH && event != SCENE_CREATE && event != SCENE_UPDATE) return 0;
    CFTypeRef bundle = (CFTypeRef)get_object(process, "bundleIdentifier");
    if (!bundle || CFGetTypeID(bundle) != CFStringGetTypeID() ||
        !CFEqual(bundle, CFSTR("com.apple.Preferences"))) return 0;
    return event;
}

static id extend_budget(id policy, enum watchdog_event event)
{
    if (!inherits(policy, "FBSProcessWatchdogPolicy")) return policy;
    CFArrayRef original = (CFArrayRef)get_object(policy, "provisions");
    if (!original || CFGetTypeID(original) != CFArrayGetTypeID()) return policy;
    const CFIndex count = CFArrayGetCount(original);
    if (count < 1 || count > 16) return policy;

    make_realtime_allowance make = (make_realtime_allowance)
        dlsym(RTLD_DEFAULT, "FBSProcessResourceAllowanceMakeWithRealTimeInterval");
    if (!make) return policy;
    const struct resource_allowance target = make(60.0);
    if (target.type != 1 || target.value != 60000 || target.reserved != 0) return policy;

    CFIndex index = kCFNotFound;
    struct resource_allowance allowance = {0};
    for (CFIndex i = 0; i < count; i++) {
        id provision = (id)CFArrayGetValueAtIndex(original, i);
        if (!inherits(provision, "FBSProcessResourceProvision")) continue;
        const struct resource_allowance entry =
            ((struct resource_allowance (*)(id, SEL))objc_msgSend)(provision, sel_registerName("allowance"));
        if (entry.type != target.type) continue;
        if (index != kCFNotFound) return policy;
        index = i;
        allowance = entry;
    }
    if (index == kCFNotFound || !allowance.value || allowance.value >= target.value) return policy;

    CFMutableArrayRef provisions = CFArrayCreateMutableCopy(NULL, 0, original);
    if (!provisions) return policy;

    id original_provision = (id)CFArrayGetValueAtIndex(original, index);
    id copied_provision = get_object(original_provision, "copy");
    id copied_policy = get_object(policy, "copy");
    if (!copied_provision || copied_provision == original_provision ||
        !copied_policy || copied_policy == policy) {
        release_object(copied_provision);
        release_object(copied_policy);
        CFRelease(provisions);
        return policy;
    }

    const uint64_t previous = allowance.value;
    allowance.value = target.value;
    ((void (*)(id, SEL, struct resource_allowance))objc_msgSend)(
        copied_provision, sel_registerName("setAllowance:"), allowance);
    CFArraySetValueAtIndex(provisions, index, copied_provision);
    ((void (*)(id, SEL, id))objc_msgSend)(copied_policy, sel_registerName("setProvisions:"), (id)provisions);
    release_object(copied_provision);
    CFRelease(provisions);

    const unsigned bit = 1u << event;
    if (note && !(__atomic_fetch_or(&receipts, bit, __ATOMIC_RELAXED) & bit)) {
        static const char *const event_names[] = {
            [PROCESS_LAUNCH] = "process-launch",
            [SCENE_CREATE] = "scene-create",
            [SCENE_UPDATE] = "scene-update",
        };
        note("Pong Settings watchdog: %s wall=%llu->%llu ms", event_names[event],
             (unsigned long long)previous, (unsigned long long)allowance.value);
    }
    return get_object(copied_policy, "autorelease");
}

static id process_launch_policy(id self, SEL selector, id process, id context)
{
    policy_method original = (policy_method)__atomic_load_n(&launch_original, __ATOMIC_ACQUIRE);
    id policy = original(self, selector, process, context);
    const uint64_t event = policy ? settings_event(process, context) : 0;
    /* FrontBoard uses this provider when a scene transition has no
     * SpringBoard override. Apply the same Settings-only update budget. */
    return event == PROCESS_LAUNCH || event == SCENE_UPDATE
        ? extend_budget(policy, (enum watchdog_event)event) : policy;
}

static id scene_policy(id self, SEL selector, id process, id context)
{
    policy_method original = (policy_method)__atomic_load_n(&scene_original, __ATOMIC_ACQUIRE);
    id policy = original(self, selector, process, context);
    const uint64_t event = policy ? settings_event(process, context) : 0;
    return event == SCENE_CREATE || event == SCENE_UPDATE
        ? extend_budget(policy, (enum watchdog_event)event) : policy;
}

static void install(const char *name, IMP *original, IMP replacement, unsigned bit)
{
    static const char *const parameters[] = {"@", "@"};
    static const char selector[] = "watchdogPolicyForProcess:eventContext:";
    Class cls = objc_lookUpClass(name);
    if (__atomic_load_n(original, __ATOMIC_ACQUIRE) ||
        !method_matches(cls, selector, "@", 2, parameters)) return;
    Method method = class_getInstanceMethod(cls, sel_registerName(selector));
    IMP implementation = method_getImplementation(method);
    IMP expected = NULL;
    if (__atomic_compare_exchange_n(original, &expected, implementation, false,
                                    __ATOMIC_RELEASE, __ATOMIC_RELAXED)) {
        method_setImplementation(method, replacement);
        const unsigned previous = __atomic_fetch_or(&installed, bit, __ATOMIC_ACQ_REL);
        if ((previous | bit) == 3 && previous != 3 && note) {
            note("Pong Settings watchdog: 22H374 process-launch and scene-create policies installed");
            note("Pong Settings watchdog: 22H374 scene-update policy installed");
        }
    }
}

static void install_when_ready(void)
{
    if (__atomic_load_n(&installed, __ATOMIC_ACQUIRE) == 3) return;

    Class process = objc_lookUpClass("FBProcess");
    Class context = objc_lookUpClass("FBProcessWatchdogEventContext");
    Class policy = objc_lookUpClass("FBSProcessWatchdogPolicy");
    Class provision = objc_lookUpClass("FBSProcessResourceProvision");
    static const char *const provisions[] = {"@"};
    static const char *const allowance[] = {"{?=qQQ}"};
    if (!method_matches(process, "bundleIdentifier", "@", 0, NULL) ||
        !method_matches(context, "event", "qQ", 0, NULL) ||
        !method_matches(policy, "provisions", "@", 0, NULL) ||
        !method_matches(policy, "setProvisions:", "v", 1, provisions) ||
        !method_matches(provision, "allowance", "{?=qQQ}", 0, NULL) ||
        !method_matches(provision, "setAllowance:", "v", 1, allowance)) return;

    install("FBApplicationProcessWatchdogPolicy", &launch_original, (IMP)process_launch_policy, 1u);
    install("SBSceneWatchdogProvider", &scene_original, (IMP)scene_policy, 2u);
}

static bool watchdog_image(const struct mach_header *header)
{
    if (!header || header->magic != MH_MAGIC_64) return false;
    const struct mach_header_64 *image = (const struct mach_header_64 *)header;
    if (image->sizeofcmds > 1024 * 1024) return false;
    const uint8_t *cursor = (const uint8_t *)(image + 1);
    const uint8_t *end = cursor + image->sizeofcmds;
    for (uint32_t i = 0; i < image->ncmds && (size_t)(end - cursor) >= sizeof(struct load_command); i++) {
        const struct load_command *command = (const struct load_command *)cursor;
        if (command->cmdsize < sizeof(*command) || command->cmdsize > (size_t)(end - cursor)) return false;
        if (command->cmd == LC_ID_DYLIB && command->cmdsize >= sizeof(struct dylib_command)) {
            const struct dylib_command *library = (const struct dylib_command *)cursor;
            uint32_t offset = library->dylib.name.offset;
            if (offset < sizeof(*library) || offset >= command->cmdsize) return false;
            const char *name = (const char *)(cursor + offset);
            return memchr(name, 0, command->cmdsize - offset) &&
                (!strcmp(name, "/System/Library/PrivateFrameworks/FrontBoard.framework/FrontBoard") ||
                 !strcmp(name, "/System/Library/PrivateFrameworks/FrontBoardServices.framework/FrontBoardServices") ||
                 !strcmp(name, "/System/Library/PrivateFrameworks/SpringBoard.framework/SpringBoard"));
        }
        cursor += command->cmdsize;
    }
    return false;
}

static void image_added(const struct mach_header *header, intptr_t slide)
{
    (void)slide;
    if (__atomic_load_n(&installed, __ATOMIC_ACQUIRE) != 3 && watchdog_image(header)) {
        install_when_ready();
    }
}

void pong_settings_watchdog18_start(watchdog_note receipt)
{
    if (strcmp(getprogname(), "SpringBoard")) return;
    char build[64] = {0};
    size_t size = sizeof(build);
    if (sysctlbyname("kern.osversion", build, &size, NULL, 0) || strcmp(build, "22H374")) return;
    note = receipt;
    install_when_ready();
    if (__atomic_load_n(&installed, __ATOMIC_ACQUIRE) != 3) {
        _dyld_register_func_for_add_image(image_added);
    }
}
