#!/usr/bin/env python3.13
"""Write package-v245.py from package-v244.py: the same packaging and verification, with the parent, the
libraries, the messages and the tests of v245.  Every replacement must match once."""
import os
from pathlib import Path

root = Path(os.environ['THOR_ROOT'] + '/thor-out/v245-unified')
s = (root / 'package-v245.py').read_text()

def rep(old, new):
    global s
    assert s.count(old) == 1, (s.count(old), old[:80])
    s = s.replace(old, new)

# ---------------------------------------------------------------- the description
a = s.index('"""Package the v244 boot image')
b = s.index('"""\nimport argparse') + 3
s = s[:a] + '''"""Package the v245 boot image from the v244 parent: the render server is put back on the panel's own size,
the signed UI adapter goes into PosterBoard for its wallpaper data store, and the Wi-Fi station gets an
address of its own and a complete association request.

Why (read from the console and the snapshots of 2026-11-27, v244):
- Jagged edges and frame drops.  Since v242 the render server draws every frame at 1206x2622 -- the pixel
  size of the iPhone 16 Pro, the identity the device tree carries -- and the framebuffer driver enlarges
  it to the 1440x3200 panel with the nearest neighbour (v237 to v239: native frames, a 1:1 copy).
  backboardd takes -[CADisplay bounds] as its "initial main screen size" when it starts its window server;
  in v244 that is {1206, 2622}.  libpong-display-v245 (backboardd only) reports what the display says of
  itself and answers {1440, 3200} for that one size on that panel.  Boot argument pong_ca_native=0 leaves
  it at watching.  This is an experiment on the phone: nothing of it has run there.
- Wallpaper.  PosterBoard cannot create its data store ("Failed to migrate to Version 61 from 59",
  "could not initialize data store"), so SpringBoard finds no wallpaper.  The signed UI adapter carries
  the storage adaptation for exactly this and has a branch of its own for PosterBoard, but it was only
  ever put into the processes named by the boot argument (which is full).  The kernel now puts it into
  PosterBoard as well.
- Freeform and kbd.  kbd dies without a word; Freeform ends on a Metal assertion ("computeFunction must
  not be nil") as soon as its canvas is drawn on.  libpong-logcap-v245 reports the signal, the registers
  and the stack of a fatal signal; the graphics bridge's own lines of these two processes come to the
  console too.  Freeform's log also shows UIKit looking for a phone keyboard layout the iPad restore
  does not have ("iPhone-PortraitChoco-Pinyin-Simplified resulted in (null)").
- Wi-Fi.  The radio firmware's default address (00:03:7f:12:34:2e) was used by every boot of this port,
  and an open network answered status 48 to an association request without Power Capability, Supported
  Channels, Extended Capabilities or WMM.  The station derives one address from the device tree's
  unique-chip-id, sends those elements, and logs both frames when it is refused.

The image carries the wlan node (launchd starts wifid).  Carried unchanged: everything of v244 (the
firmware LUN check, the probe and the Wi-Fi daemon's library, the Settings diagnostic, the panic record
slot), the v235 witness's messages.  No boot argument changed.

The compact image is the verified artifact and the one that gets written to boot_a (through TWRP).
"""''' + s[b:]

