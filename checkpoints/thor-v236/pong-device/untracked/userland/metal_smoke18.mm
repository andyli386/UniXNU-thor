/* SPDX-License-Identifier: MIT */
#import <Metal/Metal.h>
#import <objc/message.h>
#import <objc/runtime.h>

#include <dispatch/dispatch.h>
#include <dlfcn.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/sysctl.h>
#include <time.h>
#include <unistd.h>

#if defined(PONG_THOR_SETTINGS_WATCHDOG)
extern "C" void pong_settings_watch_stack_mark_active(void);
#endif

namespace {

constexpr size_t kBufferSize = 4096;
constexpr size_t kFillOffset = 1024;
constexpr size_t kFillSize = 2048;
constexpr uint8_t kGuardByte = 0x35;
constexpr uint8_t kFillByte = 0xa6;

bool softwareForegroundOnly;
uint64_t foregroundWatchStarted;
bool foregroundReported;

void report(const char *format, ...) __attribute__((format(printf, 1, 2)));

void report(const char *format, ...)
{
    char detail[194];
    va_list args;
    va_start(args, format);
    vsnprintf(detail, sizeof(detail), format, args);
    va_end(args);

    char line[256];
    const char *prefix = softwareForegroundOnly
        ? "Pong Settings: " : "Pong Metal: Preferences smoke ";
    const int length = snprintf(line, sizeof(line), "%s%s", prefix, detail);
    if (length <= 0 || static_cast<size_t>(length) >= sizeof(line) - 1) {
        return;
    }

    int mib[CTL_MAXNAME];
    size_t count = CTL_MAXNAME;
    if (sysctlnametomib("kern.pong_diagnostic", mib, &count) == 0) {
        sysctl(mib, static_cast<u_int>(count), nullptr, nullptr, line, length);
    }
    line[length] = '\n';
    (void)write(STDERR_FILENO, line, length + 1);
}

using Milliseconds = unsigned long long;
constexpr Milliseconds kInitDeadline = 60'000;
constexpr Milliseconds kGpuDeadline = 10'000;

enum SmokePhase : unsigned {
    kPreparing,
    kSubmitting,
    kFinished,
};

Milliseconds monotonicMilliseconds()
{
    return clock_gettime_nsec_np(CLOCK_UPTIME_RAW) / NSEC_PER_MSEC;
}

void runSmoke()
{
    const Milliseconds initStarted = monotonicMilliseconds();
    __block Milliseconds submitStarted = 0;
    __block unsigned phase = kPreparing;
    __block const char *initStage = "load-framework";

    bool (^transition)(unsigned, unsigned) = ^bool(unsigned from, unsigned to) {
        return __atomic_compare_exchange_n(&phase, &from, to, false,
                                           __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE);
    };
    bool (^claimResult)(void) = ^bool {
        return __atomic_exchange_n(&phase, kFinished, __ATOMIC_ACQ_REL) != kFinished;
    };
    void (^expire)(unsigned, const char *, Milliseconds, Milliseconds) =
        ^(unsigned from, const char *stage, Milliseconds started, Milliseconds deadline) {
            if (transition(from, kFinished)) {
                report("TIMEOUT phase=%s stage=%s elapsed_ms=%llu deadline_ms=%llu",
                       from == kPreparing ? "init" : "gpu", stage,
                       monotonicMilliseconds() - started, deadline);
            }
        };
    void (^fail)(const char *) = ^(const char *stage) {
        if (claimResult()) {
            report("FAIL stage=%s elapsed_ms=%llu", stage,
                   monotonicMilliseconds() - initStarted);
        }
    };
    bool (^preparing)(const char *) = ^bool(const char *stage) {
        __atomic_store_n(&initStage, stage, __ATOMIC_RELEASE);
        if (monotonicMilliseconds() - initStarted >= kInitDeadline) {
            expire(kPreparing, stage, initStarted, kInitDeadline);
        }
        return __atomic_load_n(&phase, __ATOMIC_ACQUIRE) == kPreparing;
    };
    bool (^gpuActive)(void) = ^bool {
        const Milliseconds started = __atomic_load_n(&submitStarted, __ATOMIC_ACQUIRE);
        if (monotonicMilliseconds() - started >= kGpuDeadline) {
            expire(kSubmitting, "submit-readback", started, kGpuDeadline);
        }
        return __atomic_load_n(&phase, __ATOMIC_ACQUIRE) == kSubmitting;
    };

    dispatch_queue_t watchdog = dispatch_get_global_queue(QOS_CLASS_DEFAULT, 0);
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, kInitDeadline * NSEC_PER_MSEC), watchdog, ^{
        expire(kPreparing, __atomic_load_n(&initStage, __ATOMIC_ACQUIRE), initStarted, kInitDeadline);
    });

    @autoreleasepool {
        @try {
            report("start");
            const Milliseconds frameworkStarted = monotonicMilliseconds();
            void *framework = dlopen("/System/Library/Frameworks/Metal.framework/Metal",
                                     RTLD_NOW | RTLD_LOCAL);
            const Milliseconds frameworkElapsed = monotonicMilliseconds() - frameworkStarted;
            if (!preparing("resolve-device-factory")) {
                return;
            }
            if (!framework) {
                fail("load-framework");
                return;
            }
            report("framework-ready dlopen_ms=%llu", frameworkElapsed);

            using CreateDefaultDevice = decltype(&MTLCreateSystemDefaultDevice);
            auto createDefaultDevice = reinterpret_cast<CreateDefaultDevice>(
                dlsym(framework, "MTLCreateSystemDefaultDevice"));
            if (!createDefaultDevice) {
                fail("resolve-device-factory");
                return;
            }
            if (!preparing("create-default-device")) {
                return;
            }
            report("factory-start elapsed_ms=%llu", monotonicMilliseconds() - initStarted);
            const Milliseconds factoryStarted = monotonicMilliseconds();
            id<MTLDevice> device = createDefaultDevice();
            const Milliseconds factoryElapsed = monotonicMilliseconds() - factoryStarted;
            if (!preparing("check-device-identity")) {
                return;
            }
            if (!device) {
                fail("default-device-unavailable");
                return;
            }
            const char *deviceClass = object_getClassName(device);
            if (strcmp(deviceClass, "NativeBridgeMetalDevice") != 0) {
                if (claimResult()) {
                    report("FAIL stage=device-identity class=%s factory_ms=%llu", deviceClass,
                           factoryElapsed);
                }
                return;
            }
            const uint64_t registryID = device.registryID;
            if (!registryID) {
                fail("device-registry-id");
                return;
            }
            report("native-device class=%s registry=0x%llx factory_ms=%llu elapsed_ms=%llu",
                   deviceClass, static_cast<unsigned long long>(registryID), factoryElapsed,
                   monotonicMilliseconds() - initStarted);

            if (!preparing("shared-buffer")) {
                return;
            }
            const Milliseconds bufferStarted = monotonicMilliseconds();
            uint8_t initial[kBufferSize];
            memset(initial, kGuardByte, sizeof(initial));
            id<MTLBuffer> buffer = [device newBufferWithBytes:initial
                                                      length:sizeof(initial)
                                                     options:MTLResourceStorageModeShared];
            if (!buffer || !buffer.contents) {
                fail("shared-buffer");
                return;
            }
            if (memcmp(buffer.contents, initial, sizeof(initial)) != 0) {
                fail("initial-buffer-contents");
                return;
            }
            report("buffer-ready buffer_ms=%llu elapsed_ms=%llu",
                   monotonicMilliseconds() - bufferStarted, monotonicMilliseconds() - initStarted);

            if (!preparing("encode-command")) {
                return;
            }
            id<MTLCommandQueue> queue = [device newCommandQueueWithMaxCommandBufferCount:1];
            id<MTLCommandBuffer> command = [queue commandBuffer];
            id<MTLBlitCommandEncoder> blit = [command blitCommandEncoder];
            if (!queue || !command || !blit) {
                fail("blit-encoder");
                return;
            }
            [blit fillBuffer:buffer range:NSMakeRange(kFillOffset, kFillSize) value:kFillByte];
            [blit endEncoding];

            [command addScheduledHandler:^(id<MTLCommandBuffer>) {
                if (gpuActive()) {
                    report("scheduled submit_ms=%llu", monotonicMilliseconds() -
                           __atomic_load_n(&submitStarted, __ATOMIC_ACQUIRE));
                }
            }];
            [command addCompletedHandler:^(id<MTLCommandBuffer> completed) {
                @autoreleasepool {
                    @try {
                        if (!gpuActive()) {
                            return;
                        }
                        const MTLCommandBufferStatus status = completed.status;
                        if (status != MTLCommandBufferStatusCompleted) {
                            if (claimResult()) {
                                report("FAIL stage=completion status=%lu error=%ld",
                                       static_cast<unsigned long>(status),
                                       static_cast<long>(completed.error.code));
                            }
                            return;
                        }
                        const auto *bytes = static_cast<const uint8_t *>(buffer.contents);
                        if (!bytes) {
                            fail("readback-pointer");
                            return;
                        }
                        for (size_t i = 0; i < kBufferSize; ++i) {
                            const uint8_t expected =
                                (i >= kFillOffset && i < kFillOffset + kFillSize) ?
                                kFillByte : kGuardByte;
                            if (bytes[i] != expected) {
                                if (claimResult()) {
                                    report("FAIL stage=readback offset=%zu expected=%02x actual=%02x",
                                           i, static_cast<unsigned>(expected),
                                           static_cast<unsigned>(bytes[i]));
                                }
                                return;
                            }
                        }
                        if (gpuActive() && claimResult()) {
                            report("PASS readback=%zu fill=%zu guards=%zu submit_ms=%llu",
                                   kBufferSize, kFillSize, kBufferSize - kFillSize,
                                   monotonicMilliseconds() -
                                   __atomic_load_n(&submitStarted, __ATOMIC_ACQUIRE));
                        }
                    } @catch (NSException *) {
                        fail("readback-exception");
                    }
                }
            }];

            if (!preparing("admit-gpu-submit")) {
                return;
            }
            const Milliseconds started = monotonicMilliseconds();
            __atomic_store_n(&submitStarted, started, __ATOMIC_RELEASE);
            if (!transition(kPreparing, kSubmitting)) {
                return;
            }
            dispatch_after(dispatch_time(DISPATCH_TIME_NOW, kGpuDeadline * NSEC_PER_MSEC), watchdog, ^{
                expire(kSubmitting, "submit-readback", started, kGpuDeadline);
            });
            report("commit elapsed_ms=%llu", started - initStarted);
            if (gpuActive()) {
                [command commit];
            }
        } @catch (NSException *) {
            fail("submission-exception");
        }
    }
}

