/* SPDX-License-Identifier: MIT
 * iPadOS 18 runtime adapter. No 20G81 QuartzCore/Settings addresses or vtables.
 * Device input reuses the actual Pong kernel event queue and runtime HID APIs.
 */
#include <CoreFoundation/CoreFoundation.h>
#include <CoreGraphics/CoreGraphics.h>
#include <dispatch/dispatch.h>
#include <dlfcn.h>
#include <CommonCrypto/CommonDigest.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <libkern/OSCacheControl.h>
#include <mach/mach.h>
#include <mach/mach_time.h>
#include <os/lock.h>
#include <mach-o/dyld.h>
#include <mach-o/loader.h>
#include <objc/message.h>
#include <objc/runtime.h>
#include <pthread.h>
#include <signal.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/mount.h>
#include <sys/sysctl.h>
#include <sys/ucontext.h>
#include <time.h>
#include <unistd.h>
#include <notify.h>
#include "touch.h"
#if defined(PONG_THOR_APP_REGISTER)
#include <spawn.h>
#include <sys/file.h>
#include <sys/wait.h>
#endif

static bool software_composition;
static bool scanout_format;
static bool renderer_installed;
static bool format_installed;
static bool personas_installed;
static bool springboard_process;
static bool backboard_process;
static bool phone_ui_process;
static bool skip_setup;
static IMP setup_reason_original;
static bool activation_installed;
static ptrdiff_t activation_offset;
static bool launchservices_process, launchservices_installed;
static bool installd_process, installd_directory_installed;
static bool pluginkit_process, pluginkit_directory_attempted;
static bool fairplay_process;
static CFURLRef ls_base, ls_system, ls_user, ls_shared;
static CFURLRef installd_library;
static CFURLRef pluginkit_container;
static IMP battery_original;
static struct {Class cls;IMP original;} statusbar_factories[5];
static unsigned statusbar_factory_count;
static Class statusbar_provider;
static bool statusbar_fallback_bridge_installed;
static bool statusbar_diagnostic_item_removed;
static char statusbar_delegate_key;
static char statusbar_rebuild_key;
static IMP statusbar_fallback_get,statusbar_fallback_set,statusbar_fallback_responds,statusbar_fallback_setup_original;
static Class (*phone_selector_original)(id,id);
static bool phone_selector_installed;
static IMP phone_prepare_original;
static Ivar phone_provider_ivar;
static IMP phone_layout_original;
static bool (*phone_gestalt_bool_original)(CFStringRef);
static CFMutableDictionaryRef (*service_matching)(const char *);
static uint32_t (*matching_service)(uint32_t,CFDictionaryRef);
static int (*release_service)(uint32_t);
static CFURLRef container_root, container_library;
static void launchservices_directories(void);
static void installd_directory(void);
static void pluginkit_directory(void);
static void phone_selector_image(const struct mach_header *,intptr_t);
static void phone_gestalt_image(const struct mach_header *,intptr_t);
static void phone_repair_actual_bar(void *);
static void stui_repair_bar(id);
static bool boot_option(const char *option);
static bool own_directory(const char *path);
void pong_boot_snapshot_start(const char *name);
void pong_boot_console_start(void);
void pong_settings18_start(void);
#if defined(PONG_THOR_METAL_CLIENT_SMOKE)
void pong_preferences_metal_smoke_start(bool run_probe);
#endif
#if defined(PONG_THOR_SETTINGS_WATCHDOG)
void pong_settings_watchdog18_start(void (*receipt)(const char *, ...));
void pong_settings_watch_stack_start(void);
#endif
void pong_audio_volume18_start(void);

/* THOR v62: key receipts also go to /dev/console. Services spawned via
 * xpcproxy inherit stdio on /dev/null, so stderr-only receipts never reach
 * the photographed screen; v59 field evidence showed the console write
 * works for the mobile uid as well as root. */
void pong_console_line(const char *line);
static void pong_say(const char *format,...)
{
    char line[512];
    va_list arguments;
    va_start(arguments,format);
    vsnprintf(line,sizeof(line),format,arguments);
    va_end(arguments);
    fprintf(stderr,"%s\n",line);
    pong_console_line(line);
}

#if defined(PONG_THOR_APP_REGISTER)
#include "thor-app-exec.h"
static const char thor_app_directory[] = "/private/var/thor-exec";
static const char thor_app_helper[] = "/private/var/thor-exec/thor-apps-install-v129";

static bool thor_app_installer_matches(const struct stat *expected)
{
    int fd = open(thor_app_helper, O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK);
    if (fd < 0) return false;
    struct stat seen;
    bool valid = !fstat(fd, &seen) && seen.st_dev == expected->st_dev &&
        seen.st_ino == expected->st_ino && seen.st_size == THOR_INSTALLER_SIZE;
    unsigned char *bytes = valid ? malloc(THOR_INSTALLER_SIZE) : NULL;
    valid = bytes != NULL;
    size_t offset = 0;
    while (valid && offset < THOR_INSTALLER_SIZE) {
        ssize_t got = read(fd, bytes + offset, THOR_INSTALLER_SIZE - offset);
        if (got < 0 && errno == EINTR) continue;
        if (got <= 0) valid = false;
        else offset += (size_t)got;
    }
    if (valid) {
        unsigned char digest[CC_SHA256_DIGEST_LENGTH];
        CC_SHA256(bytes, THOR_INSTALLER_SIZE, digest);
        valid = memcmp(digest, thor_installer_sha256, sizeof(digest)) == 0;
    }
    free(bytes);
    close(fd);
    return valid;
}

static bool thor_app_installd_armed(void)
{
    char config[1024];
    size_t size = sizeof(config);
    if (sysctlbyname("kern.exec_inject", config, &size, NULL, 0) ||
        !size || size > sizeof(config) || config[size - 1] != '\0') return false;
    char *state = NULL;
    (void)strtok_r(config, " ", &state);
    for (char *name = strtok_r(NULL, " ", &state); name; name = strtok_r(NULL, " ", &state)) {
        if (!strcmp(name, "installd")) return true;
    }
    return false;
}

static void thor_app_spawn_receipt(const char *format, ...)
{
    char line[200];
    va_list arguments;
    va_start(arguments, format);
    int size = vsnprintf(line, sizeof(line), format, arguments);
    va_end(arguments);
    if (size <= 0 || (size_t)size >= sizeof(line)) return;
    if (getuid() == 501 && geteuid() == 501) {
        int fd = open("/private/var/mobile/Library/Logs/PongAppSpawn.txt",
                      O_WRONLY | O_APPEND | O_CREAT | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK, 0600);
        if (fd >= 0) {
            struct stat status;
            if (!fstat(fd, &status) && S_ISREG(status.st_mode) && status.st_uid == 501 &&
                status.st_nlink == 1 && (status.st_mode & 07777) == 0600 &&
                status.st_size < 256 * 1024) {
                line[size] = '\n';
                (void)write(fd, line, (size_t)size + 1);
                line[size] = '\0';
            }
            close(fd);
        }
    }
    if (sysctlbyname("kern.pong_diagnostic", NULL, NULL, line, (size_t)size) != 0)
        pong_console_line(line);
}

static bool thor_app_claim_boot(void)
{
    static const char path[] = "/private/var/mobile/Library/Logs/.PongAppRegisterBoot";
    char boot[37] = {0}, previous[sizeof(boot)];
    size_t length = sizeof(boot);
    if (sysctlbyname("kern.bootsessionuuid", boot, &length, NULL, 0) ||
        length != sizeof(boot) || boot[sizeof(boot)-1] != '\0' || strlen(boot) != sizeof(boot)-1) {
        thor_app_spawn_receipt("PONG APPS SPAWN: boot UUID unavailable");
        return false;
    }

    int fd = open(path, O_RDWR | O_CREAT | O_CLOEXEC | O_NOFOLLOW, 0600);
    if (fd < 0) {
        thor_app_spawn_receipt("PONG APPS SPAWN: claim open errno=%d", errno);
        return false;
    }
    struct stat status;
    if (fstat(fd, &status) || !S_ISREG(status.st_mode) || status.st_nlink != 1 ||
        status.st_uid != 501 || (status.st_mode & 0777) != 0600) {
        close(fd);
        thor_app_spawn_receipt("PONG APPS SPAWN: unsafe claim file");
        return false;
    }
    if (flock(fd, LOCK_EX | LOCK_NB)) {
        int error = errno;
        close(fd);
        if (error != EWOULDBLOCK)
            thor_app_spawn_receipt("PONG APPS SPAWN: claim lock errno=%d", error);
        return false;
    }
    ssize_t count = pread(fd, previous, sizeof(previous), 0);
    if (count == (ssize_t)sizeof(previous) && !memcmp(boot, previous, sizeof(boot))) {
        close(fd);
        return false;
    }
    if (count < 0 || pwrite(fd, boot, sizeof(boot), 0) != (ssize_t)sizeof(boot)) {
        int error = errno;
        close(fd);
        thor_app_spawn_receipt("PONG APPS SPAWN: claim write errno=%d", error);
        return false;
    }
    close(fd);
    return true;
}

static void thor_app_spawn(void *context)
{
    (void)context;
    if (getuid() != 501 || geteuid() != 501) {
        thor_app_spawn_receipt("PONG APPS SPAWN: unexpected uid=%u euid=%u", getuid(), geteuid());
        return;
    }
    struct stat directory;
    if (lstat(thor_app_directory, &directory)) {
        thor_app_spawn_receipt("PONG APPS SPAWN: installer directory stat errno=%d", errno);
        return;
    }
    if (!S_ISDIR(directory.st_mode) || directory.st_uid != 0 || directory.st_gid != 0 ||
        (directory.st_mode & 07777) != 0755 || access(thor_app_directory, W_OK) == 0) {
        thor_app_spawn_receipt("PONG APPS SPAWN: untrusted installer directory uid=%u gid=%u mode=%o",
                               directory.st_uid, directory.st_gid, directory.st_mode & 07777);
        return;
    }
    struct stat status;
    if (lstat(thor_app_helper, &status)) {
        thor_app_spawn_receipt("PONG APPS SPAWN: helper stat errno=%d", errno);
        return;
    }
    if (!S_ISREG(status.st_mode) || status.st_nlink != 1 ||
        status.st_uid != 0 || status.st_gid != 0 || status.st_dev != directory.st_dev ||
        (status.st_mode & 07777) != 0755) {
        thor_app_spawn_receipt("PONG APPS SPAWN: unexpected helper metadata uid=%u mode=%o",
                               status.st_uid, status.st_mode & 07777);
        return;
    }
    if (!thor_app_installer_matches(&status)) {
        thor_app_spawn_receipt("PONG APPS SPAWN: installer file SHA-256 mismatch");
        return;
    }
    if (!thor_app_installd_armed()) {
        thor_app_spawn_receipt("PONG APPS SPAWN: installd boot adapter unavailable");
        return;
    }
    if (!thor_app_claim_boot()) return;

    char *const argv[] = { (char *)thor_app_helper, NULL };
    char *const env[] = {
        "HOME=/var/mobile", "CFFIXED_USER_HOME=/var/mobile",
        "TMPDIR=/private/var/mobile/tmp", "PATH=/usr/bin:/bin:/usr/sbin:/sbin", NULL,
    };
    posix_spawnattr_t attributes;
    int result = posix_spawnattr_init(&attributes);
    if (result != 0) {
        thor_app_spawn_receipt("PONG APPS SPAWN: attr init errno=%d", result);
        return;
    }
    result = posix_spawnattr_setflags(&attributes, POSIX_SPAWN_CLOEXEC_DEFAULT);
    if (result != 0) {
        posix_spawnattr_destroy(&attributes);
        thor_app_spawn_receipt("PONG APPS SPAWN: attr flags errno=%d", result);
        return;
    }
    pid_t child = 0;
    result = posix_spawn(&child, thor_app_helper, NULL, &attributes, argv, env);
    posix_spawnattr_destroy(&attributes);
    thor_app_spawn_receipt("PONG APPS SPAWN: uid=%u errno=%d pid=%d", getuid(), result, child);
    if (result != 0 || child <= 0) return;

    int child_status;
    pid_t waited;
    do {
        waited = waitpid(child, &child_status, 0);
    } while (waited < 0 && errno == EINTR);
    if (waited < 0)
        thor_app_spawn_receipt("PONG APPS SPAWN: wait pid=%d errno=%d", child, errno);
    else if (WIFEXITED(child_status))
        thor_app_spawn_receipt("PONG APPS SPAWN: wait pid=%d exit=%d", child, WEXITSTATUS(child_status));
    else if (WIFSIGNALED(child_status))
        thor_app_spawn_receipt("PONG APPS SPAWN: wait pid=%d signal=%d", child, WTERMSIG(child_status));
    else
        thor_app_spawn_receipt("PONG APPS SPAWN: wait pid=%d status=0x%x", child, child_status);
}
#endif

/* fairplayd.A2 runtime evidence. Field data (v65/v66): A2 exits 0
 * within seconds, 36-43x per run. The 1029 iOS16 line hit the same
 * wall and its author attributed it to the missing kernel kext
 * com.apple.driver.FairPlayIOKit (present in Apple's stock 22H374
 * kernelcache as an IOResources software driver, absent from ours).
 * That remains a LEADING CANDIDATE, not a proven cause: client-side
 * waiting cannot distinguish "request never received" from "received
 * then blocked" from "received then exited without reply" - these
 * hooks exist to tell those apart. The __interpose pairs are
 * process-wide but inert in every other injected process: each wrapper
 * forwards the CORRECT contract immediately when the flag is off.
 * Under the two-level namespace our own calls to the originals bind
 * straight to libSystem, so no recursion. A2 runs as mobile
 * (UserName=mobile) and the root-owned 0600 PongDiag.txt is not
 * writable for it - every A2 line goes to its own mobile-owned,
 * collector-visible file instead. */
extern void pong_console_line(const char *line);
extern kern_return_t bootstrap_check_in(mach_port_t,char *,mach_port_t *);
typedef mach_port_t pong_io_object_t;
extern pong_io_object_t IOServiceGetMatchingService(mach_port_t,CFDictionaryRef);
extern kern_return_t IOServiceOpen(pong_io_object_t,mach_port_t,uint32_t,pong_io_object_t *);
#define PONG_INTERPOSE(tag,replacement,replacee) \
    __attribute__((used)) static const void *const pong_interpose_##tag[2] \
    __attribute__((section("__DATA,__interpose"))) = { \
        (const void *)&(replacement),(const void *)&(replacee) }

static void pong_a2_line(const char *text)
{
    char path[96];
    snprintf(path,sizeof(path),"/private/var/mobile/Library/Logs/PongA2-%d.txt",getpid());
    int fd=open(path,O_WRONLY|O_CREAT|O_APPEND|O_CLOEXEC,0600);
    if(fd>=0){
        dprintf(fd,"[%lld] pid=%d euid=%d %s\n",
                (long long)time(NULL),getpid(),geteuid(),text);
        close(fd);
    }else{
        char trouble[160];
        int n=snprintf(trouble,sizeof(trouble),
                       "Pong A2 log open errno=%d",errno);
        if(n>0)pong_console_line(trouble);
    }
    pong_console_line(text);
}

/* datamigrator diagnostics hook (see the constructor branch for the
 * firmware evidence). Receipts go to a per-pid mobile-owned file like
 * the A2 channel. */
static bool method_abi(Method method,const char *result,unsigned argc,const char *arguments);
static dispatch_queue_t dm_log_queue;
static unsigned dm_log_count;
static const unsigned dm_log_limit=512;

struct dm_log_entry {
    char line[640];
    size_t size;
};

static void pong_dm_write(void *context)
{
    struct dm_log_entry *entry=context;
    char path[128];
    const char *dir=geteuid()==0?"/private/var/logs":"/private/var/mobile/Library/Logs";
    snprintf(path,sizeof(path),"%s/PongDM-%d.txt",dir,getpid());
    int fd=open(path,O_WRONLY|O_CREAT|O_APPEND|O_CLOEXEC|O_NOFOLLOW,0600);
    if(fd>=0) {
        const char *position=entry->line;
        size_t remaining=entry->size;
        while(remaining) {
            ssize_t written=write(fd,position,remaining);
            if(written<0 && errno==EINTR)continue;
            if(written<=0)break;
            position+=written;
            remaining-=(size_t)written;
        }
        close(fd);
    }
    entry->line[entry->size-1]='\0';
    pong_console_line(entry->line);
    free(entry);
}

static void pong_dm_line(const char *format,...)
{
    const int saved_errno=errno;
    if(dm_log_queue && __atomic_fetch_add(&dm_log_count,1,__ATOMIC_RELAXED)<dm_log_limit) {
        struct dm_log_entry *entry=malloc(sizeof(*entry));
        if(entry) {
            char event[512];
            va_list args;
            va_start(args,format);
            vsnprintf(event,sizeof(event),format,args);
            va_end(args);
            uint64_t tid=0;
            (void)pthread_threadid_np(NULL,&tid);
            int length=snprintf(entry->line,sizeof(entry->line),"[%lld] pid=%d tid=%llu mono_ns=%llu %s\n",
                (long long)time(NULL),getpid(),(unsigned long long)tid,
                (unsigned long long)clock_gettime_nsec_np(CLOCK_MONOTONIC),event);
            if(length>0 && (size_t)length<sizeof(entry->line)) {
                entry->size=(size_t)length;
                dispatch_async_f(dm_log_queue,entry,pong_dm_write);
            } else free(entry);
        }
    }
    errno=saved_errno;
}

/* These observers run only in the enabled UI processes. The caller records a
 * small bounded receipt in memory and calls the real API with its original
 * errno. The existing snapshot worker owns all formatting and file writes. */
enum ui_observer_role { OBSERVER_OFF, OBSERVER_SB, OBSERVER_BBD };
enum ui_observer_call { OBSERVER_STAT, OBSERVER_LSTAT, OBSERVER_GROUP_WAIT };
enum ui_observer_state { SLOT_FREE, SLOT_RESERVED, SLOT_PENDING, SLOT_FINISHED };

#define UI_OBSERVER_SLOTS 64

struct ui_observer_slot {
    _Atomic(unsigned) state;
    _Atomic(unsigned) kind;
    _Atomic(uint64_t) call, tid, started, argument, timeout, caller;
    _Atomic(uint64_t) elapsed;
    _Atomic(long) result;
    _Atomic(int) error;
};

static struct ui_observer_slot ui_observer_slots[UI_OBSERVER_SLOTS];
static _Atomic(unsigned) ui_observer_role;
static _Atomic(uint64_t) ui_observer_next;
static _Atomic(unsigned) ui_observer_overflow;
static _Thread_local bool ui_observing;

static uint64_t ui_observer_ms(void)
{
    struct timespec now;
    if(clock_gettime(CLOCK_MONOTONIC,&now)!=0)return 0;
    return (uint64_t)now.tv_sec*1000+(uint64_t)now.tv_nsec/1000000;
}

/* Only a few startup receipts are expected. Reserve each slot once and let
 * the snapshot worker log it without putting the main queue on APFS. */
static struct {
    _Atomic(bool) ready;
    uint64_t time_ms;
    char text[512];
} sb_receipts[16];
static _Atomic(unsigned) sb_receipt_index;

static void pong_sb_note(const char *format,...)
{
    if(atomic_load_explicit(&ui_observer_role,memory_order_relaxed)!=OBSERVER_SB)return;
    int saved_errno=errno;
    unsigned i=atomic_fetch_add_explicit(&sb_receipt_index,1,memory_order_relaxed);
    if(i<sizeof(sb_receipts)/sizeof(sb_receipts[0])){
        va_list arguments;
        va_start(arguments,format);
        vsnprintf(sb_receipts[i].text,sizeof(sb_receipts[i].text),format,arguments);
        va_end(arguments);
        sb_receipts[i].time_ms=ui_observer_ms();
        atomic_store_explicit(&sb_receipts[i].ready,true,memory_order_release);
    }
    errno=saved_errno;
}

static void pong_sb_drain(int fd)
{
    static unsigned next;
    static bool overflow_reported;
    while(next<sizeof(sb_receipts)/sizeof(sb_receipts[0])&&
          atomic_load_explicit(&sb_receipts[next].ready,memory_order_acquire)){
        dprintf(fd,"Pong SB receipt: pid=%d time_ms=%llu %s\n",
                getpid(),sb_receipts[next].time_ms,sb_receipts[next].text);
        pong_console_line(sb_receipts[next].text);
        next++;
    }
    if(!overflow_reported&&atomic_load_explicit(&sb_receipt_index,memory_order_relaxed)>
       sizeof(sb_receipts)/sizeof(sb_receipts[0])){
        dprintf(fd,"Pong SB receipt budget exhausted: pid=%d\n",getpid());
        overflow_reported=true;
    }
}

static struct ui_observer_slot *ui_observer_begin(
    unsigned kind,uint64_t argument,uint64_t timeout,const void *caller)
{
    uint64_t call=atomic_fetch_add_explicit(&ui_observer_next,1,memory_order_relaxed)+1;
    unsigned first=(unsigned)(call%UI_OBSERVER_SLOTS);
    for(unsigned offset=0;offset<UI_OBSERVER_SLOTS;offset++){
        struct ui_observer_slot *slot=&ui_observer_slots[(first+offset)%UI_OBSERVER_SLOTS];
        unsigned free=SLOT_FREE;
        if(!atomic_compare_exchange_strong_explicit(&slot->state,&free,SLOT_RESERVED,
                                                    memory_order_acquire,memory_order_relaxed))
            continue;
        uint64_t tid=0;
        (void)pthread_threadid_np(NULL,&tid);
        atomic_store_explicit(&slot->call,call,memory_order_relaxed);
        atomic_store_explicit(&slot->kind,kind,memory_order_relaxed);
        atomic_store_explicit(&slot->tid,tid,memory_order_relaxed);
        atomic_store_explicit(&slot->argument,argument,memory_order_relaxed);
        atomic_store_explicit(&slot->timeout,timeout,memory_order_relaxed);
        atomic_store_explicit(&slot->caller,(uintptr_t)caller,memory_order_relaxed);
        atomic_store_explicit(&slot->started,ui_observer_ms(),memory_order_relaxed);
        atomic_store_explicit(&slot->state,SLOT_PENDING,memory_order_release);
        return slot;
    }
    atomic_fetch_add_explicit(&ui_observer_overflow,1,memory_order_relaxed);
    return NULL;
}

static void ui_observer_end(struct ui_observer_slot *slot,long result,int error,
                            unsigned threshold,bool retain_failure)
{
    if(!slot)return;
    uint64_t started=atomic_load_explicit(&slot->started,memory_order_relaxed);
    uint64_t now=ui_observer_ms();
    uint64_t elapsed=started&&now>=started?now-started:0;
    if(elapsed<threshold&&!(retain_failure&&result!=0)){
        atomic_store_explicit(&slot->state,SLOT_FREE,memory_order_release);
        return;
    }
    atomic_store_explicit(&slot->result,result,memory_order_relaxed);
    atomic_store_explicit(&slot->error,error,memory_order_relaxed);
    atomic_store_explicit(&slot->elapsed,elapsed,memory_order_relaxed);
    atomic_store_explicit(&slot->state,SLOT_FINISHED,memory_order_release);
}

/* Read only from the sampler and stay inside the first pointer page.
 * An untrusted pointer or a disappearing mapping must not fault the UI. */
static void ui_observer_path(uint64_t argument,char *path,size_t size)
{
    if(!argument||!vm_page_size)return;
    vm_size_t available=vm_page_size-argument%vm_page_size;
    vm_size_t limit=available<size-1?available:size-1;
    vm_size_t got=0;
    if(vm_read_overwrite(mach_task_self(),(vm_address_t)argument,limit,
                         (vm_address_t)path,&got)!=KERN_SUCCESS||got>limit){
        path[0]='\0';
        return;
    }
    path[got]='\0';
    for(vm_size_t i=0;i<got&&path[i];i++){
        unsigned char c=(unsigned char)path[i];
        if(c<32||c>=127||c=='"')path[i]='?';
    }
}

static void ui_observer_write(int fd,const struct ui_observer_slot *slot,unsigned state,
                              uint64_t call,uint64_t now,uint64_t *reported)
{
    static const char *const labels[]={"stat","lstat","group_wait"};
    unsigned kind=atomic_load_explicit(&slot->kind,memory_order_relaxed);
    uint64_t started=atomic_load_explicit(&slot->started,memory_order_relaxed);
    uint64_t tid=atomic_load_explicit(&slot->tid,memory_order_relaxed);
    uint64_t argument=atomic_load_explicit(&slot->argument,memory_order_relaxed);
    uint64_t timeout=atomic_load_explicit(&slot->timeout,memory_order_relaxed);
    uint64_t caller=atomic_load_explicit(&slot->caller,memory_order_relaxed);
    if(state==SLOT_PENDING&&(!started||now<started||now-started<150||*reported==call))return;

    /* An in-flight fast call may return and free/reuse the slot while we
     * sample it. Recheck state and generation after loading the fields. */
    if(atomic_load_explicit(&slot->state,memory_order_acquire)!=state||
       atomic_load_explicit(&slot->call,memory_order_relaxed)!=call||
       kind>=sizeof(labels)/sizeof(labels[0]))return;
    if(state==SLOT_PENDING){
        char path[96]={0};
        if(kind!=OBSERVER_GROUP_WAIT)ui_observer_path(argument,path,sizeof(path));
        if(atomic_load_explicit(&slot->state,memory_order_acquire)!=SLOT_PENDING||
           atomic_load_explicit(&slot->call,memory_order_relaxed)!=call)return;
        *reported=call;
        dprintf(fd,"Pong UI call pending: pid=%d tid=%llu id=%llu op=%s start_ms=%llu now_ms=%llu arg=0x%llx timeout=%llu caller=0x%llx path_prefix=\"%s\"\n",
                getpid(),tid,call,labels[kind],started,now,argument,timeout,caller,path);
    }else{
        uint64_t elapsed=atomic_load_explicit(&slot->elapsed,memory_order_relaxed);
        long result=atomic_load_explicit(&slot->result,memory_order_relaxed);
        int error=atomic_load_explicit(&slot->error,memory_order_relaxed);
        dprintf(fd,"Pong UI call end: pid=%d tid=%llu id=%llu op=%s start_ms=%llu elapsed_ms=%llu arg=0x%llx timeout=%llu caller=0x%llx result=%ld errno=%d\n",
                getpid(),tid,call,labels[kind],started,elapsed,argument,timeout,caller,result,error);
    }
}

/* Single consumer: the process-local snapshot worker. Completed stat paths
 * retain only their addresses because the caller may already have freed them. */
void pong_ui_observer_drain(int fd)
{
    unsigned role=atomic_load_explicit(&ui_observer_role,memory_order_relaxed);
    if(!role)return;
    if(role==OBSERVER_SB)pong_sb_drain(fd);
    static uint64_t reported[UI_OBSERVER_SLOTS];
    static unsigned budget=900, overflow_reported;
    uint64_t now=ui_observer_ms();
    for(unsigned i=0;i<UI_OBSERVER_SLOTS;i++){
        struct ui_observer_slot *slot=&ui_observer_slots[i];
        unsigned state=atomic_load_explicit(&slot->state,memory_order_acquire);
        if(state!=SLOT_PENDING&&state!=SLOT_FINISHED)continue;
        uint64_t call=atomic_load_explicit(&slot->call,memory_order_relaxed);
        if(budget&&(state==SLOT_FINISHED||reported[i]!=call)){
            ui_observer_write(fd,slot,state,call,now,&reported[i]);
            if(state==SLOT_FINISHED||reported[i]==call)budget--;
        }
        if(state==SLOT_FINISHED)atomic_store_explicit(&slot->state,SLOT_FREE,memory_order_release);
    }
    unsigned overflow=atomic_load_explicit(&ui_observer_overflow,memory_order_relaxed);
    if(budget&&overflow_reported!=overflow){
        dprintf(fd,"Pong UI call overflow: pid=%d count=%u\n",getpid(),overflow);
        overflow_reported=overflow;
        budget--;
    }
    static bool announced_exhaustion;
    if(!budget&&!announced_exhaustion){
        dprintf(fd,"Pong UI call observation budget exhausted: pid=%d\n",getpid());
        announced_exhaustion=true;
    }
}