rep('base = root.parent / "v243-unified"', 'base = root.parent / "v244-unified"')
rep('''v243 = runpy.run_path(str(base / "package-v243.py"))                            # helpers and the lists of the parent
v242 = v243["v242"]
adt_member = kernel_member + ".adt"
BOOT_STACK = v243["BOOT_STACK"]
carried_kernel_strings = v243["carried_kernel_strings"]
witness_strings = v243["witness_strings"]
prologue_frame, symbol_address, bytes_at = v243["prologue_frame"], v243["symbol_address"], v243["bytes_at"]
''', '''v244 = runpy.run_path(str(base / "package-v244.py"))                            # helpers and the lists of the parent
adt_member = kernel_member + ".adt"
BOOT_STACK = v244["BOOT_STACK"]
carried_kernel_strings = v244["carried_kernel_strings"]
witness_strings = v244["witness_strings"]
prologue_frame, symbol_address, bytes_at = v244["prologue_frame"], v244["symbol_address"], v244["bytes_at"]
''')
rep('''VARIANTS = {
    # name: the device tree decides whether launchd starts wifid at all
    "unified": dict(release="v244-unified", wlan=True, record="unified-verification.json",
                    title="Thor v244 - Wi-Fi daemon, log capture"),
    "nowifi": dict(release="v244-nowifi", wlan=False, record="nowifi-verification.json",
                   title="Thor v244 - log capture, no Wi-Fi daemon"),
}
''', '''VARIANTS = {
    # name: the device tree names wlan, so launchd starts wifid
    "unified": dict(release="v245-unified", wlan=True, record="unified-verification.json",
                    title="Thor v245 - panel size, wallpaper store, Wi-Fi station"),
}
''')
rep('''probe_name = "libpong-probe-v244.dylib"
probe_new = root / "runtime" / probe_name
wifid_name = "libpong-wifid-v244.dylib"
logcap_name = "libpong-logcap-v244.dylib"
# name: build script, the message that must appear exactly once in it, messages that must appear at least once
new_libraries = {
    logcap_name: ("prepare/logcap/build-logcap.sh", b"Pong Probe: log v244: ", b"Pong Probe: log v244 "),
    probe_name: ("prepare/probe/build-probe.sh", b"Pong Probe: t=", b"Plugin:InterfaceNamer",
                 b"State:/Network/Interface", b"wifi gate open"),
    wifid_name: ("prepare/wifid/build-wifid.sh", b"Pong Probe: wifid hold v244: ", b"Pong Probe: "),
}
# The wallpaper catalog library of v243 is gone; the probe and the wifid hold library are new builds.
gone_runtime = {"libpong-wallpaper-v243.dylib", "libpong-probe-v241.dylib", "libpong-wifid-v241.dylib"}
''', '''probe_name = "libpong-probe-v244.dylib"         # unchanged from v244
probe_old, probe_new = base / "runtime" / probe_name, root / "runtime" / probe_name
wifid_name = "libpong-wifid-v244.dylib"         # unchanged from v244
wifid_old, wifid_new = base / "runtime" / wifid_name, root / "runtime" / wifid_name
logcap_name = "libpong-logcap-v245.dylib"
display_name = "libpong-display-v245.dylib"
# name: build script, the message that must appear exactly once in it, messages that must appear at least once
new_libraries = {
    logcap_name: ("prepare/logcap/build-logcap245.sh", b"Pong Probe: log v245: ", b"Pong Probe: log v245 "),
    display_name: ("prepare/display/build-display245.sh", b"watched in %s", b"Pong Probe: display v245: ",
                   b"pong_ca_native=0"),
}
# The log capture library of v244 is gone; the log capture and the display library are new builds.
gone_runtime = {"libpong-logcap-v244.dylib"}
''')
rep('''# Messages only this kernel prints: each present once, absent from v243's.
new_kernel_strings = (
    b"THOR WITNESS V244: the wallpaper catalog library is not injected",
    b"THOR LOGCAP V244: staged ",
    b"THOR LOGCAP V244: %s log capture armed",
    b"/private/var/pong/libpong-logcap-v244.dylib",
    b"THOR PROBE V244: staged ",
    b"/private/var/pong/libpong-probe-v244.dylib",
    b"THOR WIFI V244: staged ",
)
# Messages and paths of v243 that are gone: its wallpaper library, and the libraries that were rebuilt
# under a new name.
old_kernel_strings = (b"THOR WALLPAPER V243", b"/private/var/pong/libpong-wallpaper-v243.dylib",
                      b"THOR PROBE V241: staged ", b"/private/var/pong/libpong-probe-v241.dylib",
                      b"THOR PROBE V241: %s probe armed", b"THOR WIFI V241: staged ",
                      b"/private/var/pong/libpong-wifid-v241.dylib")
# The messages v243, v242 and v241 added are all still there, once each (minus the ones named above).
replaced = (b"THOR PROBE V241", b"/private/var/pong/libpong-probe-v241", b"/private/var/pong/libpong-wifid-v241",
           b"THOR WIFI V241: staged ")
carried_243_strings = tuple(x for x in v243["new_kernel_strings"] if not any(x.startswith(g) for g in old_kernel_strings))
carried_242_strings = tuple(x for x in v243["carried_242_strings"] if not any(x.startswith(g) for g in replaced))
carried_241_strings = tuple(x for x in v243["carried_241_strings"] if not any(x.startswith(g) for g in replaced))
''', '''# Messages only this kernel prints: each present once, absent from v244's.
new_kernel_strings = (
    b"THOR WITNESS V245: the log capture library (v245) reports the signal",
    b"THOR LOGCAP V245: staged ",
    b"THOR LOGCAP V245: %s log capture armed",
    b"/private/var/pong/libpong-logcap-v245.dylib",
    b"THOR DISPLAY V245: staged ",
    b"THOR DISPLAY V245: %s display library armed",
    b"/private/var/pong/libpong-display-v245.dylib",
    b"THOR UI runtime V245: PosterBoard adapter armed",
    b"PongWifi: no unique-chip-id in the device tree",
    b"wifi: association refused: %u (the request was",
)
# Messages and paths of v244 that are gone: its log capture library (rebuilt under a new name) and
# the station's old refusal message.
old_kernel_strings = (b"THOR WITNESS V244: the wallpaper catalog library is not injected",
                      b"THOR LOGCAP V244: staged ", b"THOR LOGCAP V244: %s log capture armed",
                      b"/private/var/pong/libpong-logcap-v244.dylib",
                      b"wifi: association refused: %u\\0")
# The messages v244, v243, v242 and v241 added are all still there, once each (minus the ones named above).
carried_244_strings = tuple(x for x in v244["new_kernel_strings"] if not any(x.startswith(g) for g in old_kernel_strings))
carried_243_strings = v244["carried_243_strings"]
carried_242_strings = v244["carried_242_strings"]
carried_241_strings = v244["carried_241_strings"]
''')
rep('''    "/System/Library/PrivateFrameworks/CoreDuetContext.framework/Resources/contextstored",
)
''', '''    "/System/Library/PrivateFrameworks/CoreDuetContext.framework/Resources/contextstored",
    "/usr/libexec/backboardd",
)
''')
a = s.index('changes = [')
b = s.index(']\n\n\ndef apple_install_names') + 1
s = s[:a] + '''changes = [
    "A display library (libpong-display-v245) goes into backboardd only: it reports what the display says of itself and "
    "answers the panel's size (1440x3200) where the render server reads the iPhone 16 Pro's (1206x2622: every frame has "
    "been enlarged with the nearest neighbour since v242, the jagged edge on text and icons, at 1.5 to 2 ms a frame more). "
    "Boot argument pong_ca_native=0 leaves it at watching.  An experiment on the phone: nothing of it has run there.",
    "The signed UI adapter (the same library as in SpringBoard and the other processes of the boot argument) goes into "
    "PosterBoard too: its storage adaptation is the only code that can give PosterBoard a data store on the unencrypted "
    "Data volume, and without one there is no wallpaper.",
    "The log capture library is v245: in Freeform and kbd a fatal signal is reported with its number, code, address, "
    "registers and stack; every line of the render server is passed on for its first ten seconds; the graphics bridge's "
    "own lines of Freeform and kbd come to the console (budget of their own); the probe channel's budget is 12000 lines.",
    "The Wi-Fi station has an address of its own, derived from the device tree's unique-chip-id (every boot to v244 came "
    "to access points under the radio firmware's default 00:03:7f:12:34:2e); its association request carries Power "
    "Capability, Supported Channels, Extended Capabilities and WMM, and sets short slot time and short preamble only where "
    "the access point does; when it is refused both frames and the access point's elements are logged.",
    "Every library the injected libraries load is compared with the names Apple's own binaries use.",
    "Carried unchanged from v244: the Wi-Fi daemon's library and the probe, the Settings diagnostic, the console capture, "
    "the firmware LUN check with the primary table deciding (v243), the retries and the record of a failed check (v242), "
    "the interface-namer marker and the en0 listing in configd (Settings -> About), the panic record slot (v238), "
    "protection classes A and B open for every process and IOSurface selector 21 (v237), the v236 GPU timing hook, the "
    "v235 witness.  No boot argument changed.",
]''' + s[b:]

