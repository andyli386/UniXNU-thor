#!/usr/bin/env python3
"""Build a compact v203 FAT-ramdisk and check the published bytes against v202 B."""
import argparse
import hashlib
import json
import os
import runpy
import shutil
import struct
import subprocess
import tempfile
import tomllib
from pathlib import Path

OUT = Path(__file__).resolve().parent
PARENT = OUT.parent / "v202-swipe-recovery"
FULL = PARENT / "boot-thor-unixnu-v202-b-software.img"
BASE = PARENT / "persistent-compact/boot-thor-unixnu-v202-b-software-compact.img"
IMAGE = None
RECEIPT = None
RECIPE = OUT / "release-inputs.json"
VARIANTS = OUT / "variants.json"
KERNEL = OUT / "kernel/kernel-ipados18-thor.macho"
pack = runpy.run_path(str(PARENT / "package-v202.py"))
fat = pack["FAT"]
check = pack["check"]
sha = pack["sha"]
sha_file = pack["sha_file"]
read = pack["read_member"]
PARENT_FULL_SHA = "68d90a081441d5a504d8583f09fbd29e1e94560cc602408d97d6199a618a056b"
PARENT_SHA = "2c9dfc9d5bb857c19fe26ed426d4e8b11f38ac27ddf3332738ff5ed027d5c8e2"
PAGE = 4096
SECTOR = 512


def json_bytes(data):
    return (json.dumps(data, indent=2, sort_keys=True) + "\n").encode()


def sections(image):
    with image.open("rb") as fp:
        header = fp.read(PAGE)
        check(header[:8] == b"ANDROID!" and struct.unpack_from("<I", header, 40)[0] == 4, "v4 boot header")
        firmware, ramdisk = struct.unpack_from("<II", header, 8)
        offset = PAGE + (firmware + PAGE - 1) // PAGE * PAGE
        front = bytearray(header + fp.read(offset - PAGE))
        check(len(front) == offset and ramdisk % PAGE == 0, "v4 section alignment")
        fp.seek(offset + ramdisk)
        signature = fp.read()
        check(len(signature) == struct.unpack_from("<I", header, 1580)[0] == PAGE, "v4 signature length")
        return front, offset, ramdisk, signature


def volume(image):
    return f"{image}@@{sections(image)[1]}"


