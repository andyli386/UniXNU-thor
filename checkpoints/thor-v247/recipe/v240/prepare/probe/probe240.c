/* SPDX-License-Identifier: MIT */
/* Pong probe v240 (the passive part is v238's): a look into processes that had left no trace,
 * and the Wi-Fi bootstrap that this port has no helper of its own for.
 *
 *   PosterBoard   starts at about 88 s, stays, and never produces a wallpaper
 *   configd       carries the bootstrap (below)
 *
 * The probe changes nothing in a process.  PosterBoard is sampled five times (image, offset and
 * the exported symbol dladdr knows), its wallpaper folders are listed and its end is reported.
 * Lines go through the kernel's diagnostic channel (kern.pong_diagnostic) to the console, as
 * "PONG PROBE <process>[pid]: ...".
 *
 * configd runs the bootstrap, a port of the upstream Pong helper "wifi-bootstrap":
 *   1. "Plugin:InterfaceNamer" in the dynamic store gets its end marker.  Apple's
 *      SCNetworkInterfaceCopyAll() (SCNetworkInterface.c, __wait_for_IOKit_to_quiesce) blocks
 *      until that dictionary holds *QUIET*, *COMPLETE* or *TIMEOUT*; the naming plugin on this
 *      port never writes one, so every caller hung forever (Settings -> About, wifid).  The marker
 *      is published as soon as configd's own server answers and put back every ten seconds for as
 *      long as configd lives, because the plugin's own updates drop it.
 *   2. Once en0 exists: it is listed in "State:/Network/Interface", it gets a network service in
 *      the preferences (IOKit is given five seconds to get quiet first), and the kernel is told
 *      that wifid may start.
 *   3. Power is requested from the system's Wi-Fi manager on a thread of its own, so a manager
 *      that never answers can hold nothing else up.
 *
 * v239 taught the v240 differences.  It also loaded this library into wifid and configd and sampled
 * the threads of both while they were starting, and wifid ran from the first second.  v240 puts
 * nothing into wifid, samples nothing in configd, and the kernel holds wifid back until configd has
 * prepared en0 and a power source is on the registry.
 */
#include <CoreFoundation/CoreFoundation.h>
#include <IOKit/IOKitLib.h>
#include <dispatch/dispatch.h>
#include <dirent.h>
#include <dlfcn.h>
#include <errno.h>
#include <inttypes.h>
#include <mach-o/dyld.h>
#include <mach/arm/thread_status.h>
#include <mach/mach.h>
#include <net/if.h>
#include <pthread.h>
#include <signal.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/sysctl.h>
#include <time.h>
#include <unistd.h>

#define PROBE_PREFIX "Pong Probe: "
#define PROBE_THREADS 12
#define PROBE_FRAMES 10
#define PROBE_LINES 270

static uint64_t started_ms;
static unsigned lines;

static uint64_t now_ms(void)
{
    struct timespec now = {0};
    (void)clock_gettime(CLOCK_MONOTONIC, &now);
    return (uint64_t)now.tv_sec * 1000 + (uint64_t)now.tv_nsec / 1000000;
}

/* One line to the kernel console; the channel takes at most 255 bytes. */
static void say(const char *format, ...) __attribute__((format(printf, 1, 2)));
static void vsay(const char *format, va_list list);
/* The kernel's line channel, resolved once by open_channel() before any thread starts. */
static int channel_mib[CTL_MAXNAME];
static size_t channel_count;
static void say(const char *format, ...)
{
    va_list list;
    va_start(list, format);
    vsay(format, list);
    va_end(list);
}
static void vsay(const char *format, va_list list)
{
    if (__atomic_fetch_add(&lines, 1, __ATOMIC_RELAXED) >= PROBE_LINES) {
        return;
    }
    char line[256];
    int length = snprintf(line, sizeof(line), PROBE_PREFIX "t=%" PRIu64 "ms ", (uint64_t)(now_ms() - started_ms));
    if (length > 0 && (size_t)length < sizeof(line)) {
        length += vsnprintf(line + length, sizeof(line) - (size_t)length, format, list);
    }
    if (length <= 0 || length >= 255) {
        return;
    }
#ifdef PROBE_HOST_TEST
    (void)write(STDERR_FILENO, line, (size_t)length);
    (void)write(STDERR_FILENO, "\n", 1);
#else
    /* Resolved once by open_channel(), before any thread starts. */
    if (channel_count) {
        (void)sysctl(channel_mib, (u_int)channel_count, NULL, NULL, line, (size_t)length);
    }
#endif
}

static const char *base_name(const char *path)
{
    const char *slash = strrchr(path, '/');
    return slash ? slash + 1 : path;
}