static int pong_observed_stat(unsigned kind,int (*original)(const char *,struct stat *),
                              const char *path,struct stat *st,const void *caller)
{
    if(atomic_load_explicit(&ui_observer_role,memory_order_relaxed)!=OBSERVER_SB)
        return original(path,st);
    int incoming_errno=errno;
    if(ui_observing){
        errno=incoming_errno;
        return original(path,st);
    }
    ui_observing=true;
    struct ui_observer_slot *slot=ui_observer_begin(kind,(uintptr_t)path,0,caller);
    errno=incoming_errno;
    int result=original(path,st);
    int saved_errno=errno;
    ui_observer_end(slot,result,result?saved_errno:0,20,false);
    ui_observing=false;
    errno=saved_errno;
    return result;
}

static int pong_sb_lstat(const char *path,struct stat *st)
{
    return pong_observed_stat(OBSERVER_LSTAT,lstat,path,st,__builtin_return_address(0));
}

static int pong_sb_stat(const char *path,struct stat *st)
{
    return pong_observed_stat(OBSERVER_STAT,stat,path,st,__builtin_return_address(0));
}

static long pong_bbd_group_wait(dispatch_group_t group,dispatch_time_t timeout)
{
    if(atomic_load_explicit(&ui_observer_role,memory_order_relaxed)!=OBSERVER_BBD)
        return dispatch_group_wait(group,timeout);
    int incoming_errno=errno;
    if(ui_observing){
        errno=incoming_errno;
        return dispatch_group_wait(group,timeout);
    }
    ui_observing=true;
    struct ui_observer_slot *slot=ui_observer_begin(OBSERVER_GROUP_WAIT,(uintptr_t)group,
                                                   timeout,__builtin_return_address(0));
    errno=incoming_errno;
    long result=dispatch_group_wait(group,timeout);
    int saved_errno=errno;
    ui_observer_end(slot,result,saved_errno,50,true);
    ui_observing=false;
    errno=saved_errno;
    return result;
}

PONG_INTERPOSE(sb_lstat,pong_sb_lstat,lstat);
PONG_INTERPOSE(sb_stat,pong_sb_stat,stat);
PONG_INTERPOSE(group_wait,pong_bbd_group_wait,dispatch_group_wait);

static bool dm_diagnostics_swizzled;
static bool dm_progress_swizzled,dm_progress_enabled;
static IMP dm_capture_original;
static IMP dm_will_run_original,dm_did_finish_original,dm_results_original;
static SEL dm_plugin_id,dm_plugin_name,dm_plugin_dependency,dm_incident_kind,dm_incident_details;

static SEL pong_dm_getter(Class cls,const char *name,const char *result)
{
    SEL selector=sel_registerName(name);
    if(method_abi(cls?class_getInstanceMethod(cls,selector):NULL,result,0,""))
        return selector;
    return NULL;
}

static void pong_dm_text(id value,char *text,size_t size)
{
    if(!value || CFGetTypeID((CFTypeRef)value)!=CFStringGetTypeID() ||
       !CFStringGetCString((CFStringRef)value,text,size,kCFStringEncodingUTF8))
        strlcpy(text,"-",size);
    for(char *c=text;*c;c++) {
        if((unsigned char)*c<32 || *c=='"')*c=' ';
    }
}

static void pong_dm_get_text(id value,SEL selector,char *text,size_t size)
{
    pong_dm_text(value && selector?((id(*)(id,SEL))objc_msgSend)(value,selector):nil,text,size);
}

static unsigned pong_dm_kind(id incident)
{
    return incident && dm_incident_kind
        ? ((unsigned(*)(id,SEL))objc_msgSend)(incident,dm_incident_kind) : 0;
}

static void pong_dm_will_run(id self,SEL command,id plugin,id phase)
{
    const int saved_errno=errno;
    char identifier[96],name[96],dependency[96];
    pong_dm_get_text(plugin,dm_plugin_id,identifier,sizeof(identifier));
    pong_dm_get_text(plugin,dm_plugin_name,name,sizeof(name));
    pong_dm_get_text(plugin,dm_plugin_dependency,dependency,sizeof(dependency));
    pong_dm_line("Pong DM: plugin begin id=\"%s\" name=\"%s\" dependency=\"%s\"",identifier,name,dependency);
    errno=saved_errno;
    ((void(*)(id,SEL,id,id))dm_will_run_original)(self,command,plugin,phase);
}

static void pong_dm_did_finish(id self,SEL command,id plugin,BOOL success,double duration,id incident,id phase)
{
    ((void(*)(id,SEL,id,BOOL,double,id,id))dm_did_finish_original)(self,command,plugin,success,duration,incident,phase);
    const int saved_errno=errno;
    char identifier[96],details[192];
    pong_dm_get_text(plugin,dm_plugin_id,identifier,sizeof(identifier));
    pong_dm_get_text(incident,dm_incident_details,details,sizeof(details));
    pong_dm_line("Pong DM: plugin end id=\"%s\" success=%d duration_s=%.3f incident=%u details=\"%s\"",
        identifier,(int)success,duration,pong_dm_kind(incident),details);
    errno=saved_errno;
}

static void pong_dm_results(id self,SEL command,BOOL success,id build)
{
    ((void(*)(id,SEL,BOOL,id))dm_results_original)(self,command,success,build);
    const int saved_errno=errno;
    char version[32];
    pong_dm_text(build,version,sizeof(version));
    pong_dm_line("Pong DM: client results sent success=%d build=\"%s\"",(int)success,version);
    errno=saved_errno;
}

static void pong_dm_progress_install(void)
{
    if(!dm_progress_enabled || dm_progress_swizzled)return;
    Class state=objc_lookUpClass("DMMigrationState");
    Class plugin=objc_lookUpClass("DataMigrationPlugin");
    Class incident=objc_lookUpClass("DMIncident");
    if(!state || !plugin || !incident)return;
    Method begin=class_getInstanceMethod(state,sel_registerName("pluginWillRun:migrationPhaseDescription:"));
    Method end=class_getInstanceMethod(state,sel_registerName("pluginDidFinish:withSuccess:duration:incident:migrationPhaseDescription:"));
    Method results=class_getInstanceMethod(state,sel_registerName("sendMigrationResultsToClientInvocationsWithSuccess:buildVersion:"));
    /* 22H374 method metadata: v32@0:8@16@24, v52@0:8@16B24d28@36@44,
     * v28@0:8B16@20. Forward the real outcomes and arguments unchanged. */
    if(!method_abi(begin,"v",2,"@@") || !method_abi(end,"v",5,"@Bd@@") ||
       !method_abi(results,"v",2,"B@"))return;
    dm_plugin_id=pong_dm_getter(plugin,"identifier","@");
    dm_plugin_name=pong_dm_getter(plugin,"name","@");
    dm_plugin_dependency=pong_dm_getter(plugin,"identifierOfDependency","@");
    dm_incident_kind=pong_dm_getter(incident,"kind","I");
    dm_incident_details=pong_dm_getter(incident,"details","@");
    if(!dm_plugin_id)return;
    dm_will_run_original=method_getImplementation(begin);
    dm_did_finish_original=method_getImplementation(end);
    dm_results_original=method_getImplementation(results);
    method_setImplementation(begin,(IMP)pong_dm_will_run);
    method_setImplementation(end,(IMP)pong_dm_did_finish);
    method_setImplementation(results,(IMP)pong_dm_results);
    dm_progress_swizzled=true;
    pong_dm_line("Pong DM: native plugin and client result receipts enabled");
}

static void pong_dm_capture(id self,SEL command,id incident,BOOL async)
{
    const int saved_errno=errno;
    char details[192];
    pong_dm_get_text(incident,dm_incident_details,details,sizeof(details));
    pong_dm_line("Pong DM: diagnostics incident=%u async=%d -> forcing async=1 details=\"%s\"",
        pong_dm_kind(incident),(int)async,details);
    errno=saved_errno;
    if(dm_capture_original)
        ((void(*)(id,SEL,id,BOOL))dm_capture_original)(self,command,incident,YES);
}

static void pong_dm_image(const struct mach_header *header,intptr_t slide)
{
    (void)header;(void)slide;
    pong_dm_progress_install();
    if(dm_diagnostics_swizzled)return;
    Class cls=objc_lookUpClass("DMDiagnostics");
    if(!cls)return;
    Method method=class_getInstanceMethod(cls,
        sel_registerName("captureDiagnosticsForIncident:async:"));
    if(!method_abi(method,"v",2,"@B"))return;
    dm_capture_original=method_getImplementation(method);
    method_setImplementation(method,(IMP)pong_dm_capture);
    dm_diagnostics_swizzled=true;
    pong_dm_line("Pong DM: diagnostics swizzled to the native async path");
}

static void dm_diagnostics_install(bool progress_receipts)
{
    dm_log_queue=dispatch_queue_create("com.pong.datamigrator.receipts",DISPATCH_QUEUE_SERIAL);
    dm_progress_enabled=progress_receipts && dm_log_queue;
    if(!dm_diagnostics_swizzled)
        _dyld_register_func_for_add_image(pong_dm_image);
}

static void fairplay_exit_site(int status,const char *kind)
{
    char line[560];
    int n=snprintf(line,sizeof(line),"Pong A2: %s(%d) site:",kind,status);
    void *frame=__builtin_frame_address(0);
    for(unsigned i=0;n>0&&(size_t)n<sizeof(line)-24&&frame&&i<12;i++) {
        void **pair=(void **)frame;
        uintptr_t site=(uintptr_t)pair[1];
        /* v66 bug: the filter was written at 1-3 TiB while real user
         * code addresses (executable, cache, this library) sit between
         * 4 and 12 GiB - every frame was dropped. Bound to that range
         * and stop at the first implausible value. */
        if(site<0x100000000ULL||site>=0x300000000ULL)break;
        n+=snprintf(line+n,sizeof(line)-(size_t)n," %llx",
                    (unsigned long long)site);
        frame=pair[0];
    }
    if(n>0)pong_a2_line(line);
}

static void pong_a2_exit(int status)
{
    if(fairplay_process)fairplay_exit_site(status,"exit");
    exit(status);
    while(1);
}
static void pong_a2_exit_raw(int status)
{
    if(fairplay_process)fairplay_exit_site(status,"_exit");
    _exit(status);
    while(1);
}
static kern_return_t pong_a2_check_in(mach_port_t port,char *service,
                                      mach_port_t *service_port)
{
    /* v66 bug fixed here: the contract is (bootstrap_port, name,
     * mach_port_t *sp) and the pair installs in every injected process;
     * v69 adds begin/end receipts so a hang inside the call is visible. */
    if(fairplay_process){
        char begin[192];
        int b=snprintf(begin,sizeof(begin),"Pong A2: bootstrap_check_in %s begin",
                       service?service:"(null)");
        if(b>0)pong_a2_line(begin);
    }
    kern_return_t result=bootstrap_check_in(port,service,service_port);
    if(fairplay_process){
        char line[256];
        int n=snprintf(line,sizeof(line),
                       "Pong A2: bootstrap_check_in %s -> %d sp=%u",
                       service?service:"(null)",result,
                       (service_port&&result==KERN_SUCCESS)?*service_port:0);
        if(n>0)pong_a2_line(line);
    }
    return result;
}
static pong_io_object_t pong_a2_matching(mach_port_t port,CFDictionaryRef matching)
{
    /* IOServiceGetMatchingService CONSUMES the dictionary reference, so
     * every string we want to log must be copied out BEFORE the call. */
    char text[3][64]={{0},{0},{0}};
    if(matching){
        static const char *keys[3]={"IOProviderClass","IOClass","IOUserClientClass"};
        for(unsigned k=0;k<3;k++){
            CFStringRef key=CFStringCreateWithCString(NULL,keys[k],
                kCFStringEncodingASCII);
            CFTypeRef value=key?CFDictionaryGetValue(matching,key):NULL;
            if(key)CFRelease(key);
            if(value&&CFGetTypeID(value)==CFStringGetTypeID())
                CFStringGetCString(value,text[k],sizeof(text[k]),
                                   kCFStringEncodingUTF8);
        }
    }
    pong_io_object_t service=IOServiceGetMatchingService(port,matching);
    if(fairplay_process){
        char line[320];
        int n=snprintf(line,sizeof(line),
                       "Pong A2: matching provider=%s class=%s uc=%s -> service=%u",
                       text[0][0]?text[0]:"-",text[1][0]?text[1]:"-",
                       text[2][0]?text[2]:"-",service);
        if(n>0)pong_a2_line(line);
    }
    return service;
}
static kern_return_t pong_a2_open(pong_io_object_t service,mach_port_t owner,
                                  uint32_t type,pong_io_object_t *connect)
{
    if(fairplay_process){
        char begin[160];
        int b=snprintf(begin,sizeof(begin),
                       "Pong A2: IOServiceOpen begin service=%u type=%u",service,type);
        if(b>0)pong_a2_line(begin);
    }
    kern_return_t result=IOServiceOpen(service,owner,type,connect);
    if(fairplay_process){
        char line[192];
        int n=snprintf(line,sizeof(line),
            "Pong A2: IOServiceOpen service=%u type=%u -> %d%s%u",
            service,type,result,
            result==KERN_SUCCESS?" connect=":" (output invalid)",
            (result==KERN_SUCCESS&&connect)?*connect:0);
        if(n>0)pong_a2_line(line);
    }
    return result;
}
extern pong_io_object_t IOServiceGetMatchingServices(mach_port_t,CFDictionaryRef,pong_io_object_t *);
extern CFMutableDictionaryRef IOServiceMatching(const char *);
static kern_return_t pong_a2_matchings(mach_port_t port,CFDictionaryRef matching,
                                        pong_io_object_t *iterator)
{
    /* A2 actually calls the PLURAL variant; it consumes the dictionary
     * exactly like the singular one, so copy strings before the call. */
    char text[3][64]={{0},{0},{0}};
    if(fairplay_process)
        pong_a2_line("Pong A2: matchings begin");
    if(matching){
        static const char *keys[3]={"IOProviderClass","IOClass","IOUserClientClass"};
        for(unsigned k=0;k<3;k++){
            CFStringRef key=CFStringCreateWithCString(NULL,keys[k],
                kCFStringEncodingASCII);
            CFTypeRef value=key?CFDictionaryGetValue(matching,key):NULL;
            if(key)CFRelease(key);
            if(value&&CFGetTypeID(value)==CFStringGetTypeID())
                CFStringGetCString(value,text[k],sizeof(text[k]),
                                   kCFStringEncodingUTF8);
        }
    }
    kern_return_t result=IOServiceGetMatchingServices(port,matching,iterator);
    if(fairplay_process){
        char line[320];
        int n=snprintf(line,sizeof(line),
                       "Pong A2: matchings provider=%s class=%s uc=%s -> %d iter=%u",
                       text[0][0]?text[0]:"-",text[1][0]?text[1]:"-",
                       text[2][0]?text[2]:"-",
                       result,(result==KERN_SUCCESS&&iterator)?*iterator:0);
        if(n>0)pong_a2_line(line);
    }
    return result;
}
static CFMutableDictionaryRef pong_a2_matching_dict(const char *name)
{
    CFMutableDictionaryRef dictionary=IOServiceMatching(name);
    if(fairplay_process){
        char line[192];
        int n=snprintf(line,sizeof(line),"Pong A2: IOServiceMatching %s -> %p",
                       name?name:"(null)",dictionary);
        if(n>0)pong_a2_line(line);
    }
    return dictionary;
}
PONG_INTERPOSE(exit,pong_a2_exit,exit);
PONG_INTERPOSE(raw_exit,pong_a2_exit_raw,_exit);
PONG_INTERPOSE(check_in,pong_a2_check_in,bootstrap_check_in);
PONG_INTERPOSE(matching,pong_a2_matching,IOServiceGetMatchingService);
PONG_INTERPOSE(matchings,pong_a2_matchings,IOServiceGetMatchingServices);
PONG_INTERPOSE(matchdict,pong_a2_matching_dict,IOServiceMatching);
PONG_INTERPOSE(open,pong_a2_open,IOServiceOpen);

/* v69: the v68 in-process walker was wrong six ways (external review:
 * __PAGEZERO taken as base (+4 GiB), pointer format 6 rejected, slots
 * already resolved by dyld, mangled import names unmatched, dlsym
 * assumptions, and success receipts without readback). It is replaced
 * by an OFFLINE-parsed, UUID-locked slot table generated from the
 * shipped /usr/sbin/fairplayd.A2 (parser: thor tools; addend
 * 0x77dc76ff for _IOServiceOpen matches the firmware review). At
 * runtime: find the image by UUID, verify the slot really holds
 * original+addend, patch to wrapper+addend, read back, and report each
 * step. exit/_exit are NOT in A2 import table - the exit call comes
 * from libdyld's start glue - so exit evidence stays with the watcher
 * kqueue. Pages are left RW after patching: the kernel may still be
 * page-in-linking unapplied chains in __DATA_CONST. */
static const struct { uint64_t vm; unsigned hook; uint64_t addend; }
pong_a2_slots[]={
    {0x101138a48ULL,4,0x6da5e697ULL},   /* _IOServiceGetMatchingServices */
    {0x10113cda8ULL,2,0x6c4fc59bULL},   /* _bootstrap_check_in */
    {0x10113d208ULL,6,0x77dc76ffULL},   /* _IOServiceOpen */
    {0x10113ff90ULL,5,0x69a8ca62ULL},   /* _IOServiceMatching */
};
static const unsigned char pong_a2_uuid[16]={
    0x2c,0x3c,0x86,0x43,0x26,0x12,0x30,0xa5,
    0x8f,0xdf,0xc6,0x26,0x88,0x4d,0x86,0x76};

static bool pong_mach_uuid(const struct mach_header *header,unsigned char out[16])
{
    if(!header||header->magic!=MH_MAGIC_64||header->sizeofcmds>0x400000)return false;
    const struct load_command *scan=(const void *)((const uint8_t *)header+sizeof(struct mach_header_64));
    for(uint32_t c=0;c<header->ncmds;c++){
        if(scan->cmdsize<sizeof(*scan))return false;
        if(scan->cmd==LC_UUID&&scan->cmdsize>=sizeof(struct uuid_command)){
            memcpy(out,((const struct uuid_command *)scan)->uuid,16);
            return true;
        }
        scan=(const void *)((const uint8_t *)scan+scan->cmdsize);
    }
    return false;
}

static void pong_a2_apply_slots(void)
{
    const struct mach_header *header=NULL;
    unsigned char uuid[16];
    for(uint32_t i=0,n=_dyld_image_count();i<n;i++){
        const struct mach_header *candidate=_dyld_get_image_header(i);
        if(pong_mach_uuid(candidate,uuid)&&
           !memcmp(uuid,pong_a2_uuid,sizeof uuid)){
            header=candidate;
            break;
        }
    }
    if(!header){
        pong_a2_line("Pong A2: slot patch: UUID image not found");
        return;
    }
    /* A2 __TEXT vmaddr is 0x100000000; the slide follows from the header.
     * v69 bug: dlsym(RTLD_DEFAULT) returned OUR wrapper (interposition
     * applies to dlsym), so every slot verified as MISMATCH against
     * wrapper+addend (field data: 128 mismatches, 32x "0 of 4", with the
     * reconstructed base matching _pong_a2_open exactly). The ORIGINAL
     * address must come from this dylib's own two-level bindings, which
     * interposition does not redirect: the address of each extern
     * reference below is libSystem/IOKit's real function. */
    intptr_t slide=(intptr_t)((const uint8_t *)header)-0x100000000LL;
    struct { const char *name; void *wrapper; void *original; } hooks[]={
        {"exit",(void *)pong_a2_exit,(void *)&exit},
        {"_exit",(void *)pong_a2_exit_raw,(void *)&_exit},
        {"bootstrap_check_in",(void *)pong_a2_check_in,(void *)&bootstrap_check_in},
        {"IOServiceGetMatchingService",(void *)pong_a2_matching,(void *)&IOServiceGetMatchingService},
        {"IOServiceGetMatchingServices",(void *)pong_a2_matchings,(void *)&IOServiceGetMatchingServices},
        {"IOServiceMatching",(void *)pong_a2_matching_dict,(void *)&IOServiceMatching},
        {"IOServiceOpen",(void *)pong_a2_open,(void *)&IOServiceOpen},
    };
    unsigned patched=0;
    for(unsigned s=0;s<sizeof(pong_a2_slots)/sizeof(pong_a2_slots[0]);s++){
        const char *name=hooks[pong_a2_slots[s].hook].name;
        void *wrapper=hooks[pong_a2_slots[s].hook].wrapper;
        uintptr_t original=(uintptr_t)hooks[pong_a2_slots[s].hook].original;
        uintptr_t addend=(uintptr_t)pong_a2_slots[s].addend;
        uintptr_t address=(uintptr_t)((int64_t)pong_a2_slots[s].vm+slide);
        uintptr_t expected=original+addend;
        uintptr_t current=0;
        vm_size_t got=0;
        char line[256];
        if(vm_read_overwrite(mach_task_self(),(vm_address_t)address,
                             sizeof current,(vm_address_t)&current,&got)!=KERN_SUCCESS||
           got!=sizeof current){
            snprintf(line,sizeof(line),"Pong A2: slot %s read failed",name);
            pong_a2_line(line);
            continue;
        }
        if(current!=expected){
            snprintf(line,sizeof(line),
                     "Pong A2: slot %s addend=0x%llx MISMATCH current=0x%llx expected=0x%llx",
                     name,(unsigned long long)addend,
                     (unsigned long long)current,(unsigned long long)expected);
            pong_a2_line(line);
            continue;
        }
        uintptr_t page=address&~(uintptr_t)(vm_page_size-1);
        if(vm_protect(mach_task_self(),(vm_address_t)page,vm_page_size,FALSE,
                      VM_PROT_READ|VM_PROT_WRITE|VM_PROT_COPY)!=KERN_SUCCESS){
            snprintf(line,sizeof(line),
                     "Pong A2: slot %s protect failed errno-style kr=%d",
                     name,(int)KERN_FAILURE);
            pong_a2_line(line);
            continue;
        }
        *(uintptr_t *)address=(uintptr_t)wrapper+addend;
        uintptr_t readback=0;
        got=0;
        if(vm_read_overwrite(mach_task_self(),(vm_address_t)address,
                             sizeof readback,(vm_address_t)&readback,&got)!=KERN_SUCCESS||
           got!=sizeof readback||readback!=(uintptr_t)wrapper+addend){
            snprintf(line,sizeof(line),"Pong A2: slot %s readback FAILED",name);
            pong_a2_line(line);
            continue;
        }
        patched++;
        snprintf(line,sizeof(line),
                 "Pong A2: slot %s addend=0x%llx verified patched readback-ok",
                 name,(unsigned long long)addend);
        pong_a2_line(line);
    }
    char summary[96];
    snprintf(summary,sizeof(summary),
             "Pong A2: slot patch done %u of %u",patched,
             (unsigned)(sizeof(pong_a2_slots)/sizeof(pong_a2_slots[0])));
    pong_a2_line(summary);
}

static char abi_type_kind(const char *type)
{
    /* Objective-C qualifiers describe ownership/direction, not the register
     * ABI. Darwin24 declares NSError ** as o^@; checking its first byte
     * against '^' silently disabled the PosterBoard storage adapter. */
    if(!type)return 0;
    while(*type && strchr("rnNoORV",*type))type++;
    return *type;
}
static bool method_abi(Method method,const char *result,unsigned argc,const char *arguments)
{
    if(!method || method_getNumberOfArguments(method)!=argc+2)return false;
    char *type=method_copyReturnType(method);
    char kind=abi_type_kind(type);
    bool valid=kind && strchr(result,kind);
    free(type);
    for(unsigned i=0;valid && i<argc;i++) {
        type=method_copyArgumentType(method,i+2);
        valid=abi_type_kind(type)==arguments[i];
        free(type);
    }
    return valid;
}

/* The adapted launch cache has one canonical LaunchDaemons directory. That
 * loses launchd's Angel classification for PosterBoard: RunningBoard chose
 * the SystemXPCService 6 MiB defaults instead of its stock Angel policy.
 * Use an identity view ONLY for this synchronous policy lookup. The original
 * identity, bundle, PID, entitlements and service registration stay native.
 * RunningBoard still constructs its own limits from the matching board plist.
 */
static IMP poster_jetsam_original;
static Class poster_jetsam_identity;
static char poster_jetsam_source_key;
static id poster_jetsam_source(id self)
{ return objc_getAssociatedObject(self,&poster_jetsam_source_key); }
static unsigned char poster_jetsam_service_type(id self,SEL selector)
{ (void)self;(void)selector;return 3; /* native osServiceType: Angel */ }
static BOOL poster_jetsam_is_application(id self,SEL selector)
{ (void)self;(void)selector;return NO; }
static id poster_jetsam_label(id self,SEL selector)
{ return ((id(*)(id,SEL))objc_msgSend)(poster_jetsam_source(self),selector); }
static id poster_jetsam_forward(id self,SEL selector,SEL query)
{ (void)selector;(void)query;return poster_jetsam_source(self); }
static BOOL poster_jetsam_responds(id self,SEL selector,SEL query)
{ (void)selector;return ((BOOL(*)(id,SEL,SEL))objc_msgSend)(poster_jetsam_source(self),sel_registerName("respondsToSelector:"),query); }
static id poster_jetsam_properties(id self,SEL selector,int pid,id identity,id bundle,BOOL platform)
{
    SEL label_query=sel_registerName("consistentLaunchdJobLabel");
    Method label=identity?class_getInstanceMethod(object_getClass(identity),label_query):NULL;
    if(method_abi(label,"@",0,"")) {
        id value=((id(*)(id,SEL))objc_msgSend)(identity,label_query);
        if(value && CFEqual((CFTypeRef)value,CFSTR("com.apple.PosterBoard"))) {
            id view=((id(*)(id,SEL))objc_msgSend)((id)poster_jetsam_identity,sel_registerName("new"));
            if(view) {
                objc_setAssociatedObject(view,&poster_jetsam_source_key,identity,OBJC_ASSOCIATION_RETAIN_NONATOMIC);
                id result=((id(*)(id,SEL,int,id,id,BOOL))poster_jetsam_original)(self,selector,pid,view,bundle,platform);
                ((void(*)(id,SEL))objc_msgSend)(view,sel_registerName("release"));
                static unsigned reported;
                if(__atomic_fetch_add(&reported,1,__ATOMIC_RELAXED)<3)
                    fprintf(stderr,"Pong Poster18: native Angel memory policy selected for PosterBoard\n");
                return result;
            }
        }
    }
    return ((id(*)(id,SEL,int,id,id,BOOL))poster_jetsam_original)(self,selector,pid,identity,bundle,platform);
}
static void poster_jetsam_image(const struct mach_header *header,intptr_t slide)
{
    (void)header;(void)slide;
    if(poster_jetsam_original)return;
    Class manager=objc_lookUpClass("RBJetsamPropertyManager");
    Class identity=objc_lookUpClass("RBSProcessIdentity");
    SEL query=sel_registerName("jetsamPropertiesForProcess:identity:bundleProperties:isPlatformBinary:");
    Method method=manager?class_getInstanceMethod(manager,query):NULL;
    Method type=identity?class_getInstanceMethod(identity,sel_registerName("osServiceType")):NULL;
    if(!method_abi(method,"@",4,"i@@B") || !method_abi(type,"C",0,""))return;
    Class view=objc_allocateClassPair(objc_lookUpClass("NSObject"),"PongPosterJetsamIdentity18",0);
    if(!view)return;
    if(!class_addMethod(view,sel_registerName("osServiceType"),(IMP)poster_jetsam_service_type,method_getTypeEncoding(type)) ||
       !class_addMethod(view,sel_registerName("isApplication"),(IMP)poster_jetsam_is_application,"B16@0:8") ||
       !class_addMethod(view,sel_registerName("consistentLaunchdJobLabel"),(IMP)poster_jetsam_label,"@16@0:8") ||
       !class_addMethod(view,sel_registerName("forwardingTargetForSelector:"),(IMP)poster_jetsam_forward,"@24@0:8:16") ||
       !class_addMethod(view,sel_registerName("respondsToSelector:"),(IMP)poster_jetsam_responds,"B24@0:8:16")) {
        objc_disposeClassPair(view);return;
    }
    objc_registerClassPair(view);poster_jetsam_identity=view;
    poster_jetsam_original=method_setImplementation(method,(IMP)poster_jetsam_properties);
}

