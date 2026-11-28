#!/usr/bin/env python3.13
"""Apply the v247 source changes to the APFS clone of the v246 recipe.  Every replacement must match the
expected number of times; nothing is written unless all of them do.

v247 adds three things to v246, each a diagnosis that costs nothing when it has nothing to report:
  kernel      kern_exit.c   every process that ends by a core-generating signal prints one line with its name,
                            signal, exception, code, address and exit reason (PONG DEATH ...).  The five corpse
                            slots of a boot are gone in its first 80 seconds and the exit line of v73 covers a fixed
                            list of names: the reasons why kbd, MessagesBlastDoorService and Freeform die were never
                            recorded.
              kern_exec.c  the log capture library is v247 and goes into MessagesBlastDoorService too (it dies every
                            ten seconds; the Messages app waits for it and the desktop stops)
              witness      the version line
  driver      device/drivers/PongPower: the read-only camera inventory of THOR CAMERA V247 (headers from
                            thor-out/v247-camera-staging), started from pong_rpmh_init() and gated by the /chosen
                            property thor-camera (absent or 0: nothing is mapped, no thread).  The property itself is
                            added by the packaging script.
  build       build-xnu-ipados18.sh embeds libpong-logcap-v247.dylib; build-kernel.sh names it
"""
import json, subprocess, sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
assert root.name == 'v247-unified', root
pending = {}     # path -> new text
def text(path):
    return pending.get(path, path.read_text())
def rep(path, old, new, count=1):
    t = text(path)
    assert t.count(old) == count, (path.name, t.count(old), old[:90])
    pending[path] = t.replace(old, new)

k = root / 'build-tree/.cache/xnu-work/bsd/kern/kern_exec.c'
e = root / 'build-tree/.cache/xnu-work/bsd/kern/kern_exit.c'
w = root / 'build-tree/.cache/xnu-work/osfmk/kern/thor_witness_v235.h'
b = root / 'device/scripts/build-xnu-ipados18.sh'
p = root / 'device/drivers/PongPower/PongPlatformResources.cpp'
for f in ('PongCamBoard.h', 'PongCamCc.h', 'PongCamCci.h', 'PongCamPower.h', 'PongCamInventory.h', 'PongCamProbe.h'):
    assert (p.parent / f).is_file(), f

# ================================================================ kern_exec.c: the log capture library is v247, in one more process
for old, new in (('THOR_LOGCAP_V246_STAGE_PATH', 'THOR_LOGCAP_V247_STAGE_PATH'),
                 ('thor_logcap_v246', 'thor_logcap_v247'),
                 ('"THOR LOGCAP V246: ', '"THOR LOGCAP V247: ')):
    rep(k, old, new, text(k).count(old))
rep(k, '''\t\t{ "kbd", "/System/Library/TextInput/kbd" },
\t};
\tstatic const char logcap_library[] = THOR_LOGCAP_V247_STAGE_PATH;
''', '''\t\t{ "kbd", "/System/Library/TextInput/kbd" },
\t\t/* v247: the helper the Messages app waits for.  It dies every ten seconds of every boot and took two of
\t\t * the five corpse slots without one line of reason; opened, the Messages app then stops the desktop
\t\t * (2026-11-27 19:52).  Matched by name alone. */
\t\t{ "MessagesBlastDoorService", NULL },
\t};
\tstatic const char logcap_library[] = THOR_LOGCAP_V247_STAGE_PATH;
''')

# ================================================================ kern_exit.c: one line for every death by signal
rep(e, '''\t\tcode = ((WTERMSIG(rv) & 0xff) << 24) |
\t\t    ((ut->uu_exception & 0x0f) << 20) |
\t\t    ((int)ut->uu_code & 0xfffff);
\t\tsubcode = ut->uu_subcode;
\t\tetype = ut->uu_exception;
''', '''\t\tcode = ((WTERMSIG(rv) & 0xff) << 24) |
\t\t    ((ut->uu_exception & 0x0f) << 20) |
\t\t    ((int)ut->uu_code & 0xfffff);
\t\tsubcode = ut->uu_subcode;
\t\tetype = ut->uu_exception;

#if defined(PONG)
\t\t/* v247: every process that ends by a core-generating signal says how, once, before anything can drop the
\t\t * information.  The five corpse slots of a boot are gone in its first 80 seconds and the exit line of
\t\t * v73 above covers a fixed list of names, so the deaths of kbd, MessagesBlastDoorService and Freeform were
\t\t * never recorded.  code carries the signal, the exception and the exception code; subcode is the address
\t\t * (a fault address, a trap address, the payload of an abort).  A process name is cut to 16 characters.
\t\t * Bounded to 300 lines a boot. */
\t\t{
\t\t\tstatic unsigned pong_death_lines;
\t\t\tif (__atomic_fetch_add(&pong_death_lines, 1U, __ATOMIC_RELAXED) < 300U) {
\t\t\t\tconst bool has_reason = PROC_HAS_EXITREASON(p);
\t\t\t\tconst char *death_description = has_reason ? exit_reason_get_string_desc(p->p_exit_reason) : NULL;
\t\t\t\tprintf("PONG DEATH %s pid=%d signal=%d exception=%u code=0x%llx subcode=0x%llx namespace=%u "
\t\t\t\t    "reason=0x%llx description=%.256s\\n",
\t\t\t\t    p->p_comm, proc_getpid(p), WTERMSIG(rv), ut->uu_exception, (unsigned long long)code,
\t\t\t\t    (unsigned long long)subcode, has_reason ? p->p_exit_reason->osr_namespace : 0,
\t\t\t\t    (unsigned long long)(has_reason ? p->p_exit_reason->osr_code : 0),
\t\t\t\t    death_description ? death_description : "none");
\t\t\t}
\t\t}
#endif
''')