/* "Image!symbol+0x1c" when dladdr knows an exported symbol at or below the address, else "Image+0x1234". */
static void describe(uint64_t address, char *out, size_t capacity)
{
    Dl_info info = {0};
    if (dladdr((const void *)(uintptr_t)address, &info) && info.dli_fname && info.dli_fbase) {
        const char *image = base_name(info.dli_fname);
        if (info.dli_sname && (uint64_t)info.dli_saddr <= address && strlen(info.dli_sname) < 72) {
            snprintf(out, capacity, "%.32s!%.70s+0x%" PRIx64, image, info.dli_sname, address - (uint64_t)info.dli_saddr);
            return;
        }
        snprintf(out, capacity, "%.32s+0x%" PRIx64, image, address - (uint64_t)info.dli_fbase);
        return;
    }
    snprintf(out, capacity, "%" PRIx64, address);
}

static void open_channel(void)
{
    size_t n = CTL_MAXNAME;
    if (sysctlnametomib("kern.pong_diagnostic", channel_mib, &n) == 0) {
        channel_count = n;
    }
}

static uint64_t frame_address(uint64_t value)
{
    /* This arm64 shim also reads the frame records of arm64e guest code. */
    return value & UINT64_C(0x0000ffffffffffff);
}

/* Reads one frame record at address; false when it cannot be read. */
static bool read_pair(uint64_t address, uint64_t pair[2])
{
    vm_size_t size = 0;
    return address && !(address & 15) &&
        vm_read_overwrite(mach_task_self(), (vm_address_t)address, 2 * sizeof(uint64_t),
                          (vm_address_t)(uintptr_t)pair, &size) == KERN_SUCCESS && size == 2 * sizeof(uint64_t);
}

/* Prints frame records from a frame pointer, newest first, under a heading that names the thread. */
static void print_frames(uint64_t fp, const char *heading, const char *continuation)
{
    char line[256], frame[136];
    int length = snprintf(line, sizeof(line), "%s:", heading);
    uint64_t cursor = fp;
    for (unsigned i = 0; i < PROBE_FRAMES; ++i) {
        uint64_t pair[2] = {0, 0};
        if (!read_pair(cursor, pair)) {
            break;
        }
        describe(frame_address(pair[1]), frame, sizeof(frame));
        if (length > 0 && (size_t)length + strlen(frame) + 1 >= sizeof(line) - 1) {
            say("%s", line);
            length = snprintf(line, sizeof(line), "%s +%u:", continuation, i);
        }
        if (length > 0 && (size_t)length < sizeof(line)) {
            length += snprintf(line + length, sizeof(line) - (size_t)length, " %s", frame);
        }
        if (pair[0] <= cursor) {
            break;
        }
        cursor = pair[0];
    }
    say("%s", line);
}

struct thread_sample {
    uint64_t id;
    int run_state;
    unsigned frames;
    uint64_t fp;
    char name[24];
    uint64_t stack[PROBE_FRAMES];
};
static struct thread_sample samples[PROBE_THREADS];
static unsigned sample_count;

/* Probe queue: every thread is stopped for its state and frame records only; names and symbols
 * are resolved once they all run again.  PosterBoard only. */
static void collect(void)
{
    thread_act_array_t list = NULL;
    mach_msg_type_number_t count = 0;
    sample_count = 0;
    if (task_threads(mach_task_self(), &list, &count) != KERN_SUCCESS) {
        return;
    }
    const thread_t self = mach_thread_self();
    for (mach_msg_type_number_t i = 0; i < count && sample_count < PROBE_THREADS; ++i) {
        const thread_t thread = list[i];
        if (thread == self || !MACH_PORT_VALID(thread)) {
            continue;
        }
        struct thread_sample *slot = &samples[sample_count];
        memset(slot, 0, sizeof(*slot));
        if (thread_suspend(thread) != KERN_SUCCESS) {
            continue;
        }
        arm_thread_state64_t state = {0};
        mach_msg_type_number_t words = ARM_THREAD_STATE64_COUNT;
        const kern_return_t result = thread_get_state(thread, ARM_THREAD_STATE64, (thread_state_t)&state, &words);
        thread_basic_info_data_t info = {0};
        words = THREAD_BASIC_INFO_COUNT;
        (void)thread_info(thread, THREAD_BASIC_INFO, (thread_info_t)&info, &words);
        if (result == KERN_SUCCESS) {
            const uint64_t sp = arm_thread_state64_get_sp(state);
            uint64_t cursor = arm_thread_state64_get_fp(state);
            slot->stack[slot->frames++] = frame_address(arm_thread_state64_get_pc(state));
            slot->fp = cursor;
            for (unsigned guard = 0; slot->frames < PROBE_FRAMES && guard < PROBE_FRAMES; ++guard) {
                uint64_t pair[2] = {0, 0};
                if (cursor < sp || cursor - sp >= 8 * 1024 * 1024 || !read_pair(cursor, pair)) {
                    break;
                }
                slot->stack[slot->frames++] = frame_address(pair[1]);
                if (pair[0] <= cursor) {
                    break;
                }
                cursor = pair[0];
            }
            slot->run_state = info.run_state;
        }
        thread_resume(thread);
        if (result != KERN_SUCCESS) {
            continue;
        }
        thread_identifier_info_data_t identity = {0};
        mach_msg_type_number_t id_words = THREAD_IDENTIFIER_INFO_COUNT;
        if (thread_info(thread, THREAD_IDENTIFIER_INFO, (thread_info_t)&identity, &id_words) == KERN_SUCCESS) {
            slot->id = identity.thread_id;
        }
        pthread_t pthread = pthread_from_mach_thread_np(thread);
        if (pthread) {
            (void)pthread_getname_np(pthread, slot->name, sizeof(slot->name));
        }
        ++sample_count;
    }
    if (MACH_PORT_VALID(self)) {
        (void)mach_port_deallocate(mach_task_self(), self);
    }
    for (mach_msg_type_number_t i = 0; i < count; ++i) {
        (void)mach_port_deallocate(mach_task_self(), list[i]);
    }
    vm_deallocate(mach_task_self(), (vm_address_t)list, count * sizeof(*list));
}