/* Phone identity still uses Pong's real screen. The native provider factory
 * falls back to a diagnostic label for an unlisted width/height tuple. Reuse
 * its real 1080px phone layout only for this main display, with the factory's
 * normal initialization and the supplied 22H374 Objective-C signatures. */
static double phone_reference_scale(id self,SEL selector)
{
    (void)self;(void)selector;
    /* This class method also runs during UIKit's screen/provider discovery.
     * Reentering +mainScreen here can wait on that same initialization. Pong's
     * ADT and framebuffer use scale 3; no UIKit calls are needed to report it. */
    return 3.0;
}
/* 22H374 can retain a diagnostic provider selected before +load completes.
 * Give that provider a real, separately allocated phone layout. Its 24-byte
 * instance cannot be changed into a 240-byte DynamicSplit object in place.
 * All regions, placements, clocks and battery views still come from UIKit;
 * none are drawn by this adapter and no hardware values are synthesized. */
static id phone_fallback_delegate(id self)
{
    id delegate=objc_getAssociatedObject(self,&statusbar_delegate_key);
    if(delegate || !statusbar_provider)return delegate;
    delegate=((id(*)(id,SEL))objc_msgSend)((id)statusbar_provider,sel_registerName("new"));
    if(!delegate)return nil;
    id bar=((id(*)(id,SEL))statusbar_fallback_get)(self,sel_registerName("statusBar"));
    ((void(*)(id,SEL,id))objc_msgSend)(delegate,sel_registerName("setStatusBar:"),bar);
    objc_setAssociatedObject(self,&statusbar_delegate_key,delegate,OBJC_ASSOCIATION_RETAIN_NONATOMIC);
    ((void(*)(id,SEL))objc_msgSend)(delegate,sel_registerName("release"));
    // This weak statusBar link also finds server-owned bars absent from
    // UIApplication.windows. Rebuild once after the current native update
    // unwinds; swapping an in-use provider synchronously is unsafe.
    if(bar && !objc_getAssociatedObject(bar,&statusbar_rebuild_key)) {
        objc_setAssociatedObject(bar,&statusbar_rebuild_key,(id)kCFBooleanTrue,OBJC_ASSOCIATION_RETAIN_NONATOMIC);
        id retained=((id(*)(id,SEL))objc_msgSend)(bar,sel_registerName("retain"));
        dispatch_async_f(dispatch_get_main_queue(),retained,phone_repair_actual_bar);
    }
    return objc_getAssociatedObject(self,&statusbar_delegate_key);
}
static void phone_fallback_set_bar(id self,SEL selector,id bar)
{
    ((void(*)(id,SEL,id))statusbar_fallback_set)(self,selector,bar);
    id delegate=phone_fallback_delegate(self);
    if(delegate)((void(*)(id,SEL,id))objc_msgSend)(delegate,selector,bar);
}
static id phone_fallback_setup(id self,SEL selector,id container)
{
    id delegate=phone_fallback_delegate(self);
    if(!delegate)return ((id(*)(id,SEL,id))statusbar_fallback_setup_original)(self,selector,container);
    id regions=((id(*)(id,SEL,id))objc_msgSend)(delegate,selector,container);
    static unsigned reported;
    if(__atomic_fetch_add(&reported,1,__ATOMIC_RELAXED)<3)
        fprintf(stderr,"Pong UI18: diagnostic provider replaced by native phone regions\n");
    return regions;
}
static id phone_fallback_placements(id self,SEL selector,id region)
{ return ((id(*)(id,SEL,id))objc_msgSend)(phone_fallback_delegate(self),selector,region); }
static id phone_fallback_style(id self,SEL selector,long long style)
{ return ((id(*)(id,SEL,long long))objc_msgSend)(phone_fallback_delegate(self),selector,style); }
static id phone_fallback_clock(id self,SEL selector)
{ return ((id(*)(id,SEL))objc_msgSend)(phone_fallback_delegate(self),selector); }
static BOOL phone_fallback_responds(id self,SEL selector,SEL query)
{
    if(((BOOL(*)(id,SEL,SEL))statusbar_fallback_responds)(self,selector,query))return YES;
    return ((BOOL(*)(id,SEL,SEL))objc_msgSend)(phone_fallback_delegate(self),selector,query);
}
static id phone_fallback_forward(id self,SEL selector,SEL query)
{
    (void)selector;
    id delegate=phone_fallback_delegate(self);
    return delegate && ((BOOL(*)(id,SEL,SEL))objc_msgSend)(delegate,sel_registerName("respondsToSelector:"),query)?delegate:nil;
}
static BOOL phone_fallback_class_flag(id self,SEL selector)
{ (void)self;return ((BOOL(*)(id,SEL))objc_msgSend)((id)statusbar_provider,selector); }
static CGSize phone_fallback_class_size(id self,SEL selector,long long orientation)
{ (void)self;return ((CGSize(*)(id,SEL,long long))objc_msgSend)((id)statusbar_provider,selector,orientation); }
static void phone_fallback_policy(void)
{
    if(statusbar_fallback_bridge_installed || !statusbar_provider)return;
    Class fallback=objc_lookUpClass("_UIStatusBarVisualProvider_Fallback");
    const char *names[]={"statusBar","setStatusBar:","setupInContainerView:",
        "orderedDisplayItemPlacementsInRegionWithIdentifier:","styleAttributesForStyle:","clockFont"};
    const char *results[]={"@","v","@","@","@","@"};
    const unsigned counts[]={0,1,1,1,1,0};
    const char *args[]={"","@","@","@","q",""};
    Method methods[6];
    for(unsigned i=0;i<6;i++) {
        SEL sel=sel_registerName(names[i]);
        methods[i]=fallback?class_getInstanceMethod(fallback,sel):NULL;
        if(!method_abi(methods[i],results[i],counts[i],args[i]) ||
           !method_abi(class_getInstanceMethod(statusbar_provider,sel),results[i],counts[i],args[i]))return;
    }
    SEL responds=sel_registerName("respondsToSelector:"),forward=sel_registerName("forwardingTargetForSelector:");
    Method rm=class_getInstanceMethod(fallback,responds),fm=class_getInstanceMethod(fallback,forward);
    if(!method_abi(rm,"Bc",1,":") || !method_abi(fm,"@",1,":"))return;
    Class meta=object_getClass((id)fallback);
    const char *flags[]={"scalesWithScreenNativeScale","requiresIterativeOverflowLayout"};
    for(unsigned i=0;i<2;i++)if(!method_abi(class_getClassMethod(fallback,sel_registerName(flags[i])),"Bc",0,"") ||
        !method_abi(class_getClassMethod(statusbar_provider,sel_registerName(flags[i])),"Bc",0,""))return;
    SEL size=sel_registerName("intrinsicContentSizeForOrientation:");
    Method sm=class_getClassMethod(fallback,size),ps=class_getClassMethod(statusbar_provider,size);
    if(!method_abi(sm,"{",1,"q") || !method_abi(ps,"{",1,"q"))return;
    statusbar_fallback_get=method_getImplementation(methods[0]);
    statusbar_fallback_set=method_setImplementation(methods[1],(IMP)phone_fallback_set_bar);
    statusbar_fallback_setup_original=method_setImplementation(methods[2],(IMP)phone_fallback_setup);
    method_setImplementation(methods[3],(IMP)phone_fallback_placements);
    method_setImplementation(methods[4],(IMP)phone_fallback_style);
    method_setImplementation(methods[5],(IMP)phone_fallback_clock);
    statusbar_fallback_responds=method_getImplementation(rm);
    class_addMethod(fallback,responds,(IMP)phone_fallback_responds,method_getTypeEncoding(rm));
    class_addMethod(fallback,forward,(IMP)phone_fallback_forward,method_getTypeEncoding(fm));
    for(unsigned i=0;i<2;i++)method_setImplementation(class_getClassMethod(fallback,sel_registerName(flags[i])),(IMP)phone_fallback_class_flag);
    method_setImplementation(sm,(IMP)phone_fallback_class_size);
    class_addMethod(meta,sel_registerName("referenceScreenScale"),(IMP)phone_reference_scale,"d16@0:8");
    statusbar_fallback_bridge_installed=true;
    fprintf(stderr,"Pong UI18: rdar diagnostic layout removed; native clock/battery layout connected\n");
}
static BOOL phone_diagnostic_item_enabled(id self,SEL selector,id item,id data)
{ (void)self;(void)selector;(void)item;(void)data;return NO; }
static void phone_remove_diagnostic_item(void)
{
    if(statusbar_diagnostic_item_removed)return;
    Class cls=objc_lookUpClass("_UIStatusBarFallbackItem");
    Method method=cls?class_getInstanceMethod(cls,sel_registerName("canEnableDisplayItem:fromData:")):NULL;
    if(!method_abi(method,"Bc",2,"@@"))return;
    method_setImplementation(method,(IMP)phone_diagnostic_item_enabled);
    statusbar_diagnostic_item_removed=true;
}
static Class phone_statusbar(unsigned index,id self,SEL selector,id screen,id info)
{
    /* Resolve the known Pong screen before entering the native candidate
     * enumerator, matching the order used before services42. */
    // UIKit uses nil for its default display during screen construction.
    // This adapter is already restricted to Pong; do not reenter mainScreen.
    if(!screen && statusbar_provider)return statusbar_provider;
    if(screen && statusbar_provider) {
        CGRect bounds=((CGRect(*)(id,SEL))objc_msgSend)(screen,sel_registerName("nativeBounds"));
        double scale=((double(*)(id,SEL))objc_msgSend)(screen,sel_registerName("nativeScale"));
        if(((bounds.size.width==1080 && bounds.size.height==2412) ||
            (bounds.size.height==1080 && bounds.size.width==2412)) && scale>0 && scale<=4) {
            static unsigned reported;
            if(__atomic_fetch_add(&reported,1,__ATOMIC_RELAXED)<3)
                fprintf(stderr,"Pong UI18: actual 1080x2412 phone status bar selected\n");
            return statusbar_provider;
        }
    }
    return ((Class(*)(id,SEL,id,id))statusbar_factories[index].original)(self,selector,screen,info);
}
/* Darwin24 compares a candidate's IMP with its parent's IMP. Each factory
 * must have a distinct wrapper, even though the Pong screen policy is shared. */
#define PHONE_FACTORY(N) static Class phone_factory##N(id s,SEL c,id screen,id info) \
    { return phone_statusbar(N,s,c,screen,info); }
PHONE_FACTORY(0)
PHONE_FACTORY(1)
PHONE_FACTORY(2)
PHONE_FACTORY(3)
#undef PHONE_FACTORY
static void phone_presentation(void)
{
    if(!phone_ui_process || statusbar_factory_count)return;
    char product[64]={0};size_t size=sizeof(product);
    if(sysctlbyname("hw.product",product,&size,NULL,0) || strcmp(product,"iPhone17,1"))return;
    phone_remove_diagnostic_item();
    Class base=objc_lookUpClass("_UIStatusBarVisualProvider_DynamicSplitMedium");
    if(!base)base=objc_lookUpClass("_UIStatusBarVisualProvider_Split1080");
    if(!base)return;
    // DynamicSplitMedium does not declare referenceScreenScale in 22H374.
    // It is an optional class query, which this concrete Pong subclass adds.
    // Requiring it on the base silently skipped the entire provider adapter.
    Class provider=objc_allocateClassPair(base,"PongStatusBar18Phone1080",0);
    if(!provider)return;
    if(!class_addMethod(object_getClass((id)provider),sel_registerName("referenceScreenScale"),(IMP)phone_reference_scale,"d16@0:8")) {
        objc_disposeClassPair(provider);return;
    }
    objc_registerClassPair(provider);statusbar_provider=provider;
    phone_fallback_policy();
    // Keep _prepareVisualProviderIfNeeded's native class selection branch:
    // it initializes scale/reference/overflow flags before provider init.
    // Darwin24's C selector compares each subclass IMP with its parent.
    // Hooking every class to the same IMP makes it discard all candidates.
    // Keep the root implementation intact and use distinct child IMPs.
    const char *names[]={"_UIStatusBarVisualProvider_iOS","_UIStatusBarVisualProvider_Phone",
        "_UIStatusBarVisualProvider_Split","_UIStatusBarVisualProvider_DynamicSplit"};
    const IMP replacements[]={(IMP)phone_factory0,(IMP)phone_factory1,(IMP)phone_factory2,(IMP)phone_factory3};
    SEL selector=sel_registerName("visualProviderSubclassForScreen:visualProviderInfo:");
    /* services43 rendered with only the iOS factory adapted. The broader
     * services44 overrides ran before any scanout and never completed the
     * main-queue wake. Keep child factory discovery native during boot;
     * the extended candidate policy remains an explicit development option. */
    unsigned count=boot_option("pong_statusbar_factories=1")?4:1;
    for(unsigned i=0;i<count;i++) {
        Class factory=objc_lookUpClass(names[i]);
        Method method=factory?class_getClassMethod(factory,selector):NULL;
        if(!method_abi(method,"#",2,"@@"))continue;
        // Do not mutate an inherited method through a child class handle.
        unsigned n=0;Method *own=class_copyMethodList(object_getClass((id)factory),&n);
        bool declared=false;
        for(unsigned j=0;j<n;j++)if(method_getName(own[j])==selector)declared=true;
        free(own);if(!declared)continue;
        statusbar_factories[i].cls=factory;
        statusbar_factories[i].original=method_setImplementation(method,replacements[i]);
        statusbar_factory_count++;
    }
    fprintf(stderr,"Pong UI18: native phone status bar factories installed=%u\n",
        statusbar_factory_count);
}

/* This restore supplies iPad artwork. Use 22H374 WallpaperKit's own
 * compatibility selectors for its real catalogs on Pong, rather than clone
 * collection UUIDs or change the user's selected iPhone identity. */
static bool wallpaper_process,wallpaper_paths_process,wallpaper_policy_installed;
static BOOL (*wallpaper_original_collection)(id,SEL,id);
static BOOL wallpaper_collection_path(id self,SEL selector,id url)
{
    if(!wallpaper_original_collection(self,selector,url))return NO;
    if(!url || CFGetTypeID((CFTypeRef)url)!=CFURLGetTypeID())return YES;
    CFStringRef path=CFURLCopyFileSystemPath((CFURLRef)url,kCFURLPOSIXPathStyle);
    char original[PATH_MAX],phone[PATH_MAX];
    bool usable=path && CFStringGetCString(path,original,sizeof original,kCFStringEncodingUTF8);
    if(path)CFRelease(path);
    if(!usable)return YES;
    const char *prefix="/Library/Wallpaper/Collections/";
    if(strncmp(original,prefix,strlen(prefix)))return YES;
    char *suffix=strstr(original,"~ipad.wallpaperCollection");
    if(!suffix || strcmp(suffix,"~ipad.wallpaperCollection"))return YES;
    // _loadSystemWallpaperCollections keys by UUID. An iPad directory
    // scanned after its phone wrapper overwrote the adapted screen variant.
    // Only exclude the duplicate when the matching real wrapper is present.
    int n=snprintf(phone,sizeof phone,"%.*s~iphone.wallpaperCollection/WallpaperCollection.plist",
        (int)(suffix-original),original);
    return n>0 && (size_t)n<sizeof phone && access(phone,R_OK)==0?NO:YES;
}
static BOOL wallpaper_resource_compat(id self,SEL selector)
{ (void)self;(void)selector;return YES; }
static void wallpaper_resource_policy(void)
{
    if(wallpaper_paths_process && !wallpaper_original_collection) {
        Class cls=objc_lookUpClass("WKWallpaperRepresentingCollection");
        Method method=cls?class_getClassMethod(cls,sel_registerName("shouldLoadWallpaperCollectionAtURL:")):NULL;
        if(method_abi(method,"Bc",1,"@")) {
            wallpaper_original_collection=(BOOL (*)(id,SEL,id))method_setImplementation(method,(IMP)wallpaper_collection_path);
            fprintf(stderr,"Pong UI18: native wallpaper scan prefers installed phone path per UUID\n");
        }
    }
    if(!wallpaper_process || wallpaper_policy_installed)return;
    Class cls=objc_lookUpClass("NSUserDefaults");
    Method product=cls?class_getInstanceMethod(cls,sel_registerName("wk_ignoreProductTypesForWallpaperBundle")):NULL;
    Method screen=cls?class_getInstanceMethod(cls,sel_registerName("wk_ignoreLogicalScreenClassForWallpaperBundle")):NULL;
    if(!method_abi(product,"Bc",0,"") || !method_abi(screen,"Bc",0,""))return;
    method_setImplementation(product,(IMP)wallpaper_resource_compat);
    method_setImplementation(screen,(IMP)wallpaper_resource_compat);
    wallpaper_policy_installed=true;
    fprintf(stderr,"Pong UI18: original 22H374 wallpaper catalogs enabled for Pong screen\n");
}

/* Same single-user container policy as the working iOS16 port. Native
 * UID containers remain in use. Do not disable launchd's Darwin24 proxy
 * personas, which are separate from per-user MCM persona containers. */
static BOOL no_container_personas(id self,SEL selector)
{ (void)self;(void)selector;return NO; }

/* 22H374 SBLockdownManager still stores its local UI state as an int.
 * Its original method maps Activated/FactoryActivated to 2. Discover the
 * ivar at runtime, rather than copying the old OS's +8 offset. This is a
 * development SpringBoard setup policy, not a mobileactivationd/SEP reply. */
static void setup_activation(id self,SEL selector)
{
    (void)selector;
    const int state=2;
    memcpy((char *)self+activation_offset,&state,sizeof(state));
    static unsigned reported;
    if(__atomic_fetch_add(&reported,1,__ATOMIC_RELAXED)==0)
        fprintf(stderr,"Pong UI18: development SpringBoard activation UI selected\n");
}

/* The supplied 22H374 method is B24@0:8Q16, as on 20G81. Keep its actual
 * transition/notification implementation and return value, passing the
 * no-assistant reason requested by the Pong bring-up boot option. */
static BOOL setup_reason(id self,SEL selector,unsigned long long reason)
{
    static unsigned reported;
    if(reason && __atomic_fetch_add(&reported,1,__ATOMIC_RELAXED)<3)
        fprintf(stderr,"Pong UI18: development setup reason %llu skipped\n",reason);
    return ((BOOL(*)(id,SEL,unsigned long long))setup_reason_original)(self,selector,0);
}

static void springboard_setup_policy(void)
{
    if(!springboard_process || !skip_setup)return;
    if(!activation_installed) {
        Class cls=objc_lookUpClass("SBLockdownManager");
        Method method=cls?class_getInstanceMethod(cls,sel_registerName("_setupActivationState")):NULL;
        Ivar ivar=cls?class_getInstanceVariable(cls,"_state"):NULL;
        const char *type=ivar?ivar_getTypeEncoding(ivar):NULL;
        ptrdiff_t offset=ivar?ivar_getOffset(ivar):-1;
        if(method_abi(method,"v",0,"") && type && type[0] && strchr("iI",type[0]) &&
           offset>=(ptrdiff_t)sizeof(void *) &&
           (size_t)offset+sizeof(int)<=class_getInstanceSize(cls)) {
            activation_offset=offset;
            method_setImplementation(method,(IMP)setup_activation);
            activation_installed=true;
            fprintf(stderr,"Pong UI18: 22H374 activation UI policy installed\n");
        }
    }
    if(!setup_reason_original) {
        Class cls=objc_lookUpClass("SBSetupManager");
        Method method=cls?class_getInstanceMethod(cls,sel_registerName("_setSetupRequiredReason:")):NULL;
        if(method_abi(method,"B",1,"Q")) {
            setup_reason_original=method_setImplementation(method,(IMP)setup_reason);
            fprintf(stderr,"Pong UI18: 22H374 Setup Assistant UI policy installed\n");
        }
    }
}

/* Reuse the working iOS16 policy, with 22H374's actual method ABI.
 * The method normally queries connectedDevices before returning nil when
 * there is no internal battery. Avoid its powerd wait only while this port
 * has no published physical power source. Keep the original path whenever
 * an IOPMPowerSource is present; never invent charge/health values. */
static id internal_battery(id self,SEL selector)
{
    CFMutableDictionaryRef match=service_matching("IOPMPowerSource");
    if(!match)return ((id(*)(id,SEL))battery_original)(self,selector);
    uint32_t source=matching_service(0,match); /* consumes match */
    if(source) {
        release_service(source);
        return ((id(*)(id,SEL))battery_original)(self,selector);
    }
    static unsigned reported;
    if(__atomic_fetch_add(&reported,1,__ATOMIC_RELAXED)==0)
        fprintf(stderr,"Pong UI18: no physical power source; omit unavailable battery status\n");
    return nil;
}

static void springboard_platform_policy(void)
{
    if(!springboard_process || battery_original)return;
    Class cls=objc_lookUpClass("BCBatteryDeviceController");
    Method method=cls?class_getInstanceMethod(cls,sel_registerName("sb_deviceInternalBattery")):NULL;
    if(!method_abi(method,"@",0,""))return;
    service_matching=(__typeof__(service_matching))dlsym(RTLD_DEFAULT,"IOServiceMatching");
    matching_service=(__typeof__(matching_service))dlsym(RTLD_DEFAULT,"IOServiceGetMatchingService");
    release_service=(__typeof__(release_service))dlsym(RTLD_DEFAULT,"IOObjectRelease");
    if(!service_matching || !matching_service || !release_service)return;
    battery_original=method_setImplementation(method,(IMP)internal_battery);
}

static void container_persona_policy(void)
{
    if(personas_installed)return;
    Class cls=objc_lookUpClass("MCMUserIdentityCache");
    Method method=cls?class_getClassMethod(cls,sel_registerName("personasAreSupported")):NULL;
    if(!method_abi(method,"Bc",0,""))return;
    method_setImplementation(method,(IMP)no_container_personas);
    personas_installed=true;
    pong_say("Pong UI18: native single-user container paths selected");
}

/* A new unencrypted Data has no persona.kb. In 22H374 --init can finish
 * without creating it, but the daemon's disk loader then aborts on ENOENT.
 * Let the matching executable's own platform class create and serialize
 * its empty initial registry. No plist schema, users, key material or
 * persona IDs are fabricated; existing registries are never replaced. */
