#!/usr/bin/env python3.13
"""Write package-v246.py from a copy of package-v245.py: the same packaging and verification with the parent, the
libraries, the messages and the tests of v246.  Every replacement must match once."""
import os
from pathlib import Path

root = Path(os.environ['THOR_ROOT'] + '/thor-out/v246-unified')
s = (root / 'package-v246.py').read_text()
def rep(old, new):
    global s
    assert s.count(old) == 1, (s.count(old), old[:90])
    s = s.replace(old, new)

a = s.index('"""Package the v245 boot image')
b = s.index('"""\nimport argparse') + 3
s = s[:a] + '''"""Package the v246 boot image from the v245 parent: the display library also answers the panel's size for a
display without a size of its own, the wallpaper process reports why it dies with the UI adapter, and the Wi-Fi
station joins once more as a plain 802.11g station when its request is refused.

Why (read from the console of 2026-11-27 19:48, v245):
- Jagged edges.  The display library of v245 loaded in backboardd and -bounds was called at the start of the
  window server; the display had no size of its own then (native bounds 0x0, no current mode, point scale not
  a number) and answered {1206, 2622}.  The library kept it (v245 answered only a display whose native bounds
  were the panel's), and the frames stayed at 1206x2622.  backboardd's own log: "[WindowServer] Display 1 current
  mode (16383 x 16383), preferred mode (0 x 0)".  v246 answers {1440, 3200} for a display with no size as well.
- Wallpaper.  With the UI adapter PosterBoard aborted with SIGABRT three times while initialising its data
  store and ran without the adapter from the fourth start.  The adapter's own handler replaced the capture
  library's, and PosterBoard was not in crash mode, so the reason was not seen.  The capture library v246 keeps
  the last lines of PosterBoard too, passes on lines with an indicator word, and arms its handlers again after
  the other libraries ran.
- Wi-Fi.  The station had an address of its own (62:25:b4:61:44:d4) and sent the complete request; the access
  point answered status 48 again.  When a request with high-throughput capabilities is refused, the station now
  joins once more as a plain 802.11g station (no HT, no WMM, no extended capabilities).

The image carries the wlan node (launchd starts wifid).  Carried unchanged: everything of v245 and v244.  No
boot argument changed.

The compact image is the verified artifact and the one that gets written to boot_a (through TWRP).
"""''' + s[b:]

rep('base = root.parent / "v244-unified"', 'base = root.parent / "v245-unified"')
rep('''v244 = runpy.run_path(str(base / "package-v244.py"))                            # helpers and the lists of the parent
adt_member = kernel_member + ".adt"
BOOT_STACK = v244["BOOT_STACK"]
carried_kernel_strings = v244["carried_kernel_strings"]
witness_strings = v244["witness_strings"]
prologue_frame, symbol_address, bytes_at = v244["prologue_frame"], v244["symbol_address"], v244["bytes_at"]
''', '''v245 = runpy.run_path(str(base / "package-v245.py"))                            # helpers and the lists of the parent
adt_member = kernel_member + ".adt"
BOOT_STACK = v245["BOOT_STACK"]
carried_kernel_strings = v245["carried_kernel_strings"]
witness_strings = v245["witness_strings"]
prologue_frame, symbol_address, bytes_at = v245["prologue_frame"], v245["symbol_address"], v245["bytes_at"]
''')
rep('''    "unified": dict(release="v245-unified", wlan=True, record="unified-verification.json",
                    title="Thor v245 - panel size, wallpaper store, Wi-Fi station"),''',
    '''    "unified": dict(release="v246-unified", wlan=True, record="unified-verification.json",
                    title="Thor v246 - panel size without a mode, wallpaper crash, plain Wi-Fi retry"),''')
