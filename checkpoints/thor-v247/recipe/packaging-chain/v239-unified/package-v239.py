"""Package the v239 boot image from the v238 unified parent: wifid starts, About gets its answer.

The v238 console (2026-11-26 21:44 to 21:52) named the About hang with symbols and left the other
two items open:

  Settings -> About   main thread: GeneralSettingsUI (the pane's values) -> _SCNetworkInterfaceCopyAll
                      WithPreferences -> dispatch_sync -> SCDynamicStoreNotifyWait.  Apple's source
                      (configd, SystemConfiguration.fproj/SCNetworkInterface.c,
                      __wait_for_IOKit_to_quiesce) blocks that call until the dynamic store's
                      "Plugin:InterfaceNamer" holds *QUIET*, *COMPLETE* or *TIMEOUT*; the naming plugin
                      in configd sits in its run loop (its thread is idle from 97 s on) and never
                      writes one.  Upstream Pong's helper wifi-bootstrap publishes the marker.
  Wi-Fi               wifid never ran: its launchd job (com.apple.wifid.plist) carries
                      _LimitLoadToDeviceTree = [wlan, marconi-wifi], and the device tree of the image
                      has neither node; upstream removes the limit from the launchd cache of the root
                      image it assembles, which this port (booting Apple's sealed volumes) cannot.
  Wallpaper           PosterBoard starts, is idle in its run loop from 5 s on, never exits, and the
                      wallpaper store folder does not exist.  No cause yet.

v239 changes three things:

1. The device tree gains a root node "wlan" (name and a compatible string), so launchd loads the
   wifid job (RunAtLoad, KeepAlive).  wifid is the only job in the launchd cache limited to those
   names.
2. The probe library (now v239) is also injected into configd and runs the bootstrap there, a port
   of upstream's wifi-bootstrap: the interface-namer marker is published early and put back whenever
   the plugin's own updates drop it, en0 is listed in "State:/Network/Interface" and gets a network
   service in the preferences, and power is requested from the system's Wi-Fi manager.  The Wi-Fi
   daemons also get their output sent to the console.
3. Nothing else: the v238 panic record slot, the Settings hang dump with symbols, the v237 changes,
   the v236 GPU timing hook and the v235 witness are carried.  The command line is byte-identical
   to v238.

Four FAT members change: the prelinked kernel, the device tree, the manifest and the menu (title
only).  The compact image is the verified artifact, as for every version; the image that gets
flashed is the full one ("...-full.img": the layout of the v202 B image that was flashed before,
with the same members), see verify_full().
"""
import argparse
import json
import os
from pathlib import Path
import runpy
import shutil
import struct
import subprocess
import sys
import tempfile
import tomllib

