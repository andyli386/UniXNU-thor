"""Package the v241 boot images from the v240 parent.

v239 was the first image flashed to boot_a.  It fixed Settings -> General -> About, but the status
bar lost its battery number and the Wi-Fi pane of Settings stayed blank: wifid had been running
from the first second.  v240 was built to hold wifid back, and was never flashed.

What was found for v241 (read from the 22H374 frameworks of this restore, no phone needed):

- Wallpaper.  WallpaperKit opens /Library/Wallpaper/DefaultWallpapers~<device name>.plist and
  Collections~<device name>.plist, the name being MobileGestalt's GSDeviceName, and drops every
  bundle whose logicalScreenClass differs from "<w>w-<h>h@<scale>x~<DeviceClass>".  This port
  shows the identity of an iPhone (iPhone17,1) on a restore that carries only the iPad catalogs.
  With no lookup file the framework falls back to a hard-coded iPhone collection that is not
  here either, and ends in a bundle it calls "Solid Black".  That is the missing wallpaper.
  WallpaperKit is linked by SpringBoard (through SpringBoard.framework, the home screen), by
  chronod (through CoverSheet, the lock screen) and by the CollectionsPoster extension that
  renders the stock collections.  A small library now goes into these three and into
  PosterBoard: WallpaperKit's own questions get the answer "iPad" and the two getters that skip
  the screen-class check answer yes.  Nothing else is changed in a process.

- Wi-Fi.  A job that launchd has loaded keeps its service ports reserved: clients that write to
  them wait until wifid checks in.  So wifid must run and answer, or the wlan node must be
  absent.  Both are packaged from this one kernel:
    v241-unified   the device tree of v238 (no wlan node): launchd never starts wifid, Wi-Fi
                   lookups fail at once, as in v236-v238.
    v241-wifi      the device tree of v239 (wlan node): launchd starts wifid; a small library in
                   wifid waits until configd has published the interface-namer marker, listed
                   en0 in the dynamic store and a power source is on the registry (or 150 seconds
                   have gone by), then lets it run;
                   a wifid that has not asked the driver for anything 60 seconds later is killed
                   and started again, and its own log is kept in /private/var/logs/PongWifid-v241.txt.
                   Experimental: nothing of this is verified on the phone.

Carried unchanged: Settings -> About (the marker in configd), the panic record slot (v238), the
Settings hang dump with symbols (v238), protection classes A and B and IOSurface selector 21
(v237), the v236 GPU timing hook, the v235 witness.  No boot argument changed.

The compact image is the verified artifact and the one that gets flashed (as v239 was).  The
"full" image has the layout of the v202 B image (the TWRP dd method), see verify_full().
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
base = root.parent / "v240-unified"
previous = runpy.run_path(str(root.parent / "v212-integrated/package-v212.py"))
decode, util, pack, sha, digest, kernel_member, manifest_member, menu_member, api = (
    previous[n] for n in ("decode", "util", "pack", "sha", "filehash", "k", "m", "c", "api")
)
v240 = runpy.run_path(str(base / "package-v240.py"))          # pure helpers and the carried string lists
adt_member = kernel_member + ".adt"
BOOT_STACK = v240["BOOT_STACK"]
carried_kernel_strings = v240["carried_kernel_strings"]
witness_strings = v240["witness_strings"]
prologue_frame, symbol_address, bytes_at = v240["prologue_frame"], v240["symbol_address"], v240["bytes_at"]
read = lambda path: json.loads(path.read_bytes())

VARIANTS = {
    # name: the device tree decides whether launchd starts wifid at all
    "unified": dict(release="v241-unified", wlan=False, record="unified-verification.json",
                    title="Thor v241 - stock wallpaper"),
    "wifi": dict(release="v241-wifi", wlan=True, record="wifi-verification.json",
                 title="Thor v241 wifi - daemon held back"),
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
wallpaper_name = "libpong-wallpaper-v241.dylib"
wifid_name = "libpong-wifid-v241.dylib"
new_libraries = {
    wallpaper_name: ("prepare/wallpaper/build-wallpaper.sh", b"Pong Probe: wallpaper v241: "),
    wifid_name: ("prepare/wifid/build-wifid.sh", b"Pong Probe: wifid hold v241: "),
}
# The probe differs from v240's as well: configd no longer creates a network service or asks for power.
gone_runtime = {"libpong-probe-v240.dylib"}

# Messages only this kernel prints: each present once, absent from v240's.
new_kernel_strings = (
    b"THOR WITNESS V241: the stock iPad wallpaper catalogs",
    b"THOR WALLPAPER V241: staged ",
    b"THOR WALLPAPER V241: %s catalog library armed",
    b"THOR WIFI V241: staged ",
    b"THOR WIFI V241: hold library armed",
    b"THOR WIFI V241: wifid may run: ",
    b"THOR WIFI V241: wifid starts (pid",
    b"THOR WIFI V241: wifid asked the driver for something",
    b"THOR WIFI V241: wifid (pid %d) has not asked the driver",
    b"THOR WIFI V241: wifid runs once configd",
    b"THOR PROBE V241: staged ",
    b"THOR PROBE V241: %s probe armed",
    b"/private/var/pong/libpong-wallpaper-v241.dylib",
    b"/private/var/pong/libpong-wifid-v241.dylib",
    b"/private/var/pong/libpong-probe-v241.dylib",
)
# Messages and paths of v240 that are gone.
old_kernel_strings = (b"THOR WITNESS V240", b"THOR WIFI V240", b"THOR PROBE V240",
                      b"/private/var/pong/libpong-probe-v240.dylib")

changes = [
    "Wallpaper: WallpaperKit of this restore finds no catalog for the identity this port shows (an "
    "iPhone on a restore that carries only the iPad catalogs) and ends every request in its "
    "'Solid Black' bundle.  A small library in SpringBoard, chronod, PosterBoard and "
    "CollectionsPoster (the processes that link WallpaperKit) answers WallpaperKit's own "
    "MobileGestalt questions with the iPad and lets the iPad bundles pass the screen-class check.  "
    "No file and no preference changes.",
    "Wi-Fi daemon: the kernel no longer delays or refuses wifid's exec.  Where the device tree names "
    "a wlan node (the v241-wifi image) launchd starts wifid as before, a small library in it waits "
    "until configd has published the interface-namer marker, listed en0 in the dynamic store and a "
    "power source is on the registry (or 150 seconds have gone by), and a wifid that has not asked the "
    "driver for anything 60 seconds after it may run is killed and started again.  Its own log is kept.  "
    "The probe in configd now only publishes those two dynamic-store values and says when wifid may run: "
    "it no longer creates a network service from inside configd (that took the preferences' lock within "
    "the process serving it) and no longer asks the Wi-Fi manager for power (configd lacks the "
    "entitlement, and the legacy MobileWiFi port is not declared by wifid's job).",
    "Two images from one kernel: v241-unified has the device tree of v238 (no wlan node, launchd "
    "never starts wifid, Wi-Fi lookups fail at once as in v236-v238); v241-wifi has the device tree "
    "of v239 (wlan node) and is the image to try the Wi-Fi daemon with.",
    "Carried unchanged: Settings -> About (the interface-namer marker in configd), the panic record "
    "slot (v238), the Settings hang dump with symbols (v238), protection classes A and B open for "
    "every process and IOSurface selector 21 (v237), the v236 GPU timing hook, the v235 witness.  "
    "No boot argument changed.",
]


def configuration():
    parent = read(base / "unified-verification.json")
    old, fw, sig, id_ = decode(base / parent["image"])
    assert id_ == parent["image_sha256"] and parent["version"] == 240
    # The command line is byte-identical to v239 and v240; only the menu title changes.
    inject_old, = [t for t in parent["boot_args"].split() if t.startswith("pong_exec_inject=")]
    assert "PosterBoard" not in inject_old and inject_old.endswith(",Preferences")
    menu, args = pack["new_menu"](old[menu_member], {"title": title, "replace_args": {}})
    manifest = json.loads(old[manifest_member])
    assert parent["boot_args"] == manifest["boot_args"]
    assert args == parent["boot_args"]
    assert "Preferences" in args and len(args.encode()) <= 1023
    for token in ("tmlow=1", "tmfull=1"):
        assert args.split().count(token) == 1                       # loader options, see v231
    # The device tree: v239's (with the wlan node) or v238's (without), nothing else.
    tree = api["parse_adt"](old[adt_member])
    assert bytes(api["emit"](tree, "/")) == old[adt_member]
    assert list(tree["/wlan"]) == ["name", "compatible"] and tree["/wlan"]["name"] == b"wlan\0"
    if wlan:
        dt = old[adt_member]
    else:
        v238 = read(root.parent / "v238-unified/unified-verification.json")
        old238, _, _, id238 = decode(root.parent / "v238-unified" / v238["image"])
        assert id238 == v238["image_sha256"] and v238["version"] == 238
        del tree["/wlan"]
        dt = bytes(api["emit"](tree, "/"))
        assert dt == old238[adt_member], "without the wlan node the tree is v238's"
    current = read(root / "runtime/final-inputs.json")
    prior = read(base / "runtime/final-inputs.json")
    # Three runtime inputs are new (the wallpaper catalog, the wifid hold and the v241 probe libraries)
    # and the v240 probe is gone; nothing else differs.
    assert {k: v for k, v in current.items() if k != "payloads"} == {k: v for k, v in prior.items() if k != "payloads"}
    names = set(current["payloads"]) | set(prior["payloads"])
    differing = {n for n in names if current["payloads"].get(n) != prior["payloads"].get(n)}
    assert differing == set(new_libraries) | {probe_name} | gone_runtime, differing
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
    # The probe v241: the recorded one, signed, and rebuilt byte for byte from its source.  In configd it
    # publishes the marker and lists en0, and says when wifid may run; the service creation and the power
    # request of v240 are gone.
    probe_spec = current["payloads"][probe_name]
    probe_blob = probe_new.read_bytes()
    assert len(probe_blob) == probe_spec["bytes"] <= 128 * 1024
    subprocess.run(["codesign", "--verify", "--strict", str(probe_new)], check=True)
    assert subprocess.check_output(["xcrun", "dwarfdump", "--uuid", str(probe_new)], text=True).split()[1] == probe_spec["uuid"]
    for needle in (b"Pong Probe: ", b"PosterBoard", b"configd", b"com.apple.wallpaper",
                   b"Plugin:InterfaceNamer", b"State:/Network/Interface", b"wifi gate open", b"pong-wifi-marker"):
        assert probe_blob.count(needle) >= 1, needle
    for gone in (b"SCPreferences", b"WiFiManagerClient", b"pong-wifi-power", b"/dev/console"):
        assert gone not in probe_blob, gone
    rebuilt = subprocess.run(["/bin/bash", str(root / "prepare/probe/build-probe.sh")], check=True, capture_output=True, text=True)
    assert probe_spec["sha256"] in rebuilt.stdout and probe_new.read_bytes() == probe_blob
    subprocess.run([sys.executable, "-IB", str(root / "prepare/settings/replay.py")], check=True, stdout=subprocess.DEVNULL)
    # The two new libraries: the recorded ones, signed, and rebuilt byte for byte from their sources.
    for name, (script, needle) in new_libraries.items():
        spec = current["payloads"][name]
        blob = (root / "runtime" / name).read_bytes()
        assert sha(blob) == spec["sha256"] and len(blob) == spec["bytes"] <= 128 * 1024
        subprocess.run(["codesign", "--verify", "--strict", str(root / "runtime" / name)], check=True)
        assert subprocess.check_output(["xcrun", "dwarfdump", "--uuid", str(root / "runtime" / name)], text=True).split()[1] == spec["uuid"]
        assert blob.count(needle) == 1, (name, needle)
        rebuilt = subprocess.run(["/bin/bash", str(root / script)], check=True, capture_output=True, text=True)
        assert spec["sha256"] in rebuilt.stdout and (root / "runtime" / name).read_bytes() == blob, name
    # The wallpaper library's host test: WallpaperKit's own questions get the iPad, nobody else's do,
    # and the two getters answer yes only with the library.
    host = subprocess.run(["/bin/bash", str(root / "prepare/wallpaper/hosttest/run.sh")], check=True, capture_output=True, text=True).stdout
    lines = host.strip().splitlines()
    assert len(lines) == 4 and lines[0] == "== with the library" and lines[2] == "== without the library"
    assert lines[1].endswith("from WallpaperKit: GSDeviceName=iPad DeviceClass=iPad ProductType=iPhone17,1, "
                             "tail call=iPhone | ignore screen class=1 ignore product types=1"), lines[1]
    assert lines[3].endswith("from WallpaperKit: GSDeviceName=iPhone DeviceClass=iPhone ProductType=iPhone17,1, "
                             "tail call=iPhone | ignore screen class=0 ignore product types=0"), lines[3]
    # The start-up qsort frames are v231's (the boot-stack budget).
    old_qsort = prologue_frame(bytes_at(old[kernel_member], symbol_address(base / "kernel/kernel-ipados18-thor.macho", "_qsort"), 64))
    new_qsort = prologue_frame(bytes_at(kernel.read_bytes(), symbol_address(kernel, "_qsort"), 64))
    assert old_qsort == new_qsort <= 0x100, (old_qsort, new_qsort)
    manifest.update(version=241, release_variant=release, purpose=title, candidate_parent=id_,
                    candidate_change=changes, kernel_source=str(kernel), kernel_sha256=digest(kernel),
                    boot_args=args, graphics_runtime_active=True, gpu_private_boot=False,
                    hardware_validated=False, flash_performed=False, fastboot_boot_performed=False,
                    kernel_change="THOR V241: wallpaper catalog library staged and put into SpringBoard, chronod, PosterBoard, "
                                  "CollectionsPoster; probe v241; wifid hold library staged and put into wifid, kern.pong_wifi_gate, wifid start "
                                  "bookkeeping and 60-second watchdog, OS_ACTIVITY_DT_MODE for wifid; carried: v239 wlan node (wifi image only), "
                                  "v238 slot fix and About symbols, v237 changes, v236 GPU hook, v235 witness", adt_sha256=sha(dt))
    # The v232 diagnostic fields (user-space console capture) describe the same library: unchanged.
    manifest["config_change"] = dict(manifest["config_change"], bootargs_changed=False,
                                     menu_title_changed=True, chosen_changed=False,
                                     device_tree_changed=("unchanged from v239 (root node wlan: launchd runs com.apple.wifid only when "
                                                          "the device tree names wlan or marconi-wifi)" if wlan else
                                                          "v238's again: no wlan node, so launchd never runs com.apple.wifid"),
                                     wifid_start=("launchd starts wifid at boot; a library in it waits until configd has prepared en0 and a "
                                                  "power source is on the registry (or 150 s), then wifid runs; killed and started again when it "
                                                  "has not asked the driver within 60 s" if wlan else "never: no wlan node in the device tree"))
    manifest["candidate_parent_kernel_uuid"] = parent["kernel_uuid"]
    manifest["diagnostic_delta"] = ("new: libpong-wallpaper-v241.dylib in SpringBoard, chronod, PosterBoard and "
                                    "CollectionsPoster; libpong-wifid-v241.dylib in wifid (its log in "
                                    "/private/var/logs/PongWifid-v241.txt, 120 s per start); probe libpong-probe-v241.dylib in "
                                    "PosterBoard and configd (marker, en0 listed, gate signal; no service creation, no power request).  "
                                    "Unchanged: Settings diagnostic as v238; console capture unchanged from v232")
    manifest["witness"] = dict(manifest["witness"],
                               kernel_source="osfmk/kern/thor_witness_v235.h (v241 line), bsd/kern/kern_exec.c (staging and injection of the "
                                             "probe, the wallpaper catalog and the wifid hold libraries, wifid start bookkeeping and watchdog, "
                                             "kern.pong_wifi_gate), bsd/kern/kern_exit.c (diagnostic channel, gate signal)",
                               patch="prepare/probe/probe241.c (probe and the two dynamic-store values); prepare/wallpaper/wallpaper241.c; prepare/wifid/wifid241.c; "
                                     "prepare/settings/about238.patch (Settings diagnostic, carried); kernel source edits recorded in ANALYSIS.md",
                               version_line="THOR WITNESS V241: ...",
                               probe="/private/var/pong/libpong-probe-v241.dylib in PosterBoard and configd",
                               wallpaper="/private/var/pong/libpong-wallpaper-v241.dylib in SpringBoard, chronod, PosterBoard, "
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
    # The v235 witness is carried unchanged (same messages, as often); the v240 lines are gone; the v241 lines are new.
    for needle in witness_strings:
        assert payload.count(needle) >= 1 and old_payload.count(needle) == payload.count(needle), needle
    assert payload.count(b"THOR WITNESS V235") >= 12 and old_payload.count(b"THOR WITNESS V235") == payload.count(b"THOR WITNESS V235")
    for needle in old_kernel_strings:
        assert payload.count(needle) == 0 and old_payload.count(needle) >= 1, needle
    for needle in new_kernel_strings:
        assert payload.count(needle) == 1 and old_payload.count(needle) == 0, needle
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
    if wlan:
        assert data[adt_member] == old[adt_member]
    else:
        assert data[adt_member] != old[adt_member]
    # The probe v241: embedded exactly once; v240's probe and its path are gone.
    probe = probe_new.read_bytes()
    assert payload.count(probe) == 1 and old_payload.count(probe) == 0
    assert payload.count(b"/private/var/pong/libpong-probe-v240.dylib") == 0 and old_payload.count(b"/private/var/pong/libpong-probe-v240.dylib") == 1
    # Every embedded library is present exactly once and is the one recorded for this version.
    for name, was in current["payloads"].items():
        folder = root.parent / "v151-balanced-rows/runtime" if name == "thor-apps-install" else root / "runtime"
        recorded = current["payloads"].get(name, was)
        blob = (folder / name).read_bytes()
        assert sha(blob) == recorded["sha256"] and len(blob) == recorded["bytes"] and payload.count(blob) == 1, name
    uuids = {subprocess.check_output(["xcrun", "dwarfdump", "--uuid", str(root / "kernel" / f)], text=True).split()[1]
             for f in ("kernel-ipados18-thor.macho", "mach.development.pong", "mach.development.pong.unstripped")}
    assert len(uuids) == 1 and uuids != {parent["kernel_uuid"]}
    _, offset, size, _ = util["sections"](path)
    with tempfile.NamedTemporaryFile(dir=root, suffix=".fat") as tmp, path.open("rb") as image:
        image.seek(offset)
        tmp.write(image.read(size))
        tmp.flush()
        subprocess.run(["/sbin/fsck_msdos", "-n", tmp.name], check=True, stdout=subprocess.DEVNULL)
    return dict(version=241, variant=release.removeprefix("v241-"), embedded_release_id=release, image=target.name,
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


flash_images = []


def build(tmp):
    _, old, _, _, members, _, _, _, _ = configuration()
    assert digest(util["FULL"]) == util["PARENT_FULL_SHA"]
    full, compact = tmp / "full.img", tmp / "compact.img"
    shutil.copyfile(util["FULL"], full)
    epoch = 1795428802
    env = dict(os.environ, TZ="UTC", SOURCE_DATE_EPOCH=str(epoch))
    for index, (name, content) in enumerate(members.items()):
        file = tmp / str(index)
        file.write_bytes(content)
        os.utime(file, (epoch, epoch))
        subprocess.run(["mcopy", "-m", "-o", "-i", util["volume"](full), file, "::" + name], check=True, env=env)
    util["compact"](full, compact)
    # The image that will be flashed: the layout of the v202 B image that was flashed before (the
    # same firmware section, the same FAT volume size with its free space, the same signature) with
    # this version's members in it.
    if full_target.exists():
        full_target.unlink()
    os.link(full, full_target)
    flash_images.append(verify_full(full_target))
    return compact


def verify_full(path):
    """The flashable image: the layout of the image flashed before, this version's members, a sound file system."""
    _, old, firmware, signature, members, changed, _, _, _ = configuration()
    data, new_firmware, new_signature, _ = decode(path)
    assert (firmware, signature) == (new_firmware, new_signature)
    assert all(data[name] == content for name, content in members.items())
    assert {name for name in data if data[name] != old.get(name)} == changed
    assert path.stat().st_size == util["FULL"].stat().st_size
    layout = util["sections"](path)
    assert layout == util["sections"](util["FULL"]), "layout differs from the image flashed before"
    _, offset, ramdisk, _ = layout
    with tempfile.NamedTemporaryFile(dir=root, suffix=".fat") as tmp, path.open("rb") as image:
        image.seek(offset)
        tmp.write(image.read(ramdisk))
        tmp.flush()
        subprocess.run(["/sbin/fsck_msdos", "-n", tmp.name], check=True, stdout=subprocess.DEVNULL)
    return dict(image=path.name, image_bytes=path.stat().st_size, image_sha256=sha(path.read_bytes()),
                firmware_sha256=new_firmware, signature_sha256=new_signature,
                members_sha256={n: sha(c) for n, c in sorted(members.items())})