# ---------------------------------------------------------------- configuration
rep('    assert id_ == parent["image_sha256"] and parent["version"] == 243\n'
    '    # The command line is byte-identical to v239 to v243; only the menu title changes.',
    '    assert id_ == parent["image_sha256"] and parent["version"] == 244\n'
    '    # The command line is byte-identical to v239 to v244; only the menu title changes.')
rep('''    # The device tree: v243 unified carries v238's (no wlan node) and is kept for the nowifi image; the
    # default image takes v239's (with the wlan node).  Nothing else differs.
    tree = api["parse_adt"](old[adt_member])
    assert bytes(api["emit"](tree, "/")) == old[adt_member] and "/wlan" not in tree
    if wlan:
        v239 = read(root.parent / "v239-unified/unified-verification.json")
        old239, _, _, id239 = decode(root.parent / "v239-unified" / v239["image"])
        assert id239 == v239["image_sha256"] and v239["version"] == 239
        tree239 = api["parse_adt"](old239[adt_member])
        assert list(tree239["/wlan"]) == ["name", "compatible"] and tree239["/wlan"]["name"] == b"wlan\\0"
        del tree239["/wlan"]
        assert bytes(api["emit"](tree239, "/")) == old[adt_member], "v239's tree differs from v243's only by the wlan node"
        dt = old239[adt_member]
    else:
        dt = old[adt_member]
''', '''    # The device tree is v244's, unchanged: it carries the wlan node.
    tree = api["parse_adt"](old[adt_member])
    assert bytes(api["emit"](tree, "/")) == old[adt_member] and "/wlan" in tree and wlan
    assert list(tree["/wlan"]) == ["name", "compatible"] and tree["/wlan"]["name"] == b"wlan\\0"
    dt = old[adt_member]
''')
rep('''    # Three runtime inputs are new (log capture, probe v244, wifid hold v244) and three of v243's are gone;
    # the console capture and the Settings diagnostic are unchanged.
''', '''    # Two runtime inputs are new (the log capture and the display library) and v244's log capture is gone;
    # the probe, the Wi-Fi daemon's library, the console capture and the Settings diagnostic are unchanged.
''')
rep('''    assert (root / "prepare/settings/about237.patch").is_file() and (root / "prepare/settings/about238.patch").is_file()
    for needle in (b"SCPreferences", b"WiFiManagerClient", b"pong-wifi-power", b"/dev/console"):
        assert needle not in (root / "runtime" / probe_name).read_bytes(), needle
''', '''    assert (root / "prepare/settings/about237.patch").is_file() and (root / "prepare/settings/about238.patch").is_file()
    for needle in (b"SCPreferences", b"WiFiManagerClient", b"pong-wifi-power", b"/dev/console"):
        assert needle not in probe_new.read_bytes(), needle
    # The probe and the Wi-Fi daemon's library are v244's, exactly.
    for name, old_path, new_path in ((probe_name, probe_old, probe_new), (wifid_name, wifid_old, wifid_new)):
        spec_ = current["payloads"][name]
        blob = new_path.read_bytes()
        assert spec_ == prior["payloads"][name] and blob == old_path.read_bytes(), name
        assert sha(blob) == spec_["sha256"] and len(blob) == spec_["bytes"] <= 128 * 1024
        subprocess.run(["codesign", "--verify", "--strict", str(new_path)], check=True)
        assert subprocess.check_output(["xcrun", "dwarfdump", "--uuid", str(new_path)], text=True).split()[1] == spec_["uuid"]
''')
a = s.index('    # The log capture library\'s host test, compiled from the same source')
b = s.index('    subprocess.run([sys.executable, "-IB", str(root / "prepare/settings/replay.py")]')
s = s[:a] + '''    # The log capture library's host test, compiled from the same source: under the name SpringBoard only
    # the lines with a keyword are passed on and nothing is left on stderr; under the name backboardd every
    # line of the first seconds and then keyword lines; under the name Freeform the indicator lines, the
    # abort with its signal, registers and frames, and the last lines are passed on (SIGABRT); under the
    # name kbd a real fault three calls deep is reported with its address (SIGSEGV); under another name
    # nothing is captured.
    host = subprocess.run(["/bin/bash", str(root / "prepare/logcap/hosttest/run245.sh")], check=True, capture_output=True, text=True)
    lines = host.stdout.splitlines()
    names = ("SpringBoard", "backboardd", "Freeform", "kbd", "Other")
    at = {name: next(i for i, line in enumerate(lines) if line.startswith("== " + name + ":")) for name in names}
    assert "exit status 0; left on stderr: 0 bytes" in lines[at["SpringBoard"]], lines[at["SpringBoard"]]
    assert "exit status 0; left on stderr: 0 bytes" in lines[at["backboardd"]], lines[at["backboardd"]]
    assert "exit status 134; left on stderr: 0 bytes" in lines[at["Freeform"]], lines[at["Freeform"]]
    assert "exit status 139; left on stderr: 0 bytes" in lines[at["kbd"]], lines[at["kbd"]]
    assert "left on stderr: 251 bytes" in lines[at["Other"]], lines[at["Other"]]
    passed = lambda name: [line.strip() for line in lines[at[name] + 1:at[names[names.index(name) + 1]]]]
    assert passed("SpringBoard") == [
        "Pong Probe: log v245 SpringBoard: WallpaperKit: Looking for default wallpaper based on deviceProductType 'iPhone17,1'",
        "Pong Probe: log v245 SpringBoard: PBFPoster something about a poster",
        "Pong Probe: log v245 SpringBoard: a battery percent line",
        "Pong Probe: log v245 SpringBoard: WKDefaultWallpaperManager: no lookup file"], passed("SpringBoard")
    assert passed("backboardd") == [
        "Pong Probe: log v245 backboardd: noise: before the window server",
        "Pong Probe: log v245 backboardd: Pong Metal: handoff Turnip V162 active",
        "Pong Probe: log v245 backboardd: [Display] StartWindowServer: initial main screen size:{1206, 2622} scale:3",
        "Pong Probe: log v245 backboardd: a battery percent line"], passed("backboardd")
    crash = passed("Freeform")
    assert len(crash) == 14 and crash[0] == "Pong Probe: log v245 Freeform: Metal device created", crash
    assert crash[1].startswith("Pong Probe: log v245 Freeform: *** Terminating app due to uncaught exception") and \\
        crash[2].startswith("Pong Probe: log v245 Freeform: crash: fatal signal 6 code ") and \\
        crash[3].startswith("Pong Probe: log v245 Freeform: crash: pc 0x") and "fp 0x" in crash[3] and \\
        crash[4].startswith("Pong Probe: log v245 Freeform: crash: frames 0x"), crash
    assert crash[5].startswith("Pong Probe: log v245 Freeform: before the crash: noise: a line about nothing at all") and \\
        crash[-1].endswith("before the crash: *** Terminating app due to uncaught exception 'NSInvalidArgumentException', reason: 'test'"), crash
    fault = passed("kbd")
    assert len(fault) == 6 and fault[0] == "Pong Probe: log v245 kbd: LayoutStar keyboardName iPhone-PortraitChoco-Pinyin-Simplified resulted in (null)", fault
    assert fault[1] == "Pong Probe: log v245 kbd: crash: fatal signal 11 code 2 at 0x123456789" and \\
        fault[3].startswith("Pong Probe: log v245 kbd: crash: frames 0x") and \\
        fault[4] == "Pong Probe: log v245 kbd: before the crash: boring keyboard line", fault
    # The display library's host test: under the name backboardd the identity's size is answered with the
    # panel's when the native bounds are the panel's, and kept otherwise; under another name the method is
    # QuartzCore's own.
    host = subprocess.run(["/bin/bash", str(root / "prepare/display/hosttest/run.sh")], check=True, capture_output=True, text=True)
    lines = host.stdout.splitlines()
    at = {name: next(i for i, line in enumerate(lines) if line.startswith("== " + name + ":")) for name in ("backboardd", "Other")}
    assert "exit status 0; stderr 0 bytes" in lines[at["backboardd"]] and "exit status 0; stderr 0 bytes" in lines[at["Other"]], lines
    shown = [line.strip() for line in lines[at["backboardd"] + 1:at["Other"]]]
    assert shown[:3] == ["stand-in on the panel, call 1: 1440x3200", "stand-in on the panel, call 2: 1440x3200",
                         "the same size on other native bounds: 1206x2622"], shown
    assert shown[3] == "Pong Probe: display v245: -[CADisplay bounds] of CADisplay watched in backboardd; the panel's size is answered for {1206, 2622}", shown
    assert sum("the identity's size on this panel" in line for line in shown) == 2 and \\
        sum("the native bounds are not the panel's: kept" in line for line in shown) == 1, shown
    assert lines[at["Other"] + 1].strip().startswith("Other: -bounds is still QuartzCore's own 0x"), lines
''' + s[b:]

