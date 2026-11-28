#!/usr/bin/env python3.13
"""Apply the v244 source changes to the APFS clone of the v243 recipe.  Every replacement must match the
expected number of times; nothing is written unless all of them do."""
import json, subprocess, sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
assert root.name == 'v244-unified', root
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

# ---------------------------------------------------------------- probe: v244, and back into wifid
for old, new in (('THOR_PROBE_V241_STAGE_PATH', 'THOR_PROBE_V244_STAGE_PATH'),
                 ('thor_probe_v241_ready', 'thor_probe_v244_ready'),
                 ('thor_probe_v241_payload', 'thor_probe_v244_payload'),
                 ('"THOR PROBE V241: ', '"THOR PROBE V244: ')):
    rep(k, old, new, text(k).count(old))
rep(k, '''	 * run; nothing is put into wifid or airportd (wifid gets libpong-wifid-v241 instead), and no
	 * thread is sampled but PosterBoard's. */
	static const struct { const char *name, *path; } probe_targets[] = {
		{ "PosterBoard", "/Applications/PosterBoard.app/PosterBoard" },
		{ "configd", "/usr/libexec/configd" },
	};''', '''	 * run; nothing is put into airportd, and no thread is sampled but PosterBoard's.
	 * v244: wifid is back in the table (it was until v240): after the first run with the radio up
	 * (v243 with the wlan node) Settings hung on wifid and nothing said where its threads were, so
	 * libpong-probe-v244 samples them (next to libpong-wifid-v244, which holds the daemon and keeps
	 * its own log). */
	static const struct { const char *name, *path; } probe_targets[] = {
		{ "PosterBoard", "/Applications/PosterBoard.app/PosterBoard" },
		{ "configd", "/usr/libexec/configd" },
		{ "wifid", "/usr/sbin/wifid" },
	};''')

# ---------------------------------------------------------------- wifid hold library: v244
for old, new in (('THOR_WIFID_V241_STAGE_PATH', 'THOR_WIFID_V244_STAGE_PATH'),
                 ('thor_wifid_v241_ready', 'thor_wifid_v244_ready'),
                 ('thor_wifid_v241_payload', 'thor_wifid_v244_payload')):
    rep(k, old, new, text(k).count(old))
rep(k, '		printf("THOR WIFI V241: stage failed error=%d\\n", error);', '		printf("THOR WIFI V244: stage failed error=%d\\n", error);')
rep(k, '	printf("THOR WIFI V241: staged %s bytes=%zu\\n", THOR_WIFID_V244_STAGE_PATH,', '	printf("THOR WIFI V244: staged %s bytes=%zu\\n", THOR_WIFID_V244_STAGE_PATH,')

