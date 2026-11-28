"""Package the v244 boot image from the v243 parent: the wallpaper catalog library is gone, a log capture
library takes its place, and the Wi-Fi daemon keeps its own log and is sampled.

Why (read from the consoles of 2026-11-27, v243 unified and v243 with the wlan node):
- The wallpaper catalog library of v242 and v243 did nothing visible: WallpaperKit never looked for a
  catalog while it was there (not one call through the replaced lower-case method).  And the first boots
  with it are the first in which the render server draws every frame in software at 1206x2622 instead of
  1440x3200 (v237 to v239: native frames, a 1:1 copy; v242, v243: each frame enlarged by the framebuffer
  driver with nearest neighbour, which is the jagged edge on text and icons the user sees, at 1.5 to 2 ms
  a frame more).  The mechanism is not known; the library is not injected any more, and the render
  server's own log is captured to see what it does.
- Three questions need what the processes say themselves: the black wallpaper, the battery number that
  does not show although the battery is published, and why Freeform and the keyboard daemon die (no
  crash report is written: the five corpse slots of a boot are taken in its first 83 seconds).
  libpong-logcap-v244 (SpringBoard, backboardd, PosterBoard, CollectionsPoster, PowerUIAgent, Freeform,
  kbd) switches libsystem_trace to mirroring os_log to the process's error output and passes filtered
  lines on through the kernel's diagnostic channel; for Freeform and kbd it keeps the last lines and
  passes them on when the process is about to die, so the reason of an uncaught exception is not lost.
- wifid ran for the first time with the radio up: 48 networks listed, then Settings hung on it as soon
  as its Wi-Fi pane opened, an association request was refused by the access point (status 48), and the
  daemon answered nothing more.  Its capture file held 129 bytes: OS_ACTIVITY_DT_MODE did nothing.
  libpong-wifid-v244 sets libsystem_trace's switch itself and keeps the output for the life of the
  process; libpong-probe-v244 samples wifid's threads.

The image carries the wlan node (launchd starts wifid).  Carried unchanged: the firmware LUN check with
the primary table deciding (v243), the retries and the record of a failed check (v242), the probe's
values in configd, Settings -> About, the panic record slot (v238), protection classes A and B and
IOSurface selector 21 (v237), the v236 GPU timing hook, the v235 witness.  No boot argument changed.

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
base = root.parent / "v243-unified"
previous = runpy.run_path(str(root.parent / "v212-integrated/package-v212.py"))
decode, util, pack, sha, digest, kernel_member, manifest_member, menu_member, api = (
    previous[n] for n in ("decode", "util", "pack", "sha", "filehash", "k", "m", "c", "api")
)
v243 = runpy.run_path(str(base / "package-v243.py"))                            # helpers and the lists of the parent
v242 = v243["v242"]
adt_member = kernel_member + ".adt"
BOOT_STACK = v243["BOOT_STACK"]
carried_kernel_strings = v243["carried_kernel_strings"]
witness_strings = v243["witness_strings"]
prologue_frame, symbol_address, bytes_at = v243["prologue_frame"], v243["symbol_address"], v243["bytes_at"]
read = lambda path: json.loads(path.read_bytes())

VARIANTS = {
    # name: the device tree decides whether launchd starts wifid at all
    "unified": dict(release="v244-unified", wlan=True, record="unified-verification.json",
                    title="Thor v244 - Wi-Fi daemon, log capture"),
    "nowifi": dict(release="v244-nowifi", wlan=False, record="nowifi-verification.json",
                   title="Thor v244 - log capture, no Wi-Fi daemon"),
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
probe_name = "libpong-probe-v244.dylib"
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

# Messages only this kernel prints: each present once, absent from v243's.
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

# Apple's own binaries of this system, read from the OS volume: every library the injected
# libraries load must be named as one of these names it.
apple_binaries = (
    "/sbin/launchd", "/usr/sbin/wifid", "/usr/libexec/configd",
    "/System/Library/CoreServices/SpringBoard.app/SpringBoard", "/Applications/PosterBoard.app/PosterBoard",
    "/System/Library/PrivateFrameworks/ChronoCore.framework/Support/chronod",
    "/System/Library/PrivateFrameworks/CoreDuetContext.framework/Resources/contextstored",
)

changes = [
    "The wallpaper catalog library is not injected any more.  WallpaperKit never looked for a catalog while "
    "it was there (v242, v243), and those are the first boots in which the render server draws in software at "
    "1206x2622 instead of 1440x3200 (v237 to v243: native frames).  Whether the frame size returns to native is "
    "to be read from the first swaps of this image.",
    "A log capture library (libpong-logcap-v244) is in SpringBoard, backboardd, PosterBoard, CollectionsPoster, "
    "PowerUIAgent, Freeform and kbd: it switches libsystem_trace to mirroring os_log to the process's error "
    "output (the environment variable OS_ACTIVITY_DT_MODE did nothing for wifid on 2026-11-27), filters it "
    "and passes it on through the kernel's diagnostic channel; for Freeform and kbd it keeps the last lines "
    "and passes them on when the process is about to die.",
    "The Wi-Fi daemon's own library (libpong-wifid-v244) sets the same switch when it releases the daemon and "
    "keeps its output for the life of the process; the probe (libpong-probe-v244) samples wifid's threads.",
    "Every library the injected libraries load is compared with the names Apple's own binaries use.",
    "Carried unchanged: the firmware LUN check with the primary table deciding (v243), the retries and the "
    "record of a failed check (v242), the interface-namer marker and the en0 listing in configd (Settings -> "
    "About), the panic record slot (v238), the Settings hang dump with symbols (v238), protection classes A and B "
    "open for every process and IOSurface selector 21 (v237), the v236 GPU timing hook, the v235 witness.  No "
    "boot argument changed.",
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
    assert id_ == parent["image_sha256"] and parent["version"] == 243
    # The command line is byte-identical to v239 to v243; only the menu title changes.
    inject_old, = [t for t in parent["boot_args"].split() if t.startswith("pong_exec_inject=")]
    assert "PosterBoard" not in inject_old and inject_old.endswith(",Preferences")
    menu, args = pack["new_menu"](old[menu_member], {"title": title, "replace_args": {}})
    manifest = json.loads(old[manifest_member])
    assert parent["boot_args"] == manifest["boot_args"]
    assert args == parent["boot_args"]
    assert "Preferences" in args and len(args.encode()) <= 1023
    for token in ("tmlow=1", "tmfull=1"):
        assert args.split().count(token) == 1                       # loader options, see v231
    # The device tree: v243 unified carries v238's (no wlan node) and is kept for the nowifi image; the
    # default image takes v239's (with the wlan node).  Nothing else differs.
    tree = api["parse_adt"](old[adt_member])
    assert bytes(api["emit"](tree, "/")) == old[adt_member] and "/wlan" not in tree
    if wlan:
        v239 = read(root.parent / "v239-unified/unified-verification.json")
        old239, _, _, id239 = decode(root.parent / "v239-unified" / v239["image"])
        assert id239 == v239["image_sha256"] and v239["version"] == 239
        tree239 = api["parse_adt"](old239[adt_member])
        assert list(tree239["/wlan"]) == ["name", "compatible"] and tree239["/wlan"]["name"] == b"wlan\0"
        del tree239["/wlan"]
        assert bytes(api["emit"](tree239, "/")) == old[adt_member], "v239's tree differs from v243's only by the wlan node"
        dt = old239[adt_member]
    else:
        dt = old[adt_member]
    current = read(root / "runtime/final-inputs.json")
    prior = read(base / "runtime/final-inputs.json")
    # Three runtime inputs are new (log capture, probe v244, wifid hold v244) and three of v243's are gone;
    # the console capture and the Settings diagnostic are unchanged.
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
        assert needle not in (root / "runtime" / probe_name).read_bytes(), needle
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
    # the lines with a keyword are passed on and nothing is left on stderr; under the name Freeform the
    # indicator lines and the last lines before the abort are passed on and the process dies with SIGABRT;
    # under another name nothing is captured.
    host = subprocess.run(["/bin/bash", str(root / "prepare/logcap/hosttest/run.sh")], check=True, capture_output=True, text=True)
    lines = [line for line in host.stdout.splitlines()]
    at = {name: next(i for i, line in enumerate(lines) if line.startswith("== " + name + ":")) for name in ("SpringBoard", "Freeform", "Other")}
    assert "exit status 0; left on stderr: 0 bytes" in lines[at["SpringBoard"]], lines[at["SpringBoard"]]
    assert "exit status 134; left on stderr: 0 bytes" in lines[at["Freeform"]], lines[at["Freeform"]]
    assert "left on stderr: 251 bytes" in lines[at["Other"]], lines[at["Other"]]
    passed = [line.strip() for line in lines[at["SpringBoard"] + 1:at["Freeform"]]]
    assert passed == [
        "Pong Probe: log v244 SpringBoard: WallpaperKit: Looking for default wallpaper based on deviceProductType 'iPhone17,1'",
        "Pong Probe: log v244 SpringBoard: PBFPoster something about a poster",
        "Pong Probe: log v244 SpringBoard: a battery percent line",
        "Pong Probe: log v244 SpringBoard: WKDefaultWallpaperManager: no lookup file"], passed
    passed = [line.strip() for line in lines[at["Freeform"] + 1:at["Other"]]]
    assert len(passed) == 11 and passed[0] == "Pong Probe: log v244 Freeform: Metal device created", passed
    assert passed[2].startswith("Pong Probe: log v244 Freeform: before the crash: noise: a line about nothing") and \
        passed[-1].endswith("before the crash: *** Terminating app due to uncaught exception 'NSInvalidArgumentException', reason: 'test'"), passed
    subprocess.run([sys.executable, "-IB", str(root / "prepare/settings/replay.py")], check=True, stdout=subprocess.DEVNULL)
    # The start-up qsort frames are v231's (the boot-stack budget).
    old_qsort = prologue_frame(bytes_at(old[kernel_member], symbol_address(base / "kernel/kernel-ipados18-thor.macho", "_qsort"), 64))
    new_qsort = prologue_frame(bytes_at(kernel.read_bytes(), symbol_address(kernel, "_qsort"), 64))
    assert old_qsort == new_qsort <= 0x100, (old_qsort, new_qsort)
    manifest.update(version=244, release_variant=release, purpose=title, candidate_parent=id_,
                    candidate_change=changes, kernel_source=str(kernel), kernel_sha256=digest(kernel),
                    boot_args=args, graphics_runtime_active=True, gpu_private_boot=False,
                    hardware_validated=False, flash_performed=False, fastboot_boot_performed=False,
                    kernel_change="THOR V244: the wallpaper catalog library is not injected any more; log capture library v244 "
                                  "in SpringBoard, backboardd, PosterBoard, CollectionsPoster, PowerUIAgent, Freeform and kbd (os_log "
                                  "mirrored at the source and passed on through the diagnostic channel); wifid hold library v244 "
                                  "(os_log of wifid kept for the life of the process) and probe v244 (wifid's threads sampled); "
                                  "carried: the v243 firmware LUN check, the v242 retries and record, v239 marker and en0 "
                                  "listing, v238 slot fix and About symbols, v237 changes, v236 GPU hook, v235 witness",
                    adt_sha256=sha(dt))
    manifest["config_change"] = dict(manifest["config_change"], bootargs_changed=False,
                                     menu_title_changed=True, chosen_changed=False,
                                     device_tree_changed=("unchanged from v239 (root node wlan: launchd runs com.apple.wifid only when "
                                                          "the device tree names wlan or marconi-wifi)" if wlan else
                                                          "v238's again: no wlan node, so launchd never runs com.apple.wifid"),
                                     wifid_start=("launchd starts wifid at boot; a library in it waits until configd has published the "
                                                  "marker and listed en0 and a power source is on the registry (or 150 s), then wifid runs; "
                                                  "killed and started again when it has not asked the driver within 60 s" if wlan else
                                                  "never: no wlan node in the device tree"))
    manifest["candidate_parent_kernel_uuid"] = parent["kernel_uuid"]
    manifest["diagnostic_delta"] = ("new: libpong-logcap-v244.dylib in SpringBoard, backboardd, PosterBoard, CollectionsPoster, "
                                    "PowerUIAgent, Freeform and kbd (lines PONG PROBE <process>[pid]: Pong Probe: log v244 ...); "
                                    "libpong-wifid-v244.dylib in wifid (/private/var/logs/PongWifid-v244.txt); libpong-probe-v244.dylib "
                                    "in PosterBoard, configd and wifid.  Gone: libpong-wallpaper-v243.dylib.  Unchanged: Settings "
                                    "diagnostic as v238; console capture as v232")
    manifest["witness"] = dict(manifest["witness"],
                               kernel_source="osfmk/kern/thor_witness_v235.h (v244 line), bsd/kern/kern_exec.c (staging and injection of "
                                             "the libraries, wifid start bookkeeping and watchdog, kern.pong_wifi_gate), bsd/kern/kern_exit.c "
                                             "(diagnostic channel, gate signal), device/drivers/NativeUfs/ModemFirmwareAccess.h (partition-table "
                                             "check: the primary decides, the retries, the radio driver's wait)",
                               patch="prepare/logcap/logcap244.c; prepare/logcap/trace_flags.h; prepare/wifid/wifid244.c; "
                                     "prepare/probe/probe244.c; prepare/settings/about238.patch (carried); kernel edits recorded in ANALYSIS.md",
                               version_line="THOR WITNESS V244: ...",
                               probe="/private/var/pong/libpong-probe-v244.dylib in PosterBoard, configd and wifid",
                               log_capture="/private/var/pong/libpong-logcap-v244.dylib in SpringBoard, backboardd, PosterBoard, "
                                           "CollectionsPoster, PowerUIAgent, Freeform and kbd",
                               wallpaper="not injected (v242 and v243 had libpong-wallpaper in SpringBoard: no catalog lookup happened "
                                         "while it was there)",
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
    # The v235 witness is carried unchanged (same messages, as often); the v243 wallpaper lines and the
    # libraries that were rebuilt under a new name are gone; the v244 lines are new; everything else v241,
    # v242 and v243 added is still there once.
    for needle in witness_strings:
        assert payload.count(needle) >= 1 and old_payload.count(needle) == payload.count(needle), needle
    assert payload.count(b"THOR WITNESS V235") >= 12 and old_payload.count(b"THOR WITNESS V235") == payload.count(b"THOR WITNESS V235")
    for needle in old_kernel_strings:
        assert payload.count(needle) == 0 and old_payload.count(needle) >= 1, needle
    for needle in new_kernel_strings:
        assert payload.count(needle) == 1 and old_payload.count(needle) == 0, needle
    for needle in carried_241_strings + carried_242_strings + carried_243_strings:
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
    # The device tree: with the wlan node (unified) or v238's (nowifi).
    assert ("/wlan" in api["parse_adt"](data[adt_member])) == wlan
    # The libraries of v243 that are gone were embedded exactly once in its kernel and are not in this one.
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
    return dict(version=244, variant=release.removeprefix("v244-"), embedded_release_id=release, image=target.name,
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