# ================================================================ witness
t = text(w)
start = t.index('\tprintf("THOR WITNESS V246: ')
end = t.index('shared-memory host id (v237)\\n");\n', start) + len('shared-memory host id (v237)\\n");\n')
pending[w] = t[:start] + '''\tprintf("THOR WITNESS V247: every process that dies by a signal prints one line (PONG DEATH: name, signal, exception, "
	    "code, address, exit reason); the log capture library v247 reports the death of MessagesBlastDoorService too; a read-only "
	    "camera inventory runs when the device tree says thor-camera = 1 (THOR CAMERA V247; nothing is written); carried: the "
	    "display library that answers the panel's size also for a display with no size of its own, the UI adapter in PosterBoard, "
	    "the Wi-Fi station's retries (5 GHz BSS, then plain 802.11g), everything of v246 to v239, the v236 GPU timing hook, the "
	    "v235 witness\\n");
''' + t[end:]

# ================================================================ the build: which libraries are embedded
for old, new in (('THOR_LOGCAP_V246_STAGE_PATH "/private/var/pong/libpong-logcap-v246.dylib"',
                  'THOR_LOGCAP_V247_STAGE_PATH "/private/var/pong/libpong-logcap-v247.dylib"'),
                 ("c_array('thor_logcap_v246_payload', payload)", "c_array('thor_logcap_v247_payload', payload)")):
    rep(b, old, new)

# ================================================================ the driver: the camera inventory, off unless the device tree says so
rep(p, '#include "../PongProbe.h"\n', '#include "../PongProbe.h"\n#include "PongCamProbe.h"          /* THOR CAMERA V247: read-only inventory, gated by /chosen thor-camera */\n')
rep(p, '''extern "C" void pong_rpmh_init(void) {
    thor_battery_start();
    initLock(&powerLock); initLock(&smmuLeaseLock);
}
''', '''extern "C" void pong_rpmh_init(void) {
    thor_battery_start();
    initLock(&powerLock); initLock(&smmuLeaseLock);
    thor_cam_probe::start();   /* THOR CAMERA V247: reads /chosen thor-camera once; off unless the property is set */
}
''')

# ================================================================ apply
for path, new in pending.items():
    path.write_text(new)
    print('patched', path.relative_to(root))

bk = root / 'build-kernel.sh'; s = bk.read_text()
old = 'export THOR_LOGCAP_RUNTIME="$out/runtime/libpong-logcap-v246.dylib"\n'
assert s.count(old) == 1
s = s.replace(old, 'export THOR_LOGCAP_RUNTIME="$out/runtime/libpong-logcap-v247.dylib"\n')
bk.write_text(s)
print('patched build-kernel.sh')

# ================================================================ the inputs record
fj = root / 'runtime/final-inputs.json'
d = json.loads(fj.read_text())
def entry(name):
    lib = root / 'runtime' / name
    sha = subprocess.check_output(['shasum', '-a', '256', str(lib)]).decode().split()[0]
    uuid = subprocess.check_output(['dwarfdump', '--uuid', str(lib)]).decode().split()[1]
    return {'sha256': sha, 'uuid': uuid, 'bytes': lib.stat().st_size}
pays = d['payloads']
old, new = 'libpong-logcap-v246.dylib', 'libpong-logcap-v247.dylib'
assert old in pays and new not in pays, (sorted(pays))
del pays[old]
pays[new] = entry(new)
assert not (root / 'runtime' / old).exists()
old_text = fj.read_text()
new_text = json.dumps(d, indent=2, ensure_ascii=False) + '\n'
if old_text.rstrip('\n').endswith('}') and not old_text.endswith('}\n'):
    new_text = new_text.rstrip('\n')
fj.write_text(new_text)
print('inputs record rewritten')
