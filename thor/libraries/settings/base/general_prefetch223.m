/* Targeted prefetch for the measured General and About controller paths in 22H374. */
#ifndef GENERAL_PAGES_UNIT
#import <UIKit/UIKit.h>
#endif
#include <fcntl.h>
#include <mach/mach.h>
#include <mach-o/loader.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/sysctl.h>
#include <time.h>
#include <unistd.h>

struct general_reads { unsigned pages, cold, warmed, failed; };

/* Each address was obtained from the restored GeneralSettingsUI __text using
 * its symbolized Objective-C method list. They are offsets in that cache image. */
static const uint32_t methods[] = {
    0x3c70, 0x49a0, 0x4cdc, 0x53bc, 0x2e748, 0x2f330, /* General */
    0xe050, 0xa7e4, 0x9adc, 0xfef4,                     /* About */
};

static void read_if_absent(uintptr_t address, size_t page, struct general_reads *stats)
{
    char residency = 0;
    uintptr_t start = address & ~(uintptr_t)(page - 1);
    ++stats->pages;
    if (mincore((void *)start, page, &residency) != 0) { ++stats->failed; return; }
    if (residency & MINCORE_INCORE) return;
    ++stats->cold;
    unsigned char byte;
    vm_size_t copied = 0;
    kern_return_t kr = vm_read_overwrite(mach_task_self(), address, 1,
                                              (vm_address_t)(uintptr_t)&byte, &copied);
    if (kr == KERN_SUCCESS && copied == 1 && !mincore((void *)start, page, &residency) &&
        (residency & MINCORE_INCORE)) ++stats->warmed;
    else ++stats->failed;
}

static void read_general_code(uintptr_t header, size_t page, struct general_reads *stats)
{
    uintptr_t seen[sizeof(methods)/sizeof(methods[0])];
    size_t used = 0;
    for (size_t i=0; i<sizeof(methods)/sizeof(methods[0]); ++i) {
        uintptr_t method = header + methods[i], start = method & ~(uintptr_t)(page-1);
        size_t j=0;
        while (j<used && seen[j]!=start) ++j;
        if (j<used) continue;
        seen[used++]=start;
        read_if_absent(method,page,stats);
    }
}

/* Limit resource I/O to the immutable General row definitions. The native
 * controller itself still performs localization, dynamic queries and rendering. */
static void read_general_resource(const char *path, size_t page, struct general_reads *stats)
{
    int fd=open(path,O_RDONLY|O_CLOEXEC|O_NOFOLLOW);
    struct stat st;
    if (fd<0) {++stats->failed; return;}
    if (fstat(fd,&st) || !S_ISREG(st.st_mode) || st.st_size != 5563) {
        ++stats->failed; close(fd); return;
    }
    size_t n=(size_t)st.st_size;
    void *mapping=mmap(NULL,n,PROT_READ,MAP_PRIVATE,fd,0);
    close(fd);
    if (mapping==MAP_FAILED) {++stats->failed; return;}
    for (size_t offset=0; offset<n; offset+=page)
        read_if_absent((uintptr_t)mapping+offset,page,stats);
    (void)munmap(mapping,n);
}

#ifndef GENERAL_PAGES_UNIT
static const struct mach_header *general_header;
static dispatch_once_t request_once;
@interface ThorGeneralPagePreparation : NSObject
+ (void)becameActive:(NSNotification *)note;
@end
@implementation ThorGeneralPagePreparation
+ (void)becameActive:(NSNotification *)note
{
    (void)note;
    [[NSNotificationCenter defaultCenter] removeObserver:self name:UIApplicationDidBecomeActiveNotification object:nil];
    dispatch_once(&request_once, ^{
        dispatch_async(dispatch_get_global_queue(QOS_CLASS_UTILITY,0), ^{
            struct general_reads code={0}, plist={0};
            size_t page=(size_t)getpagesize();
            if (!page || (page & (page-1))) return;
            uint64_t start=clock_gettime_nsec_np(CLOCK_UPTIME_RAW);
            read_general_code((uintptr_t)general_header,page,&code);
            read_general_resource("/System/Library/PrivateFrameworks/Settings/GeneralSettingsUI.framework/General.plist",page,&plist);
            uint64_t end=clock_gettime_nsec_np(CLOCK_UPTIME_RAW);
            char line[240];
            int n=snprintf(line,sizeof(line),"Pong Settings: Trace warm223 start_us=%llu end_us=%llu code=%u/%u/%u/%u plist=%u/%u/%u/%u",
                (unsigned long long)(start/1000),(unsigned long long)(end/1000),
                code.pages,code.cold,code.warmed,code.failed,plist.pages,plist.cold,plist.warmed,plist.failed);
            if(n>0 && (size_t)n<sizeof(line)) (void)sysctlbyname("kern.pong_diagnostic",NULL,NULL,line,(size_t)n);
        });
    });
}
@end
void prepare_general_when_active(const struct mach_header *header)
{
    /* Only the exact cache-resident image UUID reaches here. Cache images
     * remain mapped for the lifetime of the process. No UIViewController is created. */
    static dispatch_once_t installed;
    dispatch_once(&installed,^{
        general_header=header;
        dispatch_async(dispatch_get_main_queue(),^{
            Class observer=[ThorGeneralPagePreparation class];
            if ([UIApplication sharedApplication].applicationState==UIApplicationStateActive) {
                [observer becameActive:nil];
            } else {
                [[NSNotificationCenter defaultCenter] addObserver:observer selector:@selector(becameActive:)
                    name:UIApplicationDidBecomeActiveNotification object:nil];
            }
        });
    });
}
#endif
