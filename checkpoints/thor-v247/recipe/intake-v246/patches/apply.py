#!/usr/bin/env python3.13
"""Apply the v246 source changes to the APFS clone of the v245 recipe.  Every replacement must match the
expected number of times; nothing is written unless all of them do.

  v245 on the phone (2026-11-27 19:48): the display library kept {1206, 2622} because the display of the render
  server has no size of its own (native bounds 0x0) when the window server starts; PosterBoard aborted with the
  adapter and the reason was not captured (the adapter's handler replaced the capture library's, and PosterBoard
  was not in crash mode); the access point refused the association again (status 48) with the station's own
  address and the complete request.

  kernel      kern_exec.c  the log capture and the display library are v246 (sources: logcap246.c, display246.c)
              witness      the version line
  build       build-xnu-ipados18.sh embeds both; build-kernel.sh names them
  station     build-pong-ath11k.py: when an access point refuses a request with high-throughput capabilities,
              the station joins once more as a plain 802.11g station (no HT, no WMM, no extended capabilities)
"""
import json, subprocess, sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
assert root.name == 'v246-unified', root
pending = {}     # path -> new text
def text(path):
    return pending.get(path, path.read_text())
def rep(path, old, new, count=1):
    t = text(path)
    assert t.count(old) == count, (path.name, t.count(old), old[:90])
    pending[path] = t.replace(old, new)

k = root / 'build-tree/.cache/xnu-work/bsd/kern/kern_exec.c'
w = root / 'build-tree/.cache/xnu-work/osfmk/kern/thor_witness_v235.h'
b = root / 'device/scripts/build-xnu-ipados18.sh'
a = root / 'device/scripts/build-pong-ath11k.py'

# ================================================================ kern_exec.c: both libraries are v246
for old, new in (('THOR_LOGCAP_V245_STAGE_PATH', 'THOR_LOGCAP_V246_STAGE_PATH'),
                 ('thor_logcap_v245', 'thor_logcap_v246'),
                 ('"THOR LOGCAP V245: ', '"THOR LOGCAP V246: '),
                 ('THOR_DISPLAY_V245_STAGE_PATH', 'THOR_DISPLAY_V246_STAGE_PATH'),
                 ('thor_display_v245', 'thor_display_v246'),
                 ('"THOR DISPLAY V245: ', '"THOR DISPLAY V246: ')):
    rep(k, old, new, text(k).count(old))
assert 'V245_STAGE_PATH' not in text(k), 'a v245 library path is left in kern_exec.c'

# ================================================================ witness
t = text(w)
start = t.index('\tprintf("THOR WITNESS V245: ')
end = t.index('shared-memory host id (v237)\\n");\n', start) + len('shared-memory host id (v237)\\n");\n')
pending[w] = t[:start] + '''\tprintf("THOR WITNESS V246: the display library in backboardd answers the panel's size (1440x3200) also when the display "
	    "has no size of its own (v245: native bounds 0x0 at the start of the window server, so {1206, 2622} was kept and the frames "
	    "stayed at 1206x2622); the log capture library v246 reports the death of PosterBoard too, and arms its handlers again "
	    "after the other libraries (v245: PosterBoard aborted three times with the UI adapter and the reason was not seen); the Wi-Fi "
	    "station joins once more as a plain 802.11g station when its request with high-throughput capabilities is refused "
	    "(v245: status 48 again, with the station's own address and the complete request); carried: everything of v245 and v244, "
	    "the v243 firmware LUN check, the v242 retries and record, the interface-namer marker, the en0 listing, the panic "
	    "record slot (v238), data volume classes A and B open for every process, the modem server's shared-memory host id (v237)\\n");
''' + t[end:]

# ================================================================ the build: which libraries are embedded
for old, new in (('THOR_LOGCAP_V245_STAGE_PATH "/private/var/pong/libpong-logcap-v245.dylib"',
                  'THOR_LOGCAP_V246_STAGE_PATH "/private/var/pong/libpong-logcap-v246.dylib"'),
                 ("c_array('thor_logcap_v245_payload', payload)", "c_array('thor_logcap_v246_payload', payload)"),
                 ('THOR_DISPLAY_V245_STAGE_PATH "/private/var/pong/libpong-display-v245.dylib"',
                  'THOR_DISPLAY_V246_STAGE_PATH "/private/var/pong/libpong-display-v246.dylib"'),
                 ("c_array('thor_display_v245_payload', payload)", "c_array('thor_display_v246_payload', payload)")):
    rep(b, old, new)

# ================================================================ the station: a plain second request after a refusal
# The build script generates the station source; its text holds the C snippets in raw strings.  The delimiters
# of those strings appear in what is matched here, so these four use double quotes around themselves.
D3 = "'" * 3
refused_old = (
    '      log_hex("association response", skb->data, skb->len < 160 ? skb->len : 160);\n'
    '      log_hex("association request", g_assoc_sent, g_assoc_sent_len);\n'
    '      log_hex("access point elements", C.ap.ie, C.ap.ie_len);\n'
    '      connect_done(NW_RES_REJECTED); return; }' + D3 + ')')
