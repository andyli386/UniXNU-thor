"""Package the v247 boot image from the v246 parent: every death by a signal is recorded, the Messages helper's
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
"""
import argparse
import json
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

assert __debug__

root = Path(__file__).resolve().parent
base = root.parent / "v246-unified"
previous = runpy.run_path(str(root.parent / "v212-integrated/package-v212.py"))
decode, util, pack, sha, digest, kernel_member, manifest_member, menu_member, api = (
    previous[n] for n in ("decode", "util", "pack", "sha", "filehash", "k", "m", "c", "api")
)
v246 = runpy.run_path(str(base / "package-v246.py"))                            # helpers and the lists of the parent
adt_member = kernel_member + ".adt"
BOOT_STACK = v246["BOOT_STACK"]
carried_kernel_strings = v246["carried_kernel_strings"]
witness_strings = v246["witness_strings"]
prologue_frame, symbol_address, bytes_at = v246["prologue_frame"], v246["symbol_address"], v246["bytes_at"]
read = lambda path: json.loads(path.read_bytes())

VARIANTS = {
    # name: the device tree names wlan, so launchd starts wifid
    "unified": dict(release="v247-unified", wlan=True, record="unified-verification.json",
                    title="Thor v247 - every death recorded, Messages helper, camera inventory"),
}
variant = VARIANTS["unified"]
release, wlan, title = variant["release"], variant["wlan"], variant["title"]
target = root / ("boot-thor-unixnu-" + release + ".img")
record = root / variant["record"]
full_target = root / ("boot-thor-unixnu-" + release + "-full.img")
kernel = root / "kernel/kernel-ipados18-thor.macho"

console_name = "libpong-console-v205.dylib"
console_old = base / "runtime" / console_name
console_new = root / "runtime" / console_name
settings_name = "libpong-settings-v185.dylib"
settings_old = base / "runtime" / settings_name
settings_new = root / "runtime" / settings_name
probe_name = "libpong-probe-v244.dylib"         # unchanged from v244
probe_old, probe_new = base / "runtime" / probe_name, root / "runtime" / probe_name
wifid_name = "libpong-wifid-v244.dylib"         # unchanged from v244
wifid_old, wifid_new = base / "runtime" / wifid_name, root / "runtime" / wifid_name
logcap_name = "libpong-logcap-v247.dylib"
display_name = "libpong-display-v246.dylib"          # unchanged from v246
display_old, display_new = base / "runtime" / display_name, root / "runtime" / display_name
# name: build script, the message that must appear exactly once in it, messages that must appear at least once
new_libraries = {
    logcap_name: ("prepare/logcap/build-logcap247.sh", b"Pong Probe: log v247: ", b"Pong Probe: log v247 ",
                  b"MessagesBlastDoorService"),
}
# The log capture library of v246 is gone (rebuilt under a new name); the display library is unchanged.
gone_runtime = {"libpong-logcap-v246.dylib"}

# Messages only this kernel prints: each present once, absent from v246's.
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

# Apple's own binaries of this system, read from the OS volume: every library the injected
# libraries load must be named as one of these names it.
apple_binaries = (
    "/sbin/launchd", "/usr/sbin/wifid", "/usr/libexec/configd",
    "/System/Library/CoreServices/SpringBoard.app/SpringBoard", "/Applications/PosterBoard.app/PosterBoard",
    "/System/Library/PrivateFrameworks/ChronoCore.framework/Support/chronod",
    "/System/Library/PrivateFrameworks/CoreDuetContext.framework/Resources/contextstored",
    "/usr/libexec/backboardd",
)

changes = [
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
]


def apple_install_names():
    names = set()
    sys.path.insert(0, str(root.parent / "session-tools"))
    import osimg
    with tempfile.TemporaryDirectory() as folder:
        for index, path in enumerate(apple_binaries):
            node = osimg.node(path)
            assert node is not None, path
            file = Path(folder) / str(index)
            file.write_bytes(node.contents())
            for line in subprocess.check_output(["otool", "-L", str(file)], text=True).splitlines()[1:]:
                names.add(line.strip().split(" (compatibility")[0])
    return names


