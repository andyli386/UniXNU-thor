// Ask one USB device (by serial) to reset, or to re-enumerate as if replugged.  macOS, IOKit only.
//   usbreset <serial> reset|reenum
#include <CoreFoundation/CoreFoundation.h>
#include <IOKit/IOKitLib.h>
#include <IOKit/IOCFPlugIn.h>
#include <IOKit/usb/IOUSBLib.h>
#include <mach/mach_port.h>
#include <stdio.h>
#include <string.h>

int main(int argc, char **argv)
{
    if (argc != 3 || (strcmp(argv[2], "reset") && strcmp(argv[2], "reenum"))) {
        fprintf(stderr, "usage: usbreset <serial> reset|reenum\n");
        return 2;
    }
    mach_port_t master = MACH_PORT_NULL;
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wunguarded-availability-new"
    IOReturn kr = IOMainPort(bootstrap_port, &master);
    if (kr != kIOReturnSuccess) { fprintf(stderr, "IOMainPort 0x%x\n", kr); return 1; }
#pragma clang diagnostic pop
    io_iterator_t it = IO_OBJECT_NULL;
    kr = IOServiceGetMatchingServices(master, IOServiceMatching(kIOUSBDeviceClassName), &it);
    if (kr != kIOReturnSuccess) { fprintf(stderr, "GetMatchingServices 0x%x\n", kr); return 1; }
    CFStringRef wanted = CFStringCreateWithCString(kCFAllocatorDefault, argv[1], kCFStringEncodingASCII);
    io_service_t service;
    int found = 0;
    while ((service = IOIteratorNext(it)) != IO_OBJECT_NULL) {
        CFTypeRef serial = IORegistryEntryCreateCFProperty(service, CFSTR("USB Serial Number"), kCFAllocatorDefault, 0);
        int mine = serial && CFGetTypeID(serial) == CFStringGetTypeID() && CFStringCompare(serial, wanted, 0) == kCFCompareEqualTo;
        char name[128] = "?", product[128] = "?";
        IORegistryEntryGetName(service, name);
        if (mine) {
            CFTypeRef p = IORegistryEntryCreateCFProperty(service, CFSTR("USB Product Name"), kCFAllocatorDefault, 0);
            if (p && CFGetTypeID(p) == CFStringGetTypeID()) CFStringGetCString(p, product, sizeof product, kCFStringEncodingASCII);
            found = 1;
            printf("found %s (%s) class=%s\n", argv[1], product, name);
            IOCFPlugInInterface **plugin = NULL;
            SInt32 score = 0;
            kr = IOCreatePlugInInterfaceForService(service, kIOUSBDeviceUserClientTypeID, kIOCFPlugInInterfaceID, &plugin, &score);
            if (kr != kIOReturnSuccess) {
                printf("IOCreatePlugInInterfaceForService -> 0x%08x\n", kr);
            } else {
                IOUSBDeviceInterface **device = NULL;
                kr = (*plugin)->QueryInterface(plugin, CFUUIDGetUUIDBytes(kIOUSBDeviceInterfaceID500), (LPVOID)&device);
                if (kr != kIOReturnSuccess || !device) {
                    printf("QueryInterface -> 0x%08x\n", kr);
                } else {
                    kr = !strcmp(argv[2], "reenum") ? (*device)->USBDeviceReEnumerate(device, 0) : (*device)->ResetDevice(device);
                    printf("%s -> 0x%08x\n", argv[2], kr);
                    (*device)->Release(device);
                }
                (*plugin)->Release(plugin);
            }
        }
        if (serial) CFRelease(serial);
        IOObjectRelease(service);
    }
    IOObjectRelease(it);
    CFRelease(wanted);
    if (master != MACH_PORT_NULL) mach_port_deallocate(mach_task_self(), master);
    if (!found) { printf("no USB device with serial %s\n", argv[1]); return 1; }
    return 0;
}
