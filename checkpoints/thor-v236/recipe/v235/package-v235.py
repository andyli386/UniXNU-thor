"""Package the v235 boot image from the v234 unified parent.

The only v234 run (2026-11-25 19:00-19:06) did panic, and its record was lost twice over:

  1. the panic record slot was never proven.  The kernel took the storage window's first byte as
     the origin of the file system's block numbers; a read-only check from TWRP showed the Data
     window holding a GPT whose APFS partition begins 1 MiB into it and the slot file at
     partition start + block * 4096.  So the raw read came from 1 MiB short of the file, the
     proof failed ("block 0 does not match its file (error 5)") and nothing was written at the panic;
  2. the GPU trace was read into a 24-byte object with a 19 KiB capacity and ran past it into
     unrelated kernel data (the scheduler's); the trace itself printed as zeros.

Of the panel only the page with the kernel-text words was photographed, and such a list cannot
tell a live frame from a stale one.  v235:

  1. the slot's origin comes from the device stack (window plus the media below the file
     system's device), with the bare window and the volume's own base as alternatives; each
     candidate must prove all 64 blocks; when none does, the window itself is searched for the
     blocks (each carries the slot number, its own number and a per-boot nonce), bounded by the
     window size and by 30 s.  The raw write is proven by a write-read-restore before a slot is
     accepted (bsd/kern/kern_exec.c, device/drivers/NativeUfs/PongUfsTransport.cpp);
  2. one buffer holds the GPU trace header and records (osfmk/kern/thor_witness_v235.h);
  3. the extras carry two real backtraces: the frame records from the faulting code's frame
     pointer, and the frame records read in PE_panic_hook, which lead to whoever called panic().
     The kernel-text words on the stack are still listed, now marked when on one of the chains.

Three FAT members change: the prelinked kernel, the manifest and the menu (title only; the
command line is byte-identical to v234).  The device tree, firmware, signature and every embedded
runtime library are byte-identical to v234.  No driver behaviour, no device tree property and no
boot argument changed: v235 only makes the next panic leave a complete record.
"""
import argparse
import json
import os
from pathlib import Path
import runpy
import shutil
import struct
import subprocess
import tempfile
import tomllib