static void initialize_persona_manifest(void)
{
    const char *path="/private/var/keybags/persona.kb";
    struct stat st;
    if(geteuid()!=0 || lstat(path,&st)==0 || errno!=ENOENT)return;
    /* THOR development image: the Data volume is authored on the host without
     * root, so the keybags directory carries the authoring uid instead of 0.
     * Keep the permission-bit guard (no group/other access); root ownership
     * is restored when the image toolchain gains that capability. */
    if(lstat("/private/var/keybags",&st)!=0 || !S_ISDIR(st.st_mode) ||
       (st.st_mode&0077))return;
    Class cls=objc_lookUpClass("UMDPersonaManifest");
    SEL make=sel_registerName("manifest"),encode=sel_registerName("dataValue");
    if(!method_abi(cls?class_getClassMethod(cls,make):NULL,"@",0,""))return;
    id manifest=((id(*)(id,SEL))objc_msgSend)((id)cls,make);
    if(!manifest || !method_abi(class_getInstanceMethod(object_getClass(manifest),encode),"@",0,""))return;
    id data=((id(*)(id,SEL))objc_msgSend)(manifest,encode);
    if(!data || CFGetTypeID((CFTypeRef)data)!=CFDataGetTypeID())return;
    CFIndex length=CFDataGetLength((CFDataRef)data);
    if(length<=0 || length>4*1024*1024)return;
    /* Publish only a complete registry. A crash during the old direct
     * O_EXCL write left a truncated persona.kb that all later boots kept. */
    char temporary[]="/private/var/keybags/.persona.kb.pong.XXXXXX";
    int fd=mkstemp(temporary);
    if(fd<0)return;
    (void)fcntl(fd,F_SETFD,FD_CLOEXEC);
    const UInt8 *bytes=CFDataGetBytePtr((CFDataRef)data);
    size_t written=0;
    while(written<(size_t)length) {
        ssize_t n=write(fd,bytes+written,(size_t)length-written);
        if(n<0 && errno==EINTR)continue;
        if(n<=0)break;
        written+=(size_t)n;
    }
    int saved=0;
    if(written!=(size_t)length || fsync(fd)!=0)saved=errno?errno:EIO;
    if(close(fd)!=0 && !saved)saved=errno;
    bool published=false;
    if(!saved) {
        if(link(temporary,path)==0)published=true;
        else if(errno!=EEXIST)saved=errno;
    }
    (void)unlink(temporary);
    if(saved) {
        pong_say("Pong UI18: native persona registry write failed errno=%d",saved);
    } else if(published) {
        int directory=open("/private/var/keybags",O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
        if(directory>=0) {
            if(fsync(directory)!=0)
                pong_say("Pong UI18: persona registry published; directory sync errno=%d",errno);
            (void)close(directory);
        }
        pong_say("Pong UI18: 22H374 serialized initial persona registry (%ld bytes)",(long)length);
    }
}

static bool executable_image_range(const struct mach_header_64 *header,intptr_t slide,
                                   uintptr_t target,size_t bytes)
{
    if(!bytes || (uintptr_t)(header+1)>UINTPTR_MAX-header->sizeofcmds)return false;
    const struct load_command *lc=(const void *)(header+1);
    const char *end=(const char *)lc+header->sizeofcmds;
    for(uint32_t i=0;i<header->ncmds;i++) {
        if((const char *)lc+sizeof(*lc)>end || lc->cmdsize<sizeof(*lc) ||
           lc->cmdsize>(size_t)(end-(const char *)lc))return false;
        if(lc->cmd==LC_SEGMENT_64 && lc->cmdsize>=sizeof(struct segment_command_64)) {
            const struct segment_command_64 *segment=(const void *)lc;
            const vm_prot_t needed=VM_PROT_READ|VM_PROT_EXECUTE;
            uintptr_t start;
            if((segment->initprot&needed)==needed) {
                if(slide>=0) {
                    if(segment->vmaddr>UINTPTR_MAX-(uintptr_t)slide)return false;
                    start=(uintptr_t)segment->vmaddr+(uintptr_t)slide;
                } else {
                    uintptr_t amount=0-(uintptr_t)slide;
                    if(segment->vmaddr<amount)return false;
                    start=(uintptr_t)segment->vmaddr-amount;
                }
                if(segment->vmsize<=UINTPTR_MAX-start && target>=start &&
                   target-start<=segment->vmsize && bytes<=segment->vmsize-(target-start))return true;
            }
        }
        lc=(const void *)((const char *)lc+lc->cmdsize);
    }
    return false;
}

static bool install_compositor_code(const struct mach_header *header,intptr_t slide,uintptr_t offset,
    const uint32_t *expected,const uint32_t *replacement,size_t bytes,const char *label)
{
    if(offset>UINTPTR_MAX-(uintptr_t)header || !bytes || bytes>vm_page_size)return false;
    uintptr_t target=(uintptr_t)header+offset;
    if(!executable_image_range((const void *)header,slide,target,bytes)) {
        pong_say("Pong UI18: %s outside executable image range; SKIP",label);return false;
    }
    if(memcmp((const void *)target,expected,bytes)) {
        pong_say("Pong UI18: 22H374 %s entry differs; SKIP",label);return false;
    }
    const vm_address_t page=target&~((vm_address_t)vm_page_size-1);
    if(target-page>vm_page_size-bytes)return false;
    kern_return_t kr=vm_protect(mach_task_self(),page,vm_page_size,FALSE,
                               VM_PROT_READ|VM_PROT_WRITE|VM_PROT_COPY);
    if(kr!=KERN_SUCCESS) {
        pong_say("Pong UI18: %s protection failed %d",label,kr);return false;
    }
    memcpy((void *)target,replacement,bytes);
    sys_icache_invalidate((void *)target,bytes);
    kr=vm_protect(mach_task_self(),page,vm_page_size,FALSE,VM_PROT_READ|VM_PROT_EXECUTE);
    if(kr!=KERN_SUCCESS) {
        memcpy((void *)target,expected,bytes);
        sys_icache_invalidate((void *)target,bytes);
        (void)vm_protect(mach_task_self(),page,vm_page_size,FALSE,VM_PROT_READ|VM_PROT_EXECUTE);
        pong_say("Pong UI18: %s RX restore failed %d; original restored",label,kr);
        return false;
    }
    pong_say("Pong UI18: 22H374 %s selected",label);return true;
}

/* 22H374 initializes its missing-calibration fallback limits by converting
 * slider values BEFORE it allocates its built-in marketing curve. The helper
 * tests for a null curve, then unconditionally reads curve[0]. services84 IPS
 * records all stop there. Use that SAME selected runtime's built-in software
 * curve during this initialization window; calibrated/native mappings still
 * call the original helper. This is not measured Pong panel photometry. */
static float (*brightness18_slider_original)(void *,float);
static float brightness18_defaults[3];
struct pong_tracked_brightness_state {unsigned char a,b,c,d;};
static void brightness18_skip_uncalibrated_telemetry(id self,SEL selector,int64_t event,
    float from,float to,struct pong_tracked_brightness_state state)
{
    (void)self;(void)selector;(void)event;(void)from;(void)to;(void)state;
    // This is only CBSliderCommitTelemetry's reporting entry, not the
    // brightness transaction or the control-center slider implementation.
}
static void brightness18_telemetry(const struct mach_header_64 *header,intptr_t slide)
{
    Class cls=objc_lookUpClass("CBSliderCommitTelemetry");
    if(!cls)return;
    unsigned count=0;Method *methods=class_copyMethodList(cls,&count);
    const uintptr_t entry=(uintptr_t)header+0xf22f0;
    if(!executable_image_range(header,slide,entry,4)){free(methods);return;}
    for(unsigned i=0;i<count;i++) {
        Method method=methods[i];
        const char *encoding=method_getTypeEncoding(method);
        if((uintptr_t)method_getImplementation(method)!=entry ||
           !method_abi(method,"v",4,"qff{") || !encoding ||
           !strstr(encoding,"{TrackedState=BBBB}"))continue;
        method_setImplementation(method,(IMP)brightness18_skip_uncalibrated_telemetry);
        fprintf(stderr,"Pong brightness18: uncalibrated slider telemetry skipped; native brightness control retained\n");
        break;
    }
    free(methods);
}
static float brightness18_slider(void *display,float slider)
{
    uintptr_t curve=0;
    if(display)memcpy(&curve,(const char *)display+0x48,sizeof(curve));
    if(display && !curve) {
        if(!(slider>=0.0f))slider=0.0f;
        if(slider>1.0f)slider=1.0f;
        const float position=slider*2.0f;
        const unsigned lower=position>=1.0f?1:0;
        static unsigned reported;
        if(__atomic_fetch_add(&reported,1,__ATOMIC_RELAXED)==0)
            fprintf(stderr,"Pong brightness18: native software curve used before mapping initialization\n");
        return brightness18_defaults[lower]+(position-(float)lower)*
            (brightness18_defaults[lower+1]-brightness18_defaults[lower]);
    }
    return brightness18_slider_original(display,slider);
}
static void brightness18_image(const struct mach_header *header,intptr_t slide)
{
    if(brightness18_slider_original || strcmp(getprogname(),"backboardd") || header->magic!=MH_MAGIC_64)return;
    static const uint8_t uuid[16]={0x0a,0x02,0x14,0x08,0xa8,0xf4,0x37,0x2c,
                                 0xb5,0xd5,0x5f,0xdf,0xcc,0x83,0xc3,0x4a};
    const struct mach_header_64 *h=(const void *)header;
    const struct load_command *lc=(const void *)(h+1);
    const char *end=(const char *)lc+h->sizeofcmds;bool matched=false;
    for(uint32_t i=0;i<h->ncmds;i++) {
        if((const char *)lc+sizeof(*lc)>end || lc->cmdsize<sizeof(*lc) ||
           lc->cmdsize>(size_t)(end-(const char *)lc))return;
        if(lc->cmd==LC_UUID && lc->cmdsize==sizeof(struct uuid_command) &&
           !memcmp(((const struct uuid_command *)lc)->uuid,uuid,sizeof(uuid)))matched=true;
        lc=(const void *)((const char *)lc+lc->cmdsize);
    }
    if(!matched)return;
    // Restrict the adapter to the actually published NT37705 DCS service.
    CFMutableDictionaryRef (*matching)(const char *)=dlsym(RTLD_DEFAULT,"IOServiceMatching");
    uint32_t (*get)(uint32_t,CFDictionaryRef)=dlsym(RTLD_DEFAULT,"IOServiceGetMatchingService");
    int (*release)(uint32_t)=dlsym(RTLD_DEFAULT,"IOObjectRelease");
    if(!matching||!get||!release)return;
    CFMutableDictionaryRef dict=matching("IOService");
    const void *key=CFSTR("PongPanel"),*value=CFSTR("NT37705 Visionox / SM8475 DSI0");
    CFDictionaryRef properties=CFDictionaryCreate(NULL,&key,&value,1,&kCFTypeDictionaryKeyCallBacks,&kCFTypeDictionaryValueCallBacks);
    if(!dict || !properties){if(dict)CFRelease(dict);if(properties)CFRelease(properties);return;}
    CFDictionarySetValue(dict,CFSTR("IOPropertyMatch"),properties);CFRelease(properties);
    uint32_t service=get(0,dict);if(!service)return;release(service);
    // Same exact 22H374 CoreBrightness UUID and actual Pong panel as below.
    // Its optional telemetry expects DisplayBrightness/NitsPhysical to be a
    // calibration dictionary; the board's native scalar state is not one.
    brightness18_telemetry(h,slide);
    const uintptr_t offset=0x144f5c,entry=(uintptr_t)header+offset;
    const uintptr_t defaults=(uintptr_t)header+0x1bead4;
    static const uint32_t expected[]={0xd10103ff,0xa9037bfd,0x9100c3fd,0xf81f83a0};
    if(!executable_image_range(h,slide,entry,sizeof(expected)) ||
       !executable_image_range(h,slide,defaults,sizeof(brightness18_defaults)) ||
       memcmp((const void *)entry,expected,sizeof(expected)))return;
    memcpy(brightness18_defaults,(const void *)defaults,sizeof(brightness18_defaults));
    if(!(brightness18_defaults[0]>0.0f && brightness18_defaults[0]<brightness18_defaults[1] &&
         brightness18_defaults[1]<brightness18_defaults[2] && brightness18_defaults[2]<100000.0f))return;
    vm_address_t trampoline=0;
    if(vm_allocate(mach_task_self(),&trampoline,vm_page_size,VM_FLAGS_ANYWHERE)!=KERN_SUCCESS)return;
    uint32_t back[]={0x58000050,0xd61f0200,(uint32_t)(entry+16),(uint32_t)((entry+16)>>32)};
    memcpy((void *)trampoline,expected,sizeof(expected));memcpy((void *)(trampoline+16),back,sizeof(back));
    sys_icache_invalidate((void *)trampoline,32);
    if(vm_protect(mach_task_self(),trampoline,vm_page_size,FALSE,VM_PROT_READ|VM_PROT_EXECUTE)!=KERN_SUCCESS) {
        (void)vm_deallocate(mach_task_self(),trampoline,vm_page_size);return;
    }
    const uintptr_t replacement=(uintptr_t)brightness18_slider;
    const uint32_t jump[]={0x58000050,0xd61f0200,(uint32_t)replacement,(uint32_t)(replacement>>32)};
    brightness18_slider_original=(float(*)(void *,float))trampoline;
    if(!install_compositor_code(header,slide,offset,expected,jump,sizeof(expected),"CoreBrightness fallback mapping initialization")) {
        brightness18_slider_original=NULL;(void)vm_deallocate(mach_task_self(),trampoline,vm_page_size);
    }
}

/* This build's generated deviceSupportsDynamicIsland getter calls
 * MGGetBoolAnswer with this exact key. Keep every other answer native: in
 * particular this does not advertise Face ID, telephony or camera hardware.
 * Identity/artwork/home gestures are provided by the ADT, not by this hook.
 */
static bool phone_gestalt_bool(CFStringRef key)
{
    if(key && (CFEqual(key,CFSTR("YlEtTtHlNesRBMal1CqRaA")) ||
               CFEqual(key,CFSTR("DeviceSupportsDynamicIsland"))))return true;
    return phone_gestalt_bool_original(key);
}
static void phone_gestalt_image(const struct mach_header *header,intptr_t slide)
{
    if(!phone_ui_process || phone_gestalt_bool_original || header->magic!=MH_MAGIC_64)return;
    static const uint8_t uuid[16]={0xbe,0xed,0x77,0xde,0x98,0x7a,0x34,0x7e,
                                 0xad,0xa9,0x88,0x63,0x4a,0x6b,0xe5,0x6a};
    const struct mach_header_64 *h=(const void *)header;
    const struct load_command *lc=(const void *)(h+1);
    const char *end=(const char *)lc+h->sizeofcmds;bool matched=false;
    for(uint32_t i=0;i<h->ncmds;i++) {
        if((const char *)lc+sizeof(*lc)>end || lc->cmdsize<sizeof(*lc) ||
           lc->cmdsize>(size_t)(end-(const char *)lc))return;
        if(lc->cmd==LC_UUID && lc->cmdsize==sizeof(struct uuid_command) &&
           !memcmp(((const struct uuid_command *)lc)->uuid,uuid,16))matched=true;
        lc=(const void *)((const char *)lc+lc->cmdsize);
    }
    if(!matched)return;
    char product[64]={0};size_t size=sizeof(product);
    if(sysctlbyname("hw.product",product,&size,NULL,0) || strcmp(product,"iPhone17,1"))return;
    const uintptr_t offset=0xf00,entry=(uintptr_t)header+offset;
    // Four position-independent prologue instructions from MGGetBoolAnswer.
    static const uint32_t expected[]={0xd100c3ff,0xa9014ff4,0xa9027bfd,0x910083fd};
    if(!executable_image_range((const void *)header,slide,entry,sizeof(expected)) ||
       memcmp((const void *)entry,expected,sizeof(expected)))return;
    vm_address_t trampoline=0;
    if(vm_allocate(mach_task_self(),&trampoline,vm_page_size,VM_FLAGS_ANYWHERE)!=KERN_SUCCESS)return;
    uint32_t back[]={0x58000050,0xd61f0200,(uint32_t)(entry+16),(uint32_t)((entry+16)>>32)};
    memcpy((void *)trampoline,expected,16);memcpy((void *)(trampoline+16),back,16);
    sys_icache_invalidate((void *)trampoline,32);
    if(vm_protect(mach_task_self(),trampoline,vm_page_size,FALSE,VM_PROT_READ|VM_PROT_EXECUTE)!=KERN_SUCCESS) {
        (void)vm_deallocate(mach_task_self(),trampoline,vm_page_size);return;
    }
    const uintptr_t replacement=(uintptr_t)phone_gestalt_bool;
    uint32_t jump[]={0x58000050,0xd61f0200,(uint32_t)replacement,(uint32_t)(replacement>>32)};
    phone_gestalt_bool_original=(bool(*)(CFStringRef))trampoline;
    if(!install_compositor_code(header,slide,offset,expected,jump,sizeof(expected),"MobileGestalt Dynamic Island")) {
        phone_gestalt_bool_original=NULL;(void)vm_deallocate(mach_task_self(),trampoline,vm_page_size);
    }
}

/* 22H374 _prepareVisualProviderIfNeeded directly calls this C selector,
 * bypassing the iOS class factory used by 20G81. Adapt that selection point
 * before UIKit computes provider flags/scale, preserving the native result
 * for supported layouts and every screen other than Pong's real panel. */
static Class phone_selected_class(id screen,id info)
{
    Class selected=phone_selector_original(screen,info);
    Class fallback=objc_lookUpClass("_UIStatusBarVisualProvider_Fallback");
    static unsigned decisions;
    if(__atomic_fetch_add(&decisions,1,__ATOMIC_RELAXED)<2)
        fprintf(stderr,"Pong UI18: native provider decision=%s screen=%s\n",
            selected?class_getName(selected):"nil",screen?object_getClassName(screen):"default");
    if((selected && selected!=fallback) || !statusbar_provider)return selected;
    /* UIKit also asks for its default display with nil while UIScreen is
     * being initialized. Calling mainScreen here would recurse into that
     * initialization; use the same Pong-only default as the class factory. */
    if(screen) {
        CGRect bounds=((CGRect(*)(id,SEL))objc_msgSend)(screen,sel_registerName("nativeBounds"));
        double scale=((double(*)(id,SEL))objc_msgSend)(screen,sel_registerName("nativeScale"));
        if(!((bounds.size.width==1080 && bounds.size.height==2412) ||
             (bounds.size.width==2412 && bounds.size.height==1080)) || scale!=3.0)return selected;
    }
    static unsigned reported;
    if(__atomic_fetch_add(&reported,1,__ATOMIC_RELAXED)<3)
        fprintf(stderr,"Pong UI18: Darwin24 selector chose native 1080 phone provider\n");
    return statusbar_provider;
}
static void phone_prepare_provider(id self,SEL selector)
{
    /* A class assigned before the selector was adapted skips the selector
     * entirely in Darwin24. Re-run native discovery only for an unbuilt
     * diagnostic provider; do not replace a live provider or pre-set its
     * scale/overflow flags. Both accessors and the ivar are from this OS's
     * runtime metadata, not a 20G81 offset. */
    Class assigned=((Class(*)(id,SEL))objc_msgSend)(self,sel_registerName("_visualProviderClass"));
    Class fallback=objc_lookUpClass("_UIStatusBarVisualProvider_Fallback");
    id current=object_getIvar(self,phone_provider_ivar);
    if(current && object_getClass(current)==fallback) {
        // Native callers inside UIKit can invoke this entry directly. Tear
        // down an already built diagnostic region before native discovery
        // recomputes scale/overflow flags and constructs the phone provider.
        ((void(*)(id,SEL))objc_msgSend)(self,sel_registerName("resetVisualProvider"));
        object_setIvarWithStrongDefault(self,phone_provider_ivar,nil);
        ((void(*)(id,SEL,Class))objc_msgSend)(self,sel_registerName("_setVisualProviderClass:"),Nil);
    }
    if(assigned==fallback && !object_getIvar(self,phone_provider_ivar)) {
        ((void(*)(id,SEL,Class))objc_msgSend)(self,sel_registerName("_setVisualProviderClass:"),Nil);
    }
    ((void(*)(id,SEL))phone_prepare_original)(self,selector);
}
static void phone_repair_actual_bar(void *context)
{
    id bar=(id)context;
    Class cls=objc_lookUpClass("_UIStatusBar");
    if(phone_prepare_original && phone_provider_ivar && cls &&
       ((BOOL(*)(id,SEL,Class))objc_msgSend)(bar,sel_registerName("isKindOfClass:"),cls)) {
        id current=object_getIvar(bar,phone_provider_ivar);
        if(current && object_getClass(current)==objc_lookUpClass("_UIStatusBarVisualProvider_Fallback")) {
            phone_prepare_provider(bar,sel_registerName("_prepareVisualProviderIfNeeded"));
            ((void(*)(id,SEL))objc_msgSend)(bar,sel_registerName("setNeedsLayout"));
            current=object_getIvar(bar,phone_provider_ivar);
            fprintf(stderr,"Pong UI18: actual status bar rebuilt provider=%s\n",current?object_getClassName(current):"nil");
        }
    }
    ((void(*)(id,SEL))objc_msgSend)(bar,sel_registerName("release"));
}
static void phone_layout_provider(id self,SEL selector)
{
    // A status-bar server window need not belong to UIApplication.windows.
    // Rebuild an early fallback at its first layout, on the actual view,
    // using native teardown and preparation rather than changing its class.
    id current=object_getIvar(self,phone_provider_ivar);
    Class fallback=objc_lookUpClass("_UIStatusBarVisualProvider_Fallback");
    if(current && object_getClass(current)==fallback) {
        SEL reset=sel_registerName("resetVisualProvider");
        ((void(*)(id,SEL))objc_msgSend)(self,reset);
        object_setIvarWithStrongDefault(self,phone_provider_ivar,nil);
        ((void(*)(id,SEL,Class))objc_msgSend)(self,sel_registerName("_setVisualProviderClass:"),Nil);
        ((void(*)(id,SEL))phone_prepare_original)(self,sel_registerName("_prepareVisualProviderIfNeeded"));
        current=object_getIvar(self,phone_provider_ivar);
        fprintf(stderr,"Pong UI18: status layout rebuilt provider=%s\n",current?object_getClassName(current):"nil");
    }
    ((void(*)(id,SEL))phone_layout_original)(self,selector);
}
static void phone_prepare_policy(const struct mach_header *header,intptr_t slide)
{
    if(phone_prepare_original || !phone_selector_installed)return;
    Class cls=objc_lookUpClass("_UIStatusBar");
    Method prepare=cls?class_getInstanceMethod(cls,sel_registerName("_prepareVisualProviderIfNeeded")):NULL;
    Method get=cls?class_getInstanceMethod(cls,sel_registerName("_visualProviderClass")):NULL;
    Method set=cls?class_getInstanceMethod(cls,sel_registerName("_setVisualProviderClass:")):NULL;
    Ivar ivar=cls?class_getInstanceVariable(cls,"_visualProvider"):NULL;
    const char *type=ivar?ivar_getTypeEncoding(ivar):NULL;
    if(!method_abi(prepare,"v",0,"") || !method_abi(get,"#",0,"") ||
       !method_abi(set,"v",1,"#") || !type || type[0]!='@')return;
    // In 22H374 UIKit's own status paths also call the native IMP directly;
    // a method-table replacement alone leaves those cached callers intact.
    // This exact four-instruction prologue contains no PC-relative operand.
    const uintptr_t offset=0x1860f2fa0ULL-0x184f96000ULL;
    const uintptr_t entry=(uintptr_t)header+offset;
    static const uint32_t expected[]={0xd102c3ff,0x6d0623e9,0xa9075ff8,0xa90857f6};
    if(!executable_image_range((const void *)header,slide,entry,sizeof(expected)) ||
       memcmp((const void *)entry,expected,sizeof(expected)))return;
    vm_address_t trampoline=0;
    if(vm_allocate(mach_task_self(),&trampoline,vm_page_size,VM_FLAGS_ANYWHERE)!=KERN_SUCCESS)return;
    uint32_t back[]={0x58000050,0xd61f0200,(uint32_t)(entry+16),(uint32_t)((entry+16)>>32)};
    memcpy((void *)trampoline,expected,16);memcpy((void *)(trampoline+16),back,16);
    sys_icache_invalidate((void *)trampoline,32);
    if(vm_protect(mach_task_self(),trampoline,vm_page_size,FALSE,VM_PROT_READ|VM_PROT_EXECUTE)!=KERN_SUCCESS) {
        (void)vm_deallocate(mach_task_self(),trampoline,vm_page_size);return;
    }
    const uintptr_t replacement=(uintptr_t)phone_prepare_provider;
    uint32_t jump[]={0x58000050,0xd61f0200,(uint32_t)replacement,(uint32_t)(replacement>>32)};
    phone_provider_ivar=ivar;
    phone_prepare_original=(IMP)trampoline;
    if(!install_compositor_code(header,slide,offset,expected,jump,sizeof(expected),"phone provider prepare entry")) {
        phone_prepare_original=NULL;(void)vm_deallocate(mach_task_self(),trampoline,vm_page_size);return;
    }
    method_setImplementation(prepare,(IMP)phone_prepare_provider);
    SEL layout=sel_registerName("layoutSubviews"),reset=sel_registerName("resetVisualProvider");
    Method lm=class_getInstanceMethod(cls,layout),rm=class_getInstanceMethod(cls,reset);
    if(method_abi(lm,"v",0,"") && method_abi(rm,"v",0,"")) {
        phone_layout_original=method_getImplementation(lm);
        if(!class_addMethod(cls,layout,(IMP)phone_layout_provider,method_getTypeEncoding(lm)))
            method_setImplementation(lm,(IMP)phone_layout_provider);
    }
}

/* A status view may be built by UIKit/SpringBoard +load before the injected
 * runtime's constructor. Factory hooks cannot change that existing object.
 * On the main queue, rebuild only a real fallback provider using 22H374's
 * native region teardown, strong ivar ownership and initialization path.
 * Native scale/reference/overflow flags are computed by UIKit again.
 */
static void phone_repair_view(id view,unsigned depth,unsigned *remaining)
{
    if(!view || !depth || !*remaining)return;
    --*remaining;
    stui_repair_bar(view);
    Class bar=objc_lookUpClass("_UIStatusBar");
    if(bar && ((BOOL(*)(id,SEL,Class))objc_msgSend)(view,sel_registerName("isKindOfClass:"),bar) &&
       phone_prepare_original && phone_provider_ivar) {
        id current=object_getIvar(view,phone_provider_ivar);
        Class fallback=objc_lookUpClass("_UIStatusBarVisualProvider_Fallback");
        if(current && object_getClass(current)==fallback) {
            SEL reset=sel_registerName("resetVisualProvider"),prepare=sel_registerName("_prepareVisualProviderIfNeeded");
            Method teardown=class_getInstanceMethod(bar,reset);
            if(method_abi(teardown,"v",0,"")) {
                ((void(*)(id,SEL))objc_msgSend)(view,reset);
                object_setIvarWithStrongDefault(view,phone_provider_ivar,nil);
                ((void(*)(id,SEL,Class))objc_msgSend)(view,sel_registerName("_setVisualProviderClass:"),Nil);
                ((void(*)(id,SEL))phone_prepare_original)(view,prepare);
                ((void(*)(id,SEL))objc_msgSend)(view,sel_registerName("setNeedsLayout"));
                id installed=object_getIvar(view,phone_provider_ivar);
                fprintf(stderr,"Pong UI18: existing status provider rebuilt %s -> %s\n",
                    class_getName(fallback),installed?object_getClassName(installed):"nil");
            }
        }
    }
    id children=((id(*)(id,SEL))objc_msgSend)(view,sel_registerName("subviews"));
    if(!children)return;
    CFIndex count=CFArrayGetCount((CFArrayRef)children);
    for(CFIndex i=0;i<count && *remaining;i++)
        phone_repair_view((id)CFArrayGetValueAtIndex((CFArrayRef)children,i),depth-1,remaining);
}
static void phone_repair_existing(void *unused)
{
    (void)unused;
    if(!phone_ui_process || !phone_selector_installed)return;
    Class cls=objc_lookUpClass("UIApplication");
    id app=cls?((id(*)(id,SEL))objc_msgSend)((id)cls,sel_registerName("sharedApplication")):nil;
    id windows=app?((id(*)(id,SEL))objc_msgSend)(app,sel_registerName("windows")):nil;
    if(!windows)return;
    unsigned remaining=2048;CFIndex count=CFArrayGetCount((CFArrayRef)windows);
    for(CFIndex i=0;i<count && remaining;i++)
        phone_repair_view((id)CFArrayGetValueAtIndex((CFArrayRef)windows,i),16,&remaining);
}
static void phone_selector_image(const struct mach_header *header,intptr_t slide)
{
    static const struct mach_header *uikit;
    static intptr_t uikit_slide;
    static bool attempted;
    if(!phone_ui_process || phone_selector_installed || attempted || header->magic!=MH_MAGIC_64)return;
    static const uint8_t uuid[16]={0x15,0x0b,0x91,0xd9,0xf3,0x7e,0x35,0x9f,
                                 0xad,0x9d,0x95,0x26,0xa4,0x3b,0x81,0xaa};
    const struct mach_header_64 *h=(const void *)header;
    const struct load_command *lc=(const void *)(h+1);
    const char *end=(const char *)lc+h->sizeofcmds;
    for(uint32_t i=0;i<h->ncmds;i++) {
        if((const char *)lc+sizeof(*lc)>end || lc->cmdsize<sizeof(*lc) ||
           lc->cmdsize>(size_t)(end-(const char *)lc))return;
        if(lc->cmd==LC_UUID && lc->cmdsize==sizeof(struct uuid_command) &&
           !memcmp(((const struct uuid_command *)lc)->uuid,uuid,16)){uikit=header;uikit_slide=slide;}
        lc=(const void *)((const char *)lc+lc->cmdsize);
    }
    if(!uikit || !statusbar_provider)return;
    attempted=true;
    const uintptr_t offset=0x1860cbb90ULL-0x184f96000ULL;
    const uintptr_t entry=(uintptr_t)uikit+offset;
    // These four instructions only adjust SP/save registers; no PC-relative
    // operand is relocated. The rest of the original selector runs unchanged.
    static const uint32_t expected[]={0xd10443ff,0x6d0a23e9,0xa90b6ffc,0xa90c67fa};
    if(!executable_image_range((const void *)uikit,uikit_slide,entry,sizeof(expected)) ||
       memcmp((const void *)entry,expected,sizeof(expected)))return;
    vm_address_t trampoline=0;
    if(vm_allocate(mach_task_self(),&trampoline,vm_page_size,VM_FLAGS_ANYWHERE)!=KERN_SUCCESS)return;
    uint32_t back[]={0x58000050,0xd61f0200,(uint32_t)(entry+16),(uint32_t)((entry+16)>>32)};
    memcpy((void *)trampoline,expected,16);memcpy((void *)(trampoline+16),back,16);
    sys_icache_invalidate((void *)trampoline,32);
    if(vm_protect(mach_task_self(),trampoline,vm_page_size,FALSE,VM_PROT_READ|VM_PROT_EXECUTE)!=KERN_SUCCESS){
        (void)vm_deallocate(mach_task_self(),trampoline,vm_page_size);return;
    }
    const uintptr_t replacement=(uintptr_t)phone_selected_class;
    uint32_t jump[]={0x58000050,0xd61f0200,(uint32_t)replacement,(uint32_t)(replacement>>32)};
    phone_selector_original=(Class(*)(id,id))trampoline;
    phone_selector_installed=install_compositor_code(uikit,uikit_slide,offset,
        expected,jump,sizeof(expected),"phone provider selector");
    if(!phone_selector_installed){phone_selector_original=NULL;(void)vm_deallocate(mach_task_self(),trampoline,vm_page_size);}
    else phone_prepare_policy(uikit,uikit_slide);
}

/* Darwin24's system status bar moved out of UIKitCore. It has distinct
 * STUI classes and a 368-byte DynamicSplit provider, not the 240-byte UIKit
 * one. Select and initialize that native implementation; never mix objects,
 * copy a 20G81 offset or replace a small fallback object's isa. */
static Class stui_provider;
static Ivar stui_provider_ivar;
static IMP stui_select_original,stui_prepare_original,stui_layout_original;
static Class stui_selected_class(id screen,id info)
{
    if(stui_provider) {
        bool pong=!screen;
        if(screen) {
            CGRect bounds=((CGRect(*)(id,SEL))objc_msgSend)(screen,sel_registerName("nativeBounds"));
            double scale=((double(*)(id,SEL))objc_msgSend)(screen,sel_registerName("nativeScale"));
            pong=((bounds.size.width==1080 && bounds.size.height==2412) ||
                  (bounds.size.width==2412 && bounds.size.height==1080)) && scale==3.0;
        }
        if(pong) {
            static unsigned reported;
            if(__atomic_fetch_add(&reported,1,__ATOMIC_RELAXED)<3)
                fprintf(stderr,"Pong UI18: SystemStatusUI selected native Pong clock/battery layout\n");
            return stui_provider;
        }
    }
    return ((Class(*)(id,id))stui_select_original)(screen,info);
}
static void stui_prepare_provider(id self,SEL selector)
{
    Class fallback=objc_lookUpClass("STUIStatusBarVisualProvider_Fallback");
    id current=object_getIvar(self,stui_provider_ivar);
    Class assigned=((Class(*)(id,SEL))objc_msgSend)(self,sel_registerName("_visualProviderClass"));
    if(current && object_getClass(current)==fallback) {
        ((void(*)(id,SEL))objc_msgSend)(self,sel_registerName("resetVisualProvider"));
        object_setIvarWithStrongDefault(self,stui_provider_ivar,nil);
        ((void(*)(id,SEL,Class))objc_msgSend)(self,sel_registerName("_setVisualProviderClass:"),Nil);
    } else if(!current && assigned==fallback) {
        ((void(*)(id,SEL,Class))objc_msgSend)(self,sel_registerName("_setVisualProviderClass:"),Nil);
    }
    ((void(*)(id,SEL))stui_prepare_original)(self,selector);
}
static void stui_repair_bar(id bar)
{
    if(!stui_prepare_original || !stui_provider_ivar)return;
    Class cls=objc_lookUpClass("STUIStatusBar");
    if(!cls || !((BOOL(*)(id,SEL,Class))objc_msgSend)(bar,sel_registerName("isKindOfClass:"),cls))return;
    id current=object_getIvar(bar,stui_provider_ivar);
    if(current && object_getClass(current)==objc_lookUpClass("STUIStatusBarVisualProvider_Fallback")) {
        stui_prepare_provider(bar,sel_registerName("_prepareVisualProviderIfNeeded"));
        ((void(*)(id,SEL))objc_msgSend)(bar,sel_registerName("setNeedsLayout"));
    }
}
static void stui_layout_provider(id self,SEL selector)
{
    stui_repair_bar(self);
    ((void(*)(id,SEL))stui_layout_original)(self,selector);
}
static bool stui_install_entry(const struct mach_header *h,intptr_t slide,uintptr_t offset,
    const uint32_t expected[4],IMP replacement,IMP *saved,const char *name)
{
    const uintptr_t entry=(uintptr_t)h+offset;
    if(!executable_image_range((const void *)h,slide,entry,16) || memcmp((const void *)entry,expected,16))return false;
    vm_address_t code=0;
    if(vm_allocate(mach_task_self(),&code,vm_page_size,VM_FLAGS_ANYWHERE)!=KERN_SUCCESS)return false;
    uint32_t back[]={0x58000050,0xd61f0200,(uint32_t)(entry+16),(uint32_t)((entry+16)>>32)};
    memcpy((void *)code,expected,16);memcpy((void *)(code+16),back,16);
    sys_icache_invalidate((void *)code,32);
    if(vm_protect(mach_task_self(),code,vm_page_size,FALSE,VM_PROT_READ|VM_PROT_EXECUTE)!=KERN_SUCCESS) {
        (void)vm_deallocate(mach_task_self(),code,vm_page_size);return false;
    }
    const uintptr_t target=(uintptr_t)replacement;
    uint32_t jump[]={0x58000050,0xd61f0200,(uint32_t)target,(uint32_t)(target>>32)};
    *saved=(IMP)code;
    if(install_compositor_code(h,slide,offset,expected,jump,16,name))return true;
    *saved=NULL;(void)vm_deallocate(mach_task_self(),code,vm_page_size);return false;
}
static void stui_phone_image(const struct mach_header *header,intptr_t slide)
{
    if(!phone_ui_process || stui_select_original || header->magic!=MH_MAGIC_64)return;
    static const uint8_t uuid[]={0x0e,0x5c,0x36,0xcc,0x88,0x46,0x36,0x1c,
                                0x85,0x8b,0xba,0x51,0x0f,0xf8,0xdc,0x0d};
    const struct mach_header_64 *h=(const void *)header;
    const struct load_command *lc=(const void *)(h+1);
    const char *end=(const char *)lc+h->sizeofcmds;bool matched=false;
    for(uint32_t i=0;i<h->ncmds;i++) {
        if((const char *)lc+sizeof(*lc)>end || lc->cmdsize<sizeof(*lc) || lc->cmdsize>(size_t)(end-(const char *)lc))return;
        if(lc->cmd==LC_UUID && lc->cmdsize==sizeof(struct uuid_command))
            matched=!memcmp(((const struct uuid_command *)lc)->uuid,uuid,16);
        lc=(const void *)((const char *)lc+lc->cmdsize);
    }
    if(!matched)return;
    Class cls=objc_lookUpClass("STUIStatusBar"),base=objc_lookUpClass("STUIStatusBarVisualProvider_DynamicSplitMedium");
    Method prepare=cls?class_getInstanceMethod(cls,sel_registerName("_prepareVisualProviderIfNeeded")):NULL;
    Method reset=cls?class_getInstanceMethod(cls,sel_registerName("resetVisualProvider")):NULL;
    Method set=cls?class_getInstanceMethod(cls,sel_registerName("_setVisualProviderClass:")):NULL;
    Ivar ivar=cls?class_getInstanceVariable(cls,"_visualProvider"):NULL;
    if(!base || !method_abi(prepare,"v",0,"") || !method_abi(reset,"v",0,"") ||
       !method_abi(set,"v",1,"#") || !ivar || ivar_getTypeEncoding(ivar)[0]!='@')return;
    Class provider=objc_allocateClassPair(base,"PongSystemStatus18Phone1080",0);
    if(!provider)return;
    if(!class_addMethod(object_getClass((id)provider),sel_registerName("referenceScreenScale"),(IMP)phone_reference_scale,"d16@0:8")) {
        objc_disposeClassPair(provider);return;
    }
    objc_registerClassPair(provider);stui_provider=provider;stui_provider_ivar=ivar;
    // The matched four-instruction prologues only save SP/registers.
    static const uint32_t select_code[]={0xd102c3ff,0x6d0523e9,0xa90667fa,0xa9075ff8};
    if(!stui_install_entry(header,slide,0x1d1d5ef68ULL-0x1d1d5d000ULL,select_code,
        (IMP)stui_selected_class,&stui_select_original,"SystemStatusUI provider selector"))return;
    static const uint32_t prepare_code[]={0xd102c3ff,0x6d0623e9,0xa9075ff8,0xa90857f6};
    if(stui_install_entry(header,slide,0x1d1d61eb8ULL-0x1d1d5d000ULL,prepare_code,
        (IMP)stui_prepare_provider,&stui_prepare_original,"SystemStatusUI provider prepare")) {
        method_setImplementation(prepare,(IMP)stui_prepare_provider);
        SEL layout=sel_registerName("layoutSubviews");Method lm=class_getInstanceMethod(cls,layout);
        if(method_abi(lm,"v",0,"")) {
            stui_layout_original=method_getImplementation(lm);
            if(!class_addMethod(cls,layout,(IMP)stui_layout_provider,method_getTypeEncoding(lm)))
                method_setImplementation(lm,(IMP)stui_layout_provider);
        }
    }
    Class fallback=objc_lookUpClass("STUIStatusBarFallbackItem");
    Method diagnostic=fallback?class_getInstanceMethod(fallback,sel_registerName("canEnableDisplayItem:fromData:")):NULL;
    if(method_abi(diagnostic,"Bc",2,"@@"))method_setImplementation(diagnostic,(IMP)phone_diagnostic_item_enabled);
}

static bool boot_option(const char *key)
{
    char args[2048];size_t size=sizeof(args)-1;
    if(sysctlbyname("kern.bootargs",args,&size,NULL,0))return false;
    args[size<sizeof(args)?size:sizeof(args)-1]='\0';
    const size_t length=strlen(key);
    for(const char *p=args;(p=strstr(p,key));p+=length) {
        if((p==args || p[-1]==' ' || p[-1]=='\t') &&
           (p[length]=='\0' || p[length]==' ' || p[length]=='\t'))return true;
    }
    return false;
}

/* 22H374 QuartzCore, UUID FAF7D7EA-18F0-3C2D-816C-AC56D16C40A3.
 * Its AccelServer::render_update calls renderer(), and on nullptr branches
 * to Server::render_update -> sw_renderer() -> the original SWContext.
 * Replace the method body before the constructor's direct call, as on iOS16.
 * This selects a real software backend; no Metal device is fabricated.
 * Match this image's UUID and entry instructions, never a 20G81 address.
 */
#if defined(PONG_THOR_DESKTOP_GPU)
static bool thor_policy_file_matches(const char *path, const char *expected)
{
    const int fd = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
    if (fd < 0) return false;
    const size_t size = strlen(expected);
    struct stat info;
    char data[32];
    const bool matches = size <= sizeof(data) &&
        fstat(fd, &info) == 0 && S_ISREG(info.st_mode) &&
        info.st_uid == 0 && !(info.st_mode & 022) && info.st_size == (off_t)size &&
        read(fd, data, size) == (ssize_t)size && memcmp(data, expected, size) == 0;
    close(fd);
    return matches;
}

#if defined(PONG_THOR_MEMORY_BUDGET)
static void thor_desktop_gpu_memory_budget(void)
{
    uint64_t memory = 0;
    size_t size = sizeof(memory);
    if (sysctlbyname("hw.memsize", &memory, &size, NULL, 0) == 0 &&
        size == sizeof(memory) && memory > UINT64_C(1024) * 1024 * 1024)
        return;

    /* The signed 22H374 plugin supplies defaults with overwrite=0.
     * Set the resident budget before Metal/Turnip open. Both layers
     * apply their limits only to released or fence-retired resources. */
    const int images = setenv("METAL2VULKAN_SPARE_IMAGE_MIB", "32", 1);
    const int buffers = setenv("TU_DARWIN_BO_CACHE_MIB", "32", 1);
    const char *message = !images && !buffers
        ? "Pong Metal: Thor low-RAM cache defaults applied (spare images 32 MiB, retired BOs 32 MiB)"
        : "Pong Metal: Thor low-RAM cache defaults partially unavailable";
    (void)sysctlbyname("kern.pong_diagnostic", NULL, NULL, (void *)message, strlen(message));
}
#endif


static void
thor_metal_pool_bridge_image(const struct mach_header *mh, intptr_t slide)
{
    (void)slide;
    if (mh->magic != MH_MAGIC_64 || mh->filetype != MH_DYLIB)
        return;
    static const uint8_t known_uuid[][16] = {
        {0x7a, 0xb7, 0xb5, 0xa9, 0x0b, 0x60, 0x3c, 0x67, 0xa7, 0x65, 0x2f, 0x7d, 0xd9, 0x34, 0x6e, 0xb2},
        {0x1e, 0x0d, 0xe7, 0xf9, 0x5a, 0xf9, 0x35, 0xf1, 0x8c, 0x42, 0x63, 0x18, 0xe3, 0x99, 0xd8, 0xfd},
    };
    static const char *const labels[] = {"7AB7B5A9-0B60-3C67-A765-2F7DD9346EB2", "1E0DE7F9-5AF9-35F1-8C42-6318E399D8FD"};
    const struct mach_header_64 *header = (const void *)mh;
    const char *next = (const char *)(header + 1);
    size_t remaining = header->sizeofcmds;
    for (uint32_t i = 0; i < header->ncmds && remaining >= sizeof(struct load_command); ++i) {
        const struct load_command *cmd = (const void *)next;
        if (cmd->cmdsize < sizeof(*cmd) || cmd->cmdsize > remaining)
            return;
        if (cmd->cmd == LC_UUID && cmd->cmdsize >= sizeof(struct uuid_command)) {
            const struct uuid_command *id = (const void *)cmd;
            for (unsigned which = 0; which < sizeof(known_uuid) / sizeof(known_uuid[0]); ++which) {
                if (memcmp(id->uuid, known_uuid[which], sizeof(known_uuid[which])))
                    continue;
                static atomic_uint reported;
                const unsigned bit = 1u << which;
                if (atomic_fetch_or_explicit(&reported, bit, memory_order_relaxed) & bit)
                    return;
                Dl_info info = {0};
                const char *path = dladdr(mh, &info) && info.dli_fname ? info.dli_fname : "<path-unavailable>";
                char line[256];
                int length = snprintf(line, sizeof(line),
                    "Pong Metal: handoff pool bridge %s uuid=%s path=%.144s",
                    which == 0 ? "candidate" : "stock", labels[which], path);
                if (length > 0 && (size_t)length < sizeof(line))
                    (void)sysctlbyname("kern.pong_diagnostic", NULL, NULL, line, (size_t)length);
                return;
            }
            return;
        }
        remaining -= cmd->cmdsize;
        next += cmd->cmdsize;
    }
}

static bool thor_desktop_gpu_policy_ready(void)
{
    /* Require the explicit, signed-build OS policy; a Data marker
     * must not silently turn the desktop GPU on. */
    return thor_policy_file_matches("/System/Library/Extensions/PongAdrenoMetal.publish", "1\n") &&
        thor_policy_file_matches("/System/Library/Extensions/PongAdrenoMetal.clients", "Preferences\nbackboardd\n");
}
#endif

static void compositor_image(const struct mach_header *header,intptr_t slide)
{
    phone_gestalt_image(header,slide);
    phone_presentation();
    phone_selector_image(header,slide);
    stui_phone_image(header,slide);
    wallpaper_resource_policy();
    container_persona_policy();
    springboard_platform_policy();
    springboard_setup_policy();
    launchservices_directories();
    installd_directory();
    pluginkit_directory();
    if((!software_composition && !scanout_format) || ((!software_composition || renderer_installed) && format_installed) || header->magic!=MH_MAGIC_64)return;
    const struct mach_header_64 *h=(const struct mach_header_64 *)header;
    static const unsigned char expected_uuid[16]={
        0xfa,0xf7,0xd7,0xea,0x18,0xf0,0x3c,0x2d,0x81,0x6c,0xac,0x56,0xd1,0x6c,0x40,0xa3};
    const struct load_command *lc=(const void *)(h+1);
    const char *end=(const char *)lc+h->sizeofcmds;
    bool matched=false;
    for(uint32_t i=0;i<h->ncmds;i++) {
        if((const char *)lc+sizeof(*lc)>end || lc->cmdsize<sizeof(*lc) ||
           lc->cmdsize>(size_t)(end-(const char *)lc))return;
        if(lc->cmd==LC_UUID && lc->cmdsize==sizeof(struct uuid_command))
            matched=memcmp(((const struct uuid_command *)lc)->uuid,expected_uuid,16)==0;
        lc=(const void *)((const char *)lc+lc->cmdsize);
    }
    if(!matched)return;
#if defined(PONG_THOR_LINEAR_GPU)
    static bool linear_gpu_installed;
    if (backboard_process && !software_composition && !linear_gpu_installed) {
        /* 22H374 CA::Render::get_compressed_agx_universal_rgb_fourcc.
         * For BGRA alone, branch to the original identity return instead of
         * the &BGA return. QuartzCore then allocates a linear IOSurface. */
        static const uint32_t bgra_to_compressed[] = {0x54000200};
        static const uint32_t bgra_to_identity[] = {0x540001e0};
        linear_gpu_installed = install_compositor_code(header, slide,
            0x1843acbccULL - 0x184339000ULL, bgra_to_compressed,
            bgra_to_identity, sizeof(bgra_to_identity), "desktop BGRA compression bypass");
        const char *message = linear_gpu_installed
            ? "Pong Metal: 22H374 desktop BGRA compression bypass installed"
            : "Pong Metal: 22H374 desktop BGRA compression bypass unavailable; CPU compositor requested";
        (void)sysctlbyname("kern.pong_diagnostic", NULL, NULL, (void *)message, strlen(message));
        if (!linear_gpu_installed)
            software_composition = true;
    }
#endif
    static const uint32_t expected[]={0xa9be4ff4,0xa9017bfd};
    static const uint32_t cpu_renderer[]={0xd2800000,0xd65f03c0}; /* return nullptr */
    if(software_composition && !renderer_installed)renderer_installed=install_compositor_code(header,slide,
        0x184393468ULL-0x184339000ULL,expected,cpu_renderer,sizeof(expected),"stock CPU compositor");
    /* The 18.x IOMFBDisplay format method branches to this implementation.
     * Match its own prologue, not the four-byte thunk or 20G81 vtable.
     * MiniIOMFB's CPU backend consumes uncompressed eight-bit BGRA only. */
    static const uint32_t format_expected[]={0xa9bd57f6,0xa9014ff4,0xa9027bfd};
    static const uint32_t bgra_format[]={0x528a4820,0x72a848e0,0xd65f03c0};
    if(!format_installed)format_installed=install_compositor_code(header,slide,
        0x184396060ULL-0x184339000ULL,format_expected,bgra_format,sizeof(format_expected),"software scanout BGRA");
}

static id singleton(const char *name)
{
    Class cls=objc_lookUpClass(name);SEL selector=sel_registerName("sharedInstance");
    return method_abi(cls?class_getClassMethod(cls,selector):NULL,"@",0,"")?
        ((id(*)(id,SEL))objc_msgSend)((id)cls,selector):nil;
}

static void wake_home(void *unused)
{
    (void)unused;
    /* v73: entry receipt first - v72 had no PongSB file at all, so
     * "never entered the main queue" / "stuck before the first line" /
     * "file open failed" were indistinguishable. */
    pong_sb_note("Pong UI18: wake_home begin");
    id screen=((id(*)(id,SEL))objc_msgSend)((id)objc_lookUpClass("UIScreen"),sel_registerName("mainScreen"));
    if(screen) {
        CGRect native=((CGRect(*)(id,SEL))objc_msgSend)(screen,sel_registerName("nativeBounds"));
        CGRect logical=((CGRect(*)(id,SEL))objc_msgSend)(screen,sel_registerName("bounds"));
        double scale=((double(*)(id,SEL))objc_msgSend)(screen,sel_registerName("scale"));
        double nativeScale=((double(*)(id,SEL))objc_msgSend)(screen,sel_registerName("nativeScale"));
        pong_sb_note("Pong UI18: screen native=%.0fx%.0f logical=%.2fx%.2f scale=%.2f nativeScale=%.2f",
            native.size.width,native.size.height,logical.size.width,logical.size.height,scale,nativeScale);
    }
    id backlight=singleton("SBBacklightController"),lock=singleton("SBLockScreenManager");
    SEL hold=sel_registerName("preventIdleSleepForNumberOfSeconds:");
    if(backlight && method_abi(class_getInstanceMethod(object_getClass(backlight),hold),"v",1,"f"))
        ((void(*)(id,SEL,float))objc_msgSend)(backlight,hold,300.0f);
    SEL wake=sel_registerName("turnOnScreenFullyWithBacklightSource:");
    if(backlight && method_abi(class_getInstanceMethod(object_getClass(backlight),wake),"v",1,"q"))
        ((void(*)(id,SEL,long long))objc_msgSend)(backlight,wake,0);
    SEL locked=sel_registerName("isUILocked"),unlock=sel_registerName("unlockUIFromSource:withOptions:");
    if(lock && method_abi(class_getInstanceMethod(object_getClass(lock),locked),"Bc",0,"") &&
       ((BOOL(*)(id,SEL))objc_msgSend)(lock,locked) &&
       method_abi(class_getInstanceMethod(object_getClass(lock),unlock),"Bc",2,"i@")) {
        BOOL accepted=((BOOL(*)(id,SEL,int,id))objc_msgSend)(lock,unlock,24,nil);
        pong_sb_note("Pong UI18: native home request accepted=%d",accepted);
    }
    pong_sb_note("Pong UI18: main queue wake completed backlight=%d lockscreen=%d",backlight!=nil,lock!=nil);
}

static bool own_directory(const char *path)
{
    if(mkdir(path,0700) && errno!=EEXIST)return false;
    struct stat st;
    return lstat(path,&st)==0 && S_ISDIR(st.st_mode) && st.st_uid==geteuid();
}

static id installation_library(id self,SEL selector)
{ (void)self;(void)selector;return (id)installd_library; }

static id plugin_state_container(id self,SEL selector)
{ (void)self;(void)selector;return (id)pluginkit_container; }

/* The services20 exception stack ends at 22H374's
 * -[PKDAnnotationStore containerURL] + 0xd8: its class-10 com.apple.pkd
 * query fails with error 52. Give this daemon's annotation store a real
 * directory on Data. Keep the stock database filename, serialization,
 * extension discovery and app container/security providers unchanged.
 * A system or mobile instance uses its own UID-owned home, never another
 * user's container. An unavailable directory retains the native failure.
 */
static void pluginkit_directory(void)
{
    if(!pluginkit_process || pluginkit_directory_attempted)return;
    const uid_t uid=geteuid();
    if(uid!=0 && uid!=501)return;
    Class cls=objc_lookUpClass("PKDAnnotationStore");
    Method method=cls?class_getInstanceMethod(cls,sel_registerName("containerURL")):NULL;
    if(!method_abi(method,"@",0,""))return;
    pluginkit_directory_attempted=true;
    const char *home=uid==0?"/private/var/root/Library":"/private/var/mobile/Library";
    char parent[128],path[160];
    snprintf(parent,sizeof(parent),"%s/Pong",home);
    snprintf(path,sizeof(path),"%s/PlugInKit",parent);
    if(!own_directory(home) || !own_directory(parent) || !own_directory(path)) {
        fprintf(stderr,"Pong PK18: annotation directory unavailable uid=%u errno=%d\n",uid,errno);
        return;
    }
    pluginkit_container=CFURLCreateFromFileSystemRepresentation(NULL,
        (const UInt8 *)path,strlen(path),true);
    if(!pluginkit_container)return;
    method_setImplementation(method,(IMP)plugin_state_container);
    fprintf(stderr,"Pong PK18: native annotations use %s uid=%u\n",path,uid);
}

/* 22H374 InstalledContentLibrary's daemonUserDataLibraryDirectory queries
 * class-10 com.apple.installd with a persona, and fatally terminates when
 * the query fails. Use the actual _installd home from this build's passwd
 * on our single-user Data. Its original currentUserDataDirectory and
 * currentUserCachesDirectory append MobileInstallation and Caches to this
 * NSURL. Keep all installation, signature and app-container operations.
 * Resolve the Objective-C method at runtime; never write private ivars or
 * reuse the iOS16 framework's addresses.
 */
static void installd_directory(void)
{
    if(!installd_process || installd_directory_installed || geteuid()!=33)return;
    Class cls=objc_lookUpClass("MIDaemonConfiguration");
    Method method=cls?class_getInstanceMethod(cls,
        sel_registerName("daemonUserDataLibraryDirectory")):NULL;
    if(!method_abi(method,"@",0,""))return;
    const char *dirs[]={"/private/var/installd","/private/var/installd/Library",
        "/private/var/installd/Library/MobileInstallation",
        "/private/var/installd/Library/Caches"};
    for(unsigned i=0;i<sizeof(dirs)/sizeof(dirs[0]);i++) {
        if(!own_directory(dirs[i])) {
            fprintf(stderr,"Pong MI18: daemon directory unavailable %s errno=%d\n",dirs[i],errno);
            return;
        }
    }
    installd_library=CFURLCreateFromFileSystemRepresentation(NULL,
        (const UInt8 *)dirs[1],strlen(dirs[1]),true);
    if(!installd_library)return;
    method_setImplementation(method,(IMP)installation_library);
    installd_directory_installed=true;
    pong_console_line("Pong MI18: native installd uses writable _installd Library");
    const char receipt[] = "Pong MI18: native installd uses writable _installd Library\n";
    int fd = open("/private/var/installd/Library/PongInstallAdapter.txt",
        O_WRONLY|O_CREAT|O_APPEND|O_CLOEXEC|O_NOFOLLOW|O_NONBLOCK,0600);
    if (fd >= 0) {
        struct stat file;
        if (!fstat(fd,&file) && S_ISREG(file.st_mode) && file.st_uid == 33 &&
            file.st_nlink == 1 && (file.st_mode & 07777) == 0600 && file.st_size < 64*1024)
            (void)write(fd,receipt,sizeof(receipt)-1);
        close(fd);
    }
}

static id ls_directory(id self,SEL selector)
{
    (void)self;
    if(selector==sel_registerName("systemContainerBaseURL"))return (id)ls_base;
    if(selector==sel_registerName("systemContainerURL"))return (id)ls_system;
    if(selector==sel_registerName("systemGroupContainerURL"))return (id)ls_shared;
    return (id)ls_user;
}

/* 22H374 databaseContainerURL selects userContainerURL for an ordinary lsd.
 * LS_DATABASE_DIR covers only __LSDefaultsBaseSystemContainerURL, so it
 * cannot avoid userContainerURL's class-10 persona query -> abort path.
 * Apply the port's single-user storage policy to this server's own state,
 * preserving the original database/schema, app containers and XPC methods.
 * These are real directories, not successful replies from a fake MCM daemon.
 */
static void launchservices_directories(void)
{
    if(!launchservices_process || launchservices_installed || geteuid()!=501)return;
    Class cls=objc_lookUpClass("_LSDefaults");
    const char *names[]={"systemContainerBaseURL","systemContainerURL",
        "userContainerURL","systemGroupContainerURL"};
    Method methods[4];
    for(unsigned i=0;i<4;i++) {
        methods[i]=cls?class_getInstanceMethod(cls,sel_registerName(names[i])):NULL;
        if(!method_abi(methods[i],"@",0,""))return;
    }
    const char *dirs[]={
        "/private/var/mobile/Library/Pong",
        "/private/var/mobile/Library/Pong/LaunchServices",
        "/private/var/mobile/Library/Pong/LaunchServices/Library",
        "/private/var/mobile/Library/Pong/LaunchServices/Library/Caches",
        "/private/var/mobile/Library/Pong/LaunchServices/User",
        "/private/var/mobile/Library/Pong/LaunchServices/User/Library",
        "/private/var/mobile/Library/Pong/LaunchServices/User/Library/Caches",
        "/private/var/mobile/Library/Pong/LaunchServices/Shared",
        "/private/var/mobile/Library/Pong/LaunchServices/Shared/Library",
        "/private/var/mobile/Library/Pong/LaunchServices/Shared/Library/Caches"
    };
    for(unsigned i=0;i<sizeof(dirs)/sizeof(dirs[0]);i++) {
        if(!own_directory(dirs[i])) {
            pong_say("Pong LS18: writable directory unavailable %s errno=%d",dirs[i],errno);
            return;
        }
    }
    const char *paths[]={dirs[1],dirs[3],dirs[6],dirs[9]};
    CFURLRef urls[4]={0};
    for(unsigned i=0;i<4;i++) {
        urls[i]=CFURLCreateFromFileSystemRepresentation(NULL,(const UInt8 *)paths[i],strlen(paths[i]),true);
        if(!urls[i]) {
            for(unsigned j=0;j<i;j++)CFRelease(urls[j]);
            return;
        }
    }
    ls_base=urls[0];ls_system=urls[1];ls_user=urls[2];ls_shared=urls[3];
    for(unsigned i=0;i<4;i++)method_setImplementation(methods[i],(IMP)ls_directory);
    launchservices_installed=true;
    pong_say("Pong LS18: native lsd database uses writable single-user paths");
}

/* Keep the real fatal signal. Retain bounded frames because ReportCrash's
 * osanalytics backend is absent in this port; never resume after SIGABRT. */
static void lsd_abort(int number,siginfo_t *info,void *saved)
{
    (void)info;
    ucontext_t *context=saved;
    char line[160];uint64_t fp=0;
    if(context && context->uc_mcontext) {
        int n=snprintf(line,sizeof(line),"Pong LS18: fatal signal=%d pc=%llx lr=%llx\n",
            number,(unsigned long long)context->uc_mcontext->__ss.__pc,
            (unsigned long long)context->uc_mcontext->__ss.__lr);
        if(n>0){(void)write(2,line,(size_t)n<sizeof(line)?(size_t)n:sizeof(line)-1);pong_console_line(line);}
        fp=context->uc_mcontext->__ss.__fp;
    }
    for(unsigned i=0;i<12 && fp>=0x100000000ULL && !(fp&15);i++) {
        uint64_t frame[2];vm_size_t size=0;
        if(vm_read_overwrite(mach_task_self(),(vm_address_t)fp,sizeof(frame),
            (vm_address_t)frame,&size)!=KERN_SUCCESS || size!=sizeof(frame))break;
        int n=snprintf(line,sizeof(line),"Pong LS18: fatal frame[%u]=%llx\n",i,(unsigned long long)frame[1]);
        if(n>0){(void)write(2,line,(size_t)n<sizeof(line)?(size_t)n:sizeof(line)-1);pong_console_line(line);}
        if(frame[0]<=fp || frame[0]-fp>0x100000)break;
        fp=frame[0];
    }
    signal(number,SIG_DFL);raise(number);
}


/* Preserve the actual PosterBoard fatal signal and a small persistent stack.
 * The restored catalog and eligibility policy are the functional changes;
 * this retains a usable cause if the native 22H374 renderer still rejects it.
 */
static int poster_log=-1;
static bool poster_directory_installed;
static CFURLRef poster_data_root,poster_legacy_root;
static IMP poster_base_original,poster_store_original;
static IMP poster_database_original;
static IMP poster_resource_original,poster_resource_tree_original,poster_resource_update_original;

/* The Pong Data volume is ordinary unencrypted APFS. Its real inode class
 * is 0 and it does not advertise MNT_CPROTECT. It cannot persist a class-D
 * encryption policy. Adapt PosterBoard's expected attributes to that volume
 * capability instead of claiming a successful protection class transition.
 * Reachability, POSIX access, directory checks and SQLite validation still
 * run in the original 22H374 methods. Encrypted volumes are untouched. */
static bool poster_unprotected_data(id url)
{
    UInt8 path[1024];
    if(!url || !CFURLGetFileSystemRepresentation((CFURLRef)url,true,path,sizeof(path)))return false;
    const char *p=(const char *)path;
    const char *roots[]={"/private/var/mobile/Library/Application Support/PRBPosterExtensionDataStore",
        "/var/mobile/Library/Application Support/PRBPosterExtensionDataStore",
        "/private/var/mobile/Library/Pong/PosterBoard", "/var/mobile/Library/Pong/PosterBoard"};
    bool scoped=false;
    for(unsigned i=0;i<sizeof(roots)/sizeof(roots[0]);i++) {
        size_t n=strlen(roots[i]);
        if(!strncmp(p,roots[i],n) && (!p[n] || p[n]=='/'))scoped=true;
    }
    struct stat st;struct statfs fs;
    if(!scoped || lstat(p,&st) || S_ISLNK(st.st_mode) || st.st_uid!=geteuid() ||
       statfs(p,&fs) || strcmp(fs.f_fstypename,"apfs") ||
       (strcmp(fs.f_mntonname,"/private/var") && strcmp(fs.f_mntonname,"/var")))return false;
    // Darwin24 bsd/sys/mount.h: MNT_CPROTECT = 0x00000080.
    return !(fs.f_flags&0x00000080u);
}
static CFMutableDictionaryRef poster_volume_resources(id url,id resources)
{
    const CFStringRef *key=dlsym(RTLD_DEFAULT,"NSURLFileProtectionKey");
    if(!resources || CFGetTypeID((CFTypeRef)resources)!=CFDictionaryGetTypeID() ||
       !key || !*key || !CFDictionaryContainsKey((CFDictionaryRef)resources,*key) ||
       !poster_unprotected_data(url))return NULL;
    CFMutableDictionaryRef actual=CFDictionaryCreateMutableCopy(NULL,0,(CFDictionaryRef)resources);
    if(actual)CFDictionaryRemoveValue(actual,*key);
    static unsigned reported;
    if(actual && __atomic_fetch_add(&reported,1,__ATOMIC_RELAXED)<3)
        fprintf(stderr,"Pong Poster18: native resource validation uses unencrypted Data capabilities\n");
    return actual;
}
static BOOL poster_resource_values(id self,SEL selector,id values,id *error)
{
    CFMutableDictionaryRef actual=poster_volume_resources(self,values);
    BOOL result=((BOOL(*)(id,SEL,id,id *))poster_resource_original)(self,selector,actual?(id)actual:values,error);
    if(actual)CFRelease(actual);
    return result;
}
static BOOL poster_resource_tree(id self,SEL selector,id values,id *nonconforming,id *error)
{
    CFMutableDictionaryRef actual=poster_volume_resources(self,values);
    BOOL result=((BOOL(*)(id,SEL,id,id *,id *))poster_resource_tree_original)(self,selector,actual?(id)actual:values,nonconforming,error);
    if(actual)CFRelease(actual);
    return result;
}
static BOOL poster_resource_update(id self,SEL selector,id values,id *error)
{
    CFMutableDictionaryRef actual=poster_volume_resources(self,values);
    BOOL result=((BOOL(*)(id,SEL,id,id *))poster_resource_update_original)(self,selector,actual?(id)actual:values,error);
    if(actual)CFRelease(actual);
    return result;
}
static void poster_volume_policy(void)
{
    if(poster_resource_original)return;
    Class cls=objc_lookUpClass("NSURL");
    Method single=cls?class_getInstanceMethod(cls,sel_registerName("pbf_URLIsReachableAndConformToResourceValues:error:")):NULL;
    Method tree=cls?class_getInstanceMethod(cls,sel_registerName("pbf_recursivelyValidateContentsAreReachableAndConformToResourceValues:URLsNotConformingToAttributes:error:")):NULL;
    Method update=cls?class_getInstanceMethod(cls,sel_registerName("pbf_recursivelyUpdateResourceValues:error:")):NULL;
    if(!method_abi(single,"Bc",2,"@^") || !method_abi(tree,"Bc",3,"@^^") || !method_abi(update,"Bc",2,"@^"))return;
    poster_resource_original=method_setImplementation(single,(IMP)poster_resource_values);
    poster_resource_tree_original=method_setImplementation(tree,(IMP)poster_resource_tree);
    poster_resource_update_original=method_setImplementation(update,(IMP)poster_resource_update);
}

/* The restore can leave a current-version directory without its database.
 * 22H374's option 1 validates/opens an existing database and does not create
 * it. Its native option 8 path opens SQLite read/write/create and runs the
 * matching schema setup. Use that path only for an absent Poster database
 * below a real mobile-owned Data directory; preserve every existing store.
 */
static id poster_database(id self,SEL selector,id url,unsigned long long options,id *error)
{
    UInt8 path[1024];
    if(options==1 && url && CFURLGetFileSystemRepresentation((CFURLRef)url,true,path,sizeof(path))) {
        const char *p=(const char *)path;
        const char *native="/private/var/mobile/Library/Application Support/PRBPosterExtensionDataStore/";
        const char *port="/private/var/mobile/Library/Pong/PosterBoard/";
        const char *native_alias="/var/mobile/Library/Application Support/PRBPosterExtensionDataStore/";
        const char *port_alias="/var/mobile/Library/Pong/PosterBoard/";
        bool own_path=!strncmp(p,native,strlen(native)) || !strncmp(p,port,strlen(port)) ||
            !strncmp(p,native_alias,strlen(native_alias)) || !strncmp(p,port_alias,strlen(port_alias));
        struct stat st;
        if(own_path && lstat(p,&st)!=0 && errno==ENOENT) {
            char parent[sizeof(path)];strlcpy(parent,p,sizeof(parent));
            char *slash=strrchr(parent,'/');if(slash)*slash=0;
            if(slash && lstat(parent,&st)==0 && S_ISDIR(st.st_mode) && st.st_uid==geteuid()) {
                options|=8;
                fprintf(stderr,"Pong Poster18: initialize absent database with native schema %s\n",p);
            }
        }
    }
    id result=((id(*)(id,SEL,id,unsigned long long,id *))poster_database_original)(self,selector,url,options,error);
    if(!result) {
        id text=error && *error?((id(*)(id,SEL))objc_msgSend)(*error,sel_registerName("description")):nil;
        char detail[768]={0};if(text)CFStringGetCString((CFStringRef)text,detail,sizeof(detail),kCFStringEncodingUTF8);
        fprintf(stderr,"Pong Poster18: native database initialization failed: %s errno=%d\n",detail,errno);
    }
    return result;
}

/* The 22H374 scene constructor requires the real PBF data store. Its
 * _baseDataStoreURL: can resolve an unavailable shared/container home on
 * this single-user port. Use a UID-owned directory on the mounted Data,
 * keeping PBF's database, migration, role configuration and renderer intact. */
static void poster_directory_protection(id url)
{
    if(poster_unprotected_data(url))return;
    UInt8 path[1024];
    if(!url || !CFURLGetFileSystemRepresentation((CFURLRef)url,true,path,sizeof(path)))return;
    const char *p=(const char *)path;
    const char *roots[]={"/private/var/mobile/Library/Application Support/PRBPosterExtensionDataStore",
        "/var/mobile/Library/Application Support/PRBPosterExtensionDataStore",
        "/private/var/mobile/Library/Pong/PosterBoard/", "/var/mobile/Library/Pong/PosterBoard/"};
    bool owned=false;for(unsigned i=0;i<sizeof(roots)/sizeof(roots[0]);i++) {
        size_t n=strlen(roots[i]);
        if(!strncmp(p,roots[i],n) && (roots[i][n-1]=='/' || !p[n] || p[n]=='/'))owned=true;
    }
    if(!owned)return;
    const CFStringRef *key=dlsym(RTLD_DEFAULT,"NSURLFileProtectionKey");
    const CFStringRef *value=dlsym(RTLD_DEFAULT,"NSURLFileProtectionNone");
    if(!key || !value || !*key || !*value)return;
    // PFPosterPathURLResourceValues requires IsReadable=YES and protection
    // None. A writable directory with inherited class C still fails this
    // native check. Set the actual filesystem attribute; do not bypass it.
    // 22H374's minimum version is 59 but its current version is 61.
    // Resolve both from the loaded framework. Never create or rename a
    // version directory here: the real migrator owns that transition.
    Class store=objc_lookUpClass("PBFPosterExtensionDataStore");
    SEL minimum=sel_registerName("minimumDataStoreVersion"),current=sel_registerName("dataStoreVersion");
    if(!method_abi(store?class_getClassMethod(store,minimum):NULL,"Qq",0,"") ||
       !method_abi(class_getClassMethod(store,current),"Qq",0,""))return;
    unsigned long long first=((unsigned long long(*)(id,SEL))objc_msgSend)((id)store,minimum);
    unsigned long long last=((unsigned long long(*)(id,SEL))objc_msgSend)((id)store,current);
    if(first>last || last-first>16)return;
    const char *suffixes[]={"","/Extensions","/GalleryCache"};
    unsigned count=1+(unsigned)(last-first+1)*3;
    for(unsigned i=0;i<count;i++) {
        char target[1200];
        if(!i)strlcpy(target,p,sizeof(target));
        else snprintf(target,sizeof(target),"%s/%llu%s",p,first+(i-1)/3,suffixes[(i-1)%3]);
        struct stat st;if(lstat(target,&st) || !S_ISDIR(st.st_mode) || st.st_uid!=geteuid())continue;
        CFURLRef directory=CFURLCreateFromFileSystemRepresentation(NULL,(const UInt8 *)target,strlen(target),true);
        if(!directory)continue;
        SEL get=sel_registerName("getResourceValue:forKey:error:"),set=sel_registerName("setResourceValue:forKey:error:");
        Class cls=object_getClass((id)directory);
        Method gm=class_getInstanceMethod(cls,get),sm=class_getInstanceMethod(cls,set);
        if(method_abi(gm,"Bc",3,"^@^") && method_abi(sm,"Bc",3,"@@^")) {
            id current=nil,error=nil;
            BOOL readable=((BOOL(*)(id,SEL,id *,id,id *))objc_msgSend)((id)directory,get,&current,(id)*key,&error);
            if(!readable || !current || !CFEqual((CFTypeRef)current,*value)) {
                error=nil;
                BOOL applied=((BOOL(*)(id,SEL,id,id,id *))objc_msgSend)((id)directory,set,(id)*value,(id)*key,&error);
                char detail[512]={0};id text=error?((id(*)(id,SEL))objc_msgSend)(error,sel_registerName("description")):nil;
                if(text)CFStringGetCString((CFStringRef)text,detail,sizeof(detail),kCFStringEncodingUTF8);
                static unsigned reported;
                if(!applied || __atomic_fetch_add(&reported,1,__ATOMIC_RELAXED)<8)
                    fprintf(stderr,"Pong Poster18: native directory protection %s %s %s\n",applied?"set":"failed",target,detail);
            }
        }
        CFRelease(directory);
    }
}
static void poster_base_directory_protection(id base)
{
    // PBF's base is Application Support (or the distinct shared/legacy
    // fallback), not PRBPosterExtensionDataStore itself. Derive the same
    // store root as its native NSURL category before touching attributes.
    UInt8 path[1024];
    if(!base || !CFURLGetFileSystemRepresentation((CFURLRef)base,true,path,sizeof(path)))return;
    const char *p=(const char *)path;
    const char *allowed[]={"/private/var/mobile/Library/Application Support",
        "/var/mobile/Library/Application Support",
        "/private/var/mobile/Library/Pong/PosterBoard/Shared",
        "/private/var/mobile/Library/Pong/PosterBoard/Legacy",
        "/var/mobile/Library/Pong/PosterBoard/Shared",
        "/var/mobile/Library/Pong/PosterBoard/Legacy"};
    bool owned=false;
    for(unsigned i=0;i<sizeof(allowed)/sizeof(allowed[0]);i++)
        if(!strcmp(p,allowed[i]))owned=true;
    if(!owned)return;
    struct stat st;
    if(lstat(p,&st) || !S_ISDIR(st.st_mode) || st.st_uid!=geteuid() || access(p,W_OK|X_OK))return;
    CFURLRef root=CFURLCreateCopyAppendingPathComponent(NULL,(CFURLRef)base,
        CFSTR("PRBPosterExtensionDataStore"),true);
    if(root){poster_directory_protection((id)root);CFRelease(root);}
}
static id poster_base_url(id self,SEL selector,BOOL use_shared)
{
    id original=((id(*)(id,SEL,BOOL))poster_base_original)(self,selector,use_shared);
    UInt8 path[1024];
    if(original && CFURLGetFileSystemRepresentation((CFURLRef)original,true,path,sizeof(path)) &&
       access((const char *)path,W_OK|X_OK)==0) {
        poster_base_directory_protection(original);return original;
    }
    id selected=(id)(use_shared?poster_data_root:poster_legacy_root);
    poster_base_directory_protection(selected);return selected;
}
static id poster_store(id self,SEL selector)
{
    (void)selector;id error=nil;
    /* The stock dataStore accessor is a tail call to this exact method with
     * a null error pointer. Preserve the operation and retain its real error. */
    id store=((id(*)(id,SEL,id *))objc_msgSend)(self,sel_registerName("_dataStoreWithError:"),&error);
    if(!store) {
        id description=error?((id(*)(id,SEL))objc_msgSend)(error,sel_registerName("description")):nil;
        char detail[768]={0};
        if(description)CFStringGetCString((CFStringRef)description,detail,sizeof(detail),kCFStringEncodingUTF8);
        fprintf(stderr,"Pong Poster18: native DataStore unavailable: %s\n",detail);
        if(poster_log>=0){(void)write(poster_log,detail,strlen(detail));(void)write(poster_log,"\n",1);}
    }
    return store;
}
static void poster_image(const struct mach_header *header,intptr_t slide)
{
    (void)header;(void)slide;
    wallpaper_resource_policy();
    // MCM may arrive after PosterFoundation. Keep applying its real
    // single-user policy on later image loads, even once PBF is adapted.
    container_persona_policy();
    poster_volume_policy();
    if(poster_directory_installed)return;
    if(geteuid()!=501)return;
    Class cls=objc_lookUpClass("PBFPosterExtensionDataStoreXPCServiceGlue");
    Method base=cls?class_getInstanceMethod(cls,sel_registerName("_baseDataStoreURL:")):NULL;
    Method store=cls?class_getInstanceMethod(cls,sel_registerName("dataStore")):NULL;
    Method error=cls?class_getInstanceMethod(cls,sel_registerName("_dataStoreWithError:")):NULL;
    if(!method_abi(base,"@",1,"B") || !method_abi(store,"@",0,"") ||
       !method_abi(error,"@",1,"^"))return;
    const char *parent="/private/var/mobile/Library/Pong";
    const char *path="/private/var/mobile/Library/Pong/PosterBoard";
    const char *shared="/private/var/mobile/Library/Pong/PosterBoard/Shared";
    const char *legacy="/private/var/mobile/Library/Pong/PosterBoard/Legacy";
    if(!own_directory(parent) || !own_directory(path) ||
       !own_directory(shared) || !own_directory(legacy))return;
    // Native migration compares the old per-user and new shared locations;
    // do not alias those two distinct roots to the same directory.
    poster_data_root=CFURLCreateFromFileSystemRepresentation(NULL,(const UInt8 *)shared,strlen(shared),true);
    poster_legacy_root=CFURLCreateFromFileSystemRepresentation(NULL,(const UInt8 *)legacy,strlen(legacy),true);
    if(!poster_data_root || !poster_legacy_root)return;
    poster_base_original=method_setImplementation(base,(IMP)poster_base_url);
    poster_store_original=method_setImplementation(store,(IMP)poster_store);
    Class database=objc_lookUpClass("PBFPosterExtensionDataStoreSQLiteDatabase");
    Method initializer=database?class_getInstanceMethod(database,sel_registerName("initWithURL:options:error:")):NULL;
    if(method_abi(initializer,"@",3,"@Q^")) {
        poster_database_original=method_setImplementation(initializer,(IMP)poster_database);
    }
    poster_directory_installed=true;
    container_persona_policy();
    fprintf(stderr,"Pong Poster18: native DataStore fallback uses writable %s\n",path);
}
static void poster_fatal(int number,siginfo_t *info,void *saved)
{
    (void)info;ucontext_t *context=saved;
    char line[176];uint64_t fp=0;
    if(context && context->uc_mcontext) {
        int n=snprintf(line,sizeof(line),"Pong Poster18: fatal=%d pc=%llx lr=%llx\n",number,
            (unsigned long long)context->uc_mcontext->__ss.__pc,
            (unsigned long long)context->uc_mcontext->__ss.__lr);
        if(n>0 && n<(int)sizeof(line)){(void)write(2,line,(size_t)n);if(poster_log>=0)(void)write(poster_log,line,(size_t)n);}
        fp=context->uc_mcontext->__ss.__fp;
    }
    for(unsigned i=0;i<12 && fp>=0x100000000ULL && !(fp&15);i++) {
        uint64_t frame[2];vm_size_t bytes=0;
        if(vm_read_overwrite(mach_task_self(),(vm_address_t)fp,sizeof(frame),(vm_address_t)frame,&bytes)!=KERN_SUCCESS || bytes!=sizeof(frame))break;
        int n=snprintf(line,sizeof(line),"Pong Poster18: frame[%u]=%llx\n",i,(unsigned long long)frame[1]);
        if(n>0 && n<(int)sizeof(line)){(void)write(2,line,(size_t)n);if(poster_log>=0)(void)write(poster_log,line,(size_t)n);}
        if(frame[0]<=fp || frame[0]-fp>0x100000)break;
        fp=frame[0];
    }
    if(poster_log>=0)(void)fsync(poster_log);
    signal(number,SIG_DFL);raise(number);
}
static void poster_fatal_capture(void)
{
    poster_log=open("/private/var/mobile/Library/Logs/Pong-Poster18.log",O_WRONLY|O_CREAT|O_APPEND,0644);
    Dl_info image={0};
    if(dladdr((const void *)objc_msgSend,&image)) {
        char line[192];int n=snprintf(line,sizeof(line),"Pong Poster18: pid=%d libobjc base=%p\n",getpid(),image.dli_fbase);
        if(n>0 && n<(int)sizeof(line) && poster_log>=0)(void)write(poster_log,line,(size_t)n);
    }
    struct sigaction action={0};action.sa_sigaction=poster_fatal;
    action.sa_flags=SA_SIGINFO|SA_RESETHAND;sigemptyset(&action.sa_mask);
    (void)sigaction(SIGTRAP,&action,NULL);(void)sigaction(SIGABRT,&action,NULL);
}

/* This board has no CoreMotion activity provider yet. The iPhone identity
 * must not make contextstored synchronously query a nonexistent motion
 * service during CDMonitorManager construction. On 22H374 that blocks its
 * complete context endpoint, then PowerUIAgent and the Battery pane. Use
 * CoreMotion's own unavailable branch, scoped to contextstored; preserve
 * all battery, power, location and other contextual monitors. */
static bool context_motion_installed;
static BOOL context_activity_unavailable(id self,SEL selector)
{ (void)self;(void)selector;return NO; }
static void context_motion_image(const struct mach_header *header,intptr_t slide)
{
    (void)header;(void)slide;
    if(context_motion_installed)return;
    Class cls=objc_lookUpClass("CMMotionActivityManager");
    Method method=cls?class_getClassMethod(cls,sel_registerName("isActivityAvailable")):NULL;
    if(!method_abi(method,"Bc",0,""))return;
    method_setImplementation(method,(IMP)context_activity_unavailable);
    context_motion_installed=true;
    fprintf(stderr,"Pong context18: activity hardware unavailable; native motion monitor remains inactive\n");
}

/* The 22H374 SBRendererService aborts in NUDevice_iOS when CoreImage
 * cannot construct its Metal context. Keep a real CPU CoreImage context
 * as a failure-only path for this image service, never a fake renderer or
 * a global switch away from the board's working Metal compositor. */
static IMP poster_ci_original;
static id poster_ci_context(id self,SEL selector,id device,id options)
{
    id context=((id(*)(id,SEL,id,id))poster_ci_original)(self,selector,device,options);
    if(context)return context;
    const CFStringRef *software=dlsym(RTLD_DEFAULT,"kCIContextUseSoftwareRenderer");
    if(!software || !*software)return nil;
    CFMutableDictionaryRef fallback=options?CFDictionaryCreateMutableCopy(NULL,0,(CFDictionaryRef)options):
        CFDictionaryCreateMutable(NULL,0,&kCFTypeDictionaryKeyCallBacks,&kCFTypeDictionaryValueCallBacks);
    if(!fallback)return nil;
    CFDictionarySetValue(fallback,*software,kCFBooleanTrue);
    context=((id(*)(id,SEL,id))objc_msgSend)(self,sel_registerName("contextWithOptions:"),(id)fallback);
    CFRelease(fallback);
    return context;
}
static void poster_ci_image(const struct mach_header *header,intptr_t slide)
{
    (void)header;(void)slide;if(poster_ci_original)return;
    Class cls=objc_lookUpClass("CIContext");
    Method method=cls?class_getClassMethod(cls,sel_registerName("contextWithMTLDevice:options:")):NULL;
    Method cpu=cls?class_getClassMethod(cls,sel_registerName("contextWithOptions:")):NULL;
    if(!method_abi(method,"@",2,"@@") || !method_abi(cpu,"@",1,"@"))return;
    poster_ci_original=method_setImplementation(method,(IMP)poster_ci_context);
}

/* PowerUI's 22H374 startup asks CoreDuet synchronously for external power.
 * The board publishes that measurement on its real IOPMPowerSource. Read
 * it directly rather than making battery discovery depend on optional
 * contextual monitors. Absence of a valid measurement retains native code. */
static IMP powerui_external_original;
static CFTypeRef (*powerui_property)(uint32_t,CFStringRef,CFAllocatorRef,uint32_t);
static BOOL powerui_external(id self,SEL selector)
{
    uint32_t source=matching_service(0,service_matching("IOPMPowerSource"));
    if(source) {
        CFTypeRef value=powerui_property(source,CFSTR("ExternalConnected"),NULL,0);
        release_service(source);
        if(value) {
            bool valid=CFGetTypeID(value)==CFBooleanGetTypeID();
            BOOL connected=valid?CFBooleanGetValue(value):NO;
            CFRelease(value);if(valid)return connected;
        }
    }
    return ((BOOL(*)(id,SEL))powerui_external_original)(self,selector);
}
static void powerui_image(const struct mach_header *header,intptr_t slide)
{
    (void)header;(void)slide;if(powerui_external_original)return;
    Class cls=objc_lookUpClass("PowerUISmartChargeManager");
    Method method=cls?class_getInstanceMethod(cls,sel_registerName("isExternalConnected")):NULL;
    if(!method_abi(method,"Bc",0,""))return;
    // Image callbacks also run while dyld installs dependencies. Resolve
    // existing exports only; never recursively dlopen from this callback.
    void *kit=RTLD_DEFAULT;
    service_matching=(__typeof__(service_matching))dlsym(kit,"IOServiceMatching");
    matching_service=(__typeof__(matching_service))dlsym(kit,"IOServiceGetMatchingService");
    release_service=(__typeof__(release_service))dlsym(kit,"IOObjectRelease");
    powerui_property=(__typeof__(powerui_property))dlsym(kit,"IORegistryEntryCreateCFProperty");
    if(!service_matching||!matching_service||!release_service||!powerui_property)return;
    powerui_external_original=method_setImplementation(method,(IMP)powerui_external);
}

static id process_root(id self,SEL selector)
{ (void)self;(void)selector;return (id)container_root; }
static id process_library(id self,SEL selector)
{ (void)self;(void)selector;return (id)container_library; }

/* The 22H374 backboardd trace enters this provider with a nil system
 * container Library string. Supply the port's actual, process-owned Data
 * directories. Its original cachesPath appends Caches to libraryPath.
 * This is confined to backboardd, with no global NSURL/exception overrides
 * and no changes to launchd's native system-proxy personas.
 */
static void backboard_container(void)
{
    Class cls=objc_lookUpClass("BSSystemContainerForCurrentProcessPathProvider");
    SEL root_sel=sel_registerName("defaultPath"),lib_sel=sel_registerName("libraryPath");
    Method root=cls?class_getInstanceMethod(cls,root_sel):NULL;
    Method lib=cls?class_getInstanceMethod(cls,lib_sel):NULL;
    if(!root || !lib || method_getNumberOfArguments(root)!=2 || method_getNumberOfArguments(lib)!=2)return;
    char *root_type=method_copyReturnType(root),*lib_type=method_copyReturnType(lib);
    bool valid=root_type && lib_type && root_type[0]=='@' && lib_type[0]=='@';
    free(root_type);free(lib_type);
    if(!valid)return;
    const char *base="/private/var/mobile/Library/Pong";
    const char *path="/private/var/mobile/Library/Pong/backboardd";
    const char *library="/private/var/mobile/Library/Pong/backboardd/Library";
    if(!own_directory(base) || !own_directory(path) || !own_directory(library) ||
       !own_directory("/private/var/mobile/Library/Pong/backboardd/Library/Caches")) {
        pong_say("Pong UI18: backboardd Data directories unavailable errno=%d",errno);return;
    }
    container_root=CFURLCreateFromFileSystemRepresentation(NULL,(const UInt8 *)path,strlen(path),true);
    container_library=CFURLCreateFromFileSystemRepresentation(NULL,(const UInt8 *)library,strlen(library),true);
    if(!container_root || !container_library)return;
    class_replaceMethod(cls,root_sel,(IMP)process_root,method_getTypeEncoding(root));
    class_replaceMethod(cls,lib_sel,(IMP)process_library,method_getTypeEncoding(lib));
    pong_say("Pong UI18: backboardd container uses writable %s",path);
}

/* Sileo is a no-container system app. Resolve its real mobile directories before
 * Evander caches them and avoid requesting SEP-backed file protection for its
 * local cache/database on this software-keybag development port. */
static IMP sileo_urls_original,sileo_mkdir_original;
static id sileo_urls(id self,SEL selector,unsigned long directory,unsigned long domains)
{
    const char *path=directory==5?"/var/mobile/Library":directory==9?"/var/mobile/Documents":
        directory==13?"/var/mobile/Library/Caches":directory==14?"/var/mobile/Library/Application Support":NULL;
    if(path && (domains&1)) {
        CFURLRef url=CFURLCreateFromFileSystemRepresentation(NULL,(const UInt8 *)path,strlen(path),true);
        if(url) {CFArrayRef list=CFArrayCreate(NULL,(const void **)&url,1,&kCFTypeArrayCallBacks);CFRelease(url);
            return list?((id(*)(id,SEL))objc_msgSend)((id)list,sel_registerName("autorelease")):nil;}
    }
    return ((id(*)(id,SEL,unsigned long,unsigned long))sileo_urls_original)(self,selector,directory,domains);
}
static BOOL sileo_mkdir(id self,SEL selector,id url,BOOL parents,id attributes,id *error)
{
    UInt8 path[PATH_MAX]={0};CFMutableDictionaryRef sanitized=NULL;
    if(url && CFURLGetFileSystemRepresentation((CFURLRef)url,true,path,sizeof(path)) &&
       (!strncmp((const char *)path,"/var/jb/var/mobile/",19) ||
        !strncmp((const char *)path,"/private/var/jb/var/mobile/",27) ||
        !strncmp((const char *)path,"/var/mobile/",12) ||
        !strncmp((const char *)path,"/private/var/mobile/",20)) && attributes &&
       CFGetTypeID((CFTypeRef)attributes)==CFDictionaryGetTypeID()) {
        sanitized=CFDictionaryCreateMutableCopy(NULL,0,(CFDictionaryRef)attributes);
        if(sanitized)CFDictionaryRemoveValue(sanitized,CFSTR("NSFileProtectionKey"));
    }
    BOOL result=((BOOL(*)(id,SEL,id,BOOL,id,id *))sileo_mkdir_original)(self,selector,url,parents,
        sanitized?(id)sanitized:attributes,error);
    if(sanitized)CFRelease(sanitized);
    if(!result)fprintf(stderr,"Pong Sileo: directory create failed: %s\n",url?(const char *)path:"(nil)");
    return result;
}
static void sileo_directories(void)
{
    setenv("HOME","/var/mobile",1);setenv("CFFIXED_USER_HOME","/var/mobile",1);
    setenv("TMPDIR","/var/mobile/Library/Caches/",1);
    Class cls=objc_lookUpClass("NSFileManager");
    SEL urls=sel_registerName("URLsForDirectory:inDomains:");
    SEL mkdir=sel_registerName("createDirectoryAtURL:withIntermediateDirectories:attributes:error:");
    Method um=class_getInstanceMethod(cls,urls),dm=class_getInstanceMethod(cls,mkdir);
    if(um && dm) {
        sileo_urls_original=method_getImplementation(um);sileo_mkdir_original=method_getImplementation(dm);
        class_replaceMethod(cls,urls,(IMP)sileo_urls,method_getTypeEncoding(um));
        class_replaceMethod(cls,mkdir,(IMP)sileo_mkdir,method_getTypeEncoding(dm));
        fprintf(stderr,"Pong Sileo: writable mobile database directories selected\n");
    }
}

/* Setup is skipped on this port. A fresh Data seed therefore has locale
 * preferences but no AppleKeyboards list (confirmed in services108).
 * Initialize only an absent/empty native list; retain user choices. */
static void keyboard_defaults(void)
{
    CFTypeRef old=CFPreferencesCopyValue(CFSTR("AppleKeyboards"),kCFPreferencesAnyApplication,
        kCFPreferencesCurrentUser,kCFPreferencesAnyHost);
    bool needed=!old || (CFGetTypeID(old)==CFArrayGetTypeID() && !CFArrayGetCount(old));
    // 22H374's native TextInput preferences normalized the old seed to
    // Pinyin-Simplified, but AppleKeyboards still named the unmatched Pinyin
    // layout. Migrate that exact obsolete seed; retain all user-added modes.
    if(old && CFGetTypeID(old)==CFArrayGetTypeID() && CFArrayGetCount(old)) {
        CFMutableArrayRef updated=CFArrayCreateMutableCopy(NULL,0,(CFArrayRef)old);
        bool changed=false;
        if(updated)for(CFIndex i=0;i<CFArrayGetCount(updated);i++) {
            CFTypeRef mode=CFArrayGetValueAtIndex(updated,i);
            if(mode && CFGetTypeID(mode)==CFStringGetTypeID() &&
               CFEqual(mode,CFSTR("zh_Hans-Pinyin@sw=Pinyin;hw=US"))) {
                CFArraySetValueAtIndex(updated,i,CFSTR("zh_Hans-Pinyin@sw=Pinyin-Simplified;hw=US"));changed=true;
            }
        }
        if(changed) {
            CFPreferencesSetValue(CFSTR("AppleKeyboards"),updated,kCFPreferencesAnyApplication,
                kCFPreferencesCurrentUser,kCFPreferencesAnyHost);
            (void)CFPreferencesSynchronize(kCFPreferencesAnyApplication,kCFPreferencesCurrentUser,kCFPreferencesAnyHost);
        }
        if(updated)CFRelease(updated);
    }
    if(old)CFRelease(old);
    if(!needed)return;
    const void *modes[]={CFSTR("zh_Hans-Pinyin@sw=Pinyin-Simplified;hw=US"),
        CFSTR("en_US@sw=QWERTY;hw=Automatic"),CFSTR("emoji@sw=Emoji")};
    CFArrayRef list=CFArrayCreate(NULL,modes,3,&kCFTypeArrayCallBacks);
    if(!list)return;
    CFPreferencesSetValue(CFSTR("AppleKeyboards"),list,kCFPreferencesAnyApplication,
        kCFPreferencesCurrentUser,kCFPreferencesAnyHost);
    Boolean saved=CFPreferencesSynchronize(kCFPreferencesAnyApplication,
        kCFPreferencesCurrentUser,kCFPreferencesAnyHost);
    CFRelease(list);
    fprintf(stderr,"Pong keyboard: native Pinyin/English/emoji defaults saved=%u\n",saved);
}

static bool keyboard_native_scheduled;
static bool keyboard_phone_installed;
static void keyboard_retire_old_images(void)
{
    const char *marker="/private/var/mobile/Library/Caches/.pong-keyboard-phone152";
    struct stat st={0};if(lstat(marker,&st)==0)return;
    // Only rendered key images are disposable. Do not touch learned words,
    // replacements, input modes or the keyboard dictionaries/databases.
    Class manager=objc_lookUpClass("NSFileManager");
    if(!manager)return;
    id files=((id(*)(id,SEL))objc_msgSend)((id)manager,sel_registerName("defaultManager"));
    SEL remove=sel_registerName("removeItemAtPath:error:");
    if(!method_abi(class_getInstanceMethod(manager,remove),"B",2,"@^"))return;
    id path=(id)CFSTR("/private/var/mobile/Library/Caches/com.apple.keyboards/images");
    const char *images="/private/var/mobile/Library/Caches/com.apple.keyboards/images";
    const int present=lstat(images,&st);
    bool done=present!=0 && errno==ENOENT;
    if(present==0 && S_ISDIR(st.st_mode) && !S_ISLNK(st.st_mode))
        done=((BOOL(*)(id,SEL,id,id *))objc_msgSend)(files,remove,path,NULL);
    if(done) {
        int fd=open(marker,O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW,0600);
        if(fd>=0)close(fd);
    }
}
static long long keyboard_phone_idiom(id self,SEL selector)
{(void)self;(void)selector;return 0; /* UIUserInterfaceIdiomPhone */}
static void keyboard_phone_image(const struct mach_header *header,intptr_t slide)
{
    (void)header;(void)slide;
    if(keyboard_phone_installed)return;
    Class cls=objc_lookUpClass("UIDevice");
    Method method=cls?class_getInstanceMethod(cls,sel_registerName("userInterfaceIdiom")):NULL;
    if(!method_abi(method,"q",0,""))return;
    // InputUI is a separate native process: the SpringBoard-only phone
    // adapter never reached its keyboard/number keyplane layout decisions.
    // Use UIKit's phone layout/resources, not a coordinate/123-key emulation.
    method_setImplementation(method,(IMP)keyboard_phone_idiom);
    keyboard_phone_installed=true;
    fprintf(stderr,"Pong keyboard: InputUI native phone idiom selected\n");
}
static void keyboard_native_refresh(void *unused)
{
    (void)unused;
    Class cls=objc_lookUpClass("TIPreferencesController");
    SEL shared=sel_registerName("sharedPreferencesController");
    SEL update=sel_registerName("updateInputModes:");
    SEL shown=sel_registerName("setKeyboardShownByTouch:");
    SEL sync=sel_registerName("synchronizePreferences");
    if(!method_abi(class_getClassMethod(cls,shared),"@",0,"") ||
       !method_abi(class_getInstanceMethod(cls,update),"v",1,"@") ||
       !(method_abi(class_getInstanceMethod(cls,shown),"v",1,"B") ||
         method_abi(class_getInstanceMethod(cls,shown),"v",1,"c")))return;
    CFTypeRef list=CFPreferencesCopyValue(CFSTR("AppleKeyboards"),kCFPreferencesAnyApplication,
        kCFPreferencesCurrentUser,kCFPreferencesAnyHost);
    if(!list || CFGetTypeID(list)!=CFArrayGetTypeID() || !CFArrayGetCount(list)){if(list)CFRelease(list);return;}
    id prefs=((id(*)(id,SEL))objc_msgSend)((id)cls,shared);
    if(prefs) {
        // Use the selected OS's setter to register language preferences,
        // update its cache and issue its normal input-mode notifications.
        ((void(*)(id,SEL,id))objc_msgSend)(prefs,update,(id)list);
        ((void(*)(id,SEL,BOOL))objc_msgSend)(prefs,shown,YES);
        if(method_abi(class_getInstanceMethod(cls,sync),"v",0,""))
            ((void(*)(id,SEL))objc_msgSend)(prefs,sync);
        fprintf(stderr,"Pong keyboard: native TextInput modes refreshed count=%ld; touch keyboard enabled\n",(long)CFArrayGetCount(list));
    }
    CFRelease(list);
}
static void keyboard_native_image(const struct mach_header *header,intptr_t slide)
{
    (void)header;(void)slide;
    if(keyboard_native_scheduled || !objc_lookUpClass("TIPreferencesController"))return;
    keyboard_native_scheduled=true;
    // Never execute ObjC preference setters while dyld is loading images.
    dispatch_after_f(dispatch_time(DISPATCH_TIME_NOW,2*NSEC_PER_SEC),dispatch_get_main_queue(),NULL,keyboard_native_refresh);
}

#if defined(PONG_THOR_MOTION_LINK)
struct thor_motion_range {
    float minimum, maximum, preferred;
};
struct thor_motion_policy {
    int reduce_motion, low_power, animations;
};
struct thor_motion_notice {
    char text[320];
};

static id (*thor_motion_original_make)(id, SEL, id, SEL);
static void (*thor_motion_original_rate)(id, SEL, long);
static void (*thor_motion_original_range)(id, SEL, struct thor_motion_range);
static _Atomic(void (*)(id, SEL, id)) thor_motion_original_fire;
static double (*thor_motion_timestamp)(id, SEL);
static double (*thor_motion_target_timestamp)(id, SEL);
static struct thor_motion_range (*thor_motion_native_range)(id, SEL);
static BOOL (*thor_motion_native_reduce)(void);
static Class thor_motion_view, thor_motion_process_info;
static SEL thor_motion_range_sel, thor_motion_enabled_sel;
static SEL thor_motion_process_info_sel, thor_motion_low_power_sel;
static SEL thor_motion_action_sel, thor_motion_timestamp_sel, thor_motion_target_timestamp_sel;
static char thor_motion_target_key;
static bool thor_motion_adapt_rate, thor_motion_cap_rate;
static _Atomic unsigned thor_motion_policy_snapshot;
static _Thread_local bool thor_motion_forwarding_rate;
static mach_timebase_info_data_t thor_motion_timebase;
static bool thor_motion_fire_checked; /* Main thread only. */
enum { THOR_MOTION_CADENCE_RECEIPT_LIMIT = 12 };
static _Atomic uint64_t thor_motion_cadence_after_ticks;
static _Thread_local struct {
    const void *link;
    uint64_t prior_ticks, window_after;
    uint64_t callback_sum_us, callback_min_us, callback_max_us;
    uint64_t period_sum_us, period_min_us, period_max_us;
    unsigned pairs, periods;
} thor_motion_cadence;
static _Atomic unsigned thor_motion_cadence_receipts;
static _Atomic bool thor_motion_first_fire;
static _Atomic unsigned thor_motion_link_receipts;
static os_unfair_lock thor_motion_rate_lock = OS_UNFAIR_LOCK_INIT;
static struct thor_motion_rate_key {
    struct thor_motion_range range;
    long scalar;
    unsigned policy;
    bool modern, changed;
} thor_motion_rates[24];
static unsigned thor_motion_rate_count;
static bool thor_motion_native_cadence; /* Set before installing QuartzCore hooks. */
static bool thor_motion_policy_ready; /* Read and written only on the main thread. */
static _Atomic unsigned thor_motion_receipts;
static _Atomic bool thor_motion_notes_ready;
static _Atomic unsigned thor_motion_early_links, thor_motion_early_rates, thor_motion_early_ranges;
enum {
    THOR_MOTION_EARLY_RANGE_WRITING = 1,
    THOR_MOTION_EARLY_RANGE_READY,
};
static _Atomic unsigned thor_motion_first_early_range_state;
static struct {
    struct thor_motion_range requested;
    bool main_thread;
} thor_motion_first_early_range;

static Method thor_motion_owned_method(Class owner, SEL selector);

enum thor_motion_status {
    THOR_MOTION_NO_CLASS, THOR_MOTION_BAD_ABI, THOR_MOTION_READY
};
static enum thor_motion_status thor_motion_install_status;

static void
thor_motion_write(void *context)
{
    struct thor_motion_notice *notice = context;
    char boot[37] = {0};
    size_t boot_size = sizeof(boot);
    if (sysctlbyname("kern.bootsessionuuid", boot, &boot_size, NULL, 0) ||
        boot_size != sizeof(boot) || boot[sizeof(boot) - 1] != '\0')
        strlcpy(boot, "-", sizeof(boot));
    char line[448];
    int count = snprintf(line, sizeof(line), "PONG MOTION: program=%s boot=%s %s\n",
        getprogname() ?: "-", boot, notice->text);
    free(notice);
    if (count <= 0 || (size_t)count >= sizeof(line))
        return;

    int fd = open("/private/var/mobile/Library/Logs/PongMotion-v147.txt",
        O_WRONLY | O_APPEND | O_CREAT | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK, 0600);
    struct stat status;
    bool stored = fd >= 0 && !fstat(fd, &status) && S_ISREG(status.st_mode) &&
        status.st_nlink == 1 && status.st_uid == 501 &&
        (status.st_mode & 0777) == 0600 && status.st_size < 128 * 1024 &&
        write(fd, line, (size_t)count) == count;
    if (fd >= 0)
        close(fd);
    if (!stored) {
        line[count - 1] = '\0';
        pong_console_line(line);
    }
}

static void __attribute__((format(printf, 1, 2)))
thor_motion_note(const char *format, ...)
{
    unsigned count = atomic_load_explicit(&thor_motion_receipts, memory_order_relaxed);
    do {
        if (count >= 48)
            return;
    } while (!atomic_compare_exchange_weak_explicit(&thor_motion_receipts,
        &count, count + 1, memory_order_relaxed, memory_order_relaxed));
    int saved_errno = errno;
    struct thor_motion_notice *notice = malloc(sizeof(*notice));
    if (notice != NULL) {
        va_list args;
        va_start(args, format);
        int size = vsnprintf(notice->text, sizeof(notice->text), format, args);
        va_end(args);
        if (size > 0 && (size_t)size < sizeof(notice->text))
            dispatch_async_f(dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), notice, thor_motion_write);
        else
            free(notice);
    }
    errno = saved_errno;
}

