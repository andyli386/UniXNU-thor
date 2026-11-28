#!/usr/bin/env python3.13
"""Write package-v247.py from a copy of package-v245's successor (package-v246.py): the same packaging and
verification with the parent, the libraries, the messages and the tests of v247, and one more /chosen property
(thor-camera = 1) in the device tree.  Every replacement must match once."""
import os
from pathlib import Path

root = Path(os.environ['THOR_ROOT'] + '/thor-out/v247-unified')
s = (root / 'package-v247.py').read_text()
assert 'Package the v246 boot image' in s, 'start from a copy of package-v246.py'

def rep(old, new):
    global s
    assert s.count(old) == 1, (s.count(old), old[:90])
    s = s.replace(old, new)

def block(start, end, new):
    """replace from the unique line that starts with `start` up to (not including) the unique line `end`"""
    global s
    assert s.count(start) == 1, (s.count(start), start[:70])
    a = s.index(start)
    b = s.index(end, a)
    s = s[:a] + new + s[b:]

a = s.index('"""Package the v246 boot image')
b = s.index('"""\nimport argparse') + 3
s = s[:a] + '''"""Package the v247 boot image from the v246 parent: every death by a signal is recorded, the Messages helper's
death is captured, and a read-only camera inventory runs.

Why (console of v245, 2026-11-27 19:48):
- kbd, Freeform and MessagesBlastDoorService die and no reason is ever recorded: the five corpse slots of a boot are
  gone in its first 80 seconds, and the kernel's exit line covers a fixed list of names.  The kernel now prints one
  line for every process that ends by a core-generating signal (PONG DEATH: name, pid, signal, exception, code,
  address, exit reason).
- MessagesBlastDoorService dies every ten seconds; opened, the Messages app waits for it and the desktop stops.
  The log capture library v247 keeps its last lines and reports its death.
- Camera: nothing has ever been read of the camera hardware on this port.  A read-only inventory (clocks, power
  domains, pins, I2C controller, the rails' SPMI channels) runs once, thirty seconds after the root volume mounts,
  when /chosen says thor-camera = 1.  It writes nothing.

Carried unchanged from v246: the display library that answers the panel's size also for a display without a size of
its own, the signed UI adapter in PosterBoard with crash capture, the Wi-Fi station's retries (5 GHz BSS of the
network, then plain 802.11g).  No boot argument changed; the device tree gains the one /chosen property.

The compact image is the verified artifact and the one that gets written to boot_a (through TWRP).
"""''' + s[b:]