root = Path(__file__).resolve().parent
base = root.parent / "v234-unified"
previous = runpy.run_path(str(root.parent / "v212-integrated/package-v212.py"))
decode, util, pack, sha, digest, kernel_member, manifest_member, menu_member, api = (
    previous[n] for n in ("decode", "util", "pack", "sha", "filehash", "k", "m", "c", "api")
)
adt_member = kernel_member + ".adt"
release = "v235-unified"
target = root / ("boot-thor-unixnu-" + release + ".img")
record = root / "unified-verification.json"
kernel = root / "kernel/kernel-ipados18-thor.macho"
console_name = "libpong-console-v205.dylib"
console_old = base / "runtime" / console_name
console_new = root / "runtime" / console_name
title = "Thor v235 - panic record to storage, fixed"
changes = [
    "Panic record to storage, fixed: the first v234 boot never proved its slot because the file "
    "system's block numbers count from the APFS partition 1 MiB into the storage window, not from "
    "the window (read-only check from TWRP).  The origin now comes from the device stack, the bare "
    "window and the volume's own base are alternatives, every candidate has to prove all 64 blocks, "
    "and when none does the window is searched for the blocks themselves (per-boot mark in every "
    "block; bounded by the window and by 30 s).  The raw write is proven by write-read-restore.",
    "GPU trace: header and records now share one buffer (v234 read 19 KiB past a 24-byte object "
    "into unrelated kernel data and printed zeros).",
    "Panic extras: two real backtraces, the frame records from the faulting code's frame pointer "
    "and the frame records read when the panic is announced (who called panic); kernel-text words "
    "on the stack are listed as before, marked when they are on one of the chains.",
    "Kept from v233/v234: the panic log on the panel, the kernel hang report with symbol names, "
    "the kernel console mirror, the faulting pc/lr with their translation, the user registers, "
    "the tail of the GPU trace.  No boot argument changed.",
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
    b"thor-wifi",
    b"thor-panel-power",
    b"THOR WITNESS V233: console mirror thread started",
    b"KERNEL PANIC LOG",
    b"CONSOLE READER AND FRAMES STOPPED",
    b"cpu answered cross-call (1 yes, 0 no): ",
)
# Messages only the witness prints; each must be new in this kernel.
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
    assert id_ == parent["image_sha256"] and parent["version"] == 234
    # The command line is byte-identical to v234; only the menu title changes.
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
    # No runtime input differs from v234: the same libraries are embedded again.
    assert current == prior, "runtime inputs changed"
    spec = current["payloads"][console_name]
    blob = console_new.read_bytes()
    assert sha(blob) == spec["sha256"] == prior["payloads"][console_name]["sha256"]
    assert len(blob) == spec["bytes"] == prior["payloads"][console_name]["bytes"]
    subprocess.run(["codesign", "--verify", "--strict", str(console_new)], check=True)
    assert subprocess.check_output(["xcrun", "dwarfdump", "--uuid", str(console_new)], text=True).split()[1] == spec["uuid"]
    assert (root / "prepare/witness.patch").is_file()
    old_qsort = prologue_frame(bytes_at(old[kernel_member], symbol_address(base / "kernel/kernel-ipados18-thor.macho", "_qsort"), 64))
    new_qsort = prologue_frame(bytes_at(kernel.read_bytes(), symbol_address(kernel, "_qsort"), 64))
    assert old_qsort == new_qsort <= 0x100, (old_qsort, new_qsort)
    manifest.update(version=235, release_variant=release, purpose=title, candidate_parent=id_,
                    candidate_change=changes, kernel_source=str(kernel), kernel_sha256=digest(kernel),
                    boot_args=args, graphics_runtime_active=True, gpu_private_boot=False,
                    hardware_validated=False, flash_performed=False, fastboot_boot_performed=False,
                    kernel_change="THOR WITNESS V235: slot origin fixed and proven, GPU trace buffer, fault and panic backtraces")
    # The v232 diagnostic fields (user-space console capture) describe the same library: unchanged.
    manifest["config_change"] = dict(manifest["config_change"], bootargs_changed=False,
                                     menu_title_changed=True)
    manifest["candidate_parent_kernel_uuid"] = parent["kernel_uuid"]
    manifest["diagnostic_delta"] = "unchanged from v232: F_FULLFSYNC, 250 ms after touches"
    manifest["witness"] = dict(kernel_source="osfmk/kern/thor_witness_v235.h", patch="prepare/witness.patch",
                               console_mirror="/private/var/logs/PongConsole-kernel-v235.txt",
                               report_trigger="console unread for 3 s, or a touch with no frame for 3 s",
                               panic_screen="panic log, then panic extras (fault backtrace, panic backtrace, stack words, GPU trace), on the panel, 15 s per page",
                               panic_record="/private/var/logs/PongPanic-v235-N.bin (256 KiB, raw block writes; origin from the device stack, every block proven, window search as last resort)",
                               gpu_trace="armed by the kernel at start, read into the panic record")
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
    for token in carried_kernel_strings:
        assert payload.count(token) == 1 and old[kernel_member].count(token) == 1, token
    for needle in (b"_pong_wifi_start_optional", b"_pong_thor_wifi_firmware", b"_pong_wifi_irq_due", b"thor_bt_probe"):
        assert needle in payload, needle
    # The witness is in: its messages and its mirror file, none of them in the parent.
    for needle in witness_strings:
        assert payload.count(needle) >= 1 and old[kernel_member].count(needle) == 0, needle
    assert payload.count(b"THOR WITNESS V235") >= 12 and old[kernel_member].count(b"THOR WITNESS V235") == 0
    # The console capture the kernel stages is still v232's durable build.
    new_blob, old_blob = console_new.read_bytes(), console_old.read_bytes()
    assert new_blob == old_blob and payload.count(new_blob) == 1
    assert b"/private/var/thor-console-v232" in payload
    assert new_blob.count(b"Pong console capture V232 durable=fullfsync") == 1 and b"_fcntl" in new_blob
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
    return dict(version=235, variant="unified", embedded_release_id=release, image=target.name,
                image_bytes=path.stat().st_size, image_sha256=image_sha, kernel_uuid=uuids.pop(),
                kernel_sha256=sha(payload), parent_sha256=parent["image_sha256"], parent_kernel_uuid=parent["kernel_uuid"],
                adt_sha256=sha(data[adt_member]), chosen_selectors=manifest["config_change"]["chosen_selectors"],
                boot_args=manifest["boot_args"],
                boot_argument_bytes=len(manifest["boot_args"].encode()), qsort_frame_bytes=qsort_frame,
                boot_stack_bytes=BOOT_STACK, console_sha256=spec["sha256"], console_uuid=spec["uuid"],
                console_stage_path=current["diagnostic_stage_path"], pool_path=parent["pool_path"],
                diagnostic_path=current["diagnostic_stage_path"],
                runtime={name: dict(current["payloads"].get(name, was)) for name, was in parent["runtime"].items()},
                witness=manifest["witness"],
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
