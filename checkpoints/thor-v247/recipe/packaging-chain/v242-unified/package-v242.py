"""Package the v242 boot images from the v241 parent: the wallpaper library loads again, and the
firmware partition table is checked with retries.

v241 (flashed 2026-11-27 09:21) never reached its UI: the wallpaper catalog library named
MobileGestalt by a path that does not exist on this system (/System/Library/PrivateFrameworks/
MobileGestalt.framework/MobileGestalt; the real install name is /usr/lib/libMobileGestalt.dylib),
so dyld could not load it and SpringBoard and chronod died at every start (20 and 21 times in the
boot's log).  v242 fixes the name; the build script and this script now compare every library the
injected libraries load with the names Apple's own binaries of this system use.

The same log (and v239's, taken before flashing v241) showed why v239 had no battery number and no
Wi-Fi: the partition table of the LUN that holds the modem and Wi-Fi firmware failed its check two
seconds into the boot ("THOR MODEM FW V163: state=gpt-unavailable status=5"), once for v239 and once
for v241, the first two boots from flash.  That check ran once per boot; when it failed the Wi-Fi
firmware could not be read (no en0), the modem firmware chain and with it the audio processor that
publishes the battery never started.  It had nothing to do with wifid.  v242 retries the check with
growing pauses, records the failing step and the device's answer, and lets the radio driver wait for
the table instead of giving up.

Carried unchanged: the probe (the interface-namer marker and the en0 listing in configd, the
passive samples in PosterBoard), Settings -> About, the panic record slot (v238), the Settings hang
dump with symbols (v238), protection classes A and B and IOSurface selector 21 (v237), the v236 GPU
timing hook, the v235 witness.  No boot argument changed.

The compact image is the verified artifact and the one that gets flashed.
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
base = root.parent / "v241-unified"
previous = runpy.run_path(str(root.parent / "v212-integrated/package-v212.py"))
decode, util, pack, sha, digest, kernel_member, manifest_member, menu_member, api = (
    previous[n] for n in ("decode", "util", "pack", "sha", "filehash", "k", "m", "c", "api")
)
v240 = runpy.run_path(str(root.parent / "v240-unified/package-v240.py"))          # helpers and the carried string lists
v241 = runpy.run_path(str(base / "package-v241.py"))                            # the lists of the parent
adt_member = kernel_member + ".adt"
BOOT_STACK = v240["BOOT_STACK"]
carried_kernel_strings = v240["carried_kernel_strings"]
witness_strings = v240["witness_strings"]
prologue_frame, symbol_address, bytes_at = v240["prologue_frame"], v240["symbol_address"], v240["bytes_at"]
read = lambda path: json.loads(path.read_bytes())

VARIANTS = {
    # name: the device tree decides whether launchd starts wifid at all
    "unified": dict(release="v242-unified", wlan=False, record="unified-verification.json",
                    title="Thor v242 - wallpaper library fixed"),
    "wifi": dict(release="v242-wifi", wlan=True, record="wifi-verification.json",
                 title="Thor v242 wifi - daemon held back"),
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
probe_name = "libpong-probe-v241.dylib"
probe_new = root / "runtime" / probe_name
wifid_name = "libpong-wifid-v241.dylib"
wallpaper_name = "libpong-wallpaper-v242.dylib"
new_libraries = {
    wallpaper_name: ("prepare/wallpaper/build-wallpaper.sh", b"Pong Probe: wallpaper v242: "),
}
carried_libraries = {
    probe_name: ("prepare/probe/build-probe.sh", b"Pong Probe: ", b"Plugin:InterfaceNamer",
                 b"State:/Network/Interface", b"wifi gate open"),
    wifid_name: ("prepare/wifid/build-wifid.sh", b"Pong Probe: wifid hold v241: "),
}
# The wallpaper library of v241 is gone (it could not load); nothing else differs from v241.
gone_runtime = {"libpong-wallpaper-v241.dylib"}

# Messages only this kernel prints: each present once, absent from v241's.
new_kernel_strings = (
    b"THOR WITNESS V242: the wallpaper catalog library loads again",
    b"THOR WALLPAPER V242: staged ",
    b"THOR WALLPAPER V242: %s catalog library armed",
    b"/private/var/pong/libpong-wallpaper-v242.dylib",
    b"THOR MODEM FW V242: preflight attempt",
    b"THOR MODEM FW V242: state=gpt-unavailable status=%d after",
    b"THOR MODEM FW V242: after three failed checks",
    b"THOR MODEM FW V242: the partition table answered",
    b"THOR WLAN V242: the partition table of the firmware LUN answered",
)
# Messages and paths of v241 that are gone.
old_kernel_strings = (b"THOR WITNESS V241", b"THOR WALLPAPER V241",
                      b"/private/var/pong/libpong-wallpaper-v241.dylib")
# The messages v241 added are all still there, once each, as in v241.
carried_241_strings = tuple(s for s in v241["new_kernel_strings"] if not any(s.startswith(g) for g in old_kernel_strings))

# Apple's own binaries of this system, read from the OS volume: every library the injected
# libraries load must be named as one of these names it.
apple_binaries = (
    "/sbin/launchd", "/usr/sbin/wifid", "/usr/libexec/configd",
    "/System/Library/CoreServices/SpringBoard.app/SpringBoard", "/Applications/PosterBoard.app/PosterBoard",
    "/System/Library/PrivateFrameworks/ChronoCore.framework/Support/chronod",
    "/System/Library/PrivateFrameworks/CoreDuetContext.framework/Resources/contextstored",
)

changes = [
    "The wallpaper catalog library loads again.  In v241 it named MobileGestalt by a path that does "
    "not exist on this system, so dyld could not load it and SpringBoard and chronod died at every "
    "start: v241 never reached its UI.  The real install name is /usr/lib/libMobileGestalt.dylib.",
    "The partition table of the LUN that holds the modem and Wi-Fi firmware is checked with retries: "
    "in v239 and v241 (the first two boots from flash) the single check two seconds into the boot "
    "failed, so the Wi-Fi firmware could not be read, the modem firmware chain never ran and the audio "
    "processor that publishes the battery never started.  A failed check is now retried with growing "
    "pauses, every failure records the step and the device's answer, and the radio driver waits for the "
    "table (at most 45 seconds) instead of giving up.",
    "Every library the injected libraries load is compared with the names Apple's own binaries use.",
    "Carried unchanged: the interface-namer marker and the en0 listing in configd (Settings -> About), "
    "the panic record slot (v238), the Settings hang dump with symbols (v238), protection classes A and B "
    "open for every process and IOSurface selector 21 (v237), the v236 GPU timing hook, the v235 "
    "witness.  No boot argument changed.",
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
    assert id_ == parent["image_sha256"] and parent["version"] == 241
    # The command line is byte-identical to v239, v240 and v241; only the menu title changes.
    inject_old, = [t for t in parent["boot_args"].split() if t.startswith("pong_exec_inject=")]
    assert "PosterBoard" not in inject_old and inject_old.endswith(",Preferences")
    menu, args = pack["new_menu"](old[menu_member], {"title": title, "replace_args": {}})
    manifest = json.loads(old[manifest_member])
    assert parent["boot_args"] == manifest["boot_args"]
    assert args == parent["boot_args"]
    assert "Preferences" in args and len(args.encode()) <= 1023
    for token in ("tmlow=1", "tmfull=1"):
        assert args.split().count(token) == 1                       # loader options, see v231
    # The device tree: v241 unified carries v238's (no wlan node) and is kept as it is; the wifi image
    # takes v239's (with the wlan node).  Nothing else differs.
    tree = api["parse_adt"](old[adt_member])
    assert bytes(api["emit"](tree, "/")) == old[adt_member] and "/wlan" not in tree
    if wlan:
        v239 = read(root.parent / "v239-unified/unified-verification.json")
        old239, _, _, id239 = decode(root.parent / "v239-unified" / v239["image"])
        assert id239 == v239["image_sha256"] and v239["version"] == 239
        tree239 = api["parse_adt"](old239[adt_member])
        assert list(tree239["/wlan"]) == ["name", "compatible"] and tree239["/wlan"]["name"] == b"wlan\0"
        del tree239["/wlan"]
        assert bytes(api["emit"](tree239, "/")) == old[adt_member], "v239's tree differs from v241's only by the wlan node"
        dt = old239[adt_member]
    else:
        dt = old[adt_member]
    current = read(root / "runtime/final-inputs.json")
    prior = read(base / "runtime/final-inputs.json")
    # One runtime input is new (the v242 wallpaper catalog library) and v241's is gone; nothing else differs.
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
    # The carried libraries are v241's, exactly, and are rebuilt byte for byte from their sources.
    for name, (script, *needles) in carried_libraries.items():
        spec = current["payloads"][name]
        blob = (root / "runtime" / name).read_bytes()
        assert sha(blob) == spec["sha256"] == prior["payloads"][name]["sha256"], name
        assert len(blob) == spec["bytes"] <= 128 * 1024
        for needle in needles:
            assert blob.count(needle) >= 1, (name, needle)
        rebuilt = subprocess.run(["/bin/bash", str(root / script)], check=True, capture_output=True, text=True)
        assert spec["sha256"] in rebuilt.stdout and (root / "runtime" / name).read_bytes() == blob, name
    for needle in (b"SCPreferences", b"WiFiManagerClient", b"pong-wifi-power", b"/dev/console"):
        assert needle not in (root / "runtime" / probe_name).read_bytes(), needle
    # The new library: the recorded one, signed, and rebuilt byte for byte from its source.
    for name, (script, needle) in new_libraries.items():
        spec = current["payloads"][name]
        blob = (root / "runtime" / name).read_bytes()
        assert sha(blob) == spec["sha256"] and len(blob) == spec["bytes"] <= 128 * 1024
        subprocess.run(["codesign", "--verify", "--strict", str(root / "runtime" / name)], check=True)
        assert subprocess.check_output(["xcrun", "dwarfdump", "--uuid", str(root / "runtime" / name)], text=True).split()[1] == spec["uuid"]
        assert blob.count(needle) == 1, (name, needle)
        rebuilt = subprocess.run(["/bin/bash", str(root / script)], check=True, capture_output=True, text=True)
        assert spec["sha256"] in rebuilt.stdout and (root / "runtime" / name).read_bytes() == blob, name
    # Every library the injected libraries load exists under that name in this system: it is the
    # install name of one that Apple's own binaries load.  (v241's wallpaper library failed this.)
    apple = apple_install_names()
    assert "/usr/lib/libMobileGestalt.dylib" in apple and "/usr/lib/libobjc.A.dylib" in apple
    for name in list(new_libraries) + list(carried_libraries) + [settings_name, console_name]:
        for dependency in library_dependencies(root / "runtime" / name):
            assert dependency.startswith("/pong/") or dependency in apple, (name, dependency)
            if "MobileGestalt" in dependency:
                assert dependency == "/usr/lib/libMobileGestalt.dylib", (name, dependency)
    wallpaper = (root / "runtime" / wallpaper_name).read_bytes()
    assert wallpaper.count(b"/usr/lib/libMobileGestalt.dylib\0") == 1
    # The wallpaper library's host test, linked the way the device links it: WallpaperKit's own
    # questions get the iPad, nobody else's do, and the two getters answer yes only with the library.
    host = subprocess.run(["/bin/bash", str(root / "prepare/wallpaper/hosttest/run.sh")], check=True, capture_output=True, text=True).stdout
    lines = host.strip().splitlines()
    assert len(lines) == 4 and lines[0] == "== with the library" and lines[2] == "== without the library"
    assert lines[1].endswith("from WallpaperKit: GSDeviceName=iPad DeviceClass=iPad ProductType=iPhone17,1, "
                             "tail call=iPhone | ignore screen class=1 ignore product types=1"), lines[1]
    assert lines[3].endswith("from WallpaperKit: GSDeviceName=iPhone DeviceClass=iPhone ProductType=iPhone17,1, "
                             "tail call=iPhone | ignore screen class=0 ignore product types=0"), lines[3]
    subprocess.run([sys.executable, "-IB", str(root / "prepare/settings/replay.py")], check=True, stdout=subprocess.DEVNULL)
    # The start-up qsort frames are v231's (the boot-stack budget).
    old_qsort = prologue_frame(bytes_at(old[kernel_member], symbol_address(base / "kernel/kernel-ipados18-thor.macho", "_qsort"), 64))
    new_qsort = prologue_frame(bytes_at(kernel.read_bytes(), symbol_address(kernel, "_qsort"), 64))
    assert old_qsort == new_qsort <= 0x100, (old_qsort, new_qsort)
    manifest.update(version=242, release_variant=release, purpose=title, candidate_parent=id_,
                    candidate_change=changes, kernel_source=str(kernel), kernel_sha256=digest(kernel),
                    boot_args=args, graphics_runtime_active=True, gpu_private_boot=False,
                    hardware_validated=False, flash_performed=False, fastboot_boot_performed=False,
                    kernel_change="THOR V242: wallpaper catalog library v242 (MobileGestalt by its real install name); the partition table "
                                  "of the firmware LUN is checked with retries, each failure recorded with its step and the device's "
                                  "answer, and the radio driver waits for it; carried: probe v241, wifid hold library v241, v239 marker "
                                  "and en0 listing, v238 slot fix and About symbols, v237 changes, v236 GPU hook, v235 witness",
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
    manifest["diagnostic_delta"] = ("changed: libpong-wallpaper-v242.dylib (same code as v241's, linked against "
                                    "/usr/lib/libMobileGestalt.dylib) in SpringBoard, chronod, PosterBoard, CollectionsPoster.  New kernel "
                                    "lines: THOR MODEM FW V242 (each failed partition-table check with step and device answer) and "
                                    "THOR WLAN V242.  Unchanged: probe libpong-probe-v241.dylib in PosterBoard and configd; wifid hold "
                                    "library v241; Settings diagnostic as v238; console capture as v232")
    manifest["witness"] = dict(manifest["witness"],
                               kernel_source="osfmk/kern/thor_witness_v235.h (v242 line), bsd/kern/kern_exec.c (staging and injection of "
                                             "the libraries, wifid start bookkeeping and watchdog, kern.pong_wifi_gate), bsd/kern/kern_exit.c "
                                             "(diagnostic channel, gate signal), device/drivers/NativeUfs/ModemFirmwareAccess.h (partition-table "
                                             "check: steps, retries, the radio driver's wait)",
                               patch="prepare/wallpaper/wallpaper242.c; prepare/wifid/wifid241.c; prepare/probe/probe241.c; "
                                     "prepare/settings/about238.patch (carried); kernel and driver edits recorded in ANALYSIS.md",
                               version_line="THOR WITNESS V242: ...",
                               probe="/private/var/pong/libpong-probe-v241.dylib in PosterBoard and configd",
                               wallpaper="/private/var/pong/libpong-wallpaper-v242.dylib in SpringBoard, chronod, PosterBoard, "
                                         "CollectionsPoster",
                               wifid_hold="/private/var/pong/libpong-wifid-v241.dylib in wifid (only where the device tree names wlan)")
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
    # The v235 witness is carried unchanged (same messages, as often); the v241 wallpaper and witness lines
    # are gone; the v242 lines are new; everything else v241 added is still there once.
    for needle in witness_strings:
        assert payload.count(needle) >= 1 and old_payload.count(needle) == payload.count(needle), needle
    assert payload.count(b"THOR WITNESS V235") >= 12 and old_payload.count(b"THOR WITNESS V235") == payload.count(b"THOR WITNESS V235")
    for needle in old_kernel_strings:
        assert payload.count(needle) == 0 and old_payload.count(needle) >= 1, needle
    for needle in new_kernel_strings:
        assert payload.count(needle) == 1 and old_payload.count(needle) == 0, needle
    for needle in carried_241_strings:
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
    # The device tree: with the wlan node (wifi) or v238's (unified).
    assert ("/wlan" in api["parse_adt"](data[adt_member])) == wlan
    # The carried libraries: embedded exactly once in both kernels.  v241's wallpaper library is gone.
    for name in carried_libraries:
        blob = (root / "runtime" / name).read_bytes()
        assert payload.count(blob) == 1 and old_payload.count(blob) == 1, name
    assert old_payload.count(b"/private/var/pong/libpong-wallpaper-v241.dylib") == 1
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
    return dict(version=242, variant=release.removeprefix("v242-"), embedded_release_id=release, image=target.name,
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
    """The flashable image of the full layout (only packaged on request)."""
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