enum {
    THOR_MOTION_POLICY_VALID = 1u << 3,
};

static unsigned
thor_motion_pack_policy(struct thor_motion_policy policy)
{
    if (policy.reduce_motion < 0 || policy.low_power < 0 || policy.animations < 0)
        return 0;
    return THOR_MOTION_POLICY_VALID | (unsigned)policy.reduce_motion |
        (unsigned)policy.low_power << 1 | (unsigned)policy.animations << 2;
}

static struct thor_motion_policy
thor_motion_read_policy(void)
{
    struct thor_motion_policy policy = { -1, -1, -1 };
    if (!pthread_main_np()) {
        unsigned snapshot = atomic_load_explicit(&thor_motion_policy_snapshot, memory_order_acquire);
        if (snapshot & THOR_MOTION_POLICY_VALID)
            policy = (struct thor_motion_policy){snapshot & 1u, (snapshot >> 1) & 1u, (snapshot >> 2) & 1u};
        return policy;
    }
    if (!thor_motion_policy_ready)
        return policy;
    policy.reduce_motion = thor_motion_native_reduce();
    policy.animations = ((BOOL (*)(id, SEL))objc_msgSend)(
        (id)thor_motion_view, thor_motion_enabled_sel);
    id info = ((id (*)(id, SEL))objc_msgSend)(
        (id)thor_motion_process_info, thor_motion_process_info_sel);
    if (info != nil)
        policy.low_power = ((BOOL (*)(id, SEL))objc_msgSend)(info, thor_motion_low_power_sel);
    atomic_store_explicit(&thor_motion_policy_snapshot, thor_motion_pack_policy(policy),
        memory_order_release);
    return policy;
}