rep('base = root.parent / "v245-unified"', 'base = root.parent / "v246-unified"')
block('v245 = runpy.run_path(str(base / "package-v245.py"))', 'read = lambda path', '''v246 = runpy.run_path(str(base / "package-v246.py"))                            # helpers and the lists of the parent
adt_member = kernel_member + ".adt"
BOOT_STACK = v246["BOOT_STACK"]
carried_kernel_strings = v246["carried_kernel_strings"]
witness_strings = v246["witness_strings"]
prologue_frame, symbol_address, bytes_at = v246["prologue_frame"], v246["symbol_address"], v246["bytes_at"]
''')
rep('''    "unified": dict(release="v246-unified", wlan=True, record="unified-verification.json",
                    title="Thor v246 - panel size without a mode, wallpaper crash, plain Wi-Fi retry"),
''', '''    "unified": dict(release="v247-unified", wlan=True, record="unified-verification.json",
                    title="Thor v247 - every death recorded, Messages helper, camera inventory"),
''')
block('logcap_name = ', '# Messages only this kernel prints', '''logcap_name = "libpong-logcap-v247.dylib"
display_name = "libpong-display-v246.dylib"          # unchanged from v246
display_old, display_new = base / "runtime" / display_name, root / "runtime" / display_name
# name: build script, the message that must appear exactly once in it, messages that must appear at least once
new_libraries = {
    logcap_name: ("prepare/logcap/build-logcap247.sh", b"Pong Probe: log v247: ", b"Pong Probe: log v247 ",
                  b"MessagesBlastDoorService"),
}
# The log capture library of v246 is gone (rebuilt under a new name); the display library is unchanged.
gone_runtime = {"libpong-logcap-v246.dylib"}

''')
block('# Messages only this kernel prints', "# Apple's own binaries of this system", '''# Messages only this kernel prints: each present once, absent from v246's.
new_kernel_strings = (
    b"THOR WITNESS V247: every process that dies by a signal prints one line",
    b"THOR LOGCAP V247: staged ",
    b"THOR LOGCAP V247: %s log capture armed",
    b"/private/var/pong/libpong-logcap-v247.dylib",
    b"PONG DEATH %s pid=%d signal=%d",
    b"THOR CAMERA V247: RUNNING mode=%u readonly=%u",
    b"THOR CAMERA V247: %s snapshot done camcc_read=%u writes=0",
)
# Messages and paths of v246 that are gone: its log capture library (rebuilt under a new name).
old_kernel_strings = (b"THOR WITNESS V246: the display library in backboardd answers the panel's size",
                      b"THOR LOGCAP V246: staged ", b"THOR LOGCAP V246: %s log capture armed",
                      b"/private/var/pong/libpong-logcap-v246.dylib")
# The messages v246, v245, v244, v243, v242 and v241 added are all still there, once each (minus the ones named above).
carried_246_strings = tuple(x for x in v246["new_kernel_strings"] if not any(x.startswith(g) for g in old_kernel_strings))
carried_245_strings = v246["carried_245_strings"]
carried_244_strings = v246["carried_244_strings"]
carried_243_strings = v246["carried_243_strings"]
carried_242_strings = v246["carried_242_strings"]
carried_241_strings = v246["carried_241_strings"]

''')
block('changes = [', ']\n\n\ndef apple_install_names', '''changes = [
    "Every process that ends by a core-generating signal prints one line: name, pid, signal, exception, code, address and "
    "exit reason (PONG DEATH ...).  The five corpse slots of a boot are gone in its first 80 seconds and the exit line of "
    "v73 covers a fixed list of names, so the deaths of kbd, MessagesBlastDoorService and Freeform were never recorded.",
    "The log capture library v247 goes into MessagesBlastDoorService too (it dies every ten seconds; opened, the Messages app "
    "waits for it and the desktop stops): its last lines and the signal, registers and frames of its death are passed on.",
    "A read-only camera inventory (THOR CAMERA V247) runs once, thirty seconds after the root volume mounts, because /chosen "
    "now says thor-camera = 1: camera clocks, power domains, pins, I2C controller and the SPMI channels of the front camera's "
    "rails.  It writes nothing.  Not run on the phone before this image.",
    "Every library the injected libraries load is compared with the names Apple's own binaries use.",
    "Carried unchanged from v246: the display library (panel's size also for a display without a size of its own), the UI "
    "adapter in PosterBoard with crash capture, the Wi-Fi station's retries (5 GHz BSS, then plain 802.11g), the Wi-Fi "
    "daemon's library and the probe, the Settings diagnostic, the console capture, the firmware LUN check (v243), the "
    "retries and the record of a failed check (v242), the panic record slot (v238), the v236 GPU timing hook, the v235 "
    "witness.  No boot argument changed; the device tree gains /chosen thor-camera = 1.",
''')
rep('''    assert id_ == parent["image_sha256"] and parent["version"] == 245
    # The command line is byte-identical to v239 to v245; only the menu title changes.''',
    '''    assert id_ == parent["image_sha256"] and parent["version"] == 246
    # The command line is byte-identical to v239 to v246; only the menu title changes.''')
block("    # The device tree is v244's, unchanged: it carries the wlan node.\n", '    current = read(root / "runtime/final-inputs.json")', '''    # The device tree is v246's with one /chosen property added: thor-camera = 1 (the read-only camera inventory).
    tree = api["parse_adt"](old[adt_member])
    assert bytes(api["emit"](tree, "/")) == old[adt_member] and "/wlan" in tree and wlan
    assert list(tree["/wlan"]) == ["name", "compatible"] and tree["/wlan"]["name"] == b"wlan\\0"
    assert "thor-camera" not in tree["/chosen"]
    tree["/chosen"]["thor-camera"] = (1).to_bytes(4, "little")
    dt = bytes(api["emit"](tree, "/"))
    assert api["parse_adt"](dt) == tree and dt != old[adt_member], "the device tree did not take the property"
''')
rep('''    # Two runtime inputs are new builds (the log capture and the display library) and v245's are gone;
    # the probe, the Wi-Fi daemon's library, the console capture and the Settings diagnostic are unchanged.''',
    '''    # One runtime input is a new build (the log capture library) and v246's is gone; the display library,
    # the probe, the Wi-Fi daemon's library, the console capture and the Settings diagnostic are unchanged.''')
rep('''    # The probe and the Wi-Fi daemon's library are v244's, exactly.
    for name, old_path, new_path in ((probe_name, probe_old, probe_new), (wifid_name, wifid_old, wifid_new)):''',
    '''    # The display library is v246's and the probe and the Wi-Fi daemon's library are v244's, exactly.
    for name, old_path, new_path in ((probe_name, probe_old, probe_new), (wifid_name, wifid_old, wifid_new),
                                     (display_name, display_old, display_new)):''')
rep('    for name in list(new_libraries) + [settings_name, console_name]:',
    '    for name in list(new_libraries) + [display_name, settings_name, console_name]:')
