/* Host test of the log capture library (compiled for this Mac, once under the name SpringBoard and
   once under the name Freeform).  The program writes four kinds of lines to its error output; with the
   library the reader thread passes on only what the policy allows.  Under the name Freeform it then
   aborts itself, as an app with an uncaught exception does. */
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(void)
{
	const char *name = getprogname();
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
