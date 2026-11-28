#!/usr/bin/env python3.13
"""v245, second pass after apply.py: the two injections that are new in a critical process stop after three starts
of that process.  Should the display library or the adapter make its process crash at start, the fourth start runs
without it, so the phone reaches its desktop as with v244 instead of staying dark."""
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
assert root.name == 'v245-unified', root
k = root / 'build-tree/.cache/xnu-work/bsd/kern/kern_exec.c'
t = k.read_text()

def rep(old, new):
    global t
    assert t.count(old) == 1, (t.count(old), old[:80])
    t = t.replace(old, new)

rep('''\t\t\tvn_getpath(imgp->ip_vp, path, &path_length) != 0 || strcmp(path, display_targets[i].path)) break;
\t\tif (any) env[used++] = ':';
\t\tmemcpy(env + used, display_library, sizeof(display_library));
''', '''\t\t\tvn_getpath(imgp->ip_vp, path, &path_length) != 0 || strcmp(path, display_targets[i].path)) break;
\t\t/* Should the render server keep coming back because of the library, its fourth start runs without it. */
\t\tstatic unsigned display_starts;
\t\tif (__atomic_add_fetch(&display_starts, 1U, __ATOMIC_RELAXED) > 3) {
\t\t\tprintf("THOR DISPLAY V245: not armed: the render server is back for a fourth start\\n");
\t\t\tbreak;
\t\t}
\t\tif (any) env[used++] = ':';
\t\tmemcpy(env + used, display_library, sizeof(display_library));
''')
rep('''\t\t\tvn_getpath(imgp->ip_vp, path, &path_length) == 0 && !strcmp(path, poster_path)) {
\t\t\tif (any) env[used++] = ':';
\t\t\tmemcpy(env + used, adapter_library, sizeof(adapter_library));
\t\t\tused += sizeof(adapter_library) - 1;
\t\t\tprintf("THOR UI runtime V245: PosterBoard adapter armed\\n");
\t\t\tany = true;
\t\t}
''', '''\t\t\tvn_getpath(imgp->ip_vp, path, &path_length) == 0 && !strcmp(path, poster_path)) {
\t\t\t/* Should PosterBoard keep coming back because of the adapter, its fourth start runs without it. */
\t\t\tstatic unsigned poster_starts;
\t\t\tif (__atomic_add_fetch(&poster_starts, 1U, __ATOMIC_RELAXED) <= 3) {
\t\t\t\tif (any) env[used++] = ':';
\t\t\t\tmemcpy(env + used, adapter_library, sizeof(adapter_library));
\t\t\t\tused += sizeof(adapter_library) - 1;
\t\t\t\tprintf("THOR UI runtime V245: PosterBoard adapter armed\\n");
\t\t\t\tany = true;
\t\t\t} else {
\t\t\t\tprintf("THOR UI runtime V245: PosterBoard adapter not armed: PosterBoard is back for a fourth start\\n");
\t\t\t}
\t\t}
''')
k.write_text(t)
print('guards in', k.relative_to(root))