rep('    manifest.update(version=244, release_variant=release, purpose=title, candidate_parent=id_,',
    '    manifest.update(version=245, release_variant=release, purpose=title, candidate_parent=id_,')
a = s.index('                    kernel_change="THOR V244: the wallpaper catalog library')
b = s.index('                    adt_sha256=sha(dt))')
s = s[:a] + '''                    kernel_change="THOR V245: the display library in backboardd answers the panel's size (1440x3200) where the "
                                  "render server reads the iPhone 16 Pro's (1206x2622); the signed UI adapter goes into PosterBoard for "
                                  "its wallpaper data store; the log capture library v245 reports signal, registers and stack of a fatal "
                                  "signal in Freeform and kbd and every line of the render server at its start; the Wi-Fi station has an "
                                  "address of its own (unique-chip-id), a complete association request and logs both frames of a refusal; "
                                  "carried: everything of v244",
''' + s[b:]
rep('''                                     device_tree_changed=("unchanged from v239 (root node wlan: launchd runs com.apple.wifid only when "
                                                          "the device tree names wlan or marconi-wifi)" if wlan else
                                                          "v238's again: no wlan node, so launchd never runs com.apple.wifid"),''',
    '''                                     device_tree_changed=("unchanged from v239 through v244 (root node wlan: launchd runs com.apple.wifid only when "
                                                          "the device tree names wlan or marconi-wifi)" if wlan else
                                                          "no wlan node, so launchd never runs com.apple.wifid"),''')
