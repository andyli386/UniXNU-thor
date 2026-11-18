#include <assert.h>
#include <CoreFoundation/CoreFoundation.h>
#include <dispatch/dispatch.h>
#include <mach/mach.h>
#include <objc/runtime.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/sysctl.h>
#include <unistd.h>

static unsigned recorded, mib_lookups;
static char lines[512][256];
static _Atomic(unsigned) native_count[18];
static _Atomic(bool) delay_main, delay_worker;
static const char *fake_image(Class cls);
static int fake_lookup(const char *name, int *mib, size_t *count)
{
    assert(!pthread_main_np());
    assert(!strcmp(name,"kern.pong_diagnostic"));
    ++mib_lookups;
    assert(*count);
    mib[0]=123;*count=1;
    return 0;
}
static int fake_sysctl(int *mib,u_int count,void *old,size_t *old_size,void *body,size_t bytes)
{
    (void)old;(void)old_size;
    assert(!pthread_main_np() && count==1 && mib[0]==123);
    assert(bytes && bytes<255 && recorded<512);
    memcpy(lines[recorded],body,bytes);
    lines[recorded][bytes]=0;
    assert(!strncmp(lines[recorded],"Pong Settings: Trace ",21));
    ++recorded;
    return 0;
}
#define sysctlnametomib fake_lookup
#define sysctl fake_sysctl
#define class_getImageName fake_image
#include "../../settings_trace18.c"
#undef sysctlnametomib
#undef sysctl
#undef class_getImageName