static void
thor_motion_refresh_policy(void *context)
{
    (void)context;
    (void)thor_motion_read_policy();
}

static unsigned
thor_motion_observe_policy(void)
{
    Class notifications = objc_lookUpClass("NSNotificationCenter");
    SEL default_center = sel_registerName("defaultCenter");
    SEL observe = sel_registerName("addObserverForName:object:queue:usingBlock:");
    if (notifications == Nil ||
        !method_abi(class_getClassMethod(notifications, default_center), "@", 0, "") ||
        !method_abi(class_getInstanceMethod(notifications, observe), "@", 4, "@@@@"))
        return 0;
    id center = ((id (*)(id, SEL))objc_msgSend)((id)notifications, default_center);
    if (center == nil)
        return 0;
    void (^handler)(id) = ^(id notification) {
        (void)notification;
        if (pthread_main_np())
            thor_motion_refresh_policy(NULL);
        else
            dispatch_async_f(dispatch_get_main_queue(), NULL, thor_motion_refresh_policy);
    };
    const char *symbols[] = {
        "UIAccessibilityReduceMotionStatusDidChangeNotification",
        "NSProcessInfoPowerStateDidChangeNotification",
        "UIApplicationDidBecomeActiveNotification",
    };
    unsigned observers = 0;
    for (size_t index = 0; index < sizeof(symbols) / sizeof(symbols[0]); ++index) {
        id const *name = dlsym(RTLD_DEFAULT, symbols[index]);
        if (name != NULL && *name != nil &&
            ((id (*)(id, SEL, id, id, id, void (^)(id)))objc_msgSend)(
                center, observe, *name, nil, nil, handler) != nil)
            ++observers;
    }
    return observers;
}