def compact(full, target):
    prefix, offset, length, signature = sections(full)
    with full.open("rb") as src:
        src.seek(offset)
        body = bytearray(src.read(length))
    spc, nfat = body[13], body[16]
    bps, = struct.unpack_from("<H", body, 11)
    reserved, = struct.unpack_from("<H", body, 14)
    root_entries, = struct.unpack_from("<H", body, 17)
    fat_sectors, = struct.unpack_from("<H", body, 22)
    check(bps == SECTOR and nfat == 2 and body[54:62] == b"FAT16   ", "FAT16")
    first = reserved + nfat * fat_sectors + (root_entries * 32 + SECTOR - 1) // SECTOR
    tb = reserved * SECTOR
    size = fat_sectors * SECTOR
    entries = [v for (v,) in struct.iter_unpack("<H", body[tb:tb + size])]
    check(body[tb:tb + size] == body[tb + size:tb + 2 * size], "different FAT copies")
    count = (length // SECTOR - first) // spc
    check(all(v == 0 for v in entries[count + 2:]), "garbage beyond FAT extent")
    highest = max(i for i in range(2, count + 2) if entries[i])
    check(not any(v and not (2 <= v <= highest or 0xfff8 <= v <= 0xffff) for v in entries[2:highest + 1]), "FAT cluster link")
    end = (first + (highest - 1) * spc) * SECTOR
    trimmed = (end + PAGE - 1) // PAGE * PAGE
    check(4085 <= (trimmed // SECTOR - first) // spc < 65525, "not FAT16 after trim")
    struct.pack_into("<I", prefix, 12, trimmed)
    struct.pack_into("<I", body, 32, trimmed // SECTOR)
    with target.open("xb") as f:
        f.write(prefix)
        f.write(memoryview(body)[:trimmed])
        f.write(signature)
        f.flush()
        os.fsync(f.fileno())


def menu_and_manifest(recipe):
    src = volume(BASE)
    cfg = {"title": recipe["title"], "replace_args": recipe.get("replace_args", {}),
           "append_args": recipe.get("add_args", []), "remove_args": recipe.get("remove_args", [])}
    menu, boot_args = pack["new_menu"](read(src, "TOML"), cfg)
    manifest = json.loads(read(src, "MANIFEST"))
    manifest.update(version=203, purpose=recipe["title"], release_variant=recipe["variant"],
                    candidate_change=recipe["change"], candidate_parent=PARENT_SHA,
                    config_change={k: cfg[k] for k in ("replace_args","append_args","remove_args")},
                    boot_args=boot_args, kernel_source=str(KERNEL), kernel_sha256=sha_file(KERNEL),
                    hardware_validated=False, flash_performed=False, fastboot_boot_performed=False)
    rr = json.loads((OUT / "runtime/final-inputs.json").read_bytes())
    cname = Path(rr["diagnostic_stage_path"]).name
    manifest.update(diagnostic_stage_path=rr["diagnostic_stage_path"],
                    diagnostic_sha256=rr["payloads"][cname]["sha256"], diagnostic_uuid=rr["payloads"][cname]["uuid"])
    if recipe.get("diagnostic_delta"):
        manifest["diagnostic_delta"] = recipe["diagnostic_delta"]
    return menu, json_bytes(manifest)


def inspect_runtime(kernel, observed):
    prior = json.loads((PARENT / "BUILD-B.json").read_bytes())
    check(observed["pool_path"] == json.loads((PARENT / "runtime/final-inputs.json").read_bytes())["pool_path"], "unexpected graphics runtime pool")
    changed = Path(observed["diagnostic_stage_path"]).name
    check(changed in ("libpong-console-v203.dylib", "libpong-console-v202.dylib"), "unknown console build")
    datafile = dict(observed["payloads"])
    datafile["thor-apps-install"] = prior["runtime"]["thor-apps-install"]
    check(set(datafile) == (set(prior["runtime"]) - {"libpong-console-v202.dylib"}) | {changed}, "runtime inventory changed")
    result = {}
    for name, expected in datafile.items():
        path = (OUT.parent / "v151-balanced-rows/runtime" if name == "thor-apps-install" else OUT / "runtime") / name
        if name != changed:
            check(expected["sha256"] == prior["runtime"][name]["sha256"], "original runtime changed " + name)
        data = path.read_bytes()
        check(sha(data) == expected["sha256"] and len(data) == expected["bytes"], "invalid payload " + name)
        subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(path)], check=True)
        info = pack["PREV"]["PREV"]["MACHO"](data)
        check(info["uuid"] == expected["uuid"] and kernel.count(data) == 1, "wrong binary in kernel " + name)
        result[name] = dict(uuid=info["uuid"], sha256=sha(data), bytes=len(data), offset=kernel.index(data))
        if name == changed:
            check(info["install_name"] == "/pong/" + name and observed["diagnostic_stage_path"].encode()+bytes(1) in kernel, "unreachable diagnostic")
            if changed != "libpong-console-v202.dylib":
                check((PARENT / "runtime/libpong-console-v202.dylib").read_bytes() not in kernel, "stale v202 console embedded")
    for marker in (b"THOR PAS4 SMP2P: phase=", b"auth-gated icc=baseline-unknown", b"THOR SWAP HISTORY: requested=", b"overdue GPU frame retired"):
        check(marker in kernel, "missing inherited subsystem marker")
    return result


def verify(image, recipe):
    check(sha_file(BASE) == PARENT_SHA and sha_file(FULL) == PARENT_FULL_SHA, "published parent wrong")
    prefix, offset, length, sig = sections(image)
    baseline, base_off, base_len, base_sig = sections(BASE)
    prefix[12:16] = baseline[12:16]
    check(prefix == baseline and offset == base_off and sig == base_sig, "firmware/DTB/signature changed")
    check(length <= base_len + 1048576, "ramdisk exceeds tested compact range by more than 1 MiB")
    src, dst = volume(BASE), volume(image)
    names = fat["fat_paths"](src)
    check(names == fat["fat_paths"](dst), "unexpected FAT inventory")
    hashes = {name: fat["fat_digest"](dst, name) for name in sorted(names) if not name.endswith("/")}
    for name in hashes.keys() - {fat["KERNEL"], fat["TOML"], fat["MANIFEST"]}:
        check(hashes[name] == fat["fat_digest"](src, name), "changed FAT file " + name)
    menu, manifest = menu_and_manifest(recipe)
    check(read(dst, "TOML") == menu and read(dst, "MANIFEST") == manifest, "wrong shipped menu/manifest")
    pack["MENU"]["verify_abi"]()
    parsed = tomllib.loads(menu.decode())
    checks = pack["MENU"]["validate_menu"](parsed, json.loads(manifest))
    args = next(e["args"] for e in parsed["entry"] if e.get("type") == "xnu").split()
    common = {"fb_copy=0", "fb_hz=120"}
    metal = {"pong_gpu_scanout=1", "pong_metal=1", "pong_adreno_start=1"}
    if recipe["variant"] == "metal-cpu-present":
        check(common | metal <= set(args) and "pong_ui_software=1" not in args, "invalid Metal/CPU control")
    elif recipe["variant"] == "software":
        check(common | {"pong_ui_software=1"} <= set(args) and not metal.intersection(args), "invalid software configuration")
    else:
        raise ValueError("unrecognized compositor")
    check(all(args.count(key) == 1 for key in common), "duplicate core menu setting")
    check(tomllib.loads(menu.decode())["boot"] == {"timeout": 2, "default": "xnu"}, "unexpected boot default")
    kern = read(dst, "KERNEL")
    check(kern == KERNEL.read_bytes(), "wrong shipped kernel")
    runtime = inspect_runtime(kern, json.loads((OUT / "runtime/final-inputs.json").read_bytes()))
    source_dir = OUT / "build-tree/.cache/xnu-work"
    for name, info in recipe["sources"].items():
        check(sha_file(source_dir / name) == info["candidate_sha256"], "source mismatch " + name)
        check(sha_file(PARENT / "build-tree/.cache/xnu-work" / name) == info["parent_sha256"], "wrong source ancestor " + name)
    for file, digest in recipe["patches"].items():
        check(sha_file(OUT / file) == digest, "patch mismatch " + file)
    for marker in recipe.get("required_kernel_markers", []):
        check(marker.encode() in kern, "missing linked marker " + marker)
    targets = [name.encode() for name in recipe.get("linked_functions", [])]
    functions = pack["PREV"]["PREV"]["functions"]
    linked, previous = functions(kern, targets), functions(read(src, "KERNEL"), targets)
    for name in recipe.get("link_diff_required", []):
        check(linked[name] != previous[name], "unchanged implementation " + name)
    with tempfile.NamedTemporaryFile(dir=OUT, suffix=".fat") as fs:
        with image.open("rb") as fp:
            fp.seek(offset)
            fs.write(fp.read(length))
        fs.flush()
        fsck = subprocess.run(["/sbin/fsck_msdos", "-n", fs.name], capture_output=True)
        check(fsck.returncode == 0, fsck.stdout.decode() + fsck.stderr.decode())
    return dict(version=203, image=IMAGE.name, image_bytes=image.stat().st_size,
                ramdisk_bytes=length, image_sha256=sha_file(image), parent_sha256=PARENT_SHA,
                full_source_sha256=PARENT_FULL_SHA, kernel_sha256=sha(kern), members=hashes,
                runtime=runtime, final_menu=checks, release_inputs=recipe,
                linked_functions=linked, parent_functions=previous, fsck_status=0,
                rollback_image=str(BASE), hardware_validated=False, flash_performed=False)


def main():
    global IMAGE, RECEIPT
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("package", "verify"))
    p.add_argument("variant", choices=("B", "C"))
    args = p.parse_args()
    recipe = json.loads(RECIPE.read_bytes())
    variant = json.loads(VARIANTS.read_bytes())[args.variant]
    recipe.update(variant)
    IMAGE = OUT / recipe["image"]
    RECEIPT = OUT / ("BUILD-" + args.variant + ".json")
    if args.action == "verify" or IMAGE.exists():
        result = verify(IMAGE, recipe)
        check(result == json.loads(RECEIPT.read_bytes()), "published receipt mismatch")
    else:
        check(sha_file(FULL) == PARENT_FULL_SHA and sha_file(BASE) == PARENT_SHA, "parent hash")
        with tempfile.TemporaryDirectory(dir=OUT, prefix=".release-") as td:
            temp = Path(td)
            full, candidate = temp / "full.img", temp / "v203.img"
            shutil.copyfile(FULL, full)
            menu, manifest = menu_and_manifest(recipe)
            (temp / "menu").write_bytes(menu)
            (temp / "manifest").write_bytes(manifest)
            for path, name in ((KERNEL, "KERNEL"), (temp/"menu", "TOML"), (temp/"manifest", "MANIFEST")):
                subprocess.run(["mcopy", "-o", "-i", volume(full), str(path), fat[name]], check=True)
            compact(full, candidate)
            result = verify(candidate, recipe)
            (temp / "receipt").write_bytes(json_bytes(result))
            os.replace(temp / "receipt", RECEIPT)
            os.link(candidate, IMAGE)
    print(json_bytes(result).decode(), end="")


if __name__ == "__main__":
    main()