static void print_threads(const char *why)
{
    char line[256], frame[136], heading[96], continuation[96];
    say("sample %s threads=%u", why, sample_count);
    for (unsigned t = 0; t < sample_count; ++t) {
        const struct thread_sample *thread = &samples[t];
        if (thread->frames == 1 && thread->stack[0] == 0) {
            continue;                       /* a thread that is just starting or leaving */
        }
        snprintf(heading, sizeof(heading), "sample %s tid=%" PRIu64 " name=%.20s run=%d", why, thread->id,
                 thread->name, thread->run_state);
        snprintf(continuation, sizeof(continuation), "sample %s tid=%" PRIu64, why, thread->id);
        int length = snprintf(line, sizeof(line), "%s:", heading);
        for (unsigned i = 0; i < thread->frames; ++i) {
            describe(thread->stack[i], frame, sizeof(frame));
            if (length > 0 && (size_t)length + strlen(frame) + 1 >= sizeof(line) - 1) {
                say("%s", line);
                length = snprintf(line, sizeof(line), "%s +%u:", continuation, i);
            }
            if (length > 0 && (size_t)length < sizeof(line)) {
                length += snprintf(line + length, sizeof(line) - (size_t)length, " %s", frame);
            }
        }
        say("%s", line);
    }
}

static void sample(void *why)
{
    collect();
    print_threads((const char *)why);
}

static void look_at_folders(void *unused)
{
    (void)unused;
    static const char *const paths[] = {
        "/private/var/mobile/Library/com.apple.wallpaper",
        "/private/var/mobile/Library/com.apple.wallpaper/Store",
        "/private/var/mobile/Library/com.apple.wallpaper/Store/Index.plist",
        "/private/var/mobile/Library/PosterBoard",
        "/Library/Wallpaper/Collections",
        "/System/Library/Wallpaper",
    };
    for (unsigned i = 0; i < sizeof(paths) / sizeof(paths[0]); ++i) {
        struct stat st;
        if (stat(paths[i], &st) == 0) {
            say("path %s exists type=%s uid=%u mode=%o size=%lld", paths[i],
                S_ISDIR(st.st_mode) ? "dir" : S_ISREG(st.st_mode) ? "file" : "other",
                st.st_uid, st.st_mode & 0777, (long long)st.st_size);
        } else {
            say("path %s stat errno=%d", paths[i], errno);
        }
    }
    DIR *dir = opendir("/Library/Wallpaper/Collections");
    if (dir) {
        unsigned n = 0;
        char line[256] = "wallpaper collections:";
        const struct dirent *entry;
        while (n < 14 && (entry = readdir(dir)) != NULL) {
            if (entry->d_name[0] == '.') {
                continue;
            }
            size_t used = strlen(line);
            snprintf(line + used, sizeof(line) - used, " %.28s", entry->d_name);
            ++n;
        }
        closedir(dir);
        say("%s (%u)", line, n);
    } else {
        say("wallpaper collections opendir errno=%d", errno);
    }
}

static void report_exit(void)
{
    say("exit");
}

