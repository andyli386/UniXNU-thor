"""Package the v237 boot image from the v236 unified parent: About, wallpaper, Wi-Fi join, modem.

Four changes, one image:

1. Settings -> General -> About hung on every launch (v234: three times in one boot, 0x8BADF00D).
   The watchdog report's thread "com.apple.SettingsApp.PrimaryListLoading" sat inside
   [[PSSystemConfigurationDynamicStoreEthernetWatcher sharedManager] hasEthernetNetworkInterfaces]
   (return address 0xba1c of the Settings binary, right after that objc_msgSend), and the main
   thread's PSGAboutDataSource _loadValues waited for the lock that thread holds.  The injected
   Settings diagnostic (libpong-settings-v185.dylib, source prepare/settings: the v225 base and
   patch plus about237.patch) now answers that question with NO for the process (the device has no
   Ethernet), and carries a main-queue watchdog that prints every thread with a frame in Settings
   or PreferencesUI, image name and offset, if About (or anything else) hangs again.

2. The wallpaper never appeared.  The port's data volume is not content protected (apfs mounts it
   "unencrypted"), and the prelinked apfs kext lets a process open or create a protection class A
   or B item only when proc_ignores_content_protection() says so:
   "apfs_is_valid_class:2452: disk5s2 rejecting class open (class N) because we're not content
   protected", 94 times in the v235 boot, 54 of them six seconds after PosterBoard started.  The
   one-line change is in the kernel source (bsd/kern/kern_proc.c): the answer is yes for every
   process.  apfs is the only prelinked component that imports that function.  PosterBoard's
   first surface call (30 ms after it started) was IOSurface selector 21, set-ownership, which the
   port's surface server refused as unsupported; it is now answered like selector 44, with a task
   port in place of the task identity token, and its first calls are logged with their values.

3. Wi-Fi never associated: the station code (authentication, association, the four-way handshake,
   key installation) is compiled in and was never started, because its only trigger is an ioctl
   that wifid does not send.  The driver now joins the private network of a local build-time file
   (PONG_WIFI_TEST_CONFIG -> PongWifiTestInputs.h) when one is configured; with no file nothing is
   joined and the console says so.  No credentials go into the image record, boot args or logs.

4. The modem trial booted the modem and stopped it without ever talking to it:
   "THOR SERVE V228: smem partition rc=-2".  The server looked up the APPS-to-modem shared-memory
   partition with the modem's QRTR node id (0) instead of its SMEM host id (1).  The trial is held
   to 24 s (was 60 s) because the audio processor and the battery service start after it.

The v235 witness and the v236 GPU timing hook are carried unchanged (labels still say V235 and
V236; the console mirror is PongConsole-kernel-v235.txt, a panic record goes to
PongPanic-v235-N.bin).  Three FAT members change: the prelinked kernel, the manifest and the menu
(title only; the command line is byte-identical to v236).  The device tree, firmware, signature and
every embedded library other than the Settings diagnostic are byte-identical to v236.
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
base = root.parent / "v236-unified"
previous = runpy.run_path(str(root.parent / "v212-integrated/package-v212.py"))
decode, util, pack, sha, digest, kernel_member, manifest_member, menu_member, api = (
    previous[n] for n in ("decode", "util", "pack", "sha", "filehash", "k", "m", "c", "api")
)
adt_member = kernel_member + ".adt"
release = "v237-unified"
target = root / ("boot-thor-unixnu-" + release + ".img")
record = root / "unified-verification.json"
kernel = root / "kernel/kernel-ipados18-thor.macho"
console_name = "libpong-console-v205.dylib"
console_old = base / "runtime" / console_name
console_new = root / "runtime" / console_name
settings_name = "libpong-settings-v185.dylib"
settings_old = base / "runtime" / settings_name
settings_new = root / "runtime" / settings_name
title = "Thor v237 - About, wallpaper, Wi-Fi join, modem"
changes = [
    "Settings -> General -> About hung on every launch: a Settings thread waited inside "
    "hasEthernetNetworkInterfaces (a synchronous call that is never answered) while the main thread "
    "waited for its lock.  The injected Settings diagnostic answers that question with NO for the "
    "process and, if anything hangs again, prints every thread with a frame in Settings or "
    "PreferencesUI as image and offset.",
    "Wallpaper: the data volume is not content protected, so the prelinked apfs kext refused to open "
    "protection class A and B items for every ordinary process (apfs_is_valid_class:2452, 94 times "
    "in one v235 boot).  proc_ignores_content_protection() now answers yes for every process; apfs "
    "is its only importer.  IOSurface selector 21 (set-ownership), refused as unsupported up to v236 "
    "and first called by PosterBoard 30 ms after it started, is answered like selector 44 with a "
    "task port in place of the task identity token; its first calls are logged.",
    "Wi-Fi: the driver joins the private network of a local build-time file when one is configured "
    "(a scanned BSS only, retries with a directed scan); with no file nothing is joined.  The "
    "station code (authentication, association, four-way handshake, keys) was already compiled in "
    "and had never been started.",
    "Modem: the trial's server looked up the shared-memory partition with the modem's QRTR node id "
    "instead of its SMEM host id (rc=-2 on every boot).  The trial is held to 24 s so the audio "
    "processor and the battery service do not wait longer.",
    "Kept unchanged: the v236 GPU timing hook, the panic record to storage, the fault and panic "
    "backtraces, the panic log on the panel, the kernel hang report, the kernel console mirror.  "
    "No boot argument and no device tree property changed.",
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
# v237's own lines: once in the new kernel, absent from v236's.
new_kernel_strings = (
    b"THOR WITNESS V237: data volume classes A and B open for every process",
    b"THOR FS V237: protection classes A and B open for every process",
    b"THOR SERVE V237: partition lookup",
    b"V237 association fixture",
    b"MiniIOSurface: set-ownership V237",
)
new_settings_strings = (
    b"Pong Settings: about237 ",
    b"PSSystemConfigurationDynamicStoreEthernetWatcher",
    b"hasEthernetNetworkInterfaces answers NO on this device",
)
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
    assert id_ == parent["image_sha256"] and parent["version"] == 236
    # The command line is byte-identical to v236; only the menu title changes.
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
    assert bytes(api["emit"](tree, "/")) == old[adt_member]        # the device tree is unchanged
    dt = old[adt_member]
    current = read(root / "runtime/final-inputs.json")
    prior = read(base / "runtime/final-inputs.json")
    # Exactly one runtime input differs from v236: the Settings diagnostic.
    assert {k: v for k, v in current.items() if k != "payloads"} == {k: v for k, v in prior.items() if k != "payloads"}
    assert current["payloads"].keys() == prior["payloads"].keys()
    differing = {n for n in current["payloads"] if current["payloads"][n] != prior["payloads"][n]}
    assert differing == {settings_name}, differing
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
    assert settings_blob != settings_old.read_bytes()
    subprocess.run(["codesign", "--verify", "--strict", str(settings_new)], check=True)
    assert subprocess.check_output(["xcrun", "dwarfdump", "--uuid", str(settings_new)], text=True).split()[1] == settings_spec["uuid"]
    for needle in new_settings_strings:
        assert settings_blob.count(needle) >= 1, needle
    assert (root / "prepare/settings/about237.patch").is_file() and (root / "prepare/settings/replay.py").is_file()
    subprocess.run([sys.executable, "-IB", str(root / "prepare/settings/replay.py")], check=True, stdout=subprocess.DEVNULL)
    old_qsort = prologue_frame(bytes_at(old[kernel_member], symbol_address(base / "kernel/kernel-ipados18-thor.macho", "_qsort"), 64))
    new_qsort = prologue_frame(bytes_at(kernel.read_bytes(), symbol_address(kernel, "_qsort"), 64))
    assert old_qsort == new_qsort <= 0x100, (old_qsort, new_qsort)
    manifest.update(version=237, release_variant=release, purpose=title, candidate_parent=id_,
                    candidate_change=changes, kernel_source=str(kernel), kernel_sha256=digest(kernel),
                    boot_args=args, graphics_runtime_active=True, gpu_private_boot=False,
                    hardware_validated=False, flash_performed=False, fastboot_boot_performed=False,
                    kernel_change="THOR V237: proc_ignores_content_protection answers yes for every process (apfs opens class A/B on the unencrypted data volume); IOSurface selector 21 answered; modem server SMEM host id; Wi-Fi join fixture; v236 GPU hook and v235 witness carried")
    # The v232 diagnostic fields (user-space console capture) describe the same library: unchanged.
    manifest["config_change"] = dict(manifest["config_change"], bootargs_changed=False,
                                     menu_title_changed=True, chosen_changed=False)
    manifest["candidate_parent_kernel_uuid"] = parent["kernel_uuid"]
    manifest["diagnostic_delta"] = ("Settings diagnostic v237: hasEthernetNetworkInterfaces answers NO; main-queue watchdog prints "
                                    "threads as image+offset after 3 s; console capture unchanged from v232")
    manifest["witness"] = dict(kernel_source="osfmk/kern/thor_witness_v235.h (v237 line added), bsd/kern/kern_proc.c, iokit/Kernel/MiniIOSurface.cpp",
                               patch="prepare/settings/about237.patch (Settings diagnostic); kernel source edits recorded in ANALYSIS.md",
                               console_mirror="/private/var/logs/PongConsole-kernel-v235.txt",
                               report_trigger="console unread for 3 s, or a touch with no frame for 3 s",
                               panic_screen="panic log, then panic extras (fault backtrace, panic backtrace, stack words, GPU trace), on the panel, 15 s per page",
                               panic_record="/private/var/logs/PongPanic-v235-N.bin (256 KiB, raw block writes; origin from the device stack, every block proven, window search as last resort)",
                               gpu_trace="armed by the kernel at start, read into the panic record",
                               version_line="THOR WITNESS V237: ...")
    manifest["wifi_join"] = dict(configured=False, source="PONG_WIFI_TEST_CONFIG at build time (absent for this image)",
                                 switch="/chosen thor-wifi-join = 0")
    members = {kernel_member: kernel.read_bytes(), adt_member: dt,
               manifest_member: util["json_bytes"](manifest), menu_member: menu}
    changed = {kernel_member, manifest_member, menu_member}
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
    # The v235 witness is carried unchanged (same messages, as often), and the v237 lines are new.
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
    # The Settings diagnostic: the new library exactly once, v236's not at all.
    settings, settings_before = settings_new.read_bytes(), settings_old.read_bytes()
    assert payload.count(settings) == 1 and payload.count(settings_before) == 0 and old_payload.count(settings_before) == 1
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
    return dict(version=237, variant="unified", embedded_release_id=release, image=target.name,
                image_bytes=path.stat().st_size, image_sha256=image_sha, kernel_uuid=uuids.pop(),
                kernel_sha256=sha(payload), parent_sha256=parent["image_sha256"], parent_kernel_uuid=parent["kernel_uuid"],
                adt_sha256=sha(data[adt_member]), chosen_selectors=manifest["config_change"]["chosen_selectors"],
                boot_args=manifest["boot_args"],
                boot_argument_bytes=len(manifest["boot_args"].encode()), qsort_frame_bytes=qsort_frame,
                boot_stack_bytes=BOOT_STACK, console_sha256=spec["sha256"], console_uuid=spec["uuid"],
                console_stage_path=current["diagnostic_stage_path"], pool_path=parent["pool_path"],
                diagnostic_path=current["diagnostic_stage_path"],
                runtime={name: dict(current["payloads"].get(name, was)) for name, was in parent["runtime"].items()},
                witness=manifest["witness"], wifi_join=manifest["wifi_join"],
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
    return compact


def main():
    assert __debug__
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("package", "verify"))
    action = parser.parse_args().action
    if action == "verify" or action == "package" and target.exists():
        result = verify(target)
    else:
        with tempfile.TemporaryDirectory(dir=root, prefix=".package-") as folder:
            tmp = Path(folder)
            candidate = build(tmp)
            result = verify(candidate)
            (tmp / "receipt").write_bytes(util["json_bytes"](result))
            os.link(candidate, target)
            os.replace(tmp / "receipt", record)
    if action == "verify":
        assert result == read(record), "image differs from its receipt"
    print(util["json_bytes"](result).decode(), end="")


if __name__ == "__main__":
    main()