constexpr NSInteger kApplicationActive = 0;
constexpr int64_t kForegroundDelay = 3 * NSEC_PER_SEC;

// These values are read and written exclusively on the main queue.
uint64_t foregroundGeneration;
bool smokeStarted;
NSNotificationCenter *notificationCenter;
id activeObserver;
id resignObserver;

bool applicationIsActive()
{
    Class appClass = objc_lookUpClass("UIApplication");
    SEL shared = sel_registerName("sharedApplication");
    SEL state = sel_registerName("applicationState");
    if (!appClass || !class_getClassMethod(appClass, shared) ||
        !class_getInstanceMethod(appClass, state)) {
        return false;
    }

    using GetApplication = id (*)(Class, SEL);
    using GetState = NSInteger (*)(id, SEL);
    id app = reinterpret_cast<GetApplication>(objc_msgSend)(appClass, shared);
    return app && reinterpret_cast<GetState>(objc_msgSend)(app, state) == kApplicationActive;
}

void scheduleActiveSmoke(bool run_probe)
{
#if defined(PONG_THOR_SETTINGS_WATCHDOG)
    pong_settings_watch_stack_mark_active();
#endif
    if (!run_probe) {
        if (!foregroundReported) {
            foregroundReported = true;
            report("foreground active after %llums from constructor",
                   monotonicMilliseconds() - foregroundWatchStarted);
            if (activeObserver) {
                [notificationCenter removeObserver:activeObserver];
                activeObserver = nil;
            }
        }
        return;
    }
    if (smokeStarted) {
        return;
    }
    const uint64_t generation = ++foregroundGeneration;
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, kForegroundDelay), dispatch_get_main_queue(), ^{
        if (smokeStarted || generation != foregroundGeneration || !applicationIsActive()) {
            return;
        }
        smokeStarted = true;
        [notificationCenter removeObserver:activeObserver];
        [notificationCenter removeObserver:resignObserver];
        activeObserver = nil;
        resignObserver = nil;
        dispatch_async(dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{
            runSmoke();
        });
    });
}