rep('''logcap_name = "libpong-logcap-v245.dylib"
display_name = "libpong-display-v245.dylib"
# name: build script, the message that must appear exactly once in it, messages that must appear at least once
new_libraries = {
    logcap_name: ("prepare/logcap/build-logcap245.sh", b"Pong Probe: log v245: ", b"Pong Probe: log v245 "),
    display_name: ("prepare/display/build-display245.sh", b"watched in %s", b"Pong Probe: display v245: ",
                   b"pong_ca_native=0"),
}
# The log capture library of v244 is gone; the log capture and the display library are new builds.
gone_runtime = {"libpong-logcap-v244.dylib"}''', '''logcap_name = "libpong-logcap-v246.dylib"
display_name = "libpong-display-v246.dylib"
# name: build script, the message that must appear exactly once in it, messages that must appear at least once
new_libraries = {
    logcap_name: ("prepare/logcap/build-logcap246.sh", b"Pong Probe: log v246: ", b"Pong Probe: log v246 "),
    display_name: ("prepare/display/build-display246.sh", b"watched in %s", b"Pong Probe: display v246: ",
                   b"pong_ca_native=0"),
}
# The log capture and the display library of v245 are gone; both are new builds.
gone_runtime = {"libpong-logcap-v245.dylib", "libpong-display-v245.dylib"}''')
a = s.index('# Messages only this kernel prints: each present once, absent from v244\'s.')
b = s.index('# Apple\'s own binaries of this system, read from the OS volume')
s = s[:a] + '''# Messages only this kernel prints: each present once, absent from v245's.
new_kernel_strings = (
    b"THOR WITNESS V246: the display library in backboardd answers the panel's size",
    b"THOR LOGCAP V246: staged ",
    b"THOR LOGCAP V246: %s log capture armed",
    b"/private/var/pong/libpong-logcap-v246.dylib",
    b"THOR DISPLAY V246: staged ",
    b"THOR DISPLAY V246: %s display library armed",
    b"/private/var/pong/libpong-display-v246.dylib",
    b"wifi: joining once more as a plain 802.11g station",
    b"wifi: joining once more on a 5 GHz BSS of the same network",
    b"wifi: the access point accepted the plain request (no HT)",
    b"wifi: the plain request (no HT) was refused too",
)
# Messages and paths of v245 that are gone: its log capture and display libraries (rebuilt under new names).
old_kernel_strings = (b"THOR WITNESS V245: the log capture library (v245) reports the signal",
                      b"THOR LOGCAP V245: staged ", b"THOR LOGCAP V245: %s log capture armed",
                      b"/private/var/pong/libpong-logcap-v245.dylib",
                      b"THOR DISPLAY V245: staged ", b"THOR DISPLAY V245: %s display library armed",
                      b"/private/var/pong/libpong-display-v245.dylib")
# The messages v245, v244, v243, v242 and v241 added are all still there, once each (minus the ones named above).
carried_245_strings = tuple(x for x in v245["new_kernel_strings"] if not any(x.startswith(g) for g in old_kernel_strings))
carried_244_strings = v245["carried_244_strings"]
carried_243_strings = v245["carried_243_strings"]
carried_242_strings = v245["carried_242_strings"]
carried_241_strings = v245["carried_241_strings"]

''' + s[b:]

a = s.index('changes = [')
b = s.index(']\n\n\ndef apple_install_names') + 1
s = s[:a] + '''changes = [
    "The display library (libpong-display-v246, backboardd only) answers the panel's size (1440x3200) for -[CADisplay bounds] "
    "when the display reports {1206, 2622} and its native bounds are the panel's or empty.  v245 kept the size because the "
    "display of the render server has no size of its own when its window server starts (native bounds 0x0, no current mode). "
    "Boot argument pong_ca_native=0 leaves it at watching.  Not run on the phone before this image.",
    "The log capture library v246 keeps the last lines of PosterBoard too and passes them on with the signal, the registers "
    "and the frames when it dies; lines with an indicator word are passed on at once; in every crash process the fatal "
    "signal handlers are armed again two seconds after the start and at the first line, after the other injected libraries.",
    "The Wi-Fi station joins once more as a plain 802.11g station (no HT, no WMM, no extended capabilities) when an access "
    "point refuses its request with high-throughput capabilities; both refusals are logged with their frames.",
    "Every library the injected libraries load is compared with the names Apple's own binaries use.",
    "Carried unchanged from v245: the signed UI adapter in PosterBoard, the station's own address and complete request, "
    "the Wi-Fi daemon's library and the probe, the Settings diagnostic, the console capture, the firmware LUN check (v243), "
    "the retries and the record of a failed check (v242), the interface-namer marker and the en0 listing in configd, the "
    "panic record slot (v238), protection classes A and B and IOSurface selector 21 (v237), the v236 GPU timing hook, the "
    "v235 witness.  No boot argument changed.",
]''' + s[b:]