/* A fatal signal: the faulting place with its symbol, the frames, then the default action. */
static void fatal(int number, siginfo_t *info, void *context)
{
    char where[136], lr[136], heading[96];
    const ucontext_t *uc = context;
    const arm_thread_state64_t *state = &uc->uc_mcontext->__ss;
    describe(frame_address(arm_thread_state64_get_pc(*state)), where, sizeof(where));
    describe(frame_address(arm_thread_state64_get_lr(*state)), lr, sizeof(lr));
    say("signal=%d code=%d pc=%s lr=%s", number, info ? info->si_code : 0, where, lr);
    snprintf(heading, sizeof(heading), "signal %d frames", number);
    print_frames(frame_address(arm_thread_state64_get_fp(*state)), heading, heading);
    /* Let the process end the way it would have without the probe. */
    struct sigaction action;
    memset(&action, 0, sizeof(action));
    action.sa_handler = SIG_DFL;
    (void)sigaction(number, &action, NULL);
    signal(number, SIG_DFL);
    raise(number);
}

static void install_handlers(void)
{
    struct sigaction action;
    memset(&action, 0, sizeof(action));
    action.sa_flags = SA_SIGINFO;
    action.sa_sigaction = fatal;
    static const int list[] = {SIGABRT, SIGSEGV, SIGBUS, SIGILL, SIGFPE, SIGTRAP};
    for (unsigned i = 0; i < sizeof(list) / sizeof(list[0]); ++i) {
        (void)sigaction(list[i], &action, NULL);
    }
}

/* ---- Wi-Fi bootstrap (configd only): SystemConfiguration through dlsym, as upstream's helper does ---- */
static pthread_once_t libraries_once = PTHREAD_ONCE_INIT;
static void *sc_library, *wifi_library;
#define SC_FUNCTION(type, name, parameters) static type (*p_##name) parameters;
SC_FUNCTION(CFTypeRef, SCDynamicStoreCreate, (CFAllocatorRef, CFStringRef, void *, void *))
SC_FUNCTION(CFTypeRef, SCDynamicStoreCopyValue, (CFTypeRef, CFStringRef))
SC_FUNCTION(Boolean, SCDynamicStoreSetValue, (CFTypeRef, CFStringRef, CFTypeRef))
SC_FUNCTION(CFTypeRef, SCPreferencesCreate, (CFAllocatorRef, CFStringRef, CFStringRef))
SC_FUNCTION(Boolean, SCPreferencesLock, (CFTypeRef, Boolean))
SC_FUNCTION(Boolean, SCPreferencesUnlock, (CFTypeRef))
SC_FUNCTION(Boolean, SCPreferencesCommitChanges, (CFTypeRef))
SC_FUNCTION(Boolean, SCPreferencesApplyChanges, (CFTypeRef))
SC_FUNCTION(CFArrayRef, SCNetworkServiceCopyAll, (CFTypeRef))
SC_FUNCTION(CFTypeRef, SCNetworkServiceGetInterface, (CFTypeRef))
SC_FUNCTION(CFTypeRef, SCNetworkServiceCreate, (CFTypeRef, CFTypeRef))
SC_FUNCTION(Boolean, SCNetworkServiceEstablishDefaultConfiguration, (CFTypeRef))
SC_FUNCTION(Boolean, SCNetworkServiceSetEnabled, (CFTypeRef, Boolean))
SC_FUNCTION(CFTypeRef, SCNetworkSetCopyCurrent, (CFTypeRef))
SC_FUNCTION(CFTypeRef, SCNetworkSetCreate, (CFTypeRef))
SC_FUNCTION(Boolean, SCNetworkSetSetCurrent, (CFTypeRef))
SC_FUNCTION(Boolean, SCNetworkSetAddService, (CFTypeRef, CFTypeRef))
SC_FUNCTION(CFTypeRef, SCNetworkInterfaceCreateWithIONetworkInterfaceObject, (io_registry_entry_t))
SC_FUNCTION(CFStringRef, SCNetworkInterfaceGetInterfaceType, (CFTypeRef))
SC_FUNCTION(CFStringRef, SCNetworkInterfaceGetBSDName, (CFTypeRef))
SC_FUNCTION(CFTypeRef, WiFiManagerClientCreate, (CFAllocatorRef, unsigned))
SC_FUNCTION(void, WiFiManagerClientScheduleWithRunLoop, (CFTypeRef, CFRunLoopRef, CFStringRef))
SC_FUNCTION(CFArrayRef, WiFiManagerClientCopyDevices, (CFTypeRef))
SC_FUNCTION(CFTypeRef, WiFiManagerClientCopyProperty, (CFTypeRef, CFStringRef))
SC_FUNCTION(void, WiFiManagerClientSetPower, (CFTypeRef, Boolean))
#undef SC_FUNCTION