void onMain(dispatch_block_t block)
{
    if (pthread_main_np()) {
        block();
    } else {
        dispatch_async(dispatch_get_main_queue(), block);
    }
}

void observeForeground(bool run_probe)
{
    Class centerClass = objc_lookUpClass("NSNotificationCenter");
    if (!centerClass) {
        report("FAIL stage=notification-center");
        return;
    }
    notificationCenter = [centerClass defaultCenter];
    if (!notificationCenter) {
        report("FAIL stage=notification-center");
        return;
    }
    activeObserver = [notificationCenter addObserverForName:@"UIApplicationDidBecomeActiveNotification"
                                                     object:nil queue:nil usingBlock:^(NSNotification *) {
        onMain(^{ scheduleActiveSmoke(run_probe); });
    }];
    if (run_probe) {
        resignObserver = [notificationCenter addObserverForName:@"UIApplicationWillResignActiveNotification"
                                                         object:nil queue:nil usingBlock:^(NSNotification *) {
            onMain(^{ ++foregroundGeneration; });
        }];
        report("armed; waiting for active Settings");
    } else {
        report("foreground watcher armed without compute probe");
    }
    // Registration runs after the constructor. Catch activation that UIKit
    // already delivered before the first main-queue source was serviced.
    if (applicationIsActive()) {
        scheduleActiveSmoke(run_probe);
    }
}

} // namespace

extern "C" void pong_preferences_metal_smoke_start(bool run_probe)
{
    const char *process = getprogname();
    static unsigned armed;
    if (!process || strcmp(process, "Preferences") != 0 ||
        __atomic_exchange_n(&armed, 1, __ATOMIC_ACQ_REL)) {
        return;
    }
    softwareForegroundOnly = !run_probe;
    foregroundWatchStarted = monotonicMilliseconds();
    dispatch_async(dispatch_get_main_queue(), ^{
        observeForeground(run_probe);
    });
}