static unsigned nav_invocations;
static struct trace_span *held_nav;
void observe_navigation_completion(id owner,id target,bool pushing,bool animated,void *context,navigation_observer observer)
{
    (void)owner;(void)target;assert(animated);
    ++nav_invocations;
    if(nav_invocations==1) {
        assert(pushing); observer(context,NAV_FINISHED); observer(context,NAV_DECLINED);
    } else if(nav_invocations==2) {
        assert(!pushing); held_nav=context;
    } else {
        observer(context,NAV_NO_COORDINATOR);
    }
}
static const char *fake_image(Class cls)
{
    const char *name=class_getName(cls);
    for(unsigned i=0;i<TRACE_METHODS;++i)
        if(!strcmp(name,methods[i].owner))return images[methods[i].image].path;
    return "/wrong/image";
}
static unsigned method_index(id object,SEL selector)
{
    const char *owner=class_getName(object_getClass(object));
    for(unsigned i=0;i<TRACE_METHODS;++i)
        if(!strcmp(owner,methods[i].owner) && !strcmp(sel_getName(selector),methods[i].selector))return i;
    abort();
}
static id marker;
static void note_native(id self,SEL selector)
{
    unsigned i=method_index(self,selector);
    if(i==STORAGE_MONITOR && atomic_load(&delay_main))usleep(2100000);
    if(i==STORAGE_REMOTE && atomic_load(&delay_worker))usleep(2200000);
    atomic_fetch_add(&native_count[i],1);
}
static id native_object(id self,SEL selector)
{
    note_native(self,selector);
    return method_index(self,selector)==ABOUT_INIT?self:marker;
}
static void native_void(id self,SEL selector) {note_native(self,selector);}
static void native_bool(id self,SEL selector,BOOL animated) {assert(animated==YES);note_native(self,selector);}
static void native_push(id self,SEL selector,id target,BOOL animated)
{assert(target==marker && animated==YES);note_native(self,selector);}
static id native_pop(id self,SEL selector,BOOL animated)
{assert(animated==YES);note_native(self,selector);return marker;}
static IMP prior_push;
static void outer_push(id self,SEL selector,id target,BOOL animated)
{((void(*)(id,SEL,id,BOOL))prior_push)(self,selector,target,animated);}
static id objects[TRACE_METHODS];
static Method native_methods[TRACE_METHODS];
static void invoke(unsigned i)
{
    IMP implementation=method_getImplementation(native_methods[i]);
    const struct method_spec *spec=&methods[i];
    SEL selector=sel_registerName(spec->selector);
    switch(spec->signature) {
    case RETURNS_OBJECT:
        assert(((id(*)(id,SEL))implementation)(objects[i],selector)==(i==ABOUT_INIT?objects[i]:marker));break;
    case RETURNS_VOID:((void(*)(id,SEL))implementation)(objects[i],selector);break;
    case VOID_BOOL:((void(*)(id,SEL,BOOL))implementation)(objects[i],selector,YES);break;
    case VOID_OBJECT_BOOL:((void(*)(id,SEL,id,BOOL))implementation)(objects[i],selector,marker,YES);break;
    case OBJECT_BOOL:assert(((id(*)(id,SEL,BOOL))implementation)(objects[i],selector,YES)==marker);break;
    }
}
static unsigned containing(const char *needle)
{
    unsigned n=0;
    for(unsigned i=0;i<recorded;++i)if(strstr(lines[i],needle))++n;
    return n;
}
static _Atomic(unsigned) concurrent_counter, seen[64];
static void concurrent_reserve(void *unused, size_t index)
{
    (void)unused;(void)index;
    unsigned slot=reserve_slot(&concurrent_counter,64);
    if(slot<64)assert(atomic_fetch_add(&seen[slot],1)==0);
}
static void verify_long_fields(void)
{
    struct trace_span span={0};
    span.sequence=UINT32_MAX;
    span.method=NAV_PUSH;
    span.object=UINTPTR_MAX;
    span.object_class="ABCDEFGHIJKLMNOPQRSTUVWXYZABCDEFGHIJKLMNOPQRSTUVWXYZABCDEFGHIJKLMNOPQRSTUVWXYZ";
    span.target_class=span.object_class;
    span.thread_id=UINT64_MAX;
    span.started_ms=UINT64_MAX;
    unsigned before=recorded;
    begin_report(&span);
    assert(recorded==before+1 && strlen(lines[before])<255);
}
static void verify_final(void *unused)
{
    (void)unused;
    assert(containing("scope=request")>=1);
    assert(containing("phase=native")>=1);
    assert(containing("phase=main-queue")>=1);
    assert(containing("timeout ")>=4);
    for(unsigned i=0;i<TRACE_METHODS;++i)assert(atomic_load(&native_count[i])>=1);
    _Atomic(unsigned) limited=0;
    for(unsigned i=0;i<TRACE_DATA_SPANS;++i)assert(reserve_slot(&limited,TRACE_DATA_SPANS)==i);
    assert(reserve_slot(&limited,TRACE_DATA_SPANS)==TRACE_DATA_SPANS);
    for(unsigned i=TRACE_NAV_SPANS;i<TRACE_SPANS;++i)
        if(spans[i].source_thread!=main_thread)assert(!spans[i].owns_thread);
    dispatch_apply_f(128,dispatch_get_global_queue(QOS_CLASS_USER_INITIATED,0),NULL,concurrent_reserve);
    assert(atomic_load(&concurrent_counter)==64);
    for(unsigned i=0;i<64;++i)assert(atomic_load(&seen[i])==1);
    verify_long_fields();
    assert(mib_lookups==1 && nav_invocations==3);
    assert(containing("nav185 id=1 ")==1 && containing("result=finished")==1);
    assert(containing("nav185 id=2 ")==2 && containing("result=waiting")==1 && containing("result=cancelled")==1);
    assert(containing("nav185 id=3 ")==1 && containing("result=no-coordinator")==1);
    assert(containing("begin id=1 ")==0 && containing("end id=1 ")==0);
    puts("PASS actual helper bodies: all 18 typed forwarding paths, void/id/BOOL/native identity, own-class and class-method installation, nav wrapper composition and idempotence, background-only output, normal completion cancellation, main/native/worker timeout phases, source thread right release, bounded atomic slots, 128 concurrent reservations and worst-case log fields.");
    fflush(stdout);exit(0);
}
static void async_main_block(void *unused)
{
    (void)unused;
    atomic_store(&delay_main,false);
    invoke(NAV_PUSH);
    usleep(1900000);
    dispatch_after_f(dispatch_time(DISPATCH_TIME_NOW,300*NSEC_PER_MSEC),trace_queue,NULL,verify_final);
}
static void worker_block(void *unused)
{(void)unused;atomic_store(&delay_worker,true);invoke(STORAGE_REMOTE);}
static void native_main_block(void *unused)
{
    (void)unused;
    assert(containing("timeout ")==0);
    assert(held_nav && containing("result=waiting")==1);
    navigation_finished(held_nav,NAV_CANCELLED);
    atomic_store(&delay_main,true);
    dispatch_async_f(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED,0),NULL,worker_block);
    invoke(STORAGE_MONITOR);
    dispatch_after_f(dispatch_time(DISPATCH_TIME_NOW,400*NSEC_PER_MSEC),dispatch_get_main_queue(),NULL,async_main_block);
}
int main(void)
{
    assert(mach_timebase_info(&timebase)==KERN_SUCCESS && timebase.denom);
    assert(pthread_threadid_np(NULL,&main_tid)==0);
    main_thread=mach_thread_self();
    trace_queue=dispatch_queue_create("test.trace",DISPATCH_QUEUE_SERIAL);
    Class root=objc_allocateClassPair(Nil,"V180Root",0);objc_registerClassPair(root);
    Class target=objc_allocateClassPair(root,"V180TargetController",0);objc_registerClassPair(target);
    marker=class_createInstance(target,0);
    static const char *types[]={"@@:","v@:","v@:B","v@:@B","@@:B"};
    for(unsigned i=0;i<TRACE_METHODS;++i) {
        const struct method_spec *spec=&methods[i];
        Class cls=objc_lookUpClass(spec->owner);
        if(!cls){cls=objc_allocateClassPair(root,spec->owner,0);objc_registerClassPair(cls);}
        Class owner=spec->class_method?object_getClass((id)cls):cls;
        IMP implementation=spec->signature==RETURNS_OBJECT?(IMP)native_object:
            spec->signature==RETURNS_VOID?(IMP)native_void:spec->signature==VOID_BOOL?(IMP)native_bool:
            spec->signature==VOID_OBJECT_BOOL?(IMP)native_push:(IMP)native_pop;
        assert(class_addMethod(owner,sel_registerName(spec->selector),implementation,types[spec->signature]));
        native_methods[i]=class_getInstanceMethod(owner,sel_registerName(spec->selector));
        objects[i]=spec->class_method?(id)cls:class_createInstance(cls,0);
        assert(verify_abi(native_methods[i],spec->signature));
        atomic_store(&qualified[spec->image],true);
    }
    assert(!verify_abi(native_methods[NAV_PUSH],OBJECT_BOOL));
    install_methods(NULL);
    for(unsigned i=0;i<TRACE_METHODS;++i)assert(atomic_load(&originals[i]));
    prior_push=method_setImplementation(native_methods[NAV_PUSH],(IMP)outer_push);
    install_methods(NULL);
    assert(method_getImplementation(native_methods[NAV_PUSH])==(IMP)outer_push);
    for(unsigned i=0;i<TRACE_METHODS;++i)invoke(i);
    dispatch_after_f(dispatch_time(DISPATCH_TIME_NOW,1700*NSEC_PER_MSEC),dispatch_get_main_queue(),NULL,native_main_block);
    CFRunLoopRun();
    abort();
}