static void load_libraries(void)
{
    sc_library = dlopen("/System/Library/Frameworks/SystemConfiguration.framework/SystemConfiguration", RTLD_NOW | RTLD_LOCAL);
    if (!sc_library) {
        say("bootstrap SystemConfiguration not loaded");
        return;
    }
#define LOAD(library, name) p_##name = (__typeof__(p_##name))dlsym(library, #name); if (!p_##name) say("bootstrap %s missing", #name);
    LOAD(sc_library, SCDynamicStoreCreate)
    LOAD(sc_library, SCDynamicStoreCopyValue)
    LOAD(sc_library, SCDynamicStoreSetValue)
    LOAD(sc_library, SCPreferencesCreate)
    LOAD(sc_library, SCPreferencesLock)
    LOAD(sc_library, SCPreferencesUnlock)
    LOAD(sc_library, SCPreferencesCommitChanges)
    LOAD(sc_library, SCPreferencesApplyChanges)
    LOAD(sc_library, SCNetworkServiceCopyAll)
    LOAD(sc_library, SCNetworkServiceGetInterface)
    LOAD(sc_library, SCNetworkServiceCreate)
    LOAD(sc_library, SCNetworkServiceEstablishDefaultConfiguration)
    LOAD(sc_library, SCNetworkServiceSetEnabled)
    LOAD(sc_library, SCNetworkSetCopyCurrent)
    LOAD(sc_library, SCNetworkSetCreate)
    LOAD(sc_library, SCNetworkSetSetCurrent)
    LOAD(sc_library, SCNetworkSetAddService)
    LOAD(sc_library, SCNetworkInterfaceCreateWithIONetworkInterfaceObject)
    LOAD(sc_library, SCNetworkInterfaceGetInterfaceType)
    LOAD(sc_library, SCNetworkInterfaceGetBSDName)
#undef LOAD
}

/* The end marker of the interface-naming plugin, if the dynamic store does not have one yet. */
static bool namer_done(CFDictionaryRef record)
{
    return CFDictionaryContainsKey(record, CFSTR("*QUIET*")) ||
        CFDictionaryContainsKey(record, CFSTR("*COMPLETE*")) ||
        CFDictionaryContainsKey(record, CFSTR("*TIMEOUT*"));
}

/* The current value of a key in the dynamic store, as a dictionary to change and put back.
 * answered tells whether configd's server responded (this library is loaded before configd's
 * main() runs, and its own server is the last to come up). */
