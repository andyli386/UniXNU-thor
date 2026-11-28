#!/usr/bin/env python3.13
"""Apply the v245 source changes to the APFS clone of the v244 recipe.  Every replacement must match the
expected number of times; nothing is written unless all of them do.

  kernel      kern_exec.c   the log capture library is v245; the display library is staged and put into
                            backboardd; the signed UI adapter is put into PosterBoard
              kern_exit.c   the probe channel's budget 5000 -> 12000; Freeform's and kbd's own "Pong Metal:"
                            lines come to the console under a budget of their own
              witness       the version line
  build       build-xnu-ipados18.sh embeds libpong-display-v245.dylib
              build-pong-ath11k.py  the station: a request like every current client's, the refusal with
                            both frames, the station address from the board driver
  driver      PongWifi/ath11k/compat/PongCompat.h, PongWifi/PongWifiPlatform.cpp: the station address
"""
import json, subprocess, sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
assert root.name == 'v245-unified', root
pending = {}     # path -> new text
def text(path):
    return pending.get(path, path.read_text())
def rep(path, old, new, count=1):
    t = text(path)
    assert t.count(old) == count, (path.name, t.count(old), old[:90])
    pending[path] = t.replace(old, new)
def T(s):
    """the source's indentation: every leading group of four spaces is a tab"""
    out = []
    for line in s.split('\n'):
        stripped = line.lstrip(' ')
        spaces = len(line) - len(stripped)
        out.append('\t' * (spaces // 4) + ' ' * (spaces % 4) + stripped)
    return '\n'.join(out)

k = root / 'build-tree/.cache/xnu-work/bsd/kern/kern_exec.c'
e = root / 'build-tree/.cache/xnu-work/bsd/kern/kern_exit.c'
w = root / 'build-tree/.cache/xnu-work/osfmk/kern/thor_witness_v235.h'
b = root / 'device/scripts/build-xnu-ipados18.sh'
a = root / 'device/scripts/build-pong-ath11k.py'
compat = root / 'device/drivers/PongWifi/ath11k/compat/PongCompat.h'
platform = root / 'device/drivers/PongWifi/PongWifiPlatform.cpp'

# ================================================================ kern_exec.c
# ---- the display library and the adapter in PosterBoard, after the log capture table (matched on the v244 names)
rep(k, T('''        printf("THOR LOGCAP V244: %s log capture armed\\n", logcap_targets[i].name);
        any = true;
        break;
    }
#endif
#ifdef THOR_WIFID_V244_STAGE_PATH
    /* v241: the hold of wifid'''), T('''        printf("THOR LOGCAP V244: %s log capture armed\\n", logcap_targets[i].name);
        any = true;
        break;
    }
#endif
#ifdef THOR_DISPLAY_V245_STAGE_PATH
    /* v245: the display library, in the render server only.  Since v242 the render server draws at
     * 1206x2622 (the pixel size of the iPhone 16 Pro, the identity of the device tree) and the
     * framebuffer driver enlarges every frame to the 1440x3200 panel with the nearest neighbour: the
     * jagged edge the user sees, and 1.5-2 ms a frame.  -[BKSystemFacade startWindowServer] takes the
     * size of +[CADisplay mainDisplay] as its initial main screen size; the library reports what the
     * display says of itself and answers the panel's size for that one.  Boot argument pong_ca_native=0
     * leaves it at watching. */
    static const struct { const char *name, *path; } display_targets[] = {
        { "backboardd", "/usr/libexec/backboardd" },
    };
    static const char display_library[] = THOR_DISPLAY_V245_STAGE_PATH;
    for (unsigned i = 0; i < sizeof(display_targets) / sizeof(display_targets[0]); ++i) {
        const size_t length = strlen(display_targets[i].name);
        if (imgp->ip_ndp->ni_cnd.cn_namelen != length ||
            memcmp(name, display_targets[i].name, length)) continue;
        const size_t display_size = used + (any ? 1 : 0) + sizeof(display_library);
        char path[192];
        int path_length = sizeof(path);
        if (display_size > size || imgp->ip_argspace < display_size + 2 * sizeof(uint64_t) ||
            imgp->ip_strspace < display_size + 2 * sizeof(uint64_t) ||
            !__atomic_load_n(&thor_display_v245_ready, __ATOMIC_ACQUIRE) || !imgp->ip_vp ||
            vn_getpath(imgp->ip_vp, path, &path_length) != 0 || strcmp(path, display_targets[i].path)) break;
        if (any) env[used++] = ':';
        memcpy(env + used, display_library, sizeof(display_library));
        used += sizeof(display_library) - 1;
        printf("THOR DISPLAY V245: %s display library armed\\n", display_targets[i].name);
        any = true;
        break;
    }
#endif
#ifdef THOR_UI_RUNTIME_PAYLOAD
    /* v245: the signed UI adapter in PosterBoard.  The wallpaper stays black because PosterBoard cannot
     * create its data store on the unencrypted Data volume ("Failed to migrate to Version 61 from 59",
     * "could not initialize data store", 2026-11-27).  The adapter carries the storage adaptation for
     * exactly this (Pong Poster18: the data directory, the database initialization, the file protection
     * attributes) and has a branch of its own for the process; on Thor it was only ever put into the
     * processes named by the boot argument, which is full.  It goes in once its payload is staged. */
    static const char poster_name[] = "PosterBoard";
    static const char poster_path[] = "/Applications/PosterBoard.app/PosterBoard";
    static const char adapter_library[] = THOR_UI_RUNTIME_STAGE_PATH;
    if (imgp->ip_ndp->ni_cnd.cn_namelen == sizeof(poster_name) - 1 &&
        !memcmp(name, poster_name, sizeof(poster_name) - 1) &&
        __atomic_load_n(&thor_ui_runtime_ready, __ATOMIC_ACQUIRE)) {
        const size_t adapter_size = used + (any ? 1 : 0) + sizeof(adapter_library);
        char path[192];
        int path_length = sizeof(path);
        if (adapter_size <= size && imgp->ip_argspace >= adapter_size + 2 * sizeof(uint64_t) &&
            imgp->ip_strspace >= adapter_size + 2 * sizeof(uint64_t) && imgp->ip_vp &&
            vn_getpath(imgp->ip_vp, path, &path_length) == 0 && !strcmp(path, poster_path)) {
            if (any) env[used++] = ':';
            memcpy(env + used, adapter_library, sizeof(adapter_library));
            used += sizeof(adapter_library) - 1;
            printf("THOR UI runtime V245: PosterBoard adapter armed\\n");
            any = true;
        }
    }
#endif
#ifdef THOR_WIFID_V244_STAGE_PATH
    /* v241: the hold of wifid'''))

# ---- the staging function of the display library, before the wifid hold library's
rep(k, T('''#ifdef THOR_WIFID_V244_STAGE_PATH
static void
thor_stage_wifid_v241(proc_t p)
'''), T('''#ifdef THOR_DISPLAY_V245_STAGE_PATH
static unsigned thor_display_v245_ready;

static void
thor_stage_display_v245(proc_t p)
{
    int error = thor_stage_data_payload(p, "/private/var/pong", THOR_DISPLAY_V245_STAGE_PATH,
        thor_display_v245_payload, sizeof(thor_display_v245_payload), false);
    if (error) {
        printf("THOR DISPLAY V245: stage failed error=%d\\n", error);
        return;
    }
    __atomic_store_n(&thor_display_v245_ready, 1, __ATOMIC_RELEASE);
    printf("THOR DISPLAY V245: staged %s bytes=%zu\\n", THOR_DISPLAY_V245_STAGE_PATH,
        sizeof(thor_display_v245_payload));
}
#endif

#ifdef THOR_WIFID_V244_STAGE_PATH
static void
thor_stage_wifid_v241(proc_t p)
'''))

# ---- the adapter is ready once its payload is on Data
rep(k, T('''#ifdef THOR_UI_RUNTIME_PAYLOAD
/* Keep the existing adapter untouched. A diagnostic candidate is published
 * to the configured process list only after a successful native APFS write. */
static void
thor_stage_ui_runtime(proc_t p, bool installer_ready)
{
'''), T('''#ifdef THOR_UI_RUNTIME_PAYLOAD
/* Keep the existing adapter untouched. A diagnostic candidate is published
 * to the configured process list only after a successful native APFS write. */
static unsigned thor_ui_runtime_ready;   /* v245: set when the payload is on Data (PosterBoard gets the adapter) */

static void
thor_stage_ui_runtime(proc_t p, bool installer_ready)
{
'''))
rep(k, '\tprintf("THOR UI runtime: PASS path=%s bytes=%zu\\n",\n\t    new_path, sizeof(thor_ui_runtime_payload));\n',
       '\t__atomic_store_n(&thor_ui_runtime_ready, 1, __ATOMIC_RELEASE);\n'
       '\tprintf("THOR UI runtime: PASS path=%s bytes=%zu\\n",\n\t    new_path, sizeof(thor_ui_runtime_payload));\n')

# ---- the staging calls
rep(k, T('''#ifdef THOR_LOGCAP_V244_STAGE_PATH
        thor_stage_logcap_v244(p);
#endif
'''), T('''#ifdef THOR_LOGCAP_V244_STAGE_PATH
        thor_stage_logcap_v244(p);
#endif
#ifdef THOR_DISPLAY_V245_STAGE_PATH
        thor_stage_display_v245(p);
#endif
'''))

# ---- the log capture library is v245
for old, new in (('THOR_LOGCAP_V244_STAGE_PATH', 'THOR_LOGCAP_V245_STAGE_PATH'),
                 ('thor_logcap_v244', 'thor_logcap_v245'),
                 ('"THOR LOGCAP V244: ', '"THOR LOGCAP V245: ')):
    rep(k, old, new, text(k).count(old))
rep(k, '\t * looked for a catalog while it was there, and the first boots with it are the first with the\n'
       '\t * render server in software at another size.  A process whose path is not named is matched by\n'
       '\t * name alone (Freeform: its place was not read from this system). */',
       '\t * looked for a catalog while it was there, and the first boots with it are the first with the\n'
       '\t * render server in software at another size.  A process whose path is not named is matched by\n'
       '\t * name alone (Freeform: its place was not read from this system).\n'
       '\t * v245 (the same processes, logcap245.c): Freeform and kbd report the signal, the registers and\n'
       '\t * the stack of a fatal signal; backboardd\'s first lines are all passed on; more indicator words. */')

# ================================================================ kern_exit.c
rep(e, 'pong_metal_receipt_reserve(&pong_probe_receipt_count, 5000)', 'pong_metal_receipt_reserve(&pong_probe_receipt_count, 12000)')
rep(e, '\tif (req->newlen < 256 && (settings_timing || metal_timing || touch_timing) &&\n'
       '\t    pong_metal_receipt_reserve(touch_timing ? touch_counter :\n'
       '\t        settings ? settings_counter :\n'
       '\t        (handoff ? &pong_desktop_handoff_count : &pong_desktop_receipt_count),\n'
       '\t        touch_counter == &pong_touch_edge_receipt_count ? 2048 :\n'
       '\t        (settings_trace ? 256 : settings_about ? 900 : 48))) {   /* v238: "About" lines carry the thread dumps */',
       '\t/* v245: the graphics bridge\'s own lines of the two processes that die on it.  Freeform ends on a Metal\n'
       '\t * assertion as soon as its canvas is drawn on and kbd without a word (2026-11-27); the bridge reports\n'
       '\t * through this endpoint, but its lines were taken from the Settings app and the render server only. */\n'
       '\tstatic unsigned pong_app_metal_receipt_count;\n'
       '\tconst bool app_metal =\n'
       '\t    (!strcmp(process->p_comm, "Freeform") || !strcmp(process->p_comm, "kbd")) &&\n'
       '\t    !strncmp(text, "Pong Metal: ", sizeof("Pong Metal: ") - 1);\n'
       '\tconst bool timed = req->newlen < 256 && (settings_timing || metal_timing || touch_timing);\n'
       '\tif ((timed || (app_metal && req->newlen < 1024)) &&\n'
       '\t    pong_metal_receipt_reserve(app_metal ? &pong_app_metal_receipt_count :\n'
       '\t        touch_timing ? touch_counter :\n'
       '\t        settings ? settings_counter :\n'
       '\t        (handoff ? &pong_desktop_handoff_count : &pong_desktop_receipt_count),\n'
       '\t        app_metal ? 240 :\n'
       '\t        touch_counter == &pong_touch_edge_receipt_count ? 2048 :\n'
       '\t        (settings_trace ? 256 : settings_about ? 900 : 48))) {   /* v238: "About" lines carry the thread dumps */')

# ================================================================ witness
t = text(w)
start = t.index('\tprintf("THOR WITNESS V244: ')
end = t.index('shared-memory host id (v237)\\n");\n', start) + len('shared-memory host id (v237)\\n");\n')
pending[w] = t[:start] + '''\tprintf("THOR WITNESS V245: the log capture library (v245) reports the signal, the registers and the stack of a fatal "
	    "signal in Freeform and kbd, and every line of the render server at its start; the display library in backboardd "
	    "answers the panel's size (1440x3200) where the render server reads the iPhone 16 Pro's (1206x2622: the jagged edge "
	    "since v242); the signed UI adapter goes into PosterBoard for its wallpaper data store; the Wi-Fi station has an "
	    "address of its own (from the device tree's unique-chip-id) and sends the elements every current client sends; "
	    "carried: the v243 firmware LUN check, the v242 retries and record, the interface-namer marker, the en0 listing, "
	    "the panic record slot (v238), data volume classes A and B open for every process, the modem server's "
	    "shared-memory host id (v237)\\n");
''' + t[end:]

# ================================================================ the build: which libraries are embedded
t = text(b)
a0 = t.index("    text += '#define THOR_LOGCAP_V244_STAGE_PATH")
a1 = t.index("wifid_runtime = os.environ.get('THOR_WIFID_RUNTIME')")
t = t[:a0] + '''    text += '#define THOR_LOGCAP_V245_STAGE_PATH "/private/var/pong/libpong-logcap-v245.dylib"\\n'
    text += c_array('thor_logcap_v245_payload', payload)
display_runtime = os.environ.get('THOR_DISPLAY_RUNTIME')
if display_runtime:
    with Path(display_runtime).open('rb') as display_file:
        payload = display_file.read(128 * 1024 + 1)
    if not 32 <= len(payload) <= 128 * 1024:
        raise ValueError('Expected a bounded arm64 display dylib')
    magic, cpu, _, file_type = struct.unpack_from('<IIII', payload)
    if (magic, cpu, file_type) != (0xfeedfacf, 0x0100000c, 6):
        raise ValueError('The display library requires a thin arm64 MH_DYLIB')
    text += '#define THOR_DISPLAY_V245_STAGE_PATH "/private/var/pong/libpong-display-v245.dylib"\\n'
    text += c_array('thor_display_v245_payload', payload)
''' + t[a1:]
assert 'LOGCAP_V244' not in t and 'logcap_v244' not in t
pending[b] = t

# ================================================================ the station source (built from UniXNU-sheng's nw_sta.c)
rep(a, "    (out / 'nw_sta_pong.c').write_text(sta)\n", r"""    # v245 (Thor): the association request carries what every client of a current access point sends, and a
    # refusal shows both frames and the access point's own elements.  2026-11-27: an open network answered
    # status 48 to a request without Power Capability, Supported Channels, Extended Capabilities or WMM.
    caps = '  u16 caps = WLAN_CAPABILITY_ESS | (C.secure ? WLAN_CAPABILITY_PRIVACY : 0) | WLAN_CAPABILITY_SHORT_SLOT_TIME;'
    tail = '  if (g_vht_ie_len) skb_put_data(skb, g_vht_ie, g_vht_ie_len);\n  tx_frame(skb); }\n'
    refused = '    if (status) { lx_log(4, "wifi: association refused: %u", status); connect_done(NW_RES_REJECTED); return; }'
    helper = 'static void send_assoc(void) {\n'
    if [sta.count(x) for x in (caps, tail, refused, helper)] != [1, 1, 1, 1]:
        raise RuntimeError('shared station association ABI differs')
    sta = sta.replace(helper, r'''/* v245 (Thor): the last association request, and bytes as hexadecimal lines of 48 bytes each. */
static u8 g_assoc_sent[320]; static unsigned g_assoc_sent_len; static u16 g_assoc_caps;
static void log_hex(const char *what, const u8 *p, unsigned n) {
  char line[200];
  for (unsigned at = 0; at < n; at += 48) {
    unsigned take = n - at < 48 ? n - at : 48, k = (unsigned)snprintf(line, sizeof line, "wifi: %s [%u..%u of %u]:", what, at, at + take - 1, n);
    for (unsigned i = 0; i < take && k + 2 < sizeof line; i++) k += (unsigned)snprintf(line + k, sizeof line - k, "%02x", p[at + i]);
    lx_log(4, "%s", line); } }
''' + helper)
    sta = sta.replace(caps, '''  /* v245 (Thor): short slot time only where the access point runs it (always in 5 GHz), and short preamble likewise. */
  u16 caps = WLAN_CAPABILITY_ESS | (C.secure ? WLAN_CAPABILITY_PRIVACY : 0) |
      (C.ap.freq >= 4900 ? WLAN_CAPABILITY_SHORT_SLOT_TIME : (C.ap.caps & (WLAN_CAPABILITY_SHORT_SLOT_TIME | WLAN_CAPABILITY_SHORT_PREAMBLE)));
  g_assoc_caps = caps;''')
    sta = sta.replace(tail, r'''  if (g_vht_ie_len) skb_put_data(skb, g_vht_ie, g_vht_ie_len);
  /* v245 (Thor): Power Capability (0 to 20 dBm), Supported Channels (1 to 11), Extended Capabilities and the WMM
   * information element: an HT station is a QoS station, and spectrum management elements are valid in any request. */
  { static const u8 power[] = { WLAN_EID_PWR_CAPABILITY, 2, 0, 20 };
    static const u8 channels[] = { WLAN_EID_SUPPORTED_CHANNELS, 2, 1, 11 };
    static const u8 extended[] = { WLAN_EID_EXT_CAPABILITY, 8, 0, 0, 0, 0, 0, 0, 0, 0 };
    static const u8 wmm[] = { WLAN_EID_VENDOR_SPECIFIC, 7, 0x00, 0x50, 0xf2, 0x02, 0x01, 0x01, 0x00, 0x00 };
    skb_put_data(skb, power, sizeof power); skb_put_data(skb, channels, sizeof channels);
    skb_put_data(skb, extended, sizeof extended); skb_put_data(skb, wmm, sizeof wmm); }
  g_assoc_sent_len = skb->len < sizeof g_assoc_sent ? skb->len : sizeof g_assoc_sent;
  memcpy(g_assoc_sent, skb->data, g_assoc_sent_len);
  tx_frame(skb); }
''')
    sta = sta.replace(refused, r'''    if (status) {
      lx_log(4, "wifi: association refused: %u (the request was %u bytes with caps %04x, HT %s, VHT %s; the access point's beacon says %04x and %u bytes of elements)",
             status, g_assoc_sent_len, g_assoc_caps, g_ht_on ? "yes" : "no", g_vht_on ? "yes" : "no", C.ap.caps, C.ap.ie_len);
      log_hex("association response", skb->data, skb->len < 160 ? skb->len : 160);
      log_hex("association request", g_assoc_sent, g_assoc_sent_len);
      log_hex("access point elements", C.ap.ie, C.ap.ie_len);
      connect_done(NW_RES_REJECTED); return; }''')
    (out / 'nw_sta_pong.c').write_text(sta)
""")
rep(a, "    allowed = {'_PongWifiRandom','_pong_wifi_ps','_pong_wifi_irq_due',",
    "    allowed = {'_PongWifiRandom','_pong_wifi_ps','_pong_wifi_irq_due','_pong_wifi_station_mac',")

# ================================================================ the station address
rep(compat, T('''static inline int device_get_mac_address(struct device *device, u8 *address)
{ (void)device; memset(address, 0, 6); return -ENODEV; }
'''), T('''/* v245 (Thor): the board driver names the station's address (PongWifiPlatform.cpp, pong_wifi_station_mac).
 * With nothing from it the radio firmware's own default is used, as before. */
extern int pong_wifi_station_mac(u8 *address);
static inline int device_get_mac_address(struct device *device, u8 *address)
{ (void)device; memset(address, 0, 6); return pong_wifi_station_mac(address); }
'''))
rep(platform, '// 802.11 power save is not requested on Thor yet: the panel never sleeps.\n'
              'extern "C" int pong_wifi_ps(void) {return 0;}\n',
              '// 802.11 power save is not requested on Thor yet: the panel never sleeps.\n'
              'extern "C" int pong_wifi_ps(void) {return 0;}\n'
              '// v245: the station\'s address.  Without a calibration MAC the radio firmware answers the same default on\n'
              '// every board (Thor: 00:03:7f:12:34:2e, the "en0 is up, hardware address" line of every boot to v244), so each\n'
              '// device of this port came to an access point under one address.  The loader writes the SoC\'s unique chip ID into\n'
              '// the device tree (/chosen, unique-chip-id); one locally administered address is derived from it, the same on\n'
              '// every boot.\n'
              'extern "C" int pong_wifi_station_mac(uint8_t out[6]) {\n'
              ' uint8_t unique[16]; unsigned length=0;\n'
              ' auto chosen=IORegistryEntry::fromPath("/chosen",gIODTPlane);\n'
              ' auto data=chosen?OSDynamicCast(OSData,chosen->getProperty("unique-chip-id")):nullptr;\n'
              ' if(data){length=unsigned(data->getLength());if(length>sizeof unique)length=sizeof unique;if(length)memcpy(unique,data->getBytesNoCopy(),length);}\n'
              ' if(chosen)chosen->release();\n'
              ' if(!length){IOLog("PongWifi: no unique-chip-id in the device tree; the radio firmware\'s default address stays\\n");return -1;}\n'
              ' static const char label[]="thor pong wlan";\n'
              ' uint64_t h=1469598103934665603ull;\n'
              ' for(const char *p=label;*p;p++){h^=uint8_t(*p);h*=1099511628211ull;}\n'
              ' for(unsigned i=0;i<8;i++){h^=unique[i];h*=1099511628211ull;}\n'
              ' h+=0x9e3779b97f4a7c15ull;h=(h^(h>>30))*0xbf58476d1ce4e5b9ull;h=(h^(h>>27))*0x94d049bb133111ebull;h^=h>>31;\n'
              ' for(unsigned i=0;i<6;i++)out[i]=uint8_t(h>>(8*(5-i)));\n'
              ' out[0]=uint8_t((out[0]&0xfc)|0x02);        /* locally administered, unicast */\n'
              ' return 0;\n'
              '}\n')

# ================================================================ apply
for path, new in pending.items():
    path.write_text(new)
    print('patched', path.relative_to(root))

bk = root / 'build-kernel.sh'; s = bk.read_text()
old = 'export THOR_LOGCAP_RUNTIME="$out/runtime/libpong-logcap-v244.dylib"\n'
assert s.count(old) == 1
s = s.replace(old, 'export THOR_LOGCAP_RUNTIME="$out/runtime/libpong-logcap-v245.dylib"\nexport THOR_DISPLAY_RUNTIME="$out/runtime/libpong-display-v245.dylib"\n')
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
p = d['payloads']
assert 'libpong-logcap-v244.dylib' in p and 'libpong-display-v245.dylib' not in p, sorted(p)
del p['libpong-logcap-v244.dylib']
p['libpong-logcap-v245.dylib'] = entry('libpong-logcap-v245.dylib')
p['libpong-display-v245.dylib'] = entry('libpong-display-v245.dylib')
assert not (root / 'runtime/libpong-logcap-v244.dylib').exists()
old_text = fj.read_text()
new_text = json.dumps(d, indent=2, ensure_ascii=False) + '\n'
if old_text.rstrip('\n').endswith('}') and not old_text.endswith('}\n'):
    new_text = new_text.rstrip('\n')
fj.write_text(new_text)
print('inputs record rewritten')