root = Path(__file__).resolve().parent
base = root.parent / "v238-unified"
previous = runpy.run_path(str(root.parent / "v212-integrated/package-v212.py"))
decode, util, pack, sha, digest, kernel_member, manifest_member, menu_member, api = (
    previous[n] for n in ("decode", "util", "pack", "sha", "filehash", "k", "m", "c", "api")
)
adt_member = kernel_member + ".adt"
release = "v239-unified"
target = root / ("boot-thor-unixnu-" + release + ".img")
record = root / "unified-verification.json"
kernel = root / "kernel/kernel-ipados18-thor.macho"
full_target = root / ("boot-thor-unixnu-" + release + "-full.img")
console_name = "libpong-console-v205.dylib"
console_old = base / "runtime" / console_name
probe_name = "libpong-probe-v239.dylib"
probe_new = root / "runtime" / probe_name
console_new = root / "runtime" / console_name
settings_name = "libpong-settings-v185.dylib"
settings_old = base / "runtime" / settings_name
settings_new = root / "runtime" / settings_name
title = "Thor v239 - wifid starts, About answer"
changes = [
    "Settings -> About hangs in SCNetworkInterfaceCopyAll(), which Apple's source lets wait until the "
    "dynamic store's Plugin:InterfaceNamer holds an end marker that configd's naming plugin never writes on "
    "this port.  The probe library now runs in configd and publishes the marker (*QUIET* when IOKit got "
    "quiet within five seconds of en0, else *TIMEOUT*), early and again whenever the plugin drops it.",
    "Wi-Fi: wifid never ran because its launchd job is limited to a device tree named wlan or marconi-wifi.  "
    "The device tree gains a root node wlan.  The same library in configd lists en0 in the interface state, "
    "gives it a network service and asks the system Wi-Fi manager for power; wifid's output goes to the console.",
    "Wallpaper: PosterBoard is idle and never creates its store; unchanged in this version, the probe keeps "
    "reporting.",
    "Carried unchanged from v238: the panic record slot, the Settings hang dump with symbols, the probe in "
    "PosterBoard.  Carried from v237: protection classes A and B open for every process, IOSurface selector "
    "21, the modem server fix, the Wi-Fi join fixture (no private network configured).  No boot argument changed.",
]
carried_kernel_strings = (
    b"THOR BT V230: RUNNING mode=",
    b"THOR AUDIO V230: adsp_apps listener",
    b"THOR CPU V228: governor=load ceiling=request-only",
    b"THOR PAS1 V228: rails=",
    b"THOR SERVE V228: final=",
    b"PongWifi: Thor mode=",
    b"THOR WLAN V229: PCIe0 probes skipped",
    b"THOR WLAN V229: firmware file=",
    b"THOR FB POWER V229: request=",
    b"thor-bt-probe",
    b"thor-wifi\x00",                         # v237 adds the separate property name thor-wifi-join
    b"thor-panel-power",
    b"THOR WITNESS V233: console mirror thread started",
    b"KERNEL PANIC LOG",
    b"CONSOLE READER AND FRAMES STOPPED",
    b"cpu answered cross-call (1 yes, 0 no): ",
    b"THOR GPU V236: the VM timing hook keeps a copy of the ring clock",
    b"THOR FS V237: protection classes A and B open for every process",
    b"THOR SERVE V237: partition lookup",
    b"V237 association fixture",
    b"MiniIOSurface: set-ownership V237",
    b"PONG PROBE %s[%d]: %s",
)
# Messages only the witness prints; each must be new in this kernel.
# Messages of the v235 witness, carried unchanged: each present in both kernels, as often.
witness_strings = (
    b"THOR WITNESS V235: monitor started",
    b"THOR WITNESS V235: panic record slot",
    b"THOR WITNESS V235 PANIC EXTRAS",
    b"/private/var/logs/PongPanic-v235-",
    b"/private/var/logs/PongConsole-kernel-v235.txt",
    b"THOR WITNESS V235: console mirror ",
    b"frame records from the fault",
    b"frame records at the panic call",
    b"every block matches",
)
# This image's own lines: once in the new kernel, absent from v238's.
new_kernel_strings = (
    b"THOR WITNESS V239: the device tree names a wlan node",
    b"THOR PROBE V239: staged ",
    b"bootstrap namer %s: a marker is already there",
)
new_settings_strings = ()       # the Settings diagnostic is v238's, unchanged
BOOT_STACK = 0x4000      # INTSTACK_SIZE (4*4096): the stack arm_init() and kernel_bootstrap() run on
read = lambda path: json.loads(path.read_bytes())


def segments(data):
    ncmds = struct.unpack_from("<I", data, 16)[0]
    out, off = [], 32
    for _ in range(ncmds):
        cmd, size = struct.unpack_from("<II", data, off)
        if cmd == 0x19:
            vmaddr, vmsize, fileoff, filesize = struct.unpack_from("<QQQQ", data, off + 24)
            out.append((vmaddr, filesize, fileoff))
        off += size
    return out


def bytes_at(data, vmaddr, count):
    for va, filesize, fileoff in segments(data):
        if va <= vmaddr and vmaddr + count <= va + filesize:
            return data[fileoff + vmaddr - va:fileoff + vmaddr - va + count]
    raise ValueError("0x%x is not file-backed" % vmaddr)


def symbol_address(path, name):
    out = subprocess.check_output(["xcrun", "nm", "-arch", "arm64", str(path)], text=True)
    hits = [int(l.split()[0], 16) for l in out.splitlines() if l.split()[-1:] == [name]]
    assert len(hits) == 1, (name, hits)
    return hits[0]