static bool store_dict(CFStringRef key, CFDictionaryRef *old, CFMutableDictionaryRef *record, CFTypeRef *store, bool *answered)
{
    *old = NULL;
    *answered = false;
    *store = p_SCDynamicStoreCreate(NULL, CFSTR("Pong probe bootstrap"), NULL, NULL);
    if (!*store) {
        return false;
    }
    *answered = true;
    CFTypeRef value = p_SCDynamicStoreCopyValue(*store, key);
    if (value && CFGetTypeID(value) == CFDictionaryGetTypeID()) {
        *old = value;
        *record = CFDictionaryCreateMutableCopy(NULL, 0, value);
    } else {
        if (value) {
            CFRelease(value);
        }
        *record = CFDictionaryCreateMutable(NULL, 0, &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
    }
    return *record != NULL;
}

static void store_done(CFTypeRef store, CFDictionaryRef old, CFTypeRef record)
{
    if (record) {
        CFRelease(record);
    }
    if (old) {
        CFRelease(old);
    }
    if (store) {
        CFRelease(store);
    }
}

/* Put the end marker into "Plugin:InterfaceNamer" unless one is there.  With wait, IOKit is given
 * five seconds to get quiet first: the marker is then *QUIET* when it did, *TIMEOUT* when not. */
static bool publish_namer_marker(const char *why, bool wait, bool *answered)
{
    pthread_once(&libraries_once, load_libraries);
    if (answered) {
        *answered = false;
    }
    if (!p_SCDynamicStoreCreate || !p_SCDynamicStoreCopyValue || !p_SCDynamicStoreSetValue) {
        return false;
    }
    kern_return_t waited = KERN_SUCCESS;
    if (wait) {
        mach_timespec_t limit = {5, 0};
        waited = IOKitWaitQuiet(kIOMainPortDefault, &limit);
        if (waited != KERN_SUCCESS && waited != kIOReturnTimeout) {
            say("bootstrap IOKit wait failed rc=%d", (int)waited);
        }
    }
    CFDictionaryRef old = NULL;
    CFMutableDictionaryRef record = NULL;
    CFTypeRef store = NULL;
    bool spoke = false;
    bool done = false;
    const bool periodic = !wait && !strcmp(why, "recheck");
    static unsigned republished;
    if (store_dict(CFSTR("Plugin:InterfaceNamer"), &old, &record, &store, &spoke) && record) {
        if (namer_done(record)) {
            if (!periodic) {
                say("bootstrap namer %s: a marker is already there", why);
            }
            done = true;
        } else {
            CFDictionarySetValue(record, waited == KERN_SUCCESS ? CFSTR("*QUIET*") : CFSTR("*TIMEOUT*"), kCFBooleanTrue);
            done = p_SCDynamicStoreSetValue(store, CFSTR("Plugin:InterfaceNamer"), record);
            ++republished;
            if (!periodic || republished <= 12 || republished % 50 == 0) {
                say("bootstrap namer %s: iokit=%s publish=%d (republished %u)", why,
                    waited == KERN_SUCCESS ? "quiet" : "busy", done, republished);
            }
        }
    } else if (spoke && !periodic) {
        say("bootstrap namer %s: the store has no interface-namer dictionary", why);
    }
    if (answered) {
        *answered = spoke;
    }
    store_done(store, old, record);
    return done;
}

/* En0 in "State:/Network/Interface", the list the interface-naming plugin and wifid look at. */
static bool publish_interface(void)
{
    pthread_once(&libraries_once, load_libraries);
    if (!p_SCDynamicStoreCreate || !p_SCDynamicStoreCopyValue || !p_SCDynamicStoreSetValue) {
        return false;
    }
    CFDictionaryRef old = NULL;
    CFMutableDictionaryRef record = NULL;
    CFTypeRef store = NULL;
    bool answered = false;
    bool done = false;
    if (store_dict(CFSTR("State:/Network/Interface"), &old, &record, &store, &answered) && record) {
        CFArrayRef list = CFDictionaryGetValue(record, CFSTR("Interfaces"));
        CFIndex count = (list && CFGetTypeID(list) == CFArrayGetTypeID()) ? CFArrayGetCount(list) : 0;
        bool present = false;
        for (CFIndex i = 0; i < count; ++i) {
            const void *name = CFArrayGetValueAtIndex(list, i);
            present |= name && CFGetTypeID(name) == CFStringGetTypeID() && CFEqual(name, CFSTR("en0"));
        }
        if (present) {
            say("bootstrap interface: en0 is already listed");
            done = true;
        } else {
            CFMutableArrayRef names = count ? CFArrayCreateMutableCopy(NULL, 0, list) :
                CFArrayCreateMutable(NULL, 0, &kCFTypeArrayCallBacks);
            CFArrayAppendValue(names, CFSTR("en0"));
            CFDictionarySetValue(record, CFSTR("Interfaces"), names);
            done = p_SCDynamicStoreSetValue(store, CFSTR("State:/Network/Interface"), record);
            say("bootstrap interface: en0 listed publish=%d (was %ld)", done, (long)count);
            CFRelease(names);
        }
    }
    store_done(store, old, record);
    return done;
}

/* A network service for en0 in the preferences, as upstream's helper creates one. */
static bool configure_interface(void)
{
    pthread_once(&libraries_once, load_libraries);
    if (!p_SCPreferencesCreate || !p_SCPreferencesLock || !p_SCPreferencesUnlock ||
        !p_SCPreferencesCommitChanges || !p_SCPreferencesApplyChanges || !p_SCNetworkServiceCopyAll ||
        !p_SCNetworkServiceGetInterface || !p_SCNetworkServiceCreate ||
        !p_SCNetworkServiceEstablishDefaultConfiguration || !p_SCNetworkServiceSetEnabled ||
        !p_SCNetworkSetCopyCurrent || !p_SCNetworkSetCreate || !p_SCNetworkSetSetCurrent ||
        !p_SCNetworkSetAddService || !p_SCNetworkInterfaceCreateWithIONetworkInterfaceObject ||
        !p_SCNetworkInterfaceGetInterfaceType || !p_SCNetworkInterfaceGetBSDName) {
        return false;
    }
    io_service_t entry = IOServiceGetMatchingService(kIOMainPortDefault, IOServiceMatching("IO80211Interface"));
    if (!entry) {
        say("bootstrap service: no IO80211Interface service");
        return false;
    }
    CFTypeRef name_ref = IORegistryEntryCreateCFProperty(entry, CFSTR("BSD Name"), NULL, 0);
    bool is_en0 = name_ref && CFGetTypeID(name_ref) == CFStringGetTypeID() && CFEqual(name_ref, CFSTR("en0"));
    CFTypeRef interface = is_en0 ? p_SCNetworkInterfaceCreateWithIONetworkInterfaceObject(entry) : NULL;
    if (name_ref) {
        CFRelease(name_ref);
    }
    IOObjectRelease(entry);
    if (!interface) {
        say("bootstrap service: SystemConfiguration would not take en0");
        return false;
    }
    CFStringRef type = p_SCNetworkInterfaceGetInterfaceType(interface);
    bool wireless = type && CFEqual(type, CFSTR("IEEE80211"));
    if (type) {
        CFRelease(type);
    }
    if (!wireless) {
        say("bootstrap service: en0 is not IEEE80211 to SystemConfiguration");
        CFRelease(interface);
        return false;
    }
    CFTypeRef prefs = p_SCPreferencesCreate(NULL, CFSTR("Pong probe: en0 service"), NULL);
    bool done = false;
    if (prefs && p_SCPreferencesLock(prefs, false)) {
        CFArrayRef services = p_SCNetworkServiceCopyAll(prefs);
        bool found = false;
        if (services) {
            for (CFIndex i = 0; !found && i < CFArrayGetCount(services); ++i) {
                CFTypeRef candidate = p_SCNetworkServiceGetInterface(CFArrayGetValueAtIndex(services, i));
                CFStringRef bsd = candidate ? p_SCNetworkInterfaceGetBSDName(candidate) : NULL;
                found = bsd && CFEqual(bsd, CFSTR("en0"));
            }
        }
        if (services) {
            CFRelease(services);
        }
        if (found) {
            done = true;
            say("bootstrap service: en0 already has a service");
        } else {
            CFTypeRef set = p_SCNetworkSetCopyCurrent(prefs);
            bool current = set != NULL;
            if (!set) {
                set = p_SCNetworkSetCreate(prefs);
                current = set && p_SCNetworkSetSetCurrent(set);
            }
            CFTypeRef service = current ? p_SCNetworkServiceCreate(prefs, interface) : NULL;
            int steps = 0;
            if (service) {
                steps += p_SCNetworkServiceEstablishDefaultConfiguration(service);
                steps += p_SCNetworkServiceSetEnabled(service, true);
                steps += p_SCNetworkSetAddService(set, service);
                CFRelease(service);
            }
            steps += p_SCPreferencesCommitChanges(prefs);
            steps += p_SCPreferencesApplyChanges(prefs);
            say("bootstrap service: en0 service created steps=%d of 5", steps);
            done = steps == 5;
        }
        (void)p_SCPreferencesUnlock(prefs);
    } else {
        say("bootstrap service: preferences lock refused");
    }
    if (prefs) {
        CFRelease(prefs);
    }
    CFRelease(interface);
    return done;
}

/* Power on through the system's own Wi-Fi manager, as upstream's helper does.  Its own thread:
 * a manager that never answers can hold nothing else up. */
static bool power_on(void)
{
    wifi_library = wifi_library ?: dlopen("/System/Library/PrivateFrameworks/MobileWiFi.framework/MobileWiFi", RTLD_NOW | RTLD_LOCAL);
    if (!wifi_library) {
        say("bootstrap power: MobileWiFi not loaded");
        return false;
    }
#define LOAD(library, name) if (!p_##name && !(p_##name = (__typeof__(p_##name))dlsym(library, #name))) { say("bootstrap %s missing", #name); return false; }
    LOAD(wifi_library, WiFiManagerClientCreate)
    LOAD(wifi_library, WiFiManagerClientScheduleWithRunLoop)
    LOAD(wifi_library, WiFiManagerClientCopyDevices)
    LOAD(wifi_library, WiFiManagerClientCopyProperty)
    LOAD(wifi_library, WiFiManagerClientSetPower)
#undef LOAD
    CFTypeRef manager = p_WiFiManagerClientCreate(kCFAllocatorDefault, 0);
    if (!manager) {
        say("bootstrap power: the Wi-Fi manager is not reachable");
        return false;
    }
    p_WiFiManagerClientScheduleWithRunLoop(manager, CFRunLoopGetCurrent(), kCFRunLoopDefaultMode);
    CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.25, false);
    CFTypeRef airplane = p_WiFiManagerClientCopyProperty(manager, CFSTR("AirplaneMode"));
    bool blocked = airplane && CFGetTypeID(airplane) == CFBooleanGetTypeID() && CFBooleanGetValue(airplane);
    if (airplane) {
        CFRelease(airplane);
    }
    if (blocked) {
        say("bootstrap power: airplane mode is on, left alone");
        CFRelease(manager);
        return false;
    }
    p_WiFiManagerClientSetPower(manager, true);
    say("bootstrap power: power on requested");
    bool seen = false;
    for (unsigned attempt = 0; attempt < 40 && !seen; ++attempt) {
        CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.5, false);
        CFArrayRef devices = p_WiFiManagerClientCopyDevices(manager);
        seen = devices && CFGetTypeID(devices) == CFArrayGetTypeID() && CFArrayGetCount(devices) > 0;
        if (devices) {
            CFRelease(devices);
        }
    }
    say("bootstrap power: devices=%d", seen);
    CFRelease(manager);
    return seen;
}

