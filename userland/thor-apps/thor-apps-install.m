/* SPDX-License-Identifier: MIT */
#import <Foundation/Foundation.h>
#include <CommonCrypto/CommonDigest.h>
#include <copyfile.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <objc/message.h>
#include <objc/runtime.h>
#include <os/lock.h>
#include <signal.h>
#include <stdarg.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>

@interface NSObject (ThorNativeApplicationState)
+ (id)applicationProxyForIdentifier:(NSString *)identifier;
- (NSURL *)bundleURL;
- (id)appState;
- (BOOL)isValid;
- (BOOL)isInstalled;
- (BOOL)isPlaceholder;
@end

struct Application {
    const char *bundle, *identifier, *executable;
    unsigned char digest[CC_SHA256_DIGEST_LENGTH];
};

static const struct Application apps[] = {
#include "app-inventory.inc"
};
enum { AppCount = sizeof(apps) / sizeof(apps[0]), BatchSeconds = 240, AppSeconds = 32 };
static NSString *const stageParent = @"/private/var/mobile/Library/Pong";
static NSString *const migratorPath = @"/System/Library/DataClassMigrators/SystemAppMigrator.migrator";
static const char logPath[] = "/private/var/mobile/Library/Logs/PongAppsInstall.txt";
static volatile sig_atomic_t logFD = -1;

static void receipt(const char *format, ...) __attribute__((format(printf, 1, 2)));
static void receipt(const char *format, ...)
{
    char line[1024];
    va_list args;
    va_start(args, format);
    int length = vsnprintf(line, sizeof(line), format, args);
    va_end(args);
    if (logFD >= 0 && length > 0 && (size_t)length < sizeof(line))
        (void)write(logFD, line, (size_t)length);
}

static void log_error(const char *phase, NSError *error)
{
    for (unsigned depth = 0; depth < 4 && [error isKindOfClass:NSError.class]; ++depth) {
        receipt("PONG APPS: %s error[%u] domain=%.120s code=%ld details=%.360s\n",
                phase, depth, error.domain.UTF8String ?: "-", (long)error.code,
                error.localizedDescription.UTF8String ?: "-");
        error = error.userInfo[NSUnderlyingErrorKey];
    }
}

#include "coordinator-observer.inc"

static void deadline(int signum)
{
    (void)signum;
    static const char line[] = "PONG APPS: END deadline; checkpoint and staged source retained\n";
    if (logFD >= 0) (void)write(logFD, line, sizeof(line) - 1);
    _exit(75);
}