def prologue_frame(code):
    """Stack bytes a function's prologue reserves: pre-indexed 64-bit stp on sp plus sub sp, sp, #imm."""
    total = 0
    for (w,) in struct.iter_unpack("<I", code):
        if w & 0xFFE003E0 == 0xA9A003E0:            # stp xt, xt2, [sp, #imm7]!  (pre-index, 64-bit)
            imm7 = (w >> 15) & 0x7F
            total += (0x80 - imm7 if imm7 & 0x40 else -imm7) * 8
        elif w & 0xFF8003FF == 0xD10003FF:          # sub sp, sp, #imm{,lsl #12}
            imm12, shift = (w >> 10) & 0xFFF, (w >> 22) & 1
            total += imm12 << (12 if shift else 0)
        elif w & 0xFFFFFC1F == 0xD50323BF:          # mov sp, ...
            break
        if w == 0xD65F03C0:                         # ret
            break
    return total


def configuration():
    parent = read(base / "unified-verification.json")
    old, fw, sig, id_ = decode(base / parent["image"])
    assert id_ == parent["image_sha256"] and parent["version"] == 238
    # The command line is byte-identical to v238; the menu title and the device tree change.
    inject_old, = [t for t in parent["boot_args"].split() if t.startswith("pong_exec_inject=")]
    assert "PosterBoard" not in inject_old and inject_old.endswith(",Preferences")
    menu, args = pack["new_menu"](old[menu_member], {"title": title, "replace_args": {}})
    manifest = json.loads(old[manifest_member])
    assert parent["boot_args"] == manifest["boot_args"]
    assert args == parent["boot_args"]
    assert "Preferences" in args and len(args.encode()) <= 1023
    for token in ("tmlow=1", "tmfull=1"):
        assert args.split().count(token) == 1                       # loader options, see v231
    tree = api["parse_adt"](old[adt_member])
    assert bytes(api["emit"](tree, "/")) == old[adt_member]
    # A root node named wlan: launchd runs com.apple.wifid only when the device tree names wlan or
    # marconi-wifi.  Name and a compatible string only, like the other top-level nodes.
    assert "/wlan" not in tree and not any(key.endswith("/marconi-wifi") for key in tree)
    tree["/wlan"] = {"name": b"wlan\0", "compatible": b"thor,pong-wlan\0"}
    dt = bytes(api["emit"](tree, "/"))
    assert api["parse_adt"](dt) == tree
    without = dict(tree)
    del without["/wlan"]
    assert bytes(api["emit"](without, "/")) == old[adt_member]      # nothing else differs
    current = read(root / "runtime/final-inputs.json")
    prior = read(base / "runtime/final-inputs.json")
    # One runtime input differs from v238: the probe.  The Settings diagnostic is unchanged.
    assert {k: v for k, v in current.items() if k != "payloads"} == {k: v for k, v in prior.items() if k != "payloads"}
    differing = {n for n in current["payloads"] if current["payloads"][n] != prior["payloads"].get(n)}
    assert differing == {probe_name}, differing
    spec = current["payloads"][console_name]
    blob = console_new.read_bytes()
    assert sha(blob) == spec["sha256"] == prior["payloads"][console_name]["sha256"]
    assert len(blob) == spec["bytes"] == prior["payloads"][console_name]["bytes"]
    subprocess.run(["codesign", "--verify", "--strict", str(console_new)], check=True)
    assert subprocess.check_output(["xcrun", "dwarfdump", "--uuid", str(console_new)], text=True).split()[1] == spec["uuid"]
    # The Settings diagnostic is the recorded one and is rebuilt from its sources.
    settings_spec = current["payloads"][settings_name]
    settings_blob = settings_new.read_bytes()
    assert sha(settings_blob) == settings_spec["sha256"] and len(settings_blob) == settings_spec["bytes"] <= 128 * 1024
    assert settings_blob == settings_old.read_bytes()
    subprocess.run(["codesign", "--verify", "--strict", str(settings_new)], check=True)
    assert subprocess.check_output(["xcrun", "dwarfdump", "--uuid", str(settings_new)], text=True).split()[1] == settings_spec["uuid"]
    for needle in new_settings_strings:
        assert settings_blob.count(needle) >= 1, needle
    assert (root / "prepare/settings/about237.patch").is_file() and (root / "prepare/settings/about238.patch").is_file()
    # The probe is the recorded one, signed, and rebuilt from its source.
    probe_spec = current["payloads"][probe_name]
    probe_blob = probe_new.read_bytes()
    assert sha(probe_blob) == probe_spec["sha256"] and len(probe_blob) == probe_spec["bytes"] <= 128 * 1024
    subprocess.run(["codesign", "--verify", "--strict", str(probe_new)], check=True)
    assert subprocess.check_output(["xcrun", "dwarfdump", "--uuid", str(probe_new)], text=True).split()[1] == probe_spec["uuid"]
    for needle in (b"Pong Probe: ", b"PosterBoard", b"airportd", b"wifid", b"com.apple.wallpaper",
                   b"configd", b"Plugin:InterfaceNamer", b"SCNetworkServiceCreate", b"State:/Network/Interface"):
        assert probe_blob.count(needle) >= 1, needle
    rebuilt = subprocess.run(["/bin/bash", str(root / "prepare/probe/build-probe.sh")], check=True, capture_output=True, text=True)
    assert probe_spec["sha256"] in rebuilt.stdout and probe_new.read_bytes() == probe_blob
    subprocess.run([sys.executable, "-IB", str(root / "prepare/settings/replay.py")], check=True, stdout=subprocess.DEVNULL)
    old_qsort = prologue_frame(bytes_at(old[kernel_member], symbol_address(base / "kernel/kernel-ipados18-thor.macho", "_qsort"), 64))
    new_qsort = prologue_frame(bytes_at(kernel.read_bytes(), symbol_address(kernel, "_qsort"), 64))
    assert old_qsort == new_qsort <= 0x100, (old_qsort, new_qsort)
    manifest.update(version=239, release_variant=release, purpose=title, candidate_parent=id_,
                    candidate_change=changes, kernel_source=str(kernel), kernel_sha256=digest(kernel),
                    boot_args=args, graphics_runtime_active=True, gpu_private_boot=False,
                    hardware_validated=False, flash_performed=False, fastboot_boot_performed=False,
                    kernel_change="THOR V239: device tree root node wlan (launchd starts wifid); probe v239 with the Wi-Fi bootstrap in configd; v238 slot fix and About symbols, v237 changes, v236 GPU hook and v235 witness carried", adt_sha256=sha(dt))
    # The v232 diagnostic fields (user-space console capture) describe the same library: unchanged.
    manifest["config_change"] = dict(manifest["config_change"], bootargs_changed=False,
                                     menu_title_changed=True, chosen_changed=False,
                                     device_tree_changed="root node wlan added: launchd runs com.apple.wifid only when the device tree names wlan or marconi-wifi")
    manifest["candidate_parent_kernel_uuid"] = parent["kernel_uuid"]
    manifest["diagnostic_delta"] = ("probe libpong-probe-v239.dylib: sampling in PosterBoard and wifid, the Wi-Fi "
                                    "bootstrap (interface-namer marker, en0 service, power request) in configd; "
                                    "Settings diagnostic as v238; console capture unchanged from v232")
    manifest["witness"] = dict(kernel_source="osfmk/kern/thor_witness_v235.h (v239 line), bsd/kern/kern_exec.c (probe staging and injection), bsd/kern/kern_exit.c (probe channel); carried: slot choice, kern_proc.c, MiniIOSurface.cpp",
                               patch="prepare/probe/probe239.c (probe and Wi-Fi bootstrap); prepare/settings/about238.patch (Settings diagnostic, carried); kernel source edits recorded in ANALYSIS.md",
                               console_mirror="/private/var/logs/PongConsole-kernel-v235.txt",
                               report_trigger="console unread for 3 s, or a touch with no frame for 3 s",
                               panic_screen="panic log, then panic extras (fault backtrace, panic backtrace, stack words, GPU trace), on the panel, 15 s per page",
                               panic_record="/private/var/logs/PongPanic-v235-N.bin (256 KiB, raw block writes; origin from the device stack, every block proven, window search as last resort)",
                               gpu_trace="armed by the kernel at start, read into the panic record",
                               version_line="THOR WITNESS V239: ...", probe="/private/var/pong/libpong-probe-v239.dylib in PosterBoard, wifid, airportd, configd")
    manifest["wifi_join"] = dict(configured=False, source="PONG_WIFI_TEST_CONFIG at build time (absent for this image)",
                                 switch="/chosen thor-wifi-join = 0")
    members = {kernel_member: kernel.read_bytes(), adt_member: dt,
               manifest_member: util["json_bytes"](manifest), menu_member: menu}
    changed = {kernel_member, adt_member, manifest_member, menu_member}
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
    # The v235 witness is carried unchanged (same messages, as often), and the v239 lines are new.
    for needle in witness_strings:
        assert payload.count(needle) >= 1 and old_payload.count(needle) == payload.count(needle), needle
    assert payload.count(b"THOR WITNESS V235") >= 12 and old_payload.count(b"THOR WITNESS V235") == payload.count(b"THOR WITNESS V235")
    for needle in new_kernel_strings:
        assert payload.count(needle) == 1 and old_payload.count(needle) == 0, needle
    assert payload.count(b"_pong_wifi_test_reconnect") == 1
    # The console capture the kernel stages is still v232's durable build.
    new_blob, old_blob = console_new.read_bytes(), console_old.read_bytes()
    assert new_blob == old_blob and payload.count(new_blob) == 1
    assert b"/private/var/thor-console-v232" in payload
    assert new_blob.count(b"Pong console capture V232 durable=fullfsync") == 1 and b"_fcntl" in new_blob
    # The Settings diagnostic: v238's library, exactly once.  The device tree: the wlan node, nothing else.
    settings, settings_before = settings_new.read_bytes(), settings_old.read_bytes()
    assert settings == settings_before and payload.count(settings) == 1 and old_payload.count(settings_before) == 1
    tree = api["parse_adt"](data[adt_member])
    assert list(tree["/wlan"]) == ["name", "compatible"] and tree["/wlan"]["name"] == b"wlan\0"
    del tree["/wlan"]
    assert bytes(api["emit"](tree, "/")) == old[adt_member]
    for needle in new_settings_strings:
        assert settings.count(needle) >= 1 and settings_before.count(needle) == 0, needle
    # The probe v239: embedded exactly once, absent from v238.
    probe = probe_new.read_bytes()
    assert payload.count(probe) == 1 and old_payload.count(probe) == 0
    assert payload.count(b"/private/var/pong/libpong-probe-v239.dylib") == 1 and old_payload.count(b"/private/var/pong/libpong-probe-v239.dylib") == 0
    assert b"/private/var/pong/libpong-settings-v225.dylib" in payload and old_payload.count(b"/private/var/pong/libpong-settings-v225.dylib") == 1
    # Every embedded library is present exactly once and is the one recorded for this version.
    for name, was in parent["runtime"].items():
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
    return dict(version=239, variant="unified", embedded_release_id=release, image=target.name,
                image_bytes=path.stat().st_size, image_sha256=image_sha, kernel_uuid=uuids.pop(),
                kernel_sha256=sha(payload), parent_sha256=parent["image_sha256"], parent_kernel_uuid=parent["kernel_uuid"],
                adt_sha256=sha(data[adt_member]), chosen_selectors=manifest["config_change"]["chosen_selectors"],
                boot_args=manifest["boot_args"],
                boot_argument_bytes=len(manifest["boot_args"].encode()), qsort_frame_bytes=qsort_frame,
                boot_stack_bytes=BOOT_STACK, console_sha256=spec["sha256"], console_uuid=spec["uuid"],
                console_stage_path=current["diagnostic_stage_path"], pool_path=parent["pool_path"],
                diagnostic_path=current["diagnostic_stage_path"],
                runtime={name: dict(current["payloads"].get(name, was)) for name, was in parent["runtime"].items()},
                witness=manifest["witness"], wifi_join=manifest["wifi_join"], probe=manifest["witness"]["probe"],
                firmware_sha256=firmware, signature_sha256=signature, filesystem_check=0,
                changed_fat_members=sorted(changed), candidate_change=changes, hardware_validated=False)


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


flash_images = []


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
    assert __debug__
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("package", "verify", "fullverify"))
    action = parser.parse_args().action
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
