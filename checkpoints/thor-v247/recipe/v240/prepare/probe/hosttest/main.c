/* Host test of the probe library: the library is compiled in under the three process names.
     hosttest-main.c [crash] [seconds]  ->  worker threads sleep <seconds>; with "crash" the main
     thread writes through a bad pointer after one second (the signal report). */
#include "../probe240.c"
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
static void *worker(void *n)
{
    char name[16] = "probe.worker";
    pthread_setname_np(name);
    unsigned left = (unsigned)(uintptr_t)n;
    while (left--) {
        sleep(1);
    }
    return NULL;
}
/* The marker test, on plain dictionaries: no store or preference is touched on this Mac. */
static void marker_test(void)
{
    CFMutableDictionaryRef record = CFDictionaryCreateMutable(NULL, 0, &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
    CFDictionarySetValue(record, CFSTR("*START*"), kCFBooleanTrue);
    if (namer_done(record)) {
        say("marker test FAIL: a start stamp counted as an end marker");
    }
    CFDictionarySetValue(record, CFSTR("*TIMEOUT*"), kCFBooleanTrue);
    if (!namer_done(record)) {
        say("marker test FAIL: a timeout stamp was not seen");
    }
    CFDictionaryRemoveValue(record, CFSTR("*TIMEOUT*"));
    CFDictionarySetValue(record, CFSTR("*QUIET*"), kCFBooleanTrue);
    if (!namer_done(record)) {
        say("marker test FAIL: a quiet stamp was not seen");
    }
    CFRelease(record);
    say("marker test done");
}
int main(int argc, char **argv)
{
    marker_test();
    pthread_t t;
    pthread_create(&t, NULL, worker, (void *)(uintptr_t)(argc > 2 ? (unsigned)atoi(argv[2]) : 1));
    if (argc > 1 && !strcmp(argv[1], "crash")) {
        sleep(1);
        *(volatile int *)(uintptr_t)0x2000 = 1;
    }
    pthread_join(t, NULL);
    return 0;
}
