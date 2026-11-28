/* Host test of the log capture library v245 (compiled for this Mac under several process names).
   Every run writes a few kinds of lines to its error output; with the library the reader thread
   passes on only what the policy allows.
     SpringBoard: keyword lines only;
     backboardd: every line for the first ten seconds, then keyword lines;
     Freeform: an indicator line, then an abort, as an app with an uncaught Metal assertion;
     kbd: an indicator line, then a write to an unmapped address three calls deep (a real fault with
          frame records). */
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static __attribute__((noinline)) void level3(volatile int *address) { *address = 1; }
static __attribute__((noinline)) void level2(volatile int *address) { level3(address); }
static __attribute__((noinline)) void level1(volatile int *address) { level2(address); }

int main(void)
{
	const char *name = getprogname();
	if (!strcmp(name, "backboardd")) {
		fprintf(stderr, "noise: before the window server\n");
		fprintf(stderr, "Pong Metal: handoff Turnip V162 active\n");
		fprintf(stderr, "[Display] StartWindowServer: initial main screen size:{1206, 2622} scale:3\n");
		sleep(11);                   /* the window (10 s) is closed now */
		fprintf(stderr, "a battery percent line\n");
		fprintf(stderr, "noise: after the window\n");
		usleep(300000);                 /* let the reader pass on what it may */
		return 0;
	}
	if (!strcmp(name, "MessagesBlastDoorService")) {
	 fprintf(stderr, "noise: a blast door line\n");
  usleep(300000);
  fprintf(stderr, "*** Terminating app due to uncaught exception 'NSInternalInconsistencyException', reason: 'test blast door'\n");
  abort();
 }
	if (!strcmp(name, "PosterBoard")) {
		fprintf(stderr, "noise: a poster line\n");
		fprintf(stderr, "[DataStore] Gonna init the datastore\n");
		usleep(300000);
		fprintf(stderr, "*** Terminating app due to uncaught exception 'NSInvalidArgumentException', reason: 'test poster'\n");
		abort();
	}
	if (!strcmp(name, "kbd")) {
		fprintf(stderr, "boring keyboard line\n");
		fprintf(stderr, "LayoutStar keyboardName iPhone-PortraitChoco-Pinyin-Simplified resulted in (null)\n");
		usleep(200000);
		level1((volatile int *)0x123456789);
		return 0;
	}
	fprintf(stderr, "noise: a line about nothing at all\n");
	fprintf(stderr, "WallpaperKit: Looking for default wallpaper based on deviceProductType 'iPhone17,1'\n");
	fprintf(stderr, "PBFPoster something about a poster\n");
	fprintf(stderr, "boring line two\n");
	fprintf(stderr, "a battery percent line\n");
	fprintf(stderr, "WKDefaultWallpaperManager: no lookup file\n");
	if (!strcmp(name, "Freeform")) {
		fprintf(stderr, "Metal device created\n");
		fprintf(stderr, "harmless line right before the end\n");
		usleep(200000);                 /* let the reader pass on what is there */
		fprintf(stderr, "*** Terminating app due to uncaught exception 'NSInvalidArgumentException', reason: 'test'\n");
		abort();
	}
	usleep(300000);
	fprintf(stderr, "last noise line\n");
	return 0;
}
