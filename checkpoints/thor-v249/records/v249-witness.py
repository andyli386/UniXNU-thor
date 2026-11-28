#!/usr/bin/env python3.13
"""v249: the witness line of the kernel tree of the v249 working copy names the OS patch (every replacement must match once)."""
import sys
from pathlib import Path

path = Path(sys.argv[1])
t = path.read_text()
start = t.index('\tprintf("THOR WITNESS V248: the first page of a panic names the fault')
end_marker = 'the v235 witness\\n");\n'
end = t.index(end_marker, start) + len(end_marker)
assert start < end
t = t[:start] + '''\tprintf("THOR WITNESS V249: the phone keyboard layouts go into the system volume at boot: the blocks that differ from the "
	    "installed window ride in the kernel and the storage transport writes them before any window is published, every record "
	    "verified to be the original or already new before anything is written and read back afterwards (THOR OS PATCH V249); "
	    "carried: the first page of a panic names the fault, the camera inventory is off (/chosen has no thor-camera), every death by "
	    "a signal prints one line (PONG DEATH), the log capture library v247, the display library that answers the panel's size also "
	    "for a display with no size of its own, the UI adapter in PosterBoard, the Wi-Fi station's retries, everything of v248 to v239, "
	    "the v236 GPU timing hook, the v235 witness\\n");
''' + t[end:]
path.write_text(t)
print('witness line of v249 written:', path, len(t), 'bytes')
