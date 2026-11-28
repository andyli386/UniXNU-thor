"""Package the v245 boot image from the v244 parent: the render server is put back on the panel's own size,
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
base = root.parent / "v244-unified"
previous = runpy.run_path(str(root.parent / "v212-integrated/package-v212.py"))
decode, util, pack, sha, digest, kernel_member, manifest_member, menu_member, api = (
    previous[n] for n in ("decode", "util", "pack", "sha", "filehash", "k", "m", "c", "api")
)
v244 = runpy.run_path(str(base / "package-v244.py"))                            # helpers and the lists of the parent
adt_member = kernel_member + ".adt"
BOOT_STACK = v244["BOOT_STACK"]
carried_kernel_strings = v244["carried_kernel_strings"]
witness_strings = v244["witness_strings"]
prologue_frame, symbol_address, bytes_at = v244["prologue_frame"], v244["symbol_address"], v244["bytes_at"]
read = lambda path: json.loads(path.read_bytes())

VARIANTS = {
    # name: the device tree names wlan, so launchd starts wifid
    "unified": dict(release="v245-unified", wlan=True, record="unified-verification.json",
                    title="Thor v245 - panel size, wallpaper store, Wi-Fi station"),
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

# Messages only this kernel prints: each present once, absent from v244's.
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
                      b"wifi: association refused: %u\0")
# The messages v244, v243, v242 and v241 added are all still there, once each (minus the ones named above).
carried_244_strings = tuple(x for x in v244["new_kernel_strings"] if not any(x.startswith(g) for g in old_kernel_strings))
carried_243_strings = v244["carried_243_strings"]
carried_242_strings = v244["carried_242_strings"]
carried_241_strings = v244["carried_241_strings"]

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
    assert id_ == parent["image_sha256"] and parent["version"] == 244
    # The command line is byte-identical to v239 to v244; only the menu title changes.
    inject_old, = [t for t in parent["boot_args"].split() if t.startswith("pong_exec_inject=")]
    assert "PosterBoard" not in inject_old and inject_old.endswith(",Preferences")
    menu, args = pack["new_menu"](old[menu_member], {"title": title, "replace_args": {}})
    manifest = json.loads(old[manifest_member])
    assert parent["boot_args"] == manifest["boot_args"]
    assert args == parent["boot_args"]
    assert "Preferences" in args and len(args.encode()) <= 1023
    for token in ("tmlow=1", "tmfull=1"):
        assert args.split().count(token) == 1                       # loader options, see v231
    # The device tree is v244's, unchanged: it carries the wlan node.
    tree = api["parse_adt"](old[adt_member])
    assert bytes(api["emit"](tree, "/")) == old[adt_member] and "/wlan" in tree and wlan
    assert list(tree["/wlan"]) == ["name", "compatible"] and tree["/wlan"]["name"] == b"wlan\0"
    dt = old[adt_member]
    current = read(root / "runtime/final-inputs.json")
    prior = read(base / "runtime/final-inputs.json")
    # Two runtime inputs are new (the log capture and the display library) and v244's log capture is gone;
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
    # The probe and the Wi-Fi daemon's library are v244's, exactly.
    for name, old_path, new_path in ((probe_name, probe_old, probe_new), (wifid_name, wifid_old, wifid_new)):
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
    for name in list(new_libraries) + [settings_name, console_name]:
        for dependency in library_dependencies(root / "runtime" / name):
            assert dependency.startswith("/pong/") or dependency in apple, (name, dependency)
    # The log capture library's host test, compiled from the same source: under the name SpringBoard only
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
    assert crash[1].startswith("Pong Probe: log v245 Freeform: *** Terminating app due to uncaught exception") and \
        crash[2].startswith("Pong Probe: log v245 Freeform: crash: fatal signal 6 code ") and \
        crash[3].startswith("Pong Probe: log v245 Freeform: crash: pc 0x") and "fp 0x" in crash[3] and \
        crash[4].startswith("Pong Probe: log v245 Freeform: crash: frames 0x"), crash
    assert crash[5].startswith("Pong Probe: log v245 Freeform: before the crash: noise: a line about nothing at all") and \
        crash[-1].endswith("before the crash: *** Terminating app due to uncaught exception 'NSInvalidArgumentException', reason: 'test'"), crash
    fault = passed("kbd")
    assert len(fault) == 6 and fault[0] == "Pong Probe: log v245 kbd: LayoutStar keyboardName iPhone-PortraitChoco-Pinyin-Simplified resulted in (null)", fault
    assert fault[1] == "Pong Probe: log v245 kbd: crash: fatal signal 11 code 2 at 0x123456789" and \
        fault[3].startswith("Pong Probe: log v245 kbd: crash: frames 0x") and \
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
    assert sum("the identity's size on this panel" in line for line in shown) == 2 and \
        sum("the native bounds are not the panel's: kept" in line for line in shown) == 1, shown
    assert lines[at["Other"] + 1].strip().startswith("Other: -bounds is still QuartzCore's own 0x"), lines
    subprocess.run([sys.executable, "-IB", str(root / "prepare/settings/replay.py")], check=True, stdout=subprocess.DEVNULL)
    # The start-up qsort frames are v231's (the boot-stack budget).
    old_qsort = prologue_frame(bytes_at(old[kernel_member], symbol_address(base / "kernel/kernel-ipados18-thor.macho", "_qsort"), 64))
    new_qsort = prologue_frame(bytes_at(kernel.read_bytes(), symbol_address(kernel, "_qsort"), 64))
    assert old_qsort == new_qsort <= 0x100, (old_qsort, new_qsort)
    manifest.update(version=245, release_variant=release, purpose=title, candidate_parent=id_,
                    candidate_change=changes, kernel_source=str(kernel), kernel_sha256=digest(kernel),
                    boot_args=args, graphics_runtime_active=True, gpu_private_boot=False,
                    hardware_validated=False, flash_performed=False, fastboot_boot_performed=False,
                    kernel_change="THOR V245: the display library in backboardd answers the panel's size (1440x3200) where the "
                                  "render server reads the iPhone 16 Pro's (1206x2622); the signed UI adapter goes into PosterBoard for "
                                  "its wallpaper data store; the log capture library v245 reports signal, registers and stack of a fatal "
                                  "signal in Freeform and kbd and every line of the render server at its start; the Wi-Fi station has an "
                                  "address of its own (unique-chip-id), a complete association request and logs both frames of a refusal; "
                                  "carried: everything of v244",
                    adt_sha256=sha(dt))
    manifest["config_change"] = dict(manifest["config_change"], bootargs_changed=False,
                                     menu_title_changed=True, chosen_changed=False,
                                     device_tree_changed=("unchanged from v239 through v244 (root node wlan: launchd runs com.apple.wifid only when "
                                                          "the device tree names wlan or marconi-wifi)" if wlan else
                                                          "no wlan node, so launchd never runs com.apple.wifid"),
                                     wifid_start=("launchd starts wifid at boot; a library in it waits until configd has published the "
                                                  "marker and listed en0 and a power source is on the registry (or 150 s), then wifid runs; "
                                                  "killed and started again when it has not asked the driver within 60 s" if wlan else
                                                  "never: no wlan node in the device tree"))
    manifest["candidate_parent_kernel_uuid"] = parent["kernel_uuid"]
    manifest["diagnostic_delta"] = ("new: libpong-display-v245.dylib in backboardd (lines PONG PROBE backboardd[pid]: Pong Probe: "
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
    # The v235 witness is carried unchanged (same messages, as often); the v244 log capture lines and the
    # station's old refusal message are gone; the v245 lines are new; everything else v241 to v244 added is
    # still there once.
    for needle in witness_strings:
        assert payload.count(needle) >= 1 and old_payload.count(needle) == payload.count(needle), needle
    assert payload.count(b"THOR WITNESS V235") >= 12 and old_payload.count(b"THOR WITNESS V235") == payload.count(b"THOR WITNESS V235")
    for needle in old_kernel_strings:
        assert payload.count(needle) == 0 and old_payload.count(needle) >= 1, needle
    for needle in new_kernel_strings:
        assert payload.count(needle) == 1 and old_payload.count(needle) == 0, needle
    for needle in carried_241_strings + carried_242_strings + carried_243_strings + carried_244_strings:
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
    # The station address function is in the kernel and its import is the archive's.
    assert payload.count(b"_pong_wifi_station_mac") == 1 and old_payload.count(b"_pong_wifi_station_mac") == 0
    # The libraries of v244 that are gone were embedded exactly once in its kernel and are not in this one.
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
    return dict(version=245, variant=release.removeprefix("v245-"), embedded_release_id=release, image=target.name,
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