static bool application_digest(const char *path, const unsigned char *expected)
{
    int fd = open(path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK);
    if (fd < 0) return false;
    struct stat status;
    if (fstat(fd, &status) || !S_ISREG(status.st_mode) || !(status.st_mode & 0111) ||
        status.st_size <= 0 || status.st_size > 64 * 1024 * 1024) {
        close(fd);
        return false;
    }
    CC_SHA256_CTX hash;
    CC_SHA256_Init(&hash);
    unsigned char buffer[65536];
    off_t total = 0;
    ssize_t count;
    while ((count = read(fd, buffer, sizeof(buffer))) > 0) {
        CC_SHA256_Update(&hash, buffer, (CC_LONG)count);
        total += count;
    }
    close(fd);
    unsigned char digest[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256_Final(digest, &hash);
    return count == 0 && total == status.st_size && memcmp(digest, expected, sizeof(digest)) == 0;
}

static bool valid_bundle(NSString *path, const struct Application *app)
{
    if (!path) return false;
    NSString *infoPath = [path stringByAppendingPathComponent:@"Info.plist"];
    NSString *exePath = [path stringByAppendingPathComponent:@(app->executable)];
    struct stat directory, info;
    if (lstat(path.fileSystemRepresentation, &directory) || !S_ISDIR(directory.st_mode) ||
        lstat(infoPath.fileSystemRepresentation, &info) || !S_ISREG(info.st_mode) ||
        info.st_size <= 0 || info.st_size > 256 * 1024) return false;
    NSDictionary *manifest = [NSDictionary dictionaryWithContentsOfFile:infoPath];
    return [manifest[@"CFBundleIdentifier"] isEqual:@(app->identifier)] &&
           [manifest[@"CFBundleExecutable"] isEqual:@(app->executable)] &&
           application_digest(exePath.fileSystemRepresentation, app->digest);
}

static NSString *normalize_path(NSString *path)
{
    path = path.stringByResolvingSymlinksInPath;
    if ([path hasPrefix:@"/var/"]) return [@"/private" stringByAppendingString:path];
    return path;
}

static id read_state(Class proxyClass, NSString *identifier, NSString **path)
{
    *path = nil;
    id proxy = [proxyClass applicationProxyForIdentifier:identifier];
    NSURL *url = [proxy respondsToSelector:@selector(bundleURL)] ? [proxy bundleURL] : nil;
    if ([url isKindOfClass:NSURL.class] && url.isFileURL) *path = normalize_path(url.path);
    id state = [proxy respondsToSelector:@selector(appState)] ? [proxy appState] : nil;
    if (![state respondsToSelector:@selector(isValid)] || ![state respondsToSelector:@selector(isInstalled)] ||
        ![state respondsToSelector:@selector(isPlaceholder)]) return nil;
    return state;
}

static bool ready_state(id state)
{
    return [state isValid] && [state isInstalled] && ![state isPlaceholder];
}

static bool data_location(NSString *path, const struct Application *app)
{
    static NSString *const prefix = @"/private/var/containers/Bundle/Application/";
    if (![path hasPrefix:prefix]) return false;
    NSArray<NSString *> *parts = [[path substringFromIndex:prefix.length] componentsSeparatedByString:@"/"];
    return parts.count == 2 && [[NSUUID alloc] initWithUUIDString:parts[0]] != nil &&
           [parts[1] isEqual:@(app->bundle)];
}

static bool private_directory(NSString *path, bool make)
{
    const char *name = path.fileSystemRepresentation;
    if (make && mkdir(name, 0700) && errno != EEXIST) return false;
    struct stat status;
    return !lstat(name, &status) && S_ISDIR(status.st_mode) && status.st_uid == 501 &&
           !(status.st_mode & 0022);
}

static NSString *staged_bundle(NSString *source, NSString *parent, const struct Application *app)
{
    NSString *appParent = [parent stringByAppendingPathComponent:@(app->identifier)];
    if (!private_directory(appParent, true)) return nil;
    NSString *fixed = [appParent stringByAppendingPathComponent:@(app->bundle)];
    if (valid_bundle(fixed, app)) return fixed;
    // The fixed bundle may belong to an unresolved coordinator from the
    // last boot. Only the interrupted pre-transfer scratch is replaceable.
    struct stat status;
    if (!lstat(fixed.fileSystemRepresentation, &status) || errno != ENOENT) return nil;
    NSString *temp = [appParent stringByAppendingPathComponent:@".copying"];
    NSFileManager *files = NSFileManager.defaultManager;
    if (!lstat(temp.fileSystemRepresentation, &status) &&
        ![files removeItemAtPath:temp error:NULL]) return nil;
    int copied = copyfile(source.fileSystemRepresentation, temp.fileSystemRepresentation, NULL,
                          COPYFILE_ALL | COPYFILE_RECURSIVE | COPYFILE_NOFOLLOW_SRC | COPYFILE_NOFOLLOW_DST);
    if (copied || !valid_bundle(temp, app) ||
        renamex_np(temp.fileSystemRepresentation, fixed.fileSystemRepresentation, RENAME_EXCL)) {
        (void)[files removeItemAtPath:temp error:NULL];
        return nil;
    }
    return fixed;
}
static unsigned checkpoint(void)
{
    static const char prefix[] = "PONG APPS: CHECKPOINT next=";
    char tail[32769];
    off_t end = lseek(logFD, 0, SEEK_END);
    if (end <= 0) return 0;
    off_t begin = end > (off_t)(sizeof(tail) - 1) ? end - (off_t)(sizeof(tail) - 1) : 0;
    ssize_t count = pread(logFD, tail, sizeof(tail) - 1, begin);
    if (count <= 0) return 0;
    tail[count] = '\0';
    unsigned next = 0;
    for (const char *line = tail; (line = strstr(line, prefix)) != NULL; line += sizeof(prefix) - 1) {
        const char *value = line + sizeof(prefix) - 1;
        char *stop;
        unsigned long slot = strtoul(value, &stop, 10);
        if (stop != value && *stop == '\n' && slot < AppCount) next = (unsigned)slot;
    }
    return next;
}

static void pause_until_callbacks(void)
{
    NSTimeInterval before = NSProcessInfo.processInfo.systemUptime;
    [NSRunLoop.currentRunLoop runUntilDate:[NSDate dateWithTimeIntervalSinceNow:1]];
    NSTimeInterval remaining = 1 - (NSProcessInfo.processInfo.systemUptime - before);
    if (remaining > 0) [NSThread sleepForTimeInterval:remaining];
}

enum Result { Present, Installed, Unavailable, Pending };

static enum Result install_one(unsigned slot, Class proxy, id migrator, SEL install, NSString *stage)
{
    const struct Application *app = &apps[slot];
    NSString *appID = @(app->identifier);
    NSString *source = [@"/Applications" stringByAppendingPathComponent:@(app->bundle)];
    NSString *placeholder = [@"/System/Library/AppPlaceholders" stringByAppendingPathComponent:@(app->bundle)];
    NSString *path = nil;
    id state = read_state(proxy, appID, &path);
    // Any valid installed version is left alone, including a user update.
    if (ready_state(state)) {
        bool verified = ([path isEqual:source] || data_location(path,app)) && valid_bundle(path,app);
        receipt("PONG APPS: id=%s state=present stock_verified=%d path=%.260s\n",
                app->identifier, verified, path.UTF8String ?: "-");
        return Present;
    }
    if (![state isValid] || ![state isPlaceholder] || ![path isEqual:placeholder] ||
        !valid_bundle(source, app)) {
        receipt("PONG APPS: id=%s state=unavailable source_or_placeholder path=%.260s\n",
                app->identifier, path.UTF8String ?: "-");
        return Unavailable;
    }
    NSString *copy = staged_bundle(source, stage, app);
    if (!copy) {
        receipt("PONG APPS: id=%s state=unavailable staging errno=%d\n", app->identifier, errno);
        return Unavailable;
    }
    state = read_state(proxy, appID, &path);
    if (ready_state(state)) {
        receipt("PONG APPS: id=%s state=present appeared_during_staging\n", app->identifier);
        return Present;
    }
    if (![state isValid] || ![state isPlaceholder] || ![path isEqual:placeholder]) {
        receipt("PONG APPS: id=%s state=unavailable changed_placeholder\n", app->identifier);
        return Unavailable;
    }
    select_application(appID);
    receipt("PONG APPS: id=%s invoking stock installer source=%.260s\n", app->identifier, copy.UTF8String);
    BOOL scheduled = ((BOOL (*)(id, SEL, NSString *, NSURL *, BOOL))objc_msgSend)(
        migrator, install, appID, [NSURL fileURLWithPath:copy isDirectory:YES], NO);
    receipt("PONG APPS: id=%s native_scheduled=%d\n", app->identifier, scheduled);
    if (!scheduled && atomic_load(&sawFactory) && !atomic_load(&coordinatorHandedOut)) {
        select_application(nil);
        receipt("PONG APPS: id=%s state=unavailable native_declined_without_coordinator\n", app->identifier);
        return Unavailable;
    }
    NSTimeInterval cutoff = NSProcessInfo.processInfo.systemUptime + 16;
    do {
        @autoreleasepool {
            state = read_state(proxy, appID, &path);
            if (ready_state(state) && data_location(path,app)) {
                select_application(nil);
                receipt("PONG APPS: id=%s state=installed stock_verified=%d path=%.260s\n",
                        app->identifier, valid_bundle(path,app), path.UTF8String);
                return Installed;
            }
            pause_until_callbacks();
        }
    } while (NSProcessInfo.processInfo.systemUptime < cutoff);
    select_application(nil);
    receipt("PONG APPS: id=%s state=pending retained_for_next_sweep\n", app->identifier);
    return Pending;
}

static int batch(NSTimeInterval hardStop)
{
    NSDictionary *version = [NSDictionary dictionaryWithContentsOfFile:
        @"/System/Library/CoreServices/SystemVersion.plist"];
    if (![version[@"ProductBuildVersion"] isEqual:@"22H374"]) {
        receipt("PONG APPS: END unexpected OS build\n");
        return 65;
    }
    dlopen("/System/Library/Frameworks/CoreServices.framework/CoreServices", RTLD_NOW | RTLD_LOCAL);
    Class proxy = NSClassFromString(@"LSApplicationProxy");
    if (![proxy respondsToSelector:@selector(applicationProxyForIdentifier:)]) return 69;
    bool ready = false;
    for (unsigned i = 0; i < 17; ++i) {
        @autoreleasepool {
            NSString *path = nil;
            id state = read_state(proxy, @"com.apple.Preferences", &path);
            if (ready_state(state) && [path isEqual:@"/Applications/Preferences.app"]) {
                ready = true;
                break;
            }
            [NSThread sleepForTimeInterval:3];
        }
    }
    if (!ready) {
        receipt("PONG APPS: END mobile LaunchServices not ready\n");
        return 75;
    }
    NSError *error = nil;
    NSBundle *bundle = [NSBundle bundleWithPath:migratorPath];
    if (!bundle || ![bundle loadAndReturnError:&error]) {
        receipt("PONG APPS: END stock migrator load failed\n");
        log_error("load", error);
        return 69;
    }
    Class migratorClass = bundle.principalClass;
    SEL install = sel_registerName("installOrUpdateAppWithBundleID:appURL:isInstalled:");
    Method method = class_getInstanceMethod(migratorClass, install);
    const char *encoding = method ? method_getTypeEncoding(method) : NULL;
    if (migratorClass != objc_lookUpClass("MISystemAppMigrator") ||
        !encoding || strcmp(encoding, "B36@0:8@16@24B32")) {
        receipt("PONG APPS: END unexpected stock migrator ABI %.100s\n", encoding ?: "-");
        return 69;
    }
    __attribute__((objc_precise_lifetime)) id migrator = [[migratorClass alloc] init];
    if (!migrator) return 69;
    install_coordinator_observer();
    if (!nativeCoordinatorFactory) return 69;
    NSString *stage = [stageParent stringByAppendingPathComponent:@"ThorSystemApps-v129"];
    if (!private_directory(stageParent, false) || !private_directory(stage, true)) {
        receipt("PONG APPS: END private staging parent unavailable\n");
        return 65;
    }
    unsigned start = checkpoint(), results[4] = {0};
    for (unsigned i = 0; i < AppCount; ++i) {
        unsigned slot = (start + i) % AppCount;
        NSTimeInterval remaining = hardStop - NSProcessInfo.processInfo.systemUptime;
        if (remaining <= 1) break;
        alarm(remaining < AppSeconds ? (unsigned)remaining : AppSeconds);
        receipt("PONG APPS: CHECKPOINT next=%u\n",(slot + 1) % AppCount);
        if (fsync(logFD)) {
            receipt("PONG APPS: END checkpoint sync failed errno=%d\n",errno);
            return 74;
        }
        @autoreleasepool {
            enum Result result = install_one(slot, proxy, migrator, install, stage);
            ++results[result];
        }
        alarm(0);
    }
    receipt("PONG APPS: END present=%u installed=%u unavailable=%u pending=%u inventory=%u\n",
            results[Present], results[Installed], results[Unavailable], results[Pending], (unsigned)AppCount);
    return results[Present] + results[Installed] == AppCount ? 0 : 75;
}

int main(void)
{
    if (getuid() != 501 || geteuid() != 501) return 77;
    int fd = open(logPath, O_RDWR | O_APPEND | O_CREAT | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK, 0600);
    struct stat status;
    if (fd < 0 || fstat(fd, &status) || !S_ISREG(status.st_mode) || status.st_uid != 501 ||
        status.st_nlink != 1 || (status.st_mode & 0777) != 0600 ||
        status.st_size >= 2 * 1024 * 1024 || flock(fd, LOCK_EX | LOCK_NB)) {
        if (fd >= 0) close(fd);
        return 73;
    }
    logFD = fd;
    struct sigaction action = {0};
    action.sa_handler = deadline;
    if (sigemptyset(&action.sa_mask) || sigaction(SIGALRM, &action, NULL)) return 71;
    alarm(BatchSeconds);
    NSTimeInterval hardStop = NSProcessInfo.processInfo.systemUptime + BatchSeconds;
    receipt("PONG APPS: BEGIN v129 uid=%u inventory=%u\n",getuid(),(unsigned)AppCount);
    int result;
    @autoreleasepool { result = batch(hardStop); }
    alarm(0);
    (void)fsync(fd);
    close(fd);
    return result;
}
