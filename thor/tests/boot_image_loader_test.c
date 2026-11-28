/* The boot-image side of the OS patch on the Mac: the file is taken from the built image exactly as the transport
 * takes it from boot_a (whole blocks of the partition, the file not aligned with them, shifted in a scratch), and
 * the kernel's apply code is run with that as its source, against a copy of the installed system window.
 *
 *   clang -O1 -o loadtest loadtest.c -I <kernel tree>/bsd/kern
 *   ./loadtest <copy of the window> <boot image> <thor-os-patch-offset> <thor-os-patch-bytes> <bytes of the window>
 *
 * thorOsPatchLoad and the range arithmetic are copies of drivers/NativeUfs/PongUfsTransport.cpp; loadtest.sh
 * refuses to run when the copy and the driver's text differ.
 */
#include <fcntl.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#define kDataMax 131072u
#define kModemFirmwareLunBlocks 713728ull
#define THOR_OS_PATCH_LOG(fmt, ...)  printf(fmt "\n", ##__VA_ARGS__)
#include "thor_os_patch.h"

static const uint64_t kBootImageFirst4k = 131334, kBootImageBlocks4k = 49152;
static uint64_t gBootImagePatchFirst4k = 0, gBootImagePatchEnd4k = 0;
static uint32_t gBootImagePatchShift = 0;
static uint8_t dma[kDataMax];                                         /* the shared payload */
#define gHost_virt_plus_kDataOffset dma
static int boot_fd, image, reads = 0;

static bool bootImageReadAllowed(uint64_t lba, uint32_t blocks)
{
  return blocks && lba >= gBootImagePatchFirst4k && lba < gBootImagePatchEnd4k &&
         blocks <= gBootImagePatchEnd4k - lba;
}

/* the driver's hostReadBoot4kLocked, with the command replaced by a read of the image file */
static int hostReadBoot4kLocked(uint64_t lba, uint32_t blocks)
{
  uint64_t length = (uint64_t)blocks * 4096;
  if (!bootImageReadAllowed(lba, blocks) || !blocks || blocks > kDataMax / 4096) return 1;
  reads++;
  return pread(boot_fd, dma, length, (off_t)(lba - kBootImageFirst4k) * 4096) == (ssize_t)length ? 0 : 1;
}

/* ---- copy of the driver, from here to the mark ---- */
static uint8_t gBootImageScratch[(kDataMax / 4096 + 1) * 4096];

static int thorOsPatchLoad(void *context, uint64_t offset, uint32_t blocks, uint8_t *buffer) {
  const uint64_t raw = gBootImagePatchFirst4k + (gBootImagePatchShift + offset) / 4096;
  const uint32_t whole = blocks + (gBootImagePatchShift ? 1u : 0u);
  uint32_t done = 0;
  (void)context;
  if ((offset & 4095) || whole > kDataMax / 4096 + 1) return 1;
  while (done < whole) {
    uint32_t count = whole - done;
    if (count > kDataMax / 4096) count = kDataMax / 4096;
    if (hostReadBoot4kLocked(raw + done, count) != 0) return 1;
    memcpy(gBootImageScratch + size_t(done) * 4096, gHost_virt_plus_kDataOffset, size_t(count) * 4096);
    done += count;
  }
  memcpy(buffer, gBootImageScratch + gBootImagePatchShift, size_t(blocks) * 4096);
  return 0;
}
/* ---- end of the copy ---- */

/* the arithmetic of the driver's bootImagePatchRange for the two values the device tree carries */
static bool bootImagePatchRange(uint32_t at, uint32_t length, uint64_t *payloadBytes)
{
  if (!length || (length & 4095) || uint64_t(at) + length > kBootImageBlocks4k * 4096ULL) return false;
  gBootImagePatchFirst4k = kBootImageFirst4k + at / 4096;
  gBootImagePatchShift = at % 4096;
  gBootImagePatchEnd4k = gBootImagePatchFirst4k + (gBootImagePatchShift + length + 4095) / 4096;
  *payloadBytes = length;
  return true;
}

static int read_blocks(void *context, uint64_t block, uint32_t blocks, uint8_t *buffer)
{
	uint64_t length = (uint64_t)blocks * 4096;
	(void)context;
	return pread(image, buffer, length, (off_t)(block * 4096)) == (ssize_t)length ? 0 : 100;
}

static int write_blocks(void *context, uint64_t block, uint32_t blocks, const uint8_t *data)
{
	uint64_t length = (uint64_t)blocks * 4096;
	(void)context;
	return pwrite(image, data, length, (off_t)(block * 4096)) == (ssize_t)length ? 0 : 101;
}

int
main(int argc, char **argv)
{
	uint64_t window_bytes, payload_bytes = 0;
	uint32_t at, length;
	size_t page = (size_t)getpagesize(), mapped = ((size_t)32 * 4096 + page - 1) & ~(page - 1);
	uint8_t *mapping, *buffer;
	int rc;

	if (argc != 6) {
		fprintf(stderr, "usage: loadtest <image of the window> <boot image> <offset> <bytes> <window bytes>\n");
		return 2;
	}
	at = (uint32_t)strtoul(argv[3], NULL, 0);
	length = (uint32_t)strtoul(argv[4], NULL, 0);
	window_bytes = strtoull(argv[5], NULL, 0);
	image = open(argv[1], O_RDWR);
	boot_fd = open(argv[2], O_RDONLY);
	if (image < 0 || boot_fd < 0) {
		perror("open");
		return 2;
	}
	mapping = (uint8_t *)mmap(NULL, mapped + page, PROT_READ | PROT_WRITE, MAP_ANON | MAP_PRIVATE, -1, 0);
	if (mprotect(mapping + mapped, page, PROT_NONE) != 0) {
		perror("mprotect");
		return 2;
	}
	buffer = mapping;                                               /* the DMA payload: 32 blocks */
	if (!bootImagePatchRange(at, length, &payload_bytes)) {
		puts("the device tree names no usable patch");
		return 2;
	}
	printf("the patch: boot_a bytes %u..%u, LUN 4 blocks %llu..%llu, shift %u\n", at, at + length - 1,
	    (unsigned long long)gBootImagePatchFirst4k, (unsigned long long)(gBootImagePatchEnd4k - 1), gBootImagePatchShift);
	rc = thor_os_patch_apply(thorOsPatchLoad, read_blocks, write_blocks, NULL, buffer, 32, window_bytes, payload_bytes);
	printf("LOADTEST: rc=%d reads of boot_a=%d\n", rc, reads);
	return rc;
}