# ---------------------------------------------------------------- the wallpaper library is gone; the log capture library takes its place
a = text(k).index('#ifdef THOR_WALLPAPER_V243_STAGE_PATH\nstatic unsigned thor_wallpaper_v243_ready;')
b2 = text(k).index('#ifdef THOR_WIFID_V244_STAGE_PATH\nstatic void\nthor_stage_wifid_v241')
pending[k] = text(k)[:a] + r'''#ifdef THOR_LOGCAP_V244_STAGE_PATH
static unsigned thor_logcap_v244_ready;

static void
thor_stage_logcap_v244(proc_t p)
{
	int error = thor_stage_data_payload(p, "/private/var/pong", THOR_LOGCAP_V244_STAGE_PATH,
	    thor_logcap_v244_payload, sizeof(thor_logcap_v244_payload), false);
	if (error) {
		printf("THOR LOGCAP V244: stage failed error=%d\n", error);
		return;
	}
	__atomic_store_n(&thor_logcap_v244_ready, 1, __ATOMIC_RELEASE);
	printf("THOR LOGCAP V244: staged %s bytes=%zu\n", THOR_LOGCAP_V244_STAGE_PATH,
	    sizeof(thor_logcap_v244_payload));
}
#endif

''' + text(k)[b2:]
a = text(k).index('#ifdef THOR_WALLPAPER_V243_STAGE_PATH\n\t/* v243: the wallpaper catalog library goes into the processes that load WallpaperKit')
b2 = text(k).index('#ifdef THOR_WIFID_V244_STAGE_PATH\n\t/* v241: the hold of wifid')
pending[k] = text(k)[:a] + r'''#ifdef THOR_LOGCAP_V244_STAGE_PATH
	/* v244: the log capture library.  It switches libsystem_trace to mirroring os_log to the process's
	 * error output, filters it and passes it on through the kernel's diagnostic channel.  In:
	 *   SpringBoard (WallpaperKit, PowerUI: the black wallpaper, the battery number),
	 *   backboardd (the render server: since v242 it draws in software at 1206x2622 instead of
	 *     1440x3200, which is the jagged edge the user sees and costs 1.5-2 ms a frame),
	 *   PosterBoard and CollectionsPoster (the wallpaper),
	 *   PowerUIAgent (the battery number),
	 *   Freeform and kbd (they die: 2026-11-27, Freeform as soon as the canvas is drawn on, kbd the
	 *     first time the keyboard was asked for), and no crash report is written because the five
	 *     corpse slots of every boot are taken in the first 83 seconds; the reason of an uncaught
	 *     exception is on the error output before the process aborts).
	 * The wallpaper catalog library of v242 and v243 is not injected any more: WallpaperKit never
	 * looked for a catalog while it was there, and the first boots with it are the first with the
	 * render server in software at another size.  A process whose path is not named is matched by
	 * name alone (Freeform: its place was not read from this system). */
	static const struct { const char *name, *path; } logcap_targets[] = {
		{ "SpringBoard", "/System/Library/CoreServices/SpringBoard.app/SpringBoard" },
		{ "backboardd", "/usr/libexec/backboardd" },
		{ "PosterBoard", "/Applications/PosterBoard.app/PosterBoard" },
		{ "CollectionsPoster",
		    "/System/Library/PrivateFrameworks/WallpaperKit.framework/PlugIns/CollectionsPoster.appex/CollectionsPoster" },
		{ "PowerUIAgent", "/usr/libexec/PowerUIAgent" },
		{ "Freeform", NULL },
		{ "kbd", "/System/Library/TextInput/kbd" },
	};
	static const char logcap_library[] = THOR_LOGCAP_V244_STAGE_PATH;
	for (unsigned i = 0; i < sizeof(logcap_targets) / sizeof(logcap_targets[0]); ++i) {
		const size_t length = strlen(logcap_targets[i].name);
		if (imgp->ip_ndp->ni_cnd.cn_namelen != length ||
		    memcmp(name, logcap_targets[i].name, length)) continue;
		const size_t logcap_size = used + (any ? 1 : 0) + sizeof(logcap_library);
		char path[192];
		int path_length = sizeof(path);
		if (logcap_size > size || imgp->ip_argspace < logcap_size + 2 * sizeof(uint64_t) ||
		    imgp->ip_strspace < logcap_size + 2 * sizeof(uint64_t) ||
		    !__atomic_load_n(&thor_logcap_v244_ready, __ATOMIC_ACQUIRE) || !imgp->ip_vp ||
		    (logcap_targets[i].path && (vn_getpath(imgp->ip_vp, path, &path_length) != 0 ||
		    strcmp(path, logcap_targets[i].path)))) break;
		if (any) env[used++] = ':';
		memcpy(env + used, logcap_library, sizeof(logcap_library));
		used += sizeof(logcap_library) - 1;
		printf("THOR LOGCAP V244: %s log capture armed\n", logcap_targets[i].name);
		any = true;
		break;
	}
#endif
''' + text(k)[b2:]
rep(k, '''#ifdef THOR_WALLPAPER_V243_STAGE_PATH
		thor_stage_wallpaper_v242(p);
#endif
''', '''#ifdef THOR_LOGCAP_V244_STAGE_PATH
		thor_stage_logcap_v244(p);
#endif
''')
assert 'WALLPAPER_V243' not in pending[k] and 'wallpaper_v243' not in pending[k]

# ---------------------------------------------------------------- the diagnostic channel: who may write, and how many lines
rep(e, '''	    strcmp(process->p_comm, "chronod") == 0 ||
	    strcmp(process->p_comm, "CollectionsPoste") == 0 ||''', '''	    strcmp(process->p_comm, "chronod") == 0 ||
	    strcmp(process->p_comm, "CollectionsPoste") == 0 ||
	    /* v244: the log capture library reports from the render server, PowerUIAgent, Freeform and kbd. */
	    strcmp(process->p_comm, "backboardd") == 0 ||
	    strcmp(process->p_comm, "PowerUIAgent") == 0 ||
	    strcmp(process->p_comm, "Freeform") == 0 ||
	    strcmp(process->p_comm, "kbd") == 0 ||''')
rep(e, 'pong_metal_receipt_reserve(&pong_probe_receipt_count, 1500)', 'pong_metal_receipt_reserve(&pong_probe_receipt_count, 5000)')