static void
thor_motion_prepare_policy(void *context)
{
    (void)context;
    thor_motion_view = objc_lookUpClass("UIView");
    thor_motion_process_info = objc_lookUpClass("NSProcessInfo");
    thor_motion_enabled_sel = sel_registerName("areAnimationsEnabled");
    thor_motion_process_info_sel = sel_registerName("processInfo");
    thor_motion_low_power_sel = sel_registerName("isLowPowerModeEnabled");
    thor_motion_native_reduce = (BOOL (*)(void))dlsym(RTLD_DEFAULT, "UIAccessibilityIsReduceMotionEnabled");
    thor_motion_policy_ready = thor_motion_native_reduce != NULL && thor_motion_view != Nil &&
        thor_motion_process_info != Nil &&
        method_abi(class_getClassMethod(thor_motion_view, thor_motion_enabled_sel), "Bc", 0, "") &&
        method_abi(class_getClassMethod(thor_motion_process_info, thor_motion_process_info_sel), "@", 0, "") &&
        method_abi(class_getInstanceMethod(thor_motion_process_info, thor_motion_low_power_sel), "Bc", 0, "");
    struct thor_motion_policy policy = thor_motion_read_policy();
    unsigned observers = thor_motion_policy_ready ? thor_motion_observe_policy() : 0;
    thor_motion_note("policy ready=%d reduce_motion=%d low_power=%d ui_animations=%d observers=%u",
        thor_motion_policy_ready, policy.reduce_motion, policy.low_power, policy.animations, observers);
}

static bool
thor_motion_take_receipt(_Atomic unsigned *counter, unsigned limit)
{
    unsigned count = atomic_load_explicit(counter, memory_order_relaxed);
    do {
        if (count >= limit)
            return false;
    } while (!atomic_compare_exchange_weak_explicit(counter, &count, count + 1,
        memory_order_relaxed, memory_order_relaxed));
    return true;
}

static void
thor_motion_record_cadence(id link, uint64_t ticks, double timestamp, double target_timestamp,
    bool main_thread, uint64_t window_after)
{
    if (thor_motion_cadence.window_after != window_after) {
        memset(&thor_motion_cadence, 0, sizeof(thor_motion_cadence));
        thor_motion_cadence.window_after = window_after;
    }
    bool same_link = thor_motion_cadence.link == (const void *)link;
    uint64_t prior_ticks = thor_motion_cadence.prior_ticks;
    thor_motion_cadence.link = (const void *)link;
    thor_motion_cadence.prior_ticks = ticks;
    if (!same_link || prior_ticks == 0 || ticks <= prior_ticks)
        return;
    uint64_t delta_ticks = ticks - prior_ticks;
    if (delta_ticks > UINT64_MAX / thor_motion_timebase.numer)
        return;
    uint64_t actual_us = delta_ticks * thor_motion_timebase.numer /
        thor_motion_timebase.denom / 1000;
    if (actual_us < 1000 || actual_us > 90000)
        return;

    if (thor_motion_cadence.pairs == 0 || actual_us < thor_motion_cadence.callback_min_us)
        thor_motion_cadence.callback_min_us = actual_us;
    if (actual_us > thor_motion_cadence.callback_max_us)
        thor_motion_cadence.callback_max_us = actual_us;
    thor_motion_cadence.callback_sum_us += actual_us;
    double target_period = target_timestamp - timestamp;
    if (target_period >= 0.001 && target_period <= 0.09) {
        uint64_t period_us = (uint64_t)(target_period * 1000000.0 + 0.5);
        if (thor_motion_cadence.periods == 0 || period_us < thor_motion_cadence.period_min_us)
            thor_motion_cadence.period_min_us = period_us;
        if (period_us > thor_motion_cadence.period_max_us)
            thor_motion_cadence.period_max_us = period_us;
        thor_motion_cadence.period_sum_us += period_us;
        ++thor_motion_cadence.periods;
    }
    if (++thor_motion_cadence.pairs < 8)
        return;

    /* One callback batch per process and time window, even across threads. */
    const uint64_t delay_ns = (window_after ? 7ull : 15ull) * NSEC_PER_SEC;
    if (delay_ns > UINT64_MAX / thor_motion_timebase.denom)
        return;
    const uint64_t delay_ticks = delay_ns * thor_motion_timebase.denom / thor_motion_timebase.numer;
    if (ticks > UINT64_MAX - delay_ticks)
        return;
    if (atomic_compare_exchange_strong_explicit(&thor_motion_cadence_after_ticks,
            &window_after, ticks + delay_ticks, memory_order_relaxed, memory_order_relaxed) &&
        thor_motion_take_receipt(&thor_motion_cadence_receipts, THOR_MOTION_CADENCE_RECEIPT_LIMIT)) {
        unsigned periods = thor_motion_cadence.periods;
        struct thor_motion_policy policy = thor_motion_read_policy();
        thor_motion_note("cadence samples=8 main=%d callback_us_min/mean/max=%llu/%llu/%llu "
            "target_samples=%u target_minus_timestamp_us_min/mean/max=%llu/%llu/%llu "
            "last_timestamp=%.6f last_targetTimestamp=%.6f low_power=%d",
            main_thread, (unsigned long long)thor_motion_cadence.callback_min_us,
            (unsigned long long)(thor_motion_cadence.callback_sum_us / 8),
            (unsigned long long)thor_motion_cadence.callback_max_us, periods,
            (unsigned long long)thor_motion_cadence.period_min_us,
            (unsigned long long)(periods ? thor_motion_cadence.period_sum_us / periods : 0),
            (unsigned long long)thor_motion_cadence.period_max_us,
            timestamp, target_timestamp, policy.low_power);
    }
    thor_motion_cadence.pairs = thor_motion_cadence.periods = 0;
    thor_motion_cadence.callback_sum_us = thor_motion_cadence.callback_max_us = 0;
    thor_motion_cadence.period_sum_us = thor_motion_cadence.period_min_us = thor_motion_cadence.period_max_us = 0;
}

static void
thor_motion_display_link_fire(id self, SEL selector, id link)
{
    void (*original)(id, SEL, id) = atomic_load_explicit(&thor_motion_original_fire,
        memory_order_acquire);
    if (!atomic_load_explicit(&thor_motion_notes_ready, memory_order_acquire) ||
        atomic_load_explicit(&thor_motion_cadence_receipts, memory_order_relaxed) >=
            THOR_MOTION_CADENCE_RECEIPT_LIMIT) {
        original(self, selector, link);
        return;
    }
    const int incoming_errno = errno;
    const uint64_t window_after = atomic_load_explicit(&thor_motion_cadence_after_ticks, memory_order_relaxed);
    const uint64_t ticks = mach_absolute_time();
    if (ticks < window_after) {
        errno = incoming_errno;
        original(self, selector, link);
        return;
    }
    bool main_thread = pthread_main_np();
    bool tagged = link != nil && objc_getAssociatedObject(link, &thor_motion_target_key) != nil;
    double timestamp = tagged ? thor_motion_timestamp(link, thor_motion_timestamp_sel) : 0;
    double target = tagged ? thor_motion_target_timestamp(link, thor_motion_target_timestamp_sel) : 0;
    errno = incoming_errno;
    original(self, selector, link);
    int saved_errno = errno;
    if (!atomic_exchange_explicit(&thor_motion_first_fire, true, memory_order_relaxed))
        thor_motion_note("cadence_first action=displayLinkFire: main=%d tagged=%d timestamp=%.6f targetTimestamp=%.6f",
            main_thread, tagged, timestamp, target);
    if (tagged && thor_motion_timebase.numer != 0 && thor_motion_timebase.denom != 0)
        thor_motion_record_cadence(link, ticks, timestamp, target, main_thread, window_after);
    errno = saved_errno;
}