refused_new = (
    '      if (g_legacy) { lx_log(4, "wifi: the plain request (no HT) was refused too"); log_hex("plain association response",'
    ' skb->data, skb->len < 64 ? skb->len : 64); connect_done(NW_RES_REJECTED); return; }\n'
    '      log_hex("association response", skb->data, skb->len < 160 ? skb->len : 160);\n'
    '      log_hex("association request", g_assoc_sent, g_assoc_sent_len);\n'
    '      log_hex("access point elements", C.ap.ie, C.ap.ie_len);\n'
    '      if (g_ht_on) {   /* v246: once more as a plain 802.11g station: no HT, no WMM, no extended capabilities */\n'
    '        lx_log(4, "wifi: joining once more as a plain 802.11g station");\n'
    '        g_legacy = g_retry = 1; teardown(0, 0); C.state = NW_STA_IDLE; C.result = 0; start_connect(); return; }\n'
    '      connect_done(NW_RES_REJECTED); return; }' + D3 + ')')
rep(a, refused_old, refused_new)
rep(a, "static u8 g_assoc_sent[320]; static unsigned g_assoc_sent_len; static u16 g_assoc_caps;\n",
    "static u8 g_assoc_sent[320]; static unsigned g_assoc_sent_len; static u16 g_assoc_caps;\n"
    "static int g_legacy, g_retry;   /* v246: the plain request of the retry, and the flag that announces it */\n"
    "static void start_connect(void);\n")
rep(a, "  { static const u8 power[] = { WLAN_EID_PWR_CAPABILITY, 2, 0, 20 };",
    "  if (!g_legacy) {   /* v246: the plain request of the retry carries none of these */\n"
    "    static const u8 power[] = { WLAN_EID_PWR_CAPABILITY, 2, 0, 20 };")
write_old = "    (out / 'nw_sta_pong.c').write_text(sta)\n"
write_new = (
    "    # v246: the plain request of the retry.  A new request begins with the full capability set again.\n"
    "    helper = 'static void start_connect(void) {\\n  if (C.state == NW_STA_UP && C.ssid_len == g_req.ssid_len &&'\n"
    "    caps_call = '  build_cap_ies(band);\\n'\n"
    "    ok = '    C.aid = aid; g_vif->cfg.aid = aid; g_vif->cfg.assoc = true; C.sta->aid = aid;'\n"
    "    if [sta.count(x) for x in (helper, caps_call, ok)] != [1, 1, 1]:\n"
    "        raise RuntimeError('shared station retry ABI differs')\n"
    "    sta = sta.replace(helper, 'static void start_connect(void) {\\n  if (!g_retry) g_legacy = 0; g_retry = 0;"
    "\\n  if (C.state == NW_STA_UP && C.ssid_len == g_req.ssid_len &&')\n"
    "    sta = sta.replace(caps_call, '  if (g_legacy) { g_ht_ie_len = g_vht_ie_len = 0; g_ht_on = g_vht_on = false; }"
    " else build_cap_ies(band);\\n')\n"
    "    sta = sta.replace(ok, '    if (g_legacy) lx_log(4, \"wifi: the access point accepted the plain request (no HT)\");"
    "\\n' + ok)\n"
    + write_old)
rep(a, write_old, write_new)

# ================================================================ apply
for path, new in pending.items():
    path.write_text(new)
    print('patched', path.relative_to(root))

bk = root / 'build-kernel.sh'; s = bk.read_text()
old = 'export THOR_LOGCAP_RUNTIME="$out/runtime/libpong-logcap-v245.dylib"\nexport THOR_DISPLAY_RUNTIME="$out/runtime/libpong-display-v245.dylib"\n'
assert s.count(old) == 1
s = s.replace(old, 'export THOR_LOGCAP_RUNTIME="$out/runtime/libpong-logcap-v246.dylib"\nexport THOR_DISPLAY_RUNTIME="$out/runtime/libpong-display-v246.dylib"\n')
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
for old, new in (('libpong-logcap-v245.dylib', 'libpong-logcap-v246.dylib'), ('libpong-display-v245.dylib', 'libpong-display-v246.dylib')):
    assert old in p and new not in p, (old, new, sorted(p))
    del p[old]
    p[new] = entry(new)
    assert not (root / 'runtime' / old).exists()
old_text = fj.read_text()
new_text = json.dumps(d, indent=2, ensure_ascii=False) + '\n'
if old_text.rstrip('\n').endswith('}') and not old_text.endswith('}\n'):
    new_text = new_text.rstrip('\n')
fj.write_text(new_text)
print('inputs record rewritten')