rep('    assert id_ == parent["image_sha256"] and parent["version"] == 244\n'
    '    # The command line is byte-identical to v239 to v244; only the menu title changes.',
    '    assert id_ == parent["image_sha256"] and parent["version"] == 245\n'
    '    # The command line is byte-identical to v239 to v245; only the menu title changes.')
rep('''    # Two runtime inputs are new (the log capture and the display library) and v244's log capture is gone;
    # the probe, the Wi-Fi daemon's library, the console capture and the Settings diagnostic are unchanged.''',
    '''    # Two runtime inputs are new builds (the log capture and the display library) and v245's are gone;
    # the probe, the Wi-Fi daemon's library, the console capture and the Settings diagnostic are unchanged.''')

a = s.index("    # The log capture library's host test, compiled from the same source")
b = s.index('    subprocess.run([sys.executable, "-IB", str(root / "prepare/settings/replay.py")]')
s = s[:a] + '''    # The log capture library's host test, compiled from the same source: under the name SpringBoard only
    # the lines with a keyword are passed on and nothing is left on stderr; under the name backboardd every
    # line of the first seconds and then keyword lines; under the name Freeform the indicator lines, the
    # abort with its signal, registers and frames, and the last lines are passed on (SIGABRT); under the
    # name kbd a real fault three calls deep is reported with its address (SIGSEGV); under the name
    # PosterBoard (v246) a keyword line, an uncaught exception, the signal and the last lines (SIGABRT);
    # under another name nothing is captured.
    host = subprocess.run(["/bin/bash", str(root / "prepare/logcap/hosttest/run246.sh")], check=True, capture_output=True, text=True)
    lines = host.stdout.splitlines()
    names = ("SpringBoard", "backboardd", "Freeform", "kbd", "PosterBoard", "Other")
    at = {name: next(i for i, line in enumerate(lines) if line.startswith("== " + name + ":")) for name in names}
    assert "exit status 0; left on stderr: 0 bytes" in lines[at["SpringBoard"]], lines[at["SpringBoard"]]
    assert "exit status 0; left on stderr: 0 bytes" in lines[at["backboardd"]], lines[at["backboardd"]]
    assert "exit status 134; left on stderr: 0 bytes" in lines[at["Freeform"]], lines[at["Freeform"]]
    assert "exit status 139; left on stderr: 0 bytes" in lines[at["kbd"]], lines[at["kbd"]]
    assert "exit status 134; left on stderr: 0 bytes" in lines[at["PosterBoard"]], lines[at["PosterBoard"]]
    assert "left on stderr: 251 bytes" in lines[at["Other"]], lines[at["Other"]]
    passed = lambda name: [line.strip() for line in lines[at[name] + 1:at[names[names.index(name) + 1]]]]
    assert passed("SpringBoard") == [
        "Pong Probe: log v246 SpringBoard: WallpaperKit: Looking for default wallpaper based on deviceProductType 'iPhone17,1'",
        "Pong Probe: log v246 SpringBoard: PBFPoster something about a poster",
        "Pong Probe: log v246 SpringBoard: a battery percent line",
        "Pong Probe: log v246 SpringBoard: WKDefaultWallpaperManager: no lookup file"], passed("SpringBoard")
    assert passed("backboardd") == [
        "Pong Probe: log v246 backboardd: noise: before the window server",
        "Pong Probe: log v246 backboardd: Pong Metal: handoff Turnip V162 active",
        "Pong Probe: log v246 backboardd: [Display] StartWindowServer: initial main screen size:{1206, 2622} scale:3",
        "Pong Probe: log v246 backboardd: a battery percent line"], passed("backboardd")
    crash = passed("Freeform")
    assert len(crash) == 14 and crash[0] == "Pong Probe: log v246 Freeform: Metal device created", crash
    assert crash[1].startswith("Pong Probe: log v246 Freeform: *** Terminating app due to uncaught exception") and \\
        crash[2].startswith("Pong Probe: log v246 Freeform: crash: fatal signal 6 code ") and \\
        crash[3].startswith("Pong Probe: log v246 Freeform: crash: pc 0x") and "fp 0x" in crash[3] and \\
        crash[4].startswith("Pong Probe: log v246 Freeform: crash: frames 0x"), crash
    assert crash[5].startswith("Pong Probe: log v246 Freeform: before the crash: noise: a line about nothing at all") and \\
        crash[-1].endswith("before the crash: *** Terminating app due to uncaught exception 'NSInvalidArgumentException', reason: 'test'"), crash
    fault = passed("kbd")
    assert len(fault) == 6 and fault[0] == "Pong Probe: log v246 kbd: LayoutStar keyboardName iPhone-PortraitChoco-Pinyin-Simplified resulted in (null)", fault
    assert fault[1] == "Pong Probe: log v246 kbd: crash: fatal signal 11 code 2 at 0x123456789" and \\
        fault[3].startswith("Pong Probe: log v246 kbd: crash: frames 0x") and \\
        fault[4] == "Pong Probe: log v246 kbd: before the crash: boring keyboard line", fault
    poster = passed("PosterBoard")
    assert len(poster) == 8 and poster[0] == "Pong Probe: log v246 PosterBoard: noise: a poster line" and \\
        poster[1].startswith("Pong Probe: log v246 PosterBoard: *** Terminating app due to uncaught exception") and \\
        poster[2].startswith("Pong Probe: log v246 PosterBoard: crash: fatal signal 6 code ") and \\
        poster[3].startswith("Pong Probe: log v246 PosterBoard: crash: pc 0x") and \\
        poster[4].startswith("Pong Probe: log v246 PosterBoard: crash: frames 0x") and \\
        poster[5] == "Pong Probe: log v246 PosterBoard: before the crash: noise: a poster line" and \\
        poster[6] == "Pong Probe: log v246 PosterBoard: before the crash: [DataStore] Gonna init the datastore", poster
    # The display library's host test: under the name backboardd the identity's size is answered with the
    # panel's when the native bounds are the panel's and when the display has no size of its own (v246), and
    # kept for another display; under another name the method is QuartzCore's own.
    host = subprocess.run(["/bin/bash", str(root / "prepare/display/hosttest/run.sh")], check=True, capture_output=True, text=True)
    lines = host.stdout.splitlines()
    at = {name: next(i for i, line in enumerate(lines) if line.startswith("== " + name + ":")) for name in ("backboardd", "Other")}
    assert "exit status 0; stderr 0 bytes" in lines[at["backboardd"]] and "exit status 0; stderr 0 bytes" in lines[at["Other"]], lines
    shown = [line.strip() for line in lines[at["backboardd"] + 1:at["Other"]]]
    assert shown[:4] == ["stand-in on the panel, call 1: 1440x3200", "stand-in on the panel, call 2: 1440x3200",
                         "the same size on other native bounds: 1206x2622",
                         "the identity's size while the display has no size of its own: 1440x3200"], shown
    assert shown[4] == "Pong Probe: display v246: -[CADisplay bounds] of CADisplay watched in backboardd; the panel's size is answered for {1206, 2622}", shown
    assert sum("the panel's size is answered" in line for line in shown) == 4 and \\
        sum("the native bounds are those of another display: kept" in line for line in shown) == 1 and \\
        sum("while the display has no size of its own" in line for line in shown) == 2, shown
    assert lines[at["Other"] + 1].strip().startswith("Other: -bounds is still QuartzCore's own 0x"), lines
''' + s[b:]