a = s.index('    manifest["diagnostic_delta"] = (')
b = s.index('    manifest["wifi_join"] = dict(')
s = s[:a] + '''    manifest["diagnostic_delta"] = ("new: libpong-display-v245.dylib in backboardd (lines PONG PROBE backboardd[pid]: Pong Probe: "
                                    "display v245: ...) and libpong-logcap-v245.dylib in SpringBoard, backboardd, PosterBoard, "
                                    "CollectionsPoster, PowerUIAgent, Freeform and kbd (Pong Probe: log v245 ...; a fatal signal in Freeform "
                                    "and kbd with number, code, address, registers and frames); the signed UI adapter "
                                    "(/private/var/pong/libpong-ui-v158-frame-cellular.dylib) in PosterBoard; the Wi-Fi station's "
                                    "address (PongWifi: ... hardware address) and its refusal lines (wifi: association refused: ... and the "
                                    "hexadecimal frames).  Gone: libpong-logcap-v244.dylib.  Unchanged: libpong-wifid-v244.dylib in wifid, "
                                    "libpong-probe-v244.dylib in PosterBoard, configd and wifid; Settings diagnostic as v238; console capture as v232")
    manifest["witness"] = dict(manifest["witness"],
                               kernel_source="osfmk/kern/thor_witness_v235.h (v245 line), bsd/kern/kern_exec.c (staging and injection of "
                                             "the libraries: the display library in backboardd, the UI adapter in PosterBoard), bsd/kern/kern_exit.c "
                                             "(diagnostic channel: probe budget 12000, the bridge's lines of Freeform and kbd), "
                                             "device/scripts/build-pong-ath11k.py (the station source), device/drivers/PongWifi (the station address)",
                               patch="prepare/display/display245.c; prepare/logcap/logcap245.c; prepare/logcap/trace_flags.h (as v244); "
                                     "prepare/wifid/wifid244.c and prepare/probe/probe244.c (carried); source changes in v245-intake/patches/apply.py",
                               version_line="THOR WITNESS V245: ...",
                               probe="/private/var/pong/libpong-probe-v244.dylib in PosterBoard, configd and wifid",
                               log_capture="/private/var/pong/libpong-logcap-v245.dylib in SpringBoard, backboardd, PosterBoard, "
                                           "CollectionsPoster, PowerUIAgent, Freeform and kbd",
                               display="/private/var/pong/libpong-display-v245.dylib in backboardd (-[CADisplay bounds]: the panel's size "
                                       "for {1206, 2622}; boot argument pong_ca_native=0 turns it off)",
                               wallpaper="the signed UI adapter in PosterBoard (its storage adaptation for the data store)",
                               wifid_hold="/private/var/pong/libpong-wifid-v244.dylib in wifid (only where the device tree names wlan)")
''' + s[b:]