static void *configd_power(void *unused)
{
    (void)unused;
    pthread_setname_np("pong-wifi-power");
    for (unsigned attempt = 1; attempt <= 12; ++attempt) {
        if (power_on()) {
            return NULL;
        }
        sleep(5);
    }
    say("bootstrap power: the Wi-Fi manager never answered");
    return NULL;
}

/* Keeps the end marker in the store for as long as configd lives: the naming plugin republishes
 * its dictionary without one on its own events.  Its own thread, started by the prepare thread
 * before anything else is tried. */
static void *configd_marker(void *unused)
{
    (void)unused;
    pthread_setname_np("pong-wifi-marker");
    for (;;) {
        sleep(10);
        (void)publish_namer_marker("recheck", false, NULL);
    }
    return NULL;
}

/* The prepare thread of configd, started at launch.  Nothing here can stop configd: every call is
 * one of the system configuration interfaces configd's own plugins use, and each failure is
 * reported. */
/* The prepare thread of configd, started at launch.  Nothing here can stop configd: every call is
 * one of the system configuration interfaces configd's own plugins use, and each failure is
 * reported. */
static void *configd_prepare(void *unused)
{
    (void)unused;
    pthread_setname_np("pong-wifi-prepare");
#ifdef PROBE_HOST_TEST
    /* The host test must not write into this Mac's own dynamic store or preferences. */
    say("bootstrap: not run by the host test");
    return NULL;
#endif
    /* The library is loaded before configd's main() runs, so its own server may not answer yet, and a
     * store that answers may still refuse the first writes: try twice a second for five minutes.
     * Every caller of SCNetworkInterfaceCopyAll() waits for this marker, and wifid keeps its manager
     * queue on that call.  The keeper thread repeats the check every ten seconds for as long as
     * configd lives, whatever becomes of this thread. */
    pthread_t keeper;
    if (pthread_create(&keeper, NULL, configd_marker, NULL) == 0) {
        pthread_detach(keeper);
    }
    bool marker = false, answered = false;
    unsigned attempts = 0;
    while (!marker && attempts < 600) {
        ++attempts;
        marker = publish_namer_marker("early", false, &answered);
        if (!marker) {
            usleep(500000);
        }
    }
    say("bootstrap early marker=%d after %u attempts (the store answered: %d)", marker, attempts, answered);
    unsigned waited = 0;
    while (!if_nametoindex("en0") && waited < 180) {
        usleep(500000);
        ++waited;
    }
    say("bootstrap en0 present=%d after %u ms", if_nametoindex("en0") != 0, waited * 500);
    (void)publish_interface();
    (void)publish_namer_marker("after-en0", true, NULL);
    (void)configure_interface();
    /* The kernel reads this line and lets wifid start (once a power source is on the registry). */
    say("wifi gate open");
    pthread_t power;
    if (pthread_create(&power, NULL, configd_power, NULL) == 0) {
        pthread_detach(power);
    }
    return NULL;
}