rep('    manifest.update(version=245, release_variant=release, purpose=title, candidate_parent=id_,',
    '    manifest.update(version=246, release_variant=release, purpose=title, candidate_parent=id_,')
a = s.index('                    kernel_change="THOR V245: the display library in backboardd answers the panel\'s size (1440x3200) where the "')
b = s.index('                    adt_sha256=sha(dt))')
s = s[:a] + '''                    kernel_change="THOR V246: the display library in backboardd answers the panel's size (1440x3200) also for a display "
                                  "without a size of its own (v245 kept {1206, 2622}); the log capture library v246 reports the death of "
                                  "PosterBoard with the adapter and arms its handlers again after the other libraries; the Wi-Fi station "
                                  "joins once more as a plain 802.11g station after a refusal; carried: everything of v245 and v244",
''' + s[b:]
a = s.index('    manifest["diagnostic_delta"] = (')
b = s.index('    manifest["wifi_join"] = dict(')
s = s[:a] + '''    manifest["diagnostic_delta"] = ("new: libpong-display-v246.dylib in backboardd (Pong Probe: display v246: ...; answers {1440, 3200} also "
                                    "for a display with no size of its own) and libpong-logcap-v246.dylib in SpringBoard, backboardd, PosterBoard, "
                                    "CollectionsPoster, PowerUIAgent, Freeform and kbd (Pong Probe: log v246 ...; PosterBoard's death reported); the "
                                    "Wi-Fi station's plain retry (wifi: joining once more as a plain 802.11g station ...).  Gone: "
                                    "libpong-logcap-v245.dylib, libpong-display-v245.dylib.  Unchanged: the signed UI adapter in PosterBoard, "
                                    "libpong-wifid-v244.dylib in wifid, libpong-probe-v244.dylib in PosterBoard, configd and wifid; Settings "
                                    "diagnostic as v238; console capture as v232")
    manifest["witness"] = dict(manifest["witness"],
                               kernel_source="osfmk/kern/thor_witness_v235.h (v246 line), bsd/kern/kern_exec.c (staging and injection of "
                                             "the libraries), device/scripts/build-pong-ath11k.py (the station source: the plain retry)",
                               patch="prepare/display/display246.c; prepare/logcap/logcap246.c; source changes in v246-intake/patches/apply.py",
                               version_line="THOR WITNESS V246: ...",
                               probe="/private/var/pong/libpong-probe-v244.dylib in PosterBoard, configd and wifid",
                               log_capture="/private/var/pong/libpong-logcap-v246.dylib in SpringBoard, backboardd, PosterBoard, "
                                           "CollectionsPoster, PowerUIAgent, Freeform and kbd",
                               display="/private/var/pong/libpong-display-v246.dylib in backboardd (-[CADisplay bounds]: the panel's size "
                                       "for {1206, 2622} when the native bounds are the panel's or empty; boot argument pong_ca_native=0 turns it off)",
                               wallpaper="the signed UI adapter in PosterBoard (its storage adaptation for the data store)",
                               wifid_hold="/private/var/pong/libpong-wifid-v244.dylib in wifid (only where the device tree names wlan)")
''' + s[b:]