def main():
    global variant, release, wlan, title, target, record, full_target
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("package", "verify", "fullverify"))
    parser.add_argument("variant", nargs="?", choices=sorted(VARIANTS), default="unified")
    args = parser.parse_args()
    variant = VARIANTS[args.variant]
    release, wlan, title = variant["release"], variant["wlan"], variant["title"]
    target = root / ("boot-thor-unixnu-" + release + ".img")
    record = root / variant["record"]
    full_target = root / ("boot-thor-unixnu-" + release + "-full.img")
    action = args.action
    if action == "fullverify":
        print(util["json_bytes"](verify_full(full_target)))
        return
    if action == "verify" or action == "package" and target.exists():
        result = verify(target)
    else:
        with tempfile.TemporaryDirectory(dir=root, prefix=".package-") as folder:
            tmp = Path(folder)
            candidate = build(tmp)
            result = verify(candidate)
            result["flash_image"] = flash_images[-1]
            (tmp / "receipt").write_bytes(util["json_bytes"](result))
            os.link(candidate, target)
            os.replace(tmp / "receipt", record)
    if action == "verify":
        recorded = read(record)
        flash = recorded.pop("flash_image")
        assert result == recorded, "image differs from its receipt"
        assert verify_full(full_target) == flash, "the flash image differs from its receipt"
    print(util["json_bytes"](result).decode(), end="")


if __name__ == "__main__":
    main()
