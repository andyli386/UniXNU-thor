#!/usr/bin/env python3
"""Create a private one-use Thor Android Boot v4 candidate, with no device access."""
from pathlib import Path
import argparse
import datetime
import gzip
import hashlib
import json
import os
import re
import struct
import subprocess
import tomllib
from package_host import (load_tools, package_commands, private_output,
                          private_seed, require, rooted, seed_source,
                          validate_mem03_artifact, validate_mem03_release)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def now():
    return datetime.datetime.now().astimezone().isoformat()


def write(path, data):
    with path.open("xb") as stream:
        stream.write(data)


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--root", type=Path,
                        default=Path("/home/vincent/Projects/thor-unixnu"))
    for name in ("source", "kernel", "fd", "fd-audit", "firmware-receipt",
                 "provenance"):
        result.add_argument("--" + name, type=Path, required=True)
    result.add_argument("--run-id", required=True)
    result.add_argument("--tool-manifest", type=Path,
                        default=Path("toolchains/android-boot-tools/package-tools.json"))
    result.add_argument("--dry-run", action="store_true")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    os.umask(0o077)
    require(re.fullmatch(r"[a-z][a-z0-9-]{6,96}", args.run_id), "Invalid run ID")
    root = args.root.resolve()
    def path(value):
        return rooted(root, value)
    work, kernel_path, fd_path, audit_path, receipt_path, provenance = [
        path(getattr(args, name))
        for name in ("source", "kernel", "fd", "fd_audit",
                     "firmware_receipt", "provenance")]
    out = path(Path("runs") / args.run_id / "l2/package")
    require(not out.exists(), "Refuse to reuse or overwrite an existing attempt")
    seed_source(os.sys.platform)
    config = tomllib.loads((root / "thor-build.toml").read_text())
    frozen, build, layout = validate_mem03_artifact(root, kernel_path.parent)
    kernel_metadata = {
        "kernel_source_revision": frozen["xnu_commit"],
        "kernel_build_source_revision": frozen["build_source_commit"],
        "kernel_artifact_manifest_sha256": digest(
            (kernel_path.parent / "manifest.json").read_bytes()),
        "kernel_build_manifest_sha256": digest(
            (kernel_path.parent / "build-manifest.json").read_bytes())}
    receipt, approved = validate_mem03_release(
        root, work, receipt_path, audit_path, frozen)
    kernel_pin, fd_pin = frozen["kernel_sha256"], receipt["uefi_fd_sha256"]
    source_pin = receipt["uefi_commit"]
    adt = work / "Platforms/Xiaomi/thorPkg/Drivers/ThorEntryBdsDxe"
    head = subprocess.check_output(
        ["git", "-C", str(work), "rev-parse", "HEAD"], text=True).strip()
    require(head == source_pin, "UEFI source revision")
    require(not subprocess.check_output(
        ["git", "-C", str(work), "status", "--porcelain"], text=True).strip(),
        "UEFI source unclean")
    kernel, fd = kernel_path.read_bytes(), fd_path.read_bytes()
    require(len(kernel) == frozen["kernel_bytes"] and
            digest(kernel) == kernel_pin, "Frozen kernel identity")
    require(len(fd) == 3145728 and digest(fd) == fd_pin, "Reviewed FD identity")
    approved = json.loads(audit_path.read_text())
    require(approved["status"] == "PASS" and
            approved["fd"]["sha256"] == fd_pin and not approved["failures"],
            "Independent FD audit")
    layout = json.loads((kernel_path.parent / "kernel-layout.json").read_text())
    if frozen["_validated_packaging_profile"] == "MEM05":
        import importlib.util
        spec = importlib.util.spec_from_file_location("mem05_geometry", work / "scripts/bind-thor-mem05-kernel.py")
        geometry = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(geometry)
        actual = geometry.geometry(kernel)
        for key in ("kernel_sha256", "kernel_bytes", "loaded_span_bytes", "loaded_span_sha256",
                    "segment_count", "entry_offset", "vectors_offset", "entry_sync_offset", "entry_sync_bytes"):
            require(layout[key] == actual[key], "Actual MEM05 Mach-O geometry: " + key)
        for key in ("virtual_base", "virtual_end"):
            require(key not in layout or layout[key] == actual[key], "Optional MEM05 VA field: " + key)
            layout.setdefault(key, actual[key])
    require(layout["kernel_sha256"] == kernel_pin and
            layout["loaded_span_bytes"] == frozen["loaded_span_bytes"] and
            layout["virtual_end"] - layout["virtual_base"] ==
            layout["loaded_span_bytes"] and layout["segment_count"] == 10,
            "Mach-O layout")
    require(receipt["kernel_sha256"] == kernel_pin, "Release kernel identity")
    base_pin = config["stock"]["verified_entry09_kernel_sha256"]
    original = root / ("artifacts/mainline-4k-entry09-vmprep-20261024/"
                       "run02/kernel.entry09-vmprep.macho")
    require(digest(original.read_bytes()) == base_pin, "Original ENTRY09 kernel")
    expected = (adt / "ThorEntry06PayloadExpected.h").read_text()
    require("#define THOR_ENTRY06_PAYLOAD_BYTES " + str(len(kernel)) + "ULL"
            in expected and "mEntry06PayloadHeader[64]" in expected,
            "UEFI kernel-header binding")
    require(provenance.is_file() and provenance.parent == adt,
            "Source-provenance input")
    require(digest(provenance.read_bytes()) != base_pin, "Non-legacy source record")
    canonical = json.loads(provenance.read_text())
    require(canonical["kernel_sha256"] == kernel_pin and
            canonical["loaded_span_sha256"] == layout["loaded_span_sha256"],
            "UEFI layout binding")
    def stock(name, pin):
        value = (root / "artifacts/thor-smoke-01" / name).read_bytes()
        require(digest(value) == pin, "Stock component changed: " + name)
        return value
    shim = stock("BootShim.bin", config["stock"]["bootshim_sha256"])
    dtb = stock("thor-stock-vendor-dtb.bin", config["stock"]["dtb_sha256"])
    require(len(shim) == 112 and len(dtb) == 6443651, "Stock component size")
    prefix = struct.pack("<16sIIQ32s", b"THOR-PAYLOAD09\0\0", 2, 128,
                         len(kernel), bytes.fromhex(kernel_pin))
    require(len(prefix) == 64 and
            digest(prefix) == canonical["payload_prefix_sha256"],
            "Header source identity")
    python, scripts, tools_meta = load_tools(root, args.tool_manifest)
    tools_meta["packager_sha256"] = digest(Path(__file__).read_bytes())
    tools_meta["helper_sha256"] = digest(
        Path(__file__).with_name("package_host.py").read_bytes())
    if args.dry_run:
        print(json.dumps({"result": "PASS_NO_ARTIFACT_CREATED",
                          "kernel_sha256": kernel_pin, "fd_sha256": fd_pin,
                          **kernel_metadata,
                          "uefi_commit": source_pin, "out": str(out),
                          "seed_source": seed_source(os.sys.platform),
                          "package_tools": tools_meta, "phone_actions": 0}, indent=2))
        return
    with private_output(out, now()):
        seed, source = private_seed()
        length = ((max(128 + len(kernel), 19289048) + 4095) // 4096) * 4096
        bundle = prefix + seed + kernel + bytes(length - 128 - len(kernel))
        require(len(bundle) == length and
                bundle.count(b"THOR-PAYLOAD09\0\0") == 1, "Wire and size limits")
        for name, value in (("kernel-bundle.bin", bundle),
                            ("SILICIUM_UEFI.fd", fd), ("BootShim.bin", shim),
                            ("thor-stock-vendor-dtb.bin", dtb),
                            ("kernel.gz-dtb", gzip.compress(shim + fd,
                                                           compresslevel=9, mtime=0) + dtb)):
            write(out / name, value)
        metadata = {
            "schema": "thor-one-use-image-v2", "created_at": now(),
            "state": "packaged-awaiting-independent-offline-review",
            "stage": "THOR_PROFILED_ONE_USE_CANDIDATE",
            "kernel_sha256": kernel_pin, **kernel_metadata,
            "uefi_commit": source_pin, "fd_sha256": fd_pin,
            "fd_audit_sha256": digest(audit_path.read_bytes()),
            "bundle_sha256": digest(bundle), "bundle_bytes": len(bundle),
            "seed_sha256": digest(seed), "seed_source": source, "seed_bytes": 64,
            "seed_raw_logged": False, "seed_reuse_allowed": False,
            "header_magic": "THOR-PAYLOAD09 v2 intentionally reused",
            "full_vm_boot": False, "ttbr1_live_verified": False, "phone_attempts": 0,
            "package_tools": tools_meta}
        write(out / "payload-contract.json",
              (json.dumps(metadata, indent=2) + "\n").encode())
        records = []
        for label, command in zip(
                ("mkbootimg", "verify-image", "unpack-crosscheck"),
                package_commands(python, scripts, out)):
            process = subprocess.run(command, capture_output=True, text=True,
                                     timeout=120, check=False)
            write(out / (label + ".stdout"), process.stdout.encode())
            write(out / (label + ".stderr"), process.stderr.encode())
            records.append({"name": label, "argv": command,
                            "returncode": process.returncode})
            require(process.returncode == 0, "Package tool failed: " + label)
        require((out / "unpacked/kernel").read_bytes() ==
                (out / "kernel.gz-dtb").read_bytes(), "Unpack kernel mismatch")
        require((out / "unpacked/ramdisk").read_bytes() == bundle,
                "Unpack ramdisk mismatch")
        image = out / "thor-boot.img"
        require(image.stat().st_size < 64 * 1024 * 1024, "Boot image cap")
        metadata.update(image_sha256=digest(image.read_bytes()),
                        image_bytes=image.stat().st_size, run_id=args.run_id,
                        checks=["source", "kernel", "FD", "FV auditor", "seed",
                                "wire", "Android v4", "unpack", "pinned host tools"],
                        package_commands=records)
        write(out / "manifest.json", (json.dumps(metadata, indent=2) + "\n").encode())
        print(json.dumps({"state": "ONE_USE_IMAGE_HOST_PACKAGED",
                          "image": str(image), "sha256": metadata["image_sha256"],
                          "bytes": metadata["image_bytes"], "seed_logged": False,
                          "phone_actions": 0}, indent=2))


if __name__ == "__main__":
    main()