# ---------------------------------------------------------------- verify
rep('''    # The v235 witness is carried unchanged (same messages, as often); the v243 wallpaper lines and the
    # libraries that were rebuilt under a new name are gone; the v244 lines are new; everything else v241,
    # v242 and v243 added is still there once.
''', '''    # The v235 witness is carried unchanged (same messages, as often); the v244 log capture lines and the
    # station's old refusal message are gone; the v245 lines are new; everything else v241 to v244 added is
    # still there once.
''')
rep('    for needle in carried_241_strings + carried_242_strings + carried_243_strings:',
    '    for needle in carried_241_strings + carried_242_strings + carried_243_strings + carried_244_strings:')
rep('''    # The device tree: with the wlan node (unified) or v238's (nowifi).
    assert ("/wlan" in api["parse_adt"](data[adt_member])) == wlan
    # The libraries of v243 that are gone were embedded exactly once in its kernel and are not in this one.
''', '''    # The device tree carries the wlan node.
    assert ("/wlan" in api["parse_adt"](data[adt_member])) == wlan
    # The station address function is in the kernel and its import is the archive's.
    assert payload.count(b"_pong_wifi_station_mac") == 1 and old_payload.count(b"_pong_wifi_station_mac") == 0
    # The libraries of v244 that are gone were embedded exactly once in its kernel and are not in this one.
''')
rep('    return dict(version=244, variant=release.removeprefix("v244-"), embedded_release_id=release, image=target.name,',
    '    return dict(version=245, variant=release.removeprefix("v245-"), embedded_release_id=release, image=target.name,')
rep('''                witness=manifest["witness"], wifi_join=manifest["wifi_join"], probe=manifest["witness"]["probe"],''',
    '''                witness=manifest["witness"], wifi_join=manifest["wifi_join"], probe=manifest["witness"]["probe"],
                display=manifest["witness"]["display"],''')
(root / 'package-v245.py').write_text(s)
import py_compile
py_compile.compile(str(root / 'package-v245.py'), doraise=True)
print('package-v245.py written', len(s))
