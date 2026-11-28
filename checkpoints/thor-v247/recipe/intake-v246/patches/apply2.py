#!/usr/bin/env python3.13
"""v246, second pass on the station source, after apply.py: after a refusal on 2.4 GHz the station first joins
once more on a 5 GHz BSS of the same network, then (if that is refused too) as a plain 802.11g station.

Why (console of v245, 2026-11-27 19:48): the network "<network>" is offered by the same access points on
2.4 GHz (a8:50:81:f8:e2:61) and 5 GHz (a8:50:81:f8:e2:71).  The station takes the strongest BSS, which is
always the 2.4 GHz one, and that BSS answers status 48 to every request, whatever the station sends.  An access
point that offers both bands can steer two-band clients to 5 GHz by refusing them on 2.4 GHz; a client that
only ever tries 2.4 GHz never gets in."""
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
assert root.name == 'v246-unified', root
a = root / 'device/scripts/build-pong-ath11k.py'
t = a.read_text()

def rep(old, new):
    global t
    assert t.count(old) == 1, (t.count(old), old[:90])
    t = t.replace(old, new)

rep("static int g_legacy, g_retry;   /* v246: the plain request of the retry, and the flag that announces it */\n",
    "static int g_legacy, g_retry, g_retry_band;   /* g_retry_band 5: only the 5 GHz BSSes of the network (the retry after a refusal on 2.4 GHz) */\n"
    "static void start_connect(void);\n"
    "/* v246: does the network also have a BSS on 5 GHz? */\n"
    "static int has_5ghz_bss(const u8 *ssid, u8 len) { int found = 0; lock();\n"
    "  for (int i = 0; i < g_nbss; i++) if (g_bss[i].freq >= 4900 && g_bss[i].ssid_len == len && !memcmp(g_bss[i].ssid, ssid, len)) { found = 1; break; }\n"
    "  unlock(); return found; }\n")
rep('''      log_hex("access point elements", C.ap.ie, C.ap.ie_len);
      if (g_ht_on) {   /* v246: once more as a plain 802.11g station''',
    '''      log_hex("access point elements", C.ap.ie, C.ap.ie_len);
      if (!g_retry_band && C.ap.freq < 4900 && has_5ghz_bss(C.ssid, C.ssid_len)) {   /* v246: an access point that offers the network on 5 GHz too may turn two-band clients away from 2.4 GHz */
        lx_log(4, "wifi: joining once more on a 5 GHz BSS of the same network");
        g_retry = 1; g_retry_band = 5; teardown(0, 0); C.state = NW_STA_IDLE; C.result = 0; start_connect(); return; }
      if (g_ht_on) {   /* v246: once more as a plain 802.11g station''')
rep("'static void start_connect(void) {\\n  if (!g_retry) g_legacy = 0; g_retry = 0;",
    "'static void start_connect(void) {\\n  if (!g_retry) { g_legacy = 0; g_retry_band = 0; } g_retry = 0;")
rep('''    (out / 'nw_sta_pong.c').write_text(sta)
''', '''    # v246: the retry on 5 GHz takes the strongest BSS of the network on that band.
    pick = 'if (g_req.has_bssid && memcmp(b->bssid, g_req.bssid, 6)) continue; if (!best || b->signal > best->signal) best = b; }'
    if sta.count(pick) != 1:
        raise RuntimeError('shared station BSS choice ABI differs')
    sta = sta.replace(pick, 'if (g_req.has_bssid && memcmp(b->bssid, g_req.bssid, 6)) continue; if (g_retry_band == 5 && b->freq < 4900) continue; if (!best || b->signal > best->signal) best = b; }')
    (out / 'nw_sta_pong.c').write_text(sta)
''')
a.write_text(t)
import py_compile
py_compile.compile(str(a), doraise=True)
print('second pass in', a.relative_to(root))