rep('str(root / "prepare/logcap/hosttest/run246.sh")', 'str(root / "prepare/logcap/hosttest/run247.sh")')
rep('    names = ("SpringBoard", "backboardd", "Freeform", "kbd", "PosterBoard", "Other")',
    '    names = ("SpringBoard", "backboardd", "Freeform", "kbd", "PosterBoard", "MessagesBlastDoorService", "Other")')
a = s.index("    # The log capture library's host test, compiled from the same source")
b = s.index("    # The display library's host test:", a)
part = s[a:b]
assert part.count('log v246') == 25, part.count('log v246')
part = part.replace('log v246', 'log v247')
assert 'log v246' not in part and 'display v246' not in part
old = '''    # under another name nothing is captured.
    host = subprocess.run('''
assert part.count(old) == 1
part = part.replace(old, '''    # under the name MessagesBlastDoorService (v247) another line and an uncaught exception, then the signal and
    # the last lines (SIGABRT); under another name nothing is captured.
    host = subprocess.run(''')
s = s[:a] + part + s[b:]
# The three crash expectations do not depend on timing: whether the "Terminating app" line is passed on at once
# (the reader thread got to it before the handler ran) or only by the handler under "before the crash" is a race
# in the library, and the v246 assertion happened to see only one of the two orders.
def chunk(start, end, new):
    global s
    assert s.count(start) == 1, (s.count(start), start)
    x = s.index(start)
    y = s.index(end, x)
    s = s[:x] + new + s[y:]
chunk('    crash = passed("Freeform")\n', '    fault = passed("kbd")\n', '''    crash = passed("Freeform")
    crash_text = "\\n".join(crash)
    assert crash[0] == "Pong Probe: log v247 Freeform: Metal device created", crash
    for needle in ("crash: fatal signal 6 code 0 at 0x", "crash: pc 0x", "crash: frames 0x",
                   "before the crash: noise: a line about nothing at all", "before the crash: WKDefaultWallpaperManager: no lookup file",
                   "before the crash: harmless line right before the end"):
        assert crash_text.count(needle) == 1 and "fp 0x" in crash_text, (needle, crash)
    assert sum(line.startswith("Pong Probe: log v247 Freeform: *** Terminating app due to uncaught exception") and
               line.endswith("reason: 'test'") for line in crash) in (1, 2), crash
''')
chunk('    poster = passed("PosterBoard")\n', '    # The display library\'s host test:', '''    poster = passed("PosterBoard")
    poster_text = "\\n".join(poster)
    assert poster[0] == "Pong Probe: log v247 PosterBoard: noise: a poster line", poster
    for needle in ("crash: fatal signal 6 code 0 at 0x", "crash: pc 0x", "crash: frames 0x",
                   "before the crash: noise: a poster line", "before the crash: [DataStore] Gonna init the datastore"):
        assert poster_text.count(needle) == 1, (needle, poster)
    assert sum(line.startswith("Pong Probe: log v247 PosterBoard: *** Terminating app due to uncaught exception") and
               line.endswith("reason: 'test poster'") for line in poster) in (1, 2), poster
    blast = passed("MessagesBlastDoorService")
    blast_text = "\\n".join(blast)
    for needle in ("crash: fatal signal 6 code 0 at 0x", "crash: pc 0x", "crash: frames 0x",
                   "before the crash: noise: a blast door line"):
        assert blast_text.count(needle) == 1, (needle, blast)
    assert sum(line.startswith("Pong Probe: log v247 MessagesBlastDoorService: *** Terminating app due to uncaught exception") and
               line.endswith("reason: 'test blast door'") for line in blast) in (1, 2), blast
    assert not any("MessagesBlastDoorService: noise: a blast door line" in line and "before the crash" not in line
                   for line in blast), blast
''')
rep('    manifest.update(version=246, release_variant=release, purpose=title, candidate_parent=id_,',
    '    manifest.update(version=247, release_variant=release, purpose=title, candidate_parent=id_,')
