/* The OS patch on the Mac: the same header the kernel is built with, run against image files instead of the phone.
 *
 *   clang -O1 -o harness thor/tests/os_patch_harness.c -I <kernel tree>/bsd/kern
 *   ./harness <image of the window> <patch file> [<window offset in bytes>] [<buffer blocks>] [S<n>: stop after n writes | fail-load]
 *
 * The patch is streamed from its file exactly as the kernel streams it from the boot partition.  The buffer is one
 * mapping with a guard page right after it, so that a write past its end is a crash and not a silent success.
 * "S<n>" simulates an interrupted patch (exit 77): run again to resume.  "fail-load" makes every read of the patch
 * fail, to see that nothing is written then.
 */
#include <fcntl.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#define THOR_OS_PATCH_LOG(fmt, ...)  printf(fmt "\n", ##__VA_ARGS__)
#include "thor_os_patch.h"

static int image, patch_file;
static uint64_t window_offset, loads_done = 0;
static long writes_left = -1, writes_done = 0;
static bool fail_load = false, torn_mode = false;

static int
load_blocks(void *context, uint64_t offset, uint32_t blocks, uint8_t *buffer)
{
	uint64_t length = (uint64_t)blocks * 4096;
	ssize_t got;
	(void)context;
	loads_done++;
	if (fail_load) {
		return 102;
	}
	got = pread(patch_file, buffer, length, (off_t)offset);
	return got == (ssize_t)length ? 0 : 100;
}

static int
read_blocks(void *context, uint64_t block, uint32_t blocks, uint8_t *buffer)
{
	uint64_t length = (uint64_t)blocks * 4096;
	ssize_t got = pread(image, buffer, length, (off_t)(window_offset + block * 4096));
	(void)context;
	return got == (ssize_t)length ? 0 : 100;
}

static int
write_blocks(void *context, uint64_t block, uint32_t blocks, const uint8_t *data)
{
	uint64_t length = (uint64_t)blocks * 4096;
	ssize_t put;
	(void)context;
	if (writes_left == 0) {
		printf("HARNESS: interrupted before write %ld\n", writes_done);
		exit(77);
	}
	if (writes_left > 0) {
		writes_left--;
	}
	if (torn_mode && writes_left == 0) {
		/* the power goes while a write of several blocks is in flight: only the first half reaches the medium */
		if (blocks > 1) {
			length /= 2;
		}
		puts("HARNESS: power lost in the middle of a write");
		exit(77);
	}
	put = pwrite(image, data, length, (off_t)(window_offset + block * 4096));
	writes_done++;
	return put == (ssize_t)length ? 0 : 101;
}

static void
sha_selftest(void)
{
	static const struct { const char *text; const char *digest; } cases[] = {
		{ "", "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855" },
		{ "abc", "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad" },
		{ "abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq",
		  "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1" },
	};
	uint8_t digest[32];
	char hex[65];
	unsigned c, i;
	for (c = 0; c < sizeof(cases) / sizeof(cases[0]); c++) {
		struct thor_os_patch_sha sha;
		size_t length = strlen(cases[c].text);
		/* in one go and in pieces of every size, to exercise the buffer of the context */
		for (i = 0; i <= 70; i++) {
			size_t first = i < length ? i : length;
			thor_os_patch_sha_init(&sha);
			thor_os_patch_sha_update(&sha, (const uint8_t *)cases[c].text, first);
			thor_os_patch_sha_update(&sha, (const uint8_t *)cases[c].text + first, length - first);
			thor_os_patch_sha_final(&sha, digest);
			for (unsigned j = 0; j < 32; j++) {
				snprintf(hex + 2 * j, 3, "%02x", digest[j]);
			}
			if (strcmp(hex, cases[c].digest) != 0) {
				printf("SHA-256 self test FAILED for \"%s\" (split %u): %s\n", cases[c].text, i, hex);
				exit(1);
			}
		}
	}
	printf("SHA-256 self test passed\n");
}

int
main(int argc, char **argv)
{
	uint64_t window_bytes, payload_bytes;
	uint32_t buffer_blocks = argc > 4 ? (uint32_t)strtoul(argv[4], NULL, 0) : 32;
	size_t buffer_bytes = (size_t)buffer_blocks * 4096, mapped, page;
	uint8_t *mapping, *buffer;
	int rc, a;
	long stop;
	long torn_at = 0;

	if (argc < 3 || buffer_blocks == 0 || buffer_blocks > 256) {
		fprintf(stderr, "usage: harness <image of the window> <patch file> [window offset] [buffer blocks 1..256] [S<n> | fail-load]\n");
		return 2;
	}
	window_offset = argc > 3 ? strtoull(argv[3], NULL, 0) : 0;
	for (a = 5; a < argc; a++) {
		if (argv[a][0] == 'S' && (stop = strtol(argv[a] + 1, NULL, 0)) > 0) {
			writes_left = stop;
		} else if (argv[a][0] == 'T') {
			torn_at = strtol(argv[a] + 1, NULL, 0);
			writes_left = torn_at;
			torn_mode = true;
		} else if (strcmp(argv[a], "fail-load") == 0) {
			fail_load = true;
			writes_left = -1;
		} else if (strcmp(argv[a], "torn") == 0) {
			writes_left = torn_at;
			torn_mode = true;
		}
	}
	sha_selftest();
	image = open(argv[1], O_RDWR);
	patch_file = open(argv[2], O_RDONLY);
	if (image < 0 || patch_file < 0) {
		perror(argv[1]);
		return 2;
	}
	window_bytes = (uint64_t)lseek(image, 0, SEEK_END) - window_offset;
	payload_bytes = (uint64_t)lseek(patch_file, 0, SEEK_END);
	page = (size_t)getpagesize();                         /* 16384 on an Apple silicon Mac */
	mapped = (buffer_bytes + page - 1) & ~(page - 1);
	mapping = mmap(NULL, mapped + page, PROT_READ | PROT_WRITE, MAP_ANON | MAP_PRIVATE, -1, 0);
	if (mapping == MAP_FAILED) {
		perror("mmap");
		return 2;
	}
	if (mprotect(mapping + mapped, page, PROT_NONE) != 0) {
		perror("mprotect");
		return 2;
	}
	buffer = mapping + mapped - buffer_bytes;                    /* ends exactly at the guard page */
	rc = thor_os_patch_apply(load_blocks, read_blocks, write_blocks, NULL, buffer, buffer_blocks, window_bytes, payload_bytes);
	printf("HARNESS: rc=%d writes=%ld loads=%llu\n", rc, writes_done, loads_done);
	return rc;
}