rep('''    # The v235 witness is carried unchanged (same messages, as often); the v244 log capture lines and the
    # station's old refusal message are gone; the v245 lines are new; everything else v241 to v244 added is
    # still there once.
''', '''    # The v235 witness is carried unchanged (same messages, as often); the v245 library lines are gone; the
    # v246 lines are new; everything else v241 to v245 added is still there once.
''')
rep('    for needle in carried_241_strings + carried_242_strings + carried_243_strings + carried_244_strings:',
    '    for needle in carried_241_strings + carried_242_strings + carried_243_strings + carried_244_strings + carried_245_strings:')
rep('''    # The device tree carries the wlan node.
    assert ("/wlan" in api["parse_adt"](data[adt_member])) == wlan
    # The station address function is in the kernel and its import is the archive's.
    assert payload.count(b"_pong_wifi_station_mac") == 1 and old_payload.count(b"_pong_wifi_station_mac") == 0
    # The libraries of v244 that are gone were embedded exactly once in its kernel and are not in this one.
''', '''    # The device tree carries the wlan node.
    assert ("/wlan" in api["parse_adt"](data[adt_member])) == wlan
    # The libraries of v245 that are gone were embedded exactly once in its kernel and are not in this one.
''')
rep('    return dict(version=245, variant=release.removeprefix("v245-"), embedded_release_id=release, image=target.name,',
    '    return dict(version=246, variant=release.removeprefix("v246-"), embedded_release_id=release, image=target.name,')
rep('''                display=manifest["witness"]["display"],''', '''                display=manifest["witness"]["display"],
                wifi_retry="once more as a plain 802.11g station after a refusal with high-throughput capabilities",''')
(root / 'package-v246.py').write_text(s)
import py_compile
py_compile.compile(str(root / 'package-v246.py'), doraise=True)
print('package-v246.py written', len(s))