# ---------------------------------------------------------------- witness
t = text(w)
a = t.index('\tprintf("THOR WITNESS V243: the primary partition table of the firmware LUN decides')
b2 = t.index('\n', t.index('shared-memory host id (v237)\\n");', a))
pending[w] = t[:b2 + 1] + '''	printf("THOR WITNESS V244: the wallpaper catalog library is not injected (WallpaperKit never looked for a catalog "
	    "while it was there, and the first boots with it are the first with the render server in software at "
	    "1206x2622); a log capture library is in SpringBoard, backboardd, PosterBoard, CollectionsPoster, PowerUIAgent, "
	    "Freeform and kbd (their own os_log, and the reason of an uncaught exception); the Wi-Fi daemon keeps its own log "
	    "with os_log switched on at its source, and is sampled by the probe again; carried: the v243 firmware LUN check, "
	    "the v242 retries and record, the interface-namer marker, the en0 listing, the panic record slot (v238), data "
	    "volume classes A and B open for every process, the modem server's shared-memory host id (v237)\\n");
''' + t[b2 + 1:]

# ---------------------------------------------------------------- the build: which libraries are embedded
t = text(b)
a = t.index("    text += '#define THOR_PROBE_V241_STAGE_PATH")
b2 = t.index("wifid_runtime = os.environ.get('THOR_WIFID_RUNTIME')")
t = t[:a] + '''    text += '#define THOR_PROBE_V244_STAGE_PATH "/private/var/pong/libpong-probe-v244.dylib"\\n'
    text += c_array('thor_probe_v244_payload', payload)
logcap_runtime = os.environ.get('THOR_LOGCAP_RUNTIME')
if logcap_runtime:
    with Path(logcap_runtime).open('rb') as logcap_file:
        payload = logcap_file.read(128 * 1024 + 1)
    if not 32 <= len(payload) <= 128 * 1024:
        raise ValueError('Expected a bounded arm64 log capture dylib')
    magic, cpu, _, file_type = struct.unpack_from('<IIII', payload)
    if (magic, cpu, file_type) != (0xfeedfacf, 0x0100000c, 6):
        raise ValueError('The log capture library requires a thin arm64 MH_DYLIB')
    text += '#define THOR_LOGCAP_V244_STAGE_PATH "/private/var/pong/libpong-logcap-v244.dylib"\\n'
    text += c_array('thor_logcap_v244_payload', payload)
''' + t[b2:]
assert t.count("THOR_WIFID_V241_STAGE_PATH") == 1 and t.count("thor_wifid_v241_payload") == 1
t = t.replace("THOR_WIFID_V241_STAGE_PATH", "THOR_WIFID_V244_STAGE_PATH").replace("thor_wifid_v241_payload", "thor_wifid_v244_payload")
assert 'WALLPAPER' not in t
pending[b] = t

# ---------------------------------------------------------------- apply
for path, new in pending.items():
    path.write_text(new)
    print('patched', path.relative_to(root))

bk = root / 'build-kernel.sh'; s = bk.read_text()
old = '''export THOR_PROBE_RUNTIME="$out/runtime/libpong-probe-v241.dylib"
export THOR_WALLPAPER_RUNTIME="$out/runtime/libpong-wallpaper-v243.dylib"
export THOR_WIFID_RUNTIME="$out/runtime/libpong-wifid-v241.dylib"
'''
assert s.count(old) == 1
s = s.replace(old, '''export THOR_PROBE_RUNTIME="$out/runtime/libpong-probe-v244.dylib"
export THOR_WIFID_RUNTIME="$out/runtime/libpong-wifid-v244.dylib"
export THOR_LOGCAP_RUNTIME="$out/runtime/libpong-logcap-v244.dylib"
''')
bk.write_text(s)
print('patched build-kernel.sh')

# ---------------------------------------------------------------- the inputs record
fj = root / 'runtime/final-inputs.json'
d = json.loads(fj.read_text())
def entry(name):
    lib = root / 'runtime' / name
    sha = subprocess.check_output(['shasum', '-a', '256', str(lib)]).decode().split()[0]
    uuid = subprocess.check_output(['dwarfdump', '--uuid', str(lib)]).decode().split()[1]
    return {'sha256': sha, 'uuid': uuid, 'bytes': lib.stat().st_size}
p = d['payloads']
for gone in ('libpong-wallpaper-v243.dylib', 'libpong-probe-v241.dylib', 'libpong-wifid-v241.dylib'):
    assert gone in p, gone
    del p[gone]
for new in ('libpong-probe-v244.dylib', 'libpong-wifid-v244.dylib', 'libpong-logcap-v244.dylib'):
    p[new] = entry(new)
old = fj.read_text()
new_text = json.dumps(d, indent=2, ensure_ascii=False) + '\n'
if old.rstrip('\n').endswith('}') and not old.endswith('}\n'):
    new_text = new_text.rstrip('\n')
fj.write_text(new_text)
for gone in ('libpong-wallpaper-v243.dylib', 'libpong-probe-v241.dylib', 'libpong-wifid-v241.dylib'):
    (root / 'runtime' / gone).unlink()
print('inputs record rewritten')