block('                    kernel_change="THOR V246:', '                    adt_sha256=sha(dt))', '''                    kernel_change="THOR V247: every process that ends by a core-generating signal prints one line (PONG DEATH); the log "
                                  "capture library v247 reports the death of MessagesBlastDoorService; a read-only camera inventory runs "
                                  "when /chosen says thor-camera = 1; carried: everything of v246 to v239, the v236 GPU hook, the v235 witness",
''')
block('    manifest["config_change"] = dict(manifest["config_change"], bootargs_changed=False,', '    manifest["candidate_parent_kernel_uuid"]', '''    selectors = dict(manifest["config_change"]["chosen_selectors"])
    assert "thor-camera" not in selectors
    selectors["thor-camera"] = 1
    manifest["config_change"] = dict(manifest["config_change"], bootargs_changed=False,
                                     menu_title_changed=True, chosen_changed=True, chosen_selectors=selectors,
                                     device_tree_changed=("v246's with one more /chosen property, thor-camera = 1 (the read-only camera "
                                                         "inventory); the root node wlan is kept: launchd runs com.apple.wifid only when "
                                                         "the device tree names wlan or marconi-wifi" if wlan else
                                                         "no wlan node, so launchd never runs com.apple.wifid"),
                                     wifid_start=("launchd starts wifid at boot; a library in it waits until configd has published the "
                                                  "marker and listed en0 and a power source is on the registry (or 150 s), then wifid runs; "
                                                  "killed and started again when it has not asked the driver within 60 s" if wlan else
                                                  "never: no wlan node in the device tree"))
''')
block('    manifest["diagnostic_delta"] = (', '    manifest["wifi_join"] = dict(', '''    manifest["diagnostic_delta"] = ("new: PONG DEATH lines (every process that ends by a core-generating signal: name, pid, signal, "
                                    "exception, code, address, exit reason); libpong-logcap-v247.dylib in SpringBoard, backboardd, PosterBoard, "
                                    "CollectionsPoster, PowerUIAgent, Freeform, kbd and MessagesBlastDoorService (Pong Probe: log v247 ...); "
                                    "THOR CAMERA V247 lines (read-only inventory, /chosen thor-camera = 1).  Gone: libpong-logcap-v246.dylib.  "
                                    "Unchanged: libpong-display-v246.dylib in backboardd, the signed UI adapter in PosterBoard, "
                                    "libpong-wifid-v244.dylib in wifid, libpong-probe-v244.dylib in PosterBoard, configd and wifid; Settings "
                                    "diagnostic as v238; console capture as v232")
    manifest["witness"] = dict(manifest["witness"],
                               kernel_source="osfmk/kern/thor_witness_v235.h (v247 line), bsd/kern/kern_exit.c (PONG DEATH line), "
                                             "bsd/kern/kern_exec.c (staging and injection of the libraries), "
                                             "device/drivers/PongPower/PongCam*.h (the camera inventory, staged in v247-camera-staging)",
                               patch="prepare/logcap/logcap247.c; prepare/display/display246.c (as v246); source changes in v247-intake/patches/apply.py",
                               version_line="THOR WITNESS V247: ...",
                               probe="/private/var/pong/libpong-probe-v244.dylib in PosterBoard, configd and wifid",
                               log_capture="/private/var/pong/libpong-logcap-v247.dylib in SpringBoard, backboardd, PosterBoard, "
                                           "CollectionsPoster, PowerUIAgent, Freeform, kbd and MessagesBlastDoorService",
                               display="/private/var/pong/libpong-display-v246.dylib in backboardd (-[CADisplay bounds]: the panel's size "
                                       "for {1206, 2622} when the native bounds are the panel's or empty; boot argument pong_ca_native=0 turns it off)",
                               camera="read-only inventory of the front camera's clocks, power domains, pins, I2C controller and rails "
                                      "(THOR CAMERA V247), switched by /chosen thor-camera = 1; nothing is written",
                               wallpaper="the signed UI adapter in PosterBoard (its storage adaptation for the data store)",
                               wifid_hold="/private/var/pong/libpong-wifid-v244.dylib in wifid (only where the device tree names wlan)")
''')
rep('    for needle in carried_241_strings + carried_242_strings + carried_243_strings + carried_244_strings + carried_245_strings:',
    '    for needle in carried_241_strings + carried_242_strings + carried_243_strings + carried_244_strings + carried_245_strings + carried_246_strings:')
old = '    assert ("/wlan" in api["parse_adt"](data[adt_member])) == wlan\n'
rep(old, old + '''    # The device tree carries the camera switch, as the manifest says.
    tree = api["parse_adt"](data[adt_member])
    assert tree["/chosen"]["thor-camera"] == (1).to_bytes(4, "little")
    assert json.loads(data[manifest_member])["config_change"]["chosen_selectors"]["thor-camera"] == 1
''')
rep('    return dict(version=246, variant=release.removeprefix("v246-"), embedded_release_id=release, image=target.name,',
    '    return dict(version=247, variant=release.removeprefix("v247-"), embedded_release_id=release, image=target.name,')
rep('                wifi_retry="once more as a plain 802.11g station after a refusal with high-throughput capabilities",',
    '''                wifi_retry="once more on the network's 5 GHz BSS after a refusal on 2.4 GHz, then as a plain 802.11g station",
                camera=manifest["witness"]["camera"],''')
(root / 'package-v247.py').write_text(s)
import py_compile
py_compile.compile(str(root / 'package-v247.py'), doraise=True)
print('package-v247.py written', len(s))