def library_dependencies(path):
    out = subprocess.check_output(["otool", "-L", str(path)], text=True).splitlines()[1:]
    return [line.strip().split(" (compatibility")[0] for line in out]


def configuration():
    parent = read(base / "unified-verification.json")
    old, fw, sig, id_ = decode(base / parent["image"])
    assert id_ == parent["image_sha256"] and parent["version"] == 246
    # The command line is byte-identical to v239 to v246; only the menu title changes.
    inject_old, = [t for t in parent["boot_args"].split() if t.startswith("pong_exec_inject=")]
    assert "PosterBoard" not in inject_old and inject_old.endswith(",Preferences")
    menu, args = pack["new_menu"](old[menu_member], {"title": title, "replace_args": {}})
    manifest = json.loads(old[manifest_member])
    assert parent["boot_args"] == manifest["boot_args"]
    assert args == parent["boot_args"]
    assert "Preferences" in args and len(args.encode()) <= 1023
    for token in ("tmlow=1", "tmfull=1"):
        assert args.split().count(token) == 1                       # loader options, see v231
    # The device tree is v246's with one /chosen property added: thor-camera = 1 (the read-only camera inventory).
    tree = api["parse_adt"](old[adt_member])
    assert bytes(api["emit"](tree, "/")) == old[adt_member] and "/wlan" in tree and wlan
    assert list(tree["/wlan"]) == ["name", "compatible"] and tree["/wlan"]["name"] == b"wlan\0"
    assert "thor-camera" not in tree["/chosen"]
    tree["/chosen"]["thor-camera"] = (1).to_bytes(4, "little")
    dt = bytes(api["emit"](tree, "/"))
    assert api["parse_adt"](dt) == tree and dt != old[adt_member], "the device tree did not take the property"
    current = read(root / "runtime/final-inputs.json")
    prior = read(base / "runtime/final-inputs.json")
    # One runtime input is a new build (the log capture library) and v246's is gone; the display library,
    # the probe, the Wi-Fi daemon's library, the console capture and the Settings diagnostic are unchanged.
    assert {k: v for k, v in current.items() if k != "payloads"} == {k: v for k, v in prior.items() if k != "payloads"}
    names = set(current["payloads"]) | set(prior["payloads"])
    differing = {n for n in names if current["payloads"].get(n) != prior["payloads"].get(n)}
    assert differing == set(new_libraries) | gone_runtime, differing
    assert not gone_runtime & set(current["payloads"]) and not (gone_runtime - set(prior["payloads"]))
    spec = current["payloads"][console_name]
    blob = console_new.read_bytes()
    assert sha(blob) == spec["sha256"] == prior["payloads"][console_name]["sha256"]
    assert len(blob) == spec["bytes"] == prior["payloads"][console_name]["bytes"]
    subprocess.run(["codesign", "--verify", "--strict", str(console_new)], check=True)
    assert subprocess.check_output(["xcrun", "dwarfdump", "--uuid", str(console_new)], text=True).split()[1] == spec["uuid"]
    # The Settings diagnostic is v238's library, exactly, and is rebuilt from its sources.
    settings_spec = current["payloads"][settings_name]
    settings_blob = settings_new.read_bytes()
    assert sha(settings_blob) == settings_spec["sha256"] and len(settings_blob) == settings_spec["bytes"] <= 128 * 1024
    assert settings_blob == settings_old.read_bytes()
    subprocess.run(["codesign", "--verify", "--strict", str(settings_new)], check=True)
    assert subprocess.check_output(["xcrun", "dwarfdump", "--uuid", str(settings_new)], text=True).split()[1] == settings_spec["uuid"]
    assert (root / "prepare/settings/about237.patch").is_file() and (root / "prepare/settings/about238.patch").is_file()
    for needle in (b"SCPreferences", b"WiFiManagerClient", b"pong-wifi-power", b"/dev/console"):
        assert needle not in probe_new.read_bytes(), needle
    # The display library is v246's and the probe and the Wi-Fi daemon's library are v244's, exactly.
    for name, old_path, new_path in ((probe_name, probe_old, probe_new), (wifid_name, wifid_old, wifid_new),
                                     (display_name, display_old, display_new)):
        spec_ = current["payloads"][name]
        blob = new_path.read_bytes()
        assert spec_ == prior["payloads"][name] and blob == old_path.read_bytes(), name
        assert sha(blob) == spec_["sha256"] and len(blob) == spec_["bytes"] <= 128 * 1024
        subprocess.run(["codesign", "--verify", "--strict", str(new_path)], check=True)
        assert subprocess.check_output(["xcrun", "dwarfdump", "--uuid", str(new_path)], text=True).split()[1] == spec_["uuid"]
    # The new libraries: the recorded ones, signed, and rebuilt byte for byte from their sources.
    for name, (script, once, *needles) in new_libraries.items():
        spec = current["payloads"][name]
        blob = (root / "runtime" / name).read_bytes()
        assert sha(blob) == spec["sha256"] and len(blob) == spec["bytes"] <= 128 * 1024
        subprocess.run(["codesign", "--verify", "--strict", str(root / "runtime" / name)], check=True)
        assert subprocess.check_output(["xcrun", "dwarfdump", "--uuid", str(root / "runtime" / name)], text=True).split()[1] == spec["uuid"]
        assert blob.count(once) == 1, (name, once)
        for needle in needles:
            assert blob.count(needle) >= 1, (name, needle)
        rebuilt = subprocess.run(["/bin/bash", str(root / script)], check=True, capture_output=True, text=True)
        assert spec["sha256"] in rebuilt.stdout and (root / "runtime" / name).read_bytes() == blob, name
    # Every library the injected libraries load exists under that name in this system: it is the
    # install name of one that Apple's own binaries load.  (v241's wallpaper library failed this.)
    apple = apple_install_names()
    assert "/usr/lib/libobjc.A.dylib" in apple and "/System/Library/Frameworks/Foundation.framework/Foundation" in apple
    for name in list(new_libraries) + [display_name, settings_name, console_name]:
        for dependency in library_dependencies(root / "runtime" / name):
            assert dependency.startswith("/pong/") or dependency in apple, (name, dependency)
    # The log capture library's host test, compiled from the same source: under the name SpringBoard only
    # the lines with a keyword are passed on and nothing is left on stderr; under the name backboardd every
    # line of the first seconds and then keyword lines; under the name Freeform the indicator lines, the
    # abort with its signal, registers and frames, and the last lines are passed on (SIGABRT); under the
    # name kbd a real fault three calls deep is reported with its address (SIGSEGV); under the name
    # PosterBoard (v246) a keyword line, an uncaught exception, the signal and the last lines (SIGABRT);
    # under the name MessagesBlastDoorService (v247) another line and an uncaught exception, then the signal and
    # the last lines (SIGABRT); under another name nothing is captured.
    host = subprocess.run(["/bin/bash", str(root / "prepare/logcap/hosttest/run247.sh")], check=True, capture_output=True, text=True)
    lines = host.stdout.splitlines()
    names = ("SpringBoard", "backboardd", "Freeform", "kbd", "PosterBoard", "MessagesBlastDoorService", "Other")
    at = {name: next(i for i, line in enumerate(lines) if line.startswith("== " + name + ":")) for name in names}
    assert "exit status 0; left on stderr: 0 bytes" in lines[at["SpringBoard"]], lines[at["SpringBoard"]]
    assert "exit status 0; left on stderr: 0 bytes" in lines[at["backboardd"]], lines[at["backboardd"]]
    assert "exit status 134; left on stderr: 0 bytes" in lines[at["Freeform"]], lines[at["Freeform"]]
    assert "exit status 139; left on stderr: 0 bytes" in lines[at["kbd"]], lines[at["kbd"]]
    assert "exit status 134; left on stderr: 0 bytes" in lines[at["PosterBoard"]], lines[at["PosterBoard"]]
    assert "left on stderr: 251 bytes" in lines[at["Other"]], lines[at["Other"]]
    passed = lambda name: [line.strip() for line in lines[at[name] + 1:at[names[names.index(name) + 1]]]]
    assert passed("SpringBoard") == [
        "Pong Probe: log v247 SpringBoard: WallpaperKit: Looking for default wallpaper based on deviceProductType 'iPhone17,1'",
        "Pong Probe: log v247 SpringBoard: PBFPoster something about a poster",
        "Pong Probe: log v247 SpringBoard: a battery percent line",
        "Pong Probe: log v247 SpringBoard: WKDefaultWallpaperManager: no lookup file"], passed("SpringBoard")
    assert passed("backboardd") == [
        "Pong Probe: log v247 backboardd: noise: before the window server",
        "Pong Probe: log v247 backboardd: Pong Metal: handoff Turnip V162 active",
        "Pong Probe: log v247 backboardd: [Display] StartWindowServer: initial main screen size:{1206, 2622} scale:3",
        "Pong Probe: log v247 backboardd: a battery percent line"], passed("backboardd")
    crash = passed("Freeform")
    crash_text = "\n".join(crash)
    assert crash[0] == "Pong Probe: log v247 Freeform: Metal device created", crash
    for needle in ("crash: fatal signal 6 code 0 at 0x", "crash: pc 0x", "crash: frames 0x",
                   "before the crash: noise: a line about nothing at all", "before the crash: WKDefaultWallpaperManager: no lookup file",
                   "before the crash: harmless line right before the end"):
        assert crash_text.count(needle) == 1 and "fp 0x" in crash_text, (needle, crash)
    assert sum(line.startswith("Pong Probe: log v247 Freeform: *** Terminating app due to uncaught exception") and
               line.endswith("reason: 'test'") for line in crash) in (1, 2), crash
    fault = passed("kbd")
    assert len(fault) == 6 and fault[0] == "Pong Probe: log v247 kbd: LayoutStar keyboardName iPhone-PortraitChoco-Pinyin-Simplified resulted in (null)", fault
    assert fault[1] == "Pong Probe: log v247 kbd: crash: fatal signal 11 code 2 at 0x123456789" and \
        fault[3].startswith("Pong Probe: log v247 kbd: crash: frames 0x") and \
        fault[4] == "Pong Probe: log v247 kbd: before the crash: boring keyboard line", fault
    poster = passed("PosterBoard")
    poster_text = "\n".join(poster)
    assert poster[0] == "Pong Probe: log v247 PosterBoard: noise: a poster line", poster
    for needle in ("crash: fatal signal 6 code 0 at 0x", "crash: pc 0x", "crash: frames 0x",
                   "before the crash: noise: a poster line", "before the crash: [DataStore] Gonna init the datastore"):
        assert poster_text.count(needle) == 1, (needle, poster)
    assert sum(line.startswith("Pong Probe: log v247 PosterBoard: *** Terminating app due to uncaught exception") and
               line.endswith("reason: 'test poster'") for line in poster) in (1, 2), poster
    blast = passed("MessagesBlastDoorService")
    blast_text = "\n".join(blast)
    for needle in ("crash: fatal signal 6 code 0 at 0x", "crash: pc 0x", "crash: frames 0x",
                   "before the crash: noise: a blast door line"):
        assert blast_text.count(needle) == 1, (needle, blast)
    assert sum(line.startswith("Pong Probe: log v247 MessagesBlastDoorService: *** Terminating app due to uncaught exception") and
               line.endswith("reason: 'test blast door'") for line in blast) in (1, 2), blast
    assert not any("MessagesBlastDoorService: noise: a blast door line" in line and "before the crash" not in line
                   for line in blast), blast
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
    assert sum("the panel's size is answered" in line for line in shown) == 4 and \
        sum("the native bounds are those of another display: kept" in line for line in shown) == 1 and \
        sum("while the display has no size of its own" in line for line in shown) == 2, shown
    assert lines[at["Other"] + 1].strip().startswith("Other: -bounds is still QuartzCore's own 0x"), lines
    subprocess.run([sys.executable, "-IB", str(root / "prepare/settings/replay.py")], check=True, stdout=subprocess.DEVNULL)
    # The start-up qsort frames are v231's (the boot-stack budget).
    old_qsort = prologue_frame(bytes_at(old[kernel_member], symbol_address(base / "kernel/kernel-ipados18-thor.macho", "_qsort"), 64))
    new_qsort = prologue_frame(bytes_at(kernel.read_bytes(), symbol_address(kernel, "_qsort"), 64))
    assert old_qsort == new_qsort <= 0x100, (old_qsort, new_qsort)
    manifest.update(version=247, release_variant=release, purpose=title, candidate_parent=id_,
                    candidate_change=changes, kernel_source=str(kernel), kernel_sha256=digest(kernel),
                    boot_args=args, graphics_runtime_active=True, gpu_private_boot=False,
                    hardware_validated=False, flash_performed=False, fastboot_boot_performed=False,
                    kernel_change="THOR V247: every process that ends by a core-generating signal prints one line (PONG DEATH); the log "
                                  "capture library v247 reports the death of MessagesBlastDoorService; a read-only camera inventory runs "
                                  "when /chosen says thor-camera = 1; carried: everything of v246 to v239, the v236 GPU hook, the v235 witness",
                    adt_sha256=sha(dt))
    selectors = dict(manifest["config_change"]["chosen_selectors"])
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
    manifest["candidate_parent_kernel_uuid"] = parent["kernel_uuid"]
    manifest["diagnostic_delta"] = ("new: PONG DEATH lines (every process that ends by a core-generating signal: name, pid, signal, "
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
    manifest["wifi_join"] = dict(configured=False, source="PONG_WIFI_TEST_CONFIG at build time (absent for this image)",
                                 switch="/chosen thor-wifi-join = 0")
    members = {kernel_member: kernel.read_bytes(), adt_member: dt,
               manifest_member: util["json_bytes"](manifest), menu_member: menu}
    changed = {kernel_member, manifest_member, menu_member} | ({adt_member} if dt != old[adt_member] else set())
    return parent, old, fw, sig, members, changed, new_qsort, current, spec


def verify(path):
    assert (root / "prepare/build.status").read_text().strip() == "0"
    parent, old, firmware, signature, members, changed, qsort_frame, current, spec = configuration()
    data, new_firmware, new_signature, image_sha = decode(path)
    assert old.keys() == data.keys() and (firmware, signature) == (new_firmware, new_signature)
    assert {name for name in old if old[name] != data[name]} == changed
    assert all(data[name] == content for name, content in members.items())
    config = tomllib.loads(data[menu_member].decode())
    manifest = json.loads(data[manifest_member])
    pack["MENU"]["verify_abi"]()
    chosen = pack["MENU"]["validate_menu"](config, manifest)
    assert config["boot"] == {"default": "xnu", "timeout": 2}
    assert chosen[0]["argument_bytes"] == len(manifest["boot_args"].encode()) <= 1023
    assert manifest["release_variant"] == release and title.encode() in data[menu_member]
    payload = data[kernel_member]
    assert prologue_frame(bytes_at(payload, symbol_address(kernel, "_qsort"), 64)) == qsort_frame
    old_payload = old[kernel_member]
    for token in carried_kernel_strings:
        assert payload.count(token) == 1 and old_payload.count(token) == 1, token
    for needle in (b"_pong_wifi_start_optional", b"_pong_wifi_irq_due", b"thor_bt_probe"):
        assert needle in payload, needle
    # The v235 witness is carried unchanged (same messages, as often); the v245 library lines are gone; the
    # v246 lines are new; everything else v241 to v245 added is still there once.
    for needle in witness_strings:
        assert payload.count(needle) >= 1 and old_payload.count(needle) == payload.count(needle), needle
    assert payload.count(b"THOR WITNESS V235") >= 12 and old_payload.count(b"THOR WITNESS V235") == payload.count(b"THOR WITNESS V235")
    for needle in old_kernel_strings:
        assert payload.count(needle) == 0 and old_payload.count(needle) >= 1, needle
    for needle in new_kernel_strings:
        assert payload.count(needle) == 1 and old_payload.count(needle) == 0, needle
    for needle in carried_241_strings + carried_242_strings + carried_243_strings + carried_244_strings + carried_245_strings + carried_246_strings:
        assert payload.count(needle) == 1 and old_payload.count(needle) == 1, needle
    assert payload.count(b"THOR MODEM FW V163: state=gpt-verified lun=4 slots=2 selected=none") == 1
    assert payload.count(b"_pong_wifi_test_reconnect") == 1
    # The console capture the kernel stages is still v232's durable build.
    new_blob, old_blob = console_new.read_bytes(), console_old.read_bytes()
    assert new_blob == old_blob and payload.count(new_blob) == 1
    assert b"/private/var/thor-console-v232" in payload
    assert new_blob.count(b"Pong console capture V232 durable=fullfsync") == 1 and b"_fcntl" in new_blob
    # The Settings diagnostic: v238's library, exactly once.
    settings, settings_before = settings_new.read_bytes(), settings_old.read_bytes()
    assert settings == settings_before and payload.count(settings) == 1 and old_payload.count(settings_before) == 1
    assert b"/private/var/pong/libpong-settings-v225.dylib" in payload and old_payload.count(b"/private/var/pong/libpong-settings-v225.dylib") == 1
    # The device tree carries the wlan node.
    assert ("/wlan" in api["parse_adt"](data[adt_member])) == wlan
    # The device tree carries the camera switch, as the manifest says.
    tree = api["parse_adt"](data[adt_member])
    assert tree["/chosen"]["thor-camera"] == (1).to_bytes(4, "little")
    assert json.loads(data[manifest_member])["config_change"]["chosen_selectors"]["thor-camera"] == 1
    # The libraries of v245 that are gone were embedded exactly once in its kernel and are not in this one.
    for name in gone_runtime:
        old_blob = (base / "runtime" / name).read_bytes()
        assert old_payload.count(old_blob) == 1 and payload.count(old_blob) == 0, name
    # Every embedded library is present exactly once and is the one recorded for this version.
    for name, spec_ in current["payloads"].items():
        blob = (root / "runtime" / name).read_bytes()
        assert sha(blob) == spec_["sha256"] and len(blob) == spec_["bytes"] and payload.count(blob) == 1, name
    uuids = {subprocess.check_output(["xcrun", "dwarfdump", "--uuid", str(root / "kernel" / f)], text=True).split()[1]
             for f in ("kernel-ipados18-thor.macho", "mach.development.pong", "mach.development.pong.unstripped")}
    assert len(uuids) == 1 and uuids != {parent["kernel_uuid"]}
    _, offset, size, _ = util["sections"](path)
    with tempfile.NamedTemporaryFile(dir=root, suffix=".fat") as tmp, path.open("rb") as image:
        image.seek(offset)
        tmp.write(image.read(size))
        tmp.flush()
        subprocess.run(["/sbin/fsck_msdos", "-n", tmp.name], check=True, stdout=subprocess.DEVNULL)
    return dict(version=247, variant=release.removeprefix("v247-"), embedded_release_id=release, image=target.name,
                image_bytes=path.stat().st_size, image_sha256=image_sha, kernel_uuid=uuids.pop(),
                kernel_sha256=sha(payload), parent_sha256=parent["image_sha256"], parent_kernel_uuid=parent["kernel_uuid"],
                adt_sha256=sha(data[adt_member]), chosen_selectors=manifest["config_change"]["chosen_selectors"],
                boot_args=manifest["boot_args"],
                boot_argument_bytes=len(manifest["boot_args"].encode()), qsort_frame_bytes=qsort_frame,
                boot_stack_bytes=BOOT_STACK, console_sha256=spec["sha256"], console_uuid=spec["uuid"],
                console_stage_path=current["diagnostic_stage_path"], pool_path=parent["pool_path"],
                diagnostic_path=current["diagnostic_stage_path"],
                runtime={name: dict(spec_) for name, spec_ in current["payloads"].items()},
                witness=manifest["witness"], wifi_join=manifest["wifi_join"], probe=manifest["witness"]["probe"],
                display=manifest["witness"]["display"],
                wifi_retry="once more on the network's 5 GHz BSS after a refusal on 2.4 GHz, then as a plain 802.11g station",
                camera=manifest["witness"]["camera"],
                wifi_daemon="started and held (wlan node in the device tree)" if wlan else "not started (no wlan node)",
                firmware_sha256=firmware, signature_sha256=signature, filesystem_check=0,
                changed_fat_members=sorted(changed), candidate_change=changes, hardware_validated=False)


def verify_full(path):
    """The image of the full layout (only packaged on request)."""
    _, old, firmware, signature, members, changed, _, _, _ = configuration()
    data, new_firmware, new_signature, _ = decode(path)
    assert (firmware, signature) == (new_firmware, new_signature)
    assert all(data[name] == content for name, content in members.items())
    assert {name for name in data if data[name] != old.get(name)} == changed
    return dict(image=path.name, image_bytes=path.stat().st_size, image_sha256=sha(path.read_bytes()))


def main():
    global variant, release, wlan, title, target, record, full_target
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("package", "verify"))
    parser.add_argument("variant", nargs="?", choices=sorted(VARIANTS), default="unified")
    args = parser.parse_args()
    variant = VARIANTS[args.variant]
    release, wlan, title = variant["release"], variant["wlan"], variant["title"]
    target = root / ("boot-thor-unixnu-" + release + ".img")
    record = root / variant["record"]
    if args.action == "package" and not target.exists():
        with tempfile.TemporaryDirectory(dir=root, prefix=".package-") as folder:
            tmp = Path(folder)
            assert digest(util["FULL"]) == util["PARENT_FULL_SHA"]
            full, candidate = tmp / "full.img", tmp / "candidate.img"
            shutil.copyfile(util["FULL"], full)
            _, _, _, _, members, _, _, _, _ = configuration()
            epoch = 1795428802
            env = dict(os.environ, TZ="UTC", SOURCE_DATE_EPOCH=str(epoch))
            for index, (name, content) in enumerate(members.items()):
                file = tmp / str(index)
                file.write_bytes(content)
                os.utime(file, (epoch, epoch))
                subprocess.run(["mcopy", "-m", "-o", "-i", util["volume"](full), file, "::" + name], check=True, env=env)
            util["compact"](full, candidate)
            result = verify(candidate)
            (tmp / "receipt").write_bytes(util["json_bytes"](result))
            os.link(candidate, target)
            os.replace(tmp / "receipt", record)
    result = verify(target)
    recorded = read(record)
    assert result == recorded, "image differs from its receipt"
    print(util["json_bytes"](result).decode(), end="")


if __name__ == "__main__":
    main()
