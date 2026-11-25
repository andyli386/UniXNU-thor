// Talk to a fastboot device directly through libusb, bypassing the fastboot tool.
//   fbprobe info                      list the fastboot device, its descriptors and who holds it
//   fbprobe cmd <string> [timeout-ms] send one command and print the reply
//   fbprobe sendzero <count>          push <count> zero bytes down the bulk-out pipe (no command framing)
#include <libusb-1.0/libusb.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <unistd.h>

#define VID 0x18d1
#define PID 0xd00d

static const char *errname(int e) { return libusb_error_name(e); }

static int pick(libusb_device ***list, struct libusb_device_descriptor *desc)
{
    ssize_t n = libusb_get_device_list(NULL, list);
    int found = -1;
    for (ssize_t i = 0; i < n; ++i) {
        struct libusb_device_descriptor d;
        if (libusb_get_device_descriptor((*list)[i], &d) == 0 && d.idVendor == VID && d.idProduct == PID) {
            found = (int)i;
            *desc = d;
        }
    }
    return found;
}

int main(int argc, char **argv)
{
    if (argc < 2) { fprintf(stderr, "usage: see source\n"); return 2; }
    alarm(90);                            /* never stay blocked on a flapping device */
    libusb_device **list = NULL;
    struct libusb_device_descriptor desc;
    if (libusb_init(NULL) != 0) { fprintf(stderr, "libusb_init failed\n"); return 1; }
    int index = pick(&list, &desc), rc = 1;
    if (index < 0) { printf("no fastboot device (no device with vid %04x pid %04x)\n", VID, PID); goto out; }
    libusb_device *dev = list[index];
    unsigned char serial[64] = "", product[64] = "", manufacturer[64] = "";
    libusb_device_handle *strings = NULL;
    if (libusb_open(dev, &strings) == 0) {
        libusb_get_string_descriptor_ascii(strings, desc.iSerialNumber, serial, sizeof serial);
        libusb_get_string_descriptor_ascii(strings, desc.iProduct, product, sizeof product);
        libusb_get_string_descriptor_ascii(strings, desc.iManufacturer, manufacturer, sizeof manufacturer);
        libusb_close(strings);
    }
    printf("device %04x:%04x speed=%d serial=%s product=%s manufacturer=%s bcdUSB=%04x\n", desc.idVendor, desc.idProduct,
           libusb_get_device_speed(dev), serial, product, manufacturer, desc.bcdUSB);
    if (!strcmp(argv[1], "info")) {
        struct libusb_config_descriptor *config = NULL;
        int e = libusb_get_active_config_descriptor(dev, &config);
        if (e != 0) { printf("get_active_config_descriptor: %s\n", errname(e)); rc = 0; goto out; }
        printf("configuration %d: %d interface(s)\n", config->bConfigurationValue, config->bNumInterfaces);
        for (uint8_t i = 0; i < config->bNumInterfaces; ++i) {
            const struct libusb_interface *itf = &config->interface[i];
            for (int a = 0; a < itf->num_altsetting; ++a) {
                const struct libusb_interface_descriptor *al = &itf->altsetting[a];
                printf("  interface %d alt %d: class %02x subclass %02x protocol %02x endpoints:", al->bInterfaceNumber, a,
                       al->bInterfaceClass, al->bInterfaceSubClass, al->bInterfaceProtocol);
                for (uint8_t k = 0; k < al->bNumEndpoints; ++k) {
                    const struct libusb_endpoint_descriptor *ep = &al->endpoint[k];
                    printf(" %02x(max %d)", ep->bEndpointAddress, ep->wMaxPacketSize);
                }
                printf("\n");
            }
        }
        libusb_device_handle *h = NULL;
        e = libusb_open(dev, &h);
        printf("libusb_open: %s\n", errname(e));
        if (e == 0) {
            int claimed = 0;
            for (uint8_t i = 0; i < config->bNumInterfaces; ++i) {
                e = libusb_kernel_driver_active(h, i);
                printf("  kernel driver active on interface %d: %s\n", i, errname(e));
                e = libusb_claim_interface(h, i);
                printf("  claim interface %d: %s\n", i, errname(e));
                if (e == 0) { claimed = 1; libusb_release_interface(h, i); }
            }
            (void)claimed;
            libusb_close(h);
        }
        libusb_free_config_descriptor(config);
        rc = 0;
    } else if (!strcmp(argv[1], "cmd") && argc >= 3) {
        long timeout = argc >= 4 ? strtol(argv[3], NULL, 10) : 5000;
        libusb_device_handle *h = NULL;
        int e = libusb_open(dev, &h);
        if (e != 0) { printf("libusb_open: %s\n", errname(e)); goto out; }
        e = libusb_claim_interface(h, 0);
        if (e != 0) { printf("claim_interface(0): %s\n", errname(e)); libusb_close(h); goto out; }
        int done = 0;
        e = libusb_bulk_transfer(h, 0x01, (unsigned char *)argv[2], (int)strlen(argv[2]), &done, (unsigned)timeout);
        printf("write \"%s\" (%zu bytes): %s, %d bytes\n", argv[2], strlen(argv[2]), errname(e), done);
        unsigned char buf[4096];
        for (int round = 0; round < 4 && e == 0; ++round) {
            int got = 0;
            e = libusb_bulk_transfer(h, 0x81, buf, sizeof buf - 1, &got, (unsigned)timeout);
            buf[got < 0 ? 0 : got] = 0;
            if (e == 0) printf("read %d bytes: %s\n", got, buf);
            else printf("read: %s\n", errname(e));
            if (e == 0 && got >= 4 && (!memcmp(buf, "OKAY", 4) || !memcmp(buf, "FAIL", 4) || !memcmp(buf, "DATA", 4))) break;
        }
        libusb_release_interface(h, 0);
        libusb_close(h);
    } else if (!strcmp(argv[1], "fill") && argc >= 3) {
        /* An interrupted `fastboot boot` leaves the device counting payload bytes and reading no
         * commands.  Feed zero bytes (an empty command if the count is already complete) until it
         * answers, so that nothing has to be flashed or rebooted to get the command loop back. */
        long count = strtol(argv[2], NULL, 0);
        libusb_device_handle *h = NULL;
        int e = libusb_open(dev, &h);
        if (e != 0) { printf("libusb_open: %s\n", errname(e)); goto out; }
        e = libusb_claim_interface(h, 0);
        if (e != 0) { printf("claim_interface(0): %s\n", errname(e)); libusb_close(h); goto out; }
        static unsigned char zeros[65536];
        long sent = 0;
        int answered = 0;
        unsigned char buf[4096];
        while (sent < count && e == 0 && !answered) {
            int done = 0;
            e = libusb_bulk_transfer(h, 0x01, zeros, sizeof zeros, &done, 5000);
            sent += done;
            if (e != 0) printf("write stopped after %ld bytes: %s\n", sent, errname(e));
            if (sent % (1 << 20) < (long)sizeof zeros) {
                int got = 0;
                int r = libusb_bulk_transfer(h, 0x81, buf, sizeof buf - 1, &got, 40);
                if (r == 0) {
                    buf[got < 0 ? 0 : got] = 0;
                    printf("after %ld bytes: reply %d bytes: %s\n", sent, got, buf);
                    answered = 1;
                } else if (r != LIBUSB_ERROR_TIMEOUT) {
                    printf("poll: %s\n", errname(r));
                }
            }
        }
        printf("sent %ld of %ld zero bytes, answered=%d\n", sent, count, answered);
        for (int round = 0; round < 2; ++round) {
            int got = 0;
            e = libusb_bulk_transfer(h, 0x81, buf, sizeof buf - 1, &got, 1000);
            if (e == 0) { buf[got] = 0; printf("read %d bytes: %s\n", got, buf); }
            else printf("read: %s\n", errname(e));
        }
        libusb_release_interface(h, 0);
        libusb_close(h);
    } else {
        fprintf(stderr, "unknown action\n");
    }
    rc = 0;
out:
    libusb_free_device_list(list, 1);
    libusb_exit(NULL);
    return rc;
}