__attribute__((constructor))
static void probe_start(void)
{
    const char *process = getprogname();
    if (!process) {
        return;
    }
    const bool poster = !strcmp(process, "PosterBoard");
    const bool configd = !strcmp(process, "configd");
    if (!poster && !configd) {
        return;
    }
    started_ms = now_ms();
    open_channel();
    char path[512] = "?";
    uint32_t path_length = sizeof(path);
    if (_NSGetExecutablePath(path, &path_length) != 0) {
        strlcpy(path, "(too long)", sizeof(path));
    }
    const char *service = getenv("XPC_SERVICE_NAME");
    const char *home = getenv("HOME");
    say("loaded %s pid=%d path=%.72s home=%.40s service=%.56s", process, getpid(), base_name(path),
        home ? home : "-", service ? service : "-");
    if (poster) {
        dispatch_queue_t queue = dispatch_get_global_queue(QOS_CLASS_UTILITY, 0);
        static const unsigned delays[] = {300, 1500, 5000, 20000, 60000};
        static const char *const whys[] = {"0.3s", "1.5s", "5s", "20s", "60s"};
        for (unsigned i = 0; i < sizeof(delays) / sizeof(delays[0]); ++i) {
            dispatch_after_f(dispatch_time(DISPATCH_TIME_NOW, (int64_t)delays[i] * NSEC_PER_MSEC), queue,
                             (void *)(uintptr_t)whys[i], sample);
        }
        dispatch_after_f(dispatch_time(DISPATCH_TIME_NOW, (int64_t)4000 * NSEC_PER_MSEC), queue, NULL,
                         look_at_folders);
        install_handlers();
        (void)atexit(report_exit);
    } else {
        pthread_t prepare;
        if (pthread_create(&prepare, NULL, configd_prepare, NULL) == 0) {
            pthread_detach(prepare);
        }
    }
}