static void
thor_motion_install_fire(id target)
{
    if (!pthread_main_np() || thor_motion_fire_checked)
        return;
    thor_motion_fire_checked = true;
    Method fire = thor_motion_owned_method(object_getClass(target), thor_motion_action_sel);
    if (thor_motion_timestamp == NULL || thor_motion_target_timestamp == NULL ||
        !method_abi(fire, "v", 1, "@") ||
        mach_timebase_info(&thor_motion_timebase) != KERN_SUCCESS) {
        thor_motion_note("cadence_hook status=unavailable");
        return;
    }
    atomic_store_explicit(&thor_motion_original_fire,
        (void (*)(id, SEL, id))method_getImplementation(fire), memory_order_release);
    method_setImplementation(fire, (IMP)thor_motion_display_link_fire);
    thor_motion_note("cadence_hook status=ready action=displayLinkFire:");
}

static id
thor_motion_make(id self, SEL selector, id target, SEL action)
{
    if (target == nil || action != thor_motion_action_sel)
        return thor_motion_original_make(self, selector, target, action);
    const char *name = object_getClassName(target);
    bool modern = strcmp(name, "UIKit.InProcessAnimationManager") == 0;
    if (!modern && strcmp(name, "UIViewInProcessAnimationManager") != 0)
        return thor_motion_original_make(self, selector, target, action);

    const int incoming_errno = errno;
    if (modern)
        thor_motion_install_fire(target);
    errno = incoming_errno;
    id link = thor_motion_original_make(self, selector, target, action);
    if (link == nil)
        return nil;
    const int saved_errno = errno;
    objc_setAssociatedObject(link, &thor_motion_target_key, (id)kCFBooleanTrue, OBJC_ASSOCIATION_ASSIGN);
    if (thor_motion_cadence.link == (const void *)link)
        thor_motion_cadence.prior_ticks = 0;
    errno = saved_errno;
    if (!atomic_load_explicit(&thor_motion_notes_ready, memory_order_acquire)) {
        atomic_fetch_add_explicit(&thor_motion_early_links, 1, memory_order_relaxed);
        return link;
    }
    if (!thor_motion_take_receipt(&thor_motion_link_receipts, 12))
        return link;
    if (thor_motion_native_range != NULL && pthread_main_np() && thor_motion_policy_ready) {
        struct thor_motion_range range = thor_motion_native_range(link, thor_motion_range_sel);
        thor_motion_note("link target=%s action=%s initial_range=%.1f/%.1f/%.1f",
            name, action != NULL ? sel_getName(action) : "-", range.minimum, range.maximum, range.preferred);
    } else {
        thor_motion_note("link target=%s action=%s initial_range=unavailable",
            name, action != NULL ? sel_getName(action) : "-");
    }
    errno = saved_errno;
    return link;
}

static bool
thor_motion_can_adapt(struct thor_motion_policy policy)
{
    /* The original QuartzCore setter applies the requested hint; native
     * scheduling still enforces Low Power, thermal and availability limits. */
    return thor_motion_adapt_rate && policy.reduce_motion == 0 &&
        policy.low_power >= 0 && policy.animations == 1;
}

static bool
thor_motion_first_rate(struct thor_motion_rate_key key)
{
    bool first = false;
    os_unfair_lock_lock(&thor_motion_rate_lock);
    for (unsigned index = 0; index < thor_motion_rate_count; ++index) {
        const struct thor_motion_rate_key *entry = &thor_motion_rates[index];
        if (entry->modern == key.modern && entry->changed == key.changed &&
            entry->policy == key.policy && entry->scalar == key.scalar &&
            memcmp(&entry->range, &key.range, sizeof(key.range)) == 0)
            goto done;
    }
    if (thor_motion_rate_count < sizeof(thor_motion_rates) / sizeof(thor_motion_rates[0])) {
        thor_motion_rates[thor_motion_rate_count++] = key;
        first = true;
    }
done:
    os_unfair_lock_unlock(&thor_motion_rate_lock);
    return first;
}

static void
thor_motion_set_rate(id self, SEL selector, long requested)
{
    if (thor_motion_forwarding_rate ||
        objc_getAssociatedObject(self, &thor_motion_target_key) == nil) {
        thor_motion_original_rate(self, selector, requested);
        return;
    }
    if (!atomic_load_explicit(&thor_motion_notes_ready, memory_order_acquire)) {
        atomic_fetch_add_explicit(&thor_motion_early_rates, 1, memory_order_relaxed);
        thor_motion_forwarding_rate = true;
        thor_motion_original_rate(self, selector, thor_motion_native_cadence ? 0 : requested);
        thor_motion_forwarding_rate = false;
        return;
    }
    int saved_errno = errno;
    struct thor_motion_policy policy = thor_motion_read_policy();
    bool eligible = (requested >= 20 && requested <= 30) ||
        (thor_motion_cap_rate && requested > 60);
    long effective = thor_motion_native_cadence ? 0 :
        (eligible && thor_motion_can_adapt(policy) ? 60 : requested);
    thor_motion_forwarding_rate = true;
    errno = saved_errno;
    thor_motion_original_rate(self, selector, effective);
    thor_motion_forwarding_rate = false;
    int result_errno = errno;
    struct thor_motion_rate_key key = {
        .scalar = requested, .policy = thor_motion_pack_policy(policy), .changed = effective != requested,
    };
    if (thor_motion_first_rate(key))
        thor_motion_note("legacy_rate requested=%ld effective=%ld eligible=%d reduce_motion=%d low_power=%d ui_animations=%d policy=%s",
            requested, effective, eligible, policy.reduce_motion, policy.low_power, policy.animations,
            pthread_main_np() ? "main" : "snapshot");
    errno = result_errno;
}

static bool
thor_motion_valid_range(struct thor_motion_range range)
{
    return isfinite(range.minimum) && isfinite(range.maximum) && isfinite(range.preferred) &&
        range.minimum >= 0 && range.minimum <= range.maximum &&
        (range.preferred == 0 || (range.preferred >= range.minimum && range.preferred <= range.maximum));
}

static bool
thor_motion_low_range(struct thor_motion_range range)
{
    bool low_preference = range.preferred >= 20 && range.preferred <= 30;
    if (range.maximum >= 60)
        return low_preference;
    return range.maximum >= 20 && range.maximum <= 30 &&
        (range.preferred == 0 || low_preference);
}

static struct thor_motion_range
thor_motion_range_at_sixty(struct thor_motion_range range)
{
    if (thor_motion_cap_rate && range.minimum > 60)
        range.minimum = 60;
    if (thor_motion_cap_rate || range.maximum < 60)
        range.maximum = 60;
    range.preferred = 60;
    return range;
}

static void
thor_motion_record_early_range(struct thor_motion_range requested)
{
    unsigned empty = 0;
    if (!atomic_compare_exchange_strong_explicit(&thor_motion_first_early_range_state, &empty,
            THOR_MOTION_EARLY_RANGE_WRITING, memory_order_relaxed, memory_order_relaxed))
        return;
    thor_motion_first_early_range.requested = requested;
    thor_motion_first_early_range.main_thread = pthread_main_np();
    atomic_store_explicit(&thor_motion_first_early_range_state, THOR_MOTION_EARLY_RANGE_READY,
        memory_order_release);
}

static void
thor_motion_set_range(id self, SEL selector, struct thor_motion_range requested)
{
    if (thor_motion_forwarding_rate ||
        objc_getAssociatedObject(self, &thor_motion_target_key) == nil) {
        thor_motion_original_range(self, selector, requested);
        return;
    }
    if (!atomic_load_explicit(&thor_motion_notes_ready, memory_order_acquire)) {
        atomic_fetch_add_explicit(&thor_motion_early_ranges, 1, memory_order_relaxed);
        thor_motion_record_early_range(requested);
        const struct thor_motion_range automatic = {0};
        thor_motion_forwarding_rate = true;
        thor_motion_original_range(self, selector, thor_motion_native_cadence ? automatic : requested);
        thor_motion_forwarding_rate = false;
        return;
    }

    int saved_errno = errno;
    struct thor_motion_policy policy = thor_motion_read_policy();
    bool eligible = thor_motion_valid_range(requested) &&
        ((thor_motion_cap_rate && requested.maximum > 60) || thor_motion_low_range(requested));
    /* The Fold 2 g298 policy clears per-link hints for this UIKit manager,
     * including the constructor-time request. QuartzCore retains policy. */
    const struct thor_motion_range automatic = {0};
    struct thor_motion_range effective = thor_motion_native_cadence ? automatic :
        (eligible && thor_motion_can_adapt(policy) ? thor_motion_range_at_sixty(requested) : requested);
    thor_motion_forwarding_rate = true;
    errno = saved_errno;
    thor_motion_original_range(self, selector, effective);
    thor_motion_forwarding_rate = false;
    int result_errno = errno;
    struct thor_motion_rate_key key = {
        .range = requested, .policy = thor_motion_pack_policy(policy), .modern = true,
        .changed = effective.minimum != requested.minimum || effective.maximum != requested.maximum ||
            effective.preferred != requested.preferred,
    };
    if (thor_motion_first_rate(key))
        thor_motion_note("range_rate requested=%.1f/%.1f/%.1f effective=%.1f/%.1f/%.1f "
            "eligible=%d reduce_motion=%d low_power=%d ui_animations=%d policy=%s soft_cap=%d",
            requested.minimum, requested.maximum, requested.preferred,
            effective.minimum, effective.maximum, effective.preferred, eligible,
            policy.reduce_motion, policy.low_power, policy.animations,
            pthread_main_np() ? "main" : "snapshot", thor_motion_cap_rate ? 60 : 0);
    errno = result_errno;
}

static Method
thor_motion_owned_method(Class owner, SEL selector)
{
    if (owner == Nil)
        return NULL;
    unsigned count = 0;
    Method *methods = class_copyMethodList(owner, &count);
    Method result = NULL;
    for (unsigned i = 0; i < count && result == NULL; ++i) {
        if (method_getName(methods[i]) == selector)
            result = methods[i];
    }
    free(methods);
    return result;
}

static bool
thor_motion_range_abi(Method method, bool setter)
{
    if (method == NULL || method_getNumberOfArguments(method) != (setter ? 3u : 2u))
        return false;
    if (setter) {
        char *result = method_copyReturnType(method);
        bool valid = result != NULL && strcmp(result, "v") == 0;
        free(result);
        if (!valid)
            return false;
    }
    char *encoding = setter ? method_copyArgumentType(method, 2) : method_copyReturnType(method);
    bool valid = encoding != NULL &&
        (strcmp(encoding, "{?=fff}") == 0 || strcmp(encoding, "{CAFrameRateRange=fff}") == 0);
    free(encoding);
    return valid;
}

static enum thor_motion_status
thor_motion_install(void)
{
    Class link = objc_lookUpClass("CADisplayLink");
    if (link == Nil)
        return THOR_MOTION_NO_CLASS;
    Method make = thor_motion_owned_method(object_getClass((id)link),
        sel_registerName("displayLinkWithTarget:selector:"));
    Method rate = thor_motion_owned_method(link, sel_registerName("setPreferredFramesPerSecond:"));
    Method set_range = thor_motion_owned_method(link, sel_registerName("setPreferredFrameRateRange:"));
    bool legacy_ok = method_abi(rate, "v", 1, "q");
    bool range_ok = thor_motion_range_abi(set_range, true);
    if (!method_abi(make, "@", 2, "@:") || (!legacy_ok && !range_ok))
        return THOR_MOTION_BAD_ABI;

    thor_motion_action_sel = sel_registerName("displayLinkFire:");
    thor_motion_range_sel = sel_registerName("preferredFrameRateRange");
    thor_motion_timestamp_sel = sel_registerName("timestamp");
    thor_motion_target_timestamp_sel = sel_registerName("targetTimestamp");
    Method range = thor_motion_owned_method(link, thor_motion_range_sel);
    Method timestamp = thor_motion_owned_method(link, thor_motion_timestamp_sel);
    Method target_timestamp = thor_motion_owned_method(link, thor_motion_target_timestamp_sel);
    if (thor_motion_range_abi(range, false))
        thor_motion_native_range = (struct thor_motion_range (*)(id, SEL))method_getImplementation(range);
    if (method_abi(timestamp, "d", 0, ""))
        thor_motion_timestamp = (double (*)(id, SEL))method_getImplementation(timestamp);
    if (method_abi(target_timestamp, "d", 0, ""))
        thor_motion_target_timestamp = (double (*)(id, SEL))method_getImplementation(target_timestamp);
    thor_motion_original_make = (id (*)(id, SEL, id, SEL))method_getImplementation(make);
    if (legacy_ok) {
        thor_motion_original_rate = (void (*)(id, SEL, long))method_getImplementation(rate);
        method_setImplementation(rate, (IMP)thor_motion_set_rate);
    }
    if (range_ok) {
        thor_motion_original_range = (void (*)(id, SEL, struct thor_motion_range))
            method_getImplementation(set_range);
        method_setImplementation(set_range, (IMP)thor_motion_set_range);
    }
    method_setImplementation(make, (IMP)thor_motion_make);
    return THOR_MOTION_READY;
}

static void
thor_motion_finish_start(void *context)
{
    (void)context;
    bool retried = thor_motion_install_status == THOR_MOTION_NO_CLASS;
    if (retried)
        thor_motion_install_status = thor_motion_install();
    if (thor_motion_install_status != THOR_MOTION_READY) {
        thor_motion_note("install status=%s retried=%d",
            thor_motion_install_status == THOR_MOTION_NO_CLASS ? "no-class" : "bad-abi", retried);
        return;
    }

    thor_motion_prepare_policy(NULL);
    atomic_store_explicit(&thor_motion_notes_ready, true, memory_order_release);
    if (atomic_load_explicit(&thor_motion_first_early_range_state, memory_order_acquire) ==
            THOR_MOTION_EARLY_RANGE_READY) {
        struct thor_motion_range requested = thor_motion_first_early_range.requested;
        thor_motion_note("early_range requested=%.1f/%.1f/%.1f effective=%.1f/%.1f/%.1f setter=%s",
            requested.minimum, requested.maximum, requested.preferred,
            thor_motion_native_cadence ? 0 : requested.minimum,
            thor_motion_native_cadence ? 0 : requested.maximum,
            thor_motion_native_cadence ? 0 : requested.preferred,
            thor_motion_first_early_range.main_thread ? "main" : "worker");
    }
    Dl_info image = {0};
    (void)dladdr((const void *)&thor_motion_install, &image);
    thor_motion_note("install status=ready stage=%s legacy_setter=%d range_setter=%d range_getter=%d "
        "adapt=%d native_cadence=%d soft_cap=%d early_links=%u early_rates=%u early_ranges=%u runtime=%.150s",
        retried ? "main" : "ctor", thor_motion_original_rate != NULL, thor_motion_original_range != NULL,
        thor_motion_native_range != NULL, thor_motion_adapt_rate, thor_motion_native_cadence,
        thor_motion_cap_rate ? 60 : 0,
        atomic_load_explicit(&thor_motion_early_links, memory_order_relaxed),
        atomic_load_explicit(&thor_motion_early_rates, memory_order_relaxed),
        atomic_load_explicit(&thor_motion_early_ranges, memory_order_relaxed),
        image.dli_fname ?: "-");
}

static bool
thor_motion_parse_boot_args(const char *args, bool springboard, bool *adapt_rate)
{
    static const char thor[] = "arp0=thor";
    static const char hz[] = "fb_hz=";
    static const char sixty[] = "fb_hz=60";
    static const char one_twenty[] = "fb_hz=120";
    static const char disable[] = "pong_motion_rate=0";
    bool thor_boot = false;
    bool software_sixty = true;
    bool software_one_twenty = false;
    *adapt_rate = true;
    for (;;) {
        args += strspn(args, " \t");
        if (*args == '\0')
            break;
        size_t size = strcspn(args, " \t");
        if (size == sizeof(thor) - 1 && memcmp(args, thor, size) == 0)
            thor_boot = true;
        if (size >= sizeof(hz) - 1 && memcmp(args, hz, sizeof(hz) - 1) == 0) {
            software_sixty = size == sizeof(sixty) - 1 && memcmp(args, sixty, size) == 0;
            software_one_twenty = size == sizeof(one_twenty) - 1 &&
                memcmp(args, one_twenty, size) == 0;
        }
        if (size == sizeof(disable) - 1 && memcmp(args, disable, size) == 0)
            *adapt_rate = false;
        args += size;
    }
    thor_motion_native_cadence = springboard && thor_boot && software_one_twenty && *adapt_rate;
    return thor_boot && software_sixty;
}

static void
thor_motion_start(bool springboard)
{
    /* Match Thor IOMFB software pacing. Explicit refresh overrides remain
     * in control, and the physical panel mode is outside this policy. */
    char args[2048];
    size_t size = sizeof(args) - 1;
    thor_motion_adapt_rate = true;
    if (sysctlbyname("kern.bootargs", args, &size, NULL, 0) == 0) {
        args[size < sizeof(args) ? size : sizeof(args) - 1] = '\0';
        thor_motion_cap_rate = thor_motion_parse_boot_args(args, springboard, &thor_motion_adapt_rate);
    }
    /* Install through runtime metadata before UIApplicationMain creates a
     * link. Native policy reads begin when the main queue is available. */
    thor_motion_install_status = thor_motion_install();
    dispatch_async_f(dispatch_get_main_queue(), NULL, thor_motion_finish_start);
}
#endif
__attribute__((constructor)) static void pong_ui18_start(void)
{
    const char *name=getprogname();
    if(!name)return;
#if defined(PONG_THOR_MOTION_LINK)
    const bool motion_springboard = strcmp(name, "SpringBoard") == 0;
    if (motion_springboard || strcmp(name, "Preferences") == 0)
        thor_motion_start(motion_springboard);
#endif
#if defined(PONG_THOR_SETTINGS_WATCHDOG)
    if(!strcmp(name,"Preferences"))pong_settings_watch_stack_start();
#endif
#if defined(PONG_THOR_METAL_CLIENT_SMOKE)
    if(!strcmp(name,"Preferences")) {
        /* Keep the Settings startup path independent of the compositor.
         * Turning Metal off should disable the workload, not run the
         * generic phone, status-bar and compositor hooks in Settings. */
        bool run_probe = boot_option("pong_metal=1");
#if defined(PONG_THOR_DESKTOP_GPU)
        run_probe = run_probe && (!boot_option("pong_gpu_scanout=1") || !thor_desktop_gpu_policy_ready());
#endif
        extern void thor_settings_navigation_timing_start(void);
        thor_settings_navigation_timing_start();
        pong_preferences_metal_smoke_start(run_probe);
        return;
    }
#endif
    /* v67: A2 hooks in FIRST, before any shared UI initialization
     * (gestalt scans, dup2/setvbuf, banner) - the smallest possible
     * perturbation for a daemon that exits within seconds. Entry
     * receipt proves the injection reached the process even if nothing
     * else ever logs. */
    if(strcmp(name,"fairplayd.A2")==0) {
        fairplay_process=true;
        pong_a2_line("entered");
        pong_a2_apply_slots();
        return;
    }
    if(!strcmp(name,"InputUI")) {
        char product[32]={};size_t length=sizeof(product);
        if(!sysctlbyname("hw.product",product,&length,NULL,0) && !strcmp(product,"iPhone17,1"))
            _dyld_register_func_for_add_image(keyboard_phone_image);
        return; // Do not import SpringBoard/status-bar/container hooks here.
    }
    if(!strcmp(name,"Sileo")){sileo_directories();return;}
    if(!strcmp(name,"SpringBoard")) {
        extern void thor_switcher_timing_start(void);
        thor_switcher_timing_start();
        keyboard_retire_old_images();
        keyboard_defaults();_dyld_register_func_for_add_image(keyboard_native_image);
        pong_audio_volume18_start();
    }
    phone_ui_process=!strcmp(name,"Preferences") || !strcmp(name,"SpringBoard") || !strcmp(name,"MobilePhone");
    // Install the capability answer before status-provider +initialize can
    // cache it during the regular all-images callback below.
    if(phone_ui_process)for(uint32_t i=0,n=_dyld_image_count();i<n;i++)
        phone_gestalt_image(_dyld_get_image_header(i),_dyld_get_image_vmaddr_slide(i));
    if(phone_ui_process) {
        dispatch_after_f(dispatch_time(DISPATCH_TIME_NOW,15*NSEC_PER_SEC),dispatch_get_main_queue(),NULL,phone_repair_existing);
        dispatch_after_f(dispatch_time(DISPATCH_TIME_NOW,35*NSEC_PER_SEC),dispatch_get_main_queue(),NULL,phone_repair_existing);
    }
    /* services42 enabled unsupported restore variants globally. Keep the
     * known phone catalog wrappers and leave native defaults unchanged. */
    wallpaper_paths_process=phone_ui_process || !strcmp(name,"PosterBoard") ||
        !strcmp(name,"CollectionsPoster") || !strcmp(name,"LegacyPoster") ||
        !strcmp(name,"WallpaperSettingsIntents");
    wallpaper_process=boot_option("pong_wallpaper_catalog_override=1") && wallpaper_paths_process;
    (void)dup2(2,1);setvbuf(stdout,NULL,_IONBF,0);setvbuf(stderr,NULL,_IONBF,0);
    pong_say("Pong iPadOS18 adapter: %s pid=%d (22H374)",name,getpid());
    if(!strcmp(name,"wifid")) {
        if(boot_option("pong_boot_snapshot=1"))pong_boot_snapshot_start(name);
        return;
    }
    /* v71: observation-only processes get snapshots and NOTHING else - no
     * compositor callback, no persona/container policy may leak into
     * them (v70 review: container_persona_policy is not process-gated).
     * v72 adds the audio-route daemons behind the current SpringBoard
     * wait (MPAVRoutingController/MRAVLocalEndpoint -> mediaremoted;
     * sharedSystemAudioContext/AVFCore -> audiomxd). */
    if(!strcmp(name,"locationd") || !strcmp(name,"filecoordinationd") ||
       !strcmp(name,"mediaremoted") || !strcmp(name,"audiomxd")) {
        if(boot_option("pong_boot_snapshot=1"))pong_boot_snapshot_start(name);
        return;
    }
    /* v71: datamigrator hook. Firmware data flow (22H374, addresses in
     * the v70 field review): the plugin-PID-timeout handler runs on the
     * same serial queue the plugin completion callback must dispatch_sync
     * onto, and the timeout handler calls DMDiagnostics
     * captureDiagnosticsForIncident:async: with async=NO - the SYNCHRONOUS
     * OSAnalytics stackshot then parks that queue for 34+ minutes while
     * the completion (which carries the real, FALSE result) waits behind
     * it. The method's real implementation already has an async path
     * (WriteStackshotReportWithPID_async): this swizzle forces it, keeping
     * every result value untouched. ABI verified v28@0:8@16B24. */
    if(!strcmp(name,"com.apple.datamigrator")) {
        bool snapshots=boot_option("pong_boot_snapshot=1");
        dm_diagnostics_install(snapshots);
        if(snapshots)pong_boot_snapshot_start(name);
        return;
    }
    if(!strcmp(name,"PowerUIAgent")) {
        _dyld_register_func_for_add_image(powerui_image);return;
    }
    if(!strcmp(name,"SBRendererService")) {
        _dyld_register_func_for_add_image(poster_ci_image);return;
    }
    if(!strcmp(name,"contextstored")) {
        _dyld_register_func_for_add_image(context_motion_image);
        return;
    }
    if(strcmp(name,"Preferences")==0)pong_settings18_start();
    // Keep SpringBoard/compositor adapters out of the poster renderer.
    // Only its native storage path and single-user identity are adapted.
    if(!strcmp(name,"PosterBoard")){
        _dyld_register_func_for_add_image(poster_ci_image);
        poster_fatal_capture();_dyld_register_func_for_add_image(poster_image);
        if(boot_option("pong_poster_snapshot=1"))pong_boot_snapshot_start(name);
        return;
    }
    if(strcmp(name,"runningboardd")==0) {
        _dyld_register_func_for_add_image(poster_jetsam_image);
        if(boot_option("memlog=1"))pong_boot_console_start();
        if(boot_option("pong_boot_snapshot=1"))pong_boot_snapshot_start(name);
        return;
    }
    installd_process=strcmp(name,"installd")==0;
    pluginkit_process=strcmp(name,"pkd")==0;
    if(strcmp(name,"lsd")==0) {
        launchservices_process=true;
        struct sigaction action={0};
        action.sa_sigaction=lsd_abort;action.sa_flags=SA_SIGINFO|SA_RESETHAND;
        sigemptyset(&action.sa_mask);
        (void)sigaction(SIGABRT,&action,NULL);
    }
    if(strcmp(name,"usermanagerd")==0) {
        /* v62 field evidence: usermanagerd's native main asserts single
         * threading very early (_os_log_set_client_type ->
         * _pthread_is_threaded_np -> brk #1), so NOTHING here may create
         * a thread before main: the v62 snapshot worker caused a 14x
         * SIGTRAP loop, launchd throttling, and runningboardd/lsd both
         * blocking on this service. Diagnose usermanagerd through the
         * console receipts and native stackshots only. */
        initialize_persona_manifest();return;
    }
    if(strcmp(name,"SpringBoard")==0) {
        springboard_process=true;
        if(boot_option("pong_boot_snapshot=1"))
            atomic_store_explicit(&ui_observer_role,OBSERVER_SB,memory_order_relaxed);
        pong_sb_note("Pong UI18: SpringBoard adapter ctor");
#if defined(PONG_THOR_APP_REGISTER)
        Dl_info trigger_image = {0};
        bool mapped = dladdr((const void *)thor_app_spawn, &trigger_image) != 0;
        thor_app_spawn_receipt("PONG APPS SPAWN: scheduled delay_s=30 runtime=%.130s",
                               mapped && trigger_image.dli_fname ? trigger_image.dli_fname : "-");
        dispatch_after_f(dispatch_time(DISPATCH_TIME_NOW, 30 * NSEC_PER_SEC),
                         dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), NULL, thor_app_spawn);
#endif
#if defined(PONG_THOR_SETTINGS_WATCHDOG)
        pong_settings_watchdog18_start(pong_sb_note);
#endif
        // Physical volume keys stay on the native HID/system event route.
        skip_setup=boot_option("pong_skip_setup=1");
        CFPreferencesSetAppValue(CFSTR("SBDisableAutoDim"),kCFBooleanTrue,kCFPreferencesCurrentApplication);
        pong_sb_note("Pong UI18: wake scheduled on main queue +10s");
        dispatch_after_f(dispatch_time(DISPATCH_TIME_NOW,10*NSEC_PER_SEC),dispatch_get_main_queue(),NULL,wake_home);
        pong_sb_note("Pong UI18: wake enqueue returned");
    }
    if(strcmp(name,"backboardd")==0) {
        backboard_process=true;
        _dyld_register_func_for_add_image(thor_metal_pool_bridge_image);
        if(boot_option("pong_boot_snapshot=1"))
            atomic_store_explicit(&ui_observer_role,OBSERVER_BBD,memory_order_relaxed);
        software_composition=boot_option("pong_ui_software=1");
#if defined(PONG_THOR_DESKTOP_GPU)
        if (boot_option("pong_gpu_scanout=1") && boot_option("pong_metal=1")) {
            const bool native = thor_desktop_gpu_policy_ready();
            software_composition = !native;
#if defined(PONG_THOR_MEMORY_BUDGET)
            if (native)
                thor_desktop_gpu_memory_budget();
#endif
            const char *message = native
                ? "Pong Metal: Thor desktop native policy selected"
                : "Pong Metal: Thor desktop software policy selected";
            (void)sysctlbyname("kern.pong_diagnostic", NULL, NULL, (void *)message, strlen(message));
        }
#endif
        scanout_format=true;
        backboard_container();
        _dyld_register_func_for_add_image(brightness18_image);
        pong_touch_start();
    }
    _dyld_register_func_for_add_image(compositor_image);
    if(boot_option("pong_boot_snapshot=1"))pong_boot_snapshot_start(name);
}
