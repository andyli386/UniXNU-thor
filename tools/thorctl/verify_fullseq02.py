#!/usr/bin/env python3
"""Independent FULLSEQ02 public-input gate and offline one-use boot verification.

Never contacts a phone, never generates entropy; all frozen inputs are hashed.
The earlier FULLSEQ01 receipts remain immutable and are not reinterpreted.
"""
import argparse
import datetime
import hashlib
import json
import os
import pathlib
import posixpath
import re
import subprocess
import sys
import tarfile
from fullseq_asset import validate as validate_asset, decode_v3
from package_host import load_tools, package_commands, rooted

RUN = "thor-20261027-ios-full-seq02"
FD = "Build/thorEntry09/RELEASE_CLANGPDB/FV/SILICIUM_UEFI.fd"
PE = "Build/thorEntry09/RELEASE_CLANGPDB/AARCH64/thorPkg/Drivers/ThorEntryBdsDxe/ThorEntryBdsDxe/OUTPUT/ThorEntryBdsDxe.efi"

def digest(path):
    h = hashlib.sha256()
    with pathlib.Path(path).open("rb") as src:
        for block in iter(lambda: src.read(1024*1024), b""):
            h.update(block)
    return h.hexdigest()

def read(path):
    return json.loads(pathlib.Path(path).read_text())

class Gate:
    def __init__(self, root):
        self.root = pathlib.Path(root).resolve()
        self.l2 = self.root / "runs" / RUN / "l2"
        self.source = self.root / "worktrees/uefi/ios-full-seq02"
        self.rows = []
        self.pins = {}
        self.bytes = {}
    def require(self, predicate, message):
        if not predicate:
            raise AssertionError(message)
        self.rows.append(dict(name=message, passed=True))
    def freeze(self, path, expected=None):
        path = pathlib.Path(path).resolve()
        self.require(path.is_file() and path.is_relative_to(self.root),
                     "bounded input exists: " + str(path))
        before = path.stat()
        actual = digest(path)
        self.require(expected is None or expected == actual,
                     "input digest correct: " + str(path))
        after = path.stat()
        self.require((before.st_size,before.st_mtime_ns,before.st_ino) ==
                     (after.st_size,after.st_mtime_ns,after.st_ino),
                     "input stable: " + str(path))
        name = str(path)
        self.require(name not in self.pins or self.pins[name] == actual,
                     "no contradictory frozen input: " + name)
        self.pins[name] = actual
        self.bytes[name] = before.st_size
        return actual
    def document(self, rel, expected=None):
        p = rooted(self.root, pathlib.Path(rel))
        self.freeze(p, expected)
        return read(p)

def validate_source_index(gate, index, audit, cpp):
    source = gate.source
    gate.require(index["schema"] == "thor-fullseq02-public-source-firmware-transfer-v1",
                 "FULLSEQ02 transfer schema")
    gate.require(index["source_commit"] == gate.head and
                 index["contains_seed"] is False and
                 index["contains_boot_image"] is False and
                 index["phone_actions"] == 0, "seedless public archive and exact revision")
    gate.require(index["actual_efi_count"] == 20 and
                 index["actual_link_input_libraries"] == 97 and
                 index["actual_native_cpp_source_inputs"] == len(cpp["files"]) == 812 and
                 index["actual_native_cpp_dependency_reports"] == len(cpp["dependency_reports"]) == 332,
                 "real compiler closure and bounded module counts")
    names = {str(pathlib.Path(item["path"]).relative_to(source))
             for item in audit["inputs"]}
    names.update(cpp["files"])
    names.update(cpp["dependency_reports"])
    gate.require(len(names) == 1523 and len(index["audited_input_files"]) == len(names),
                 "complete distinct source/archive/dependency input inventory")
    archive = gate.l2 / "public-transfer/fullseq02-public-uefi-source.tar.gz"
    gate.freeze(archive, index["public_source_archive_sha256"])
    gate.require(archive.stat().st_size == index["public_source_archive_bytes"],
                 "public archive bounded exact size")
    listed, canon = set(), {}
    for rec in index["audited_input_files"]:
        alias = rec["relative_path"]
        gate.require(alias.startswith("uefi-root/") and
                     rec["archive"] in ("fullseq02-uefi-main","fullseq02-uefi-link-inputs"),
                     "indexed source path & declared provenance")
        raw = alias[len("uefi-root/"):]
        normalized = posixpath.normpath(raw)
        p = (source / raw).resolve()
        gate.require(raw in names and p.is_relative_to(source.resolve()) and
                     normalized not in ("", ".") and not normalized.startswith("../") and
                     rec["origin_path"] == str(source / raw) and
                     rec["archive_member"] == "uefi-root/" + normalized,
                     "explicit aliases remain within actual source")
        gate.require(alias not in listed, "source index has unique provenance")
        listed.add(alias)
        gate.freeze(p, rec["sha256"])
        gate.require(p.stat().st_size == rec["bytes"], "source byte length matches audit")
        member = rec["archive_member"]
        gate.require(member not in canon or canon[member] == rec["sha256"],
                     "canonical alias has identical content")
        canon[member] = rec["sha256"]
    gate.require(len(canon) == index["actual_tar_members"] == 1522 and
                 len(listed)-len(canon) == 1, "canonical archive deduplicates only one alias")
    with tarfile.open(archive, "r:gz") as tar:
        seen = set()
        for entry in tar:
            gate.require(entry.isfile() and entry.name in canon and entry.name not in seen and
                         ".." not in pathlib.PurePosixPath(entry.name).parts,
                         "tar cannot escape or duplicate file")
            raw = tar.extractfile(entry).read()
            gate.require(hashlib.sha256(raw).hexdigest() == canon[entry.name] and
                         entry.size == len(raw), "tar member exactly matches compiler input")
            seen.add(entry.name)
        gate.require(seen == set(canon), "public archive contains entire verified input set")
    return len(listed)

def run_preflight(gate):
    source,l2,root = gate.source,gate.l2,gate.root
    gate.require(source.is_dir() and l2.is_dir(), "frozen source and run exist")
    gate.head = subprocess.check_output(["git","-C",str(source),"rev-parse","HEAD"],
                                        text=True).strip()
    status = subprocess.check_output(["git","-C",str(source),"status","--porcelain"],text=True)
    gate.require(gate.head == "57819387818c891de778ead7331626e6ada9c9f7"
                 and not status, "clean immutable firmware commit")
    manifest = root / "artifacts/loader/thor-20261027-ios-full-seq01/full-kernel-asset.json"
    asset = validate_asset(root, manifest)
    gate.freeze(manifest, asset["manifest_sha256"])
    for name in ("raw_path","compressed_path","header_path"):
        gate.freeze(root / asset[name])
    gate.require(asset["raw_sha256"] ==
                 "afaa4f5bf2dc6b862a87bfa7605521571726d1e640de64f4f0bc9f836e4fd772"
                 and asset["raw_bytes"] == 50679808 and
                 asset["loaded_span_bytes"] == 113229824 and
                 asset["scratch_bytes"] == 13376, "real full Darwin22 kernel and codec identity")
    release = gate.document(l2/"firmware-release.json")
    audit = gate.document(l2/"firmware-audit-repro/thor_fd_dc0fb92a528b-audit.json",
                          release["firmware_audit_sha256"])
    cpp = gate.document(l2/"native-cpp-inputs.json",release["native_cpp_inputs_sha256"])
    proof = gate.document(l2/"fullseq-uefi-proof.json",release["fullseq_uefi_proof_sha256"])
    stack = gate.document(l2/"native-stack-usage.json",release["native_stack_usage_sha256"])
    binding = gate.document(l2/"binding/fullseq-binding.json",release["binding_audit_sha256"])
    transition = gate.document(l2/"fullseq-transition-binding.json",
                               release["transition_binding_sha256"])
    reproducible = gate.document(l2/"fullseq-reproducible-link.json",
                                 release["reproducible_link_sha256"])
    gate.require(release["schema"] == "thor-fullseq01-uefi-release-v1" and
                 release["result"] == "PASS_ACTUAL_FULLSEQ_UEFI_RELEASE" and
                 release["uefi_commit"] == gate.head and
                 release["no_unknown_FDT_RAM"] is True and
                 release["full_kernel_entry_executed"] is False and
                 release["phone_actions"] == 0, "release binds real source without phone assertions")
    gate.require(release["execution_kernel_sha256"] == asset["raw_sha256"] and
                 release["execution_kernel_raw_bytes"] == asset["raw_bytes"] and
                 release["span_bytes"] == asset["loaded_span_bytes"] and
                 release["loaded_span_sha256"] == asset["loaded_span_sha256"] and
                 release["boot_args_abi"] == "darwin22-8796" and
                 release["boot_args_bytes"] == 736, "real Darwin22 ABI and loaded span")
    fd,pe = source/FD,source/PE
    gate.freeze(fd,release["uefi_fd_sha256"])
    gate.freeze(pe,release["uefi_pe_sha256"])
    gate.require(fd.stat().st_size == 3145728 and
                 audit["status"] == "PASS" and not audit["failures"] and
                 len(audit["actual_modules"]) == 20 and
                 len(audit["unique_link_inputs"]) == 97 and
                 len(audit["volumes"]) == 2 and
                 audit["fd"]["sha256"] == release["uefi_fd_sha256"],
                 "independently audited FV/FFS PE and linker composition")
    for lib in audit["unique_link_inputs"]:
        p = pathlib.Path(lib["artifact"]["path"])
        gate.require(p.resolve().is_relative_to(source.resolve()),
                     "actual linker archive belongs to frozen source")
        gate.freeze(p,lib["artifact"]["sha256"])
    gate.require(cpp["result"] == "PASS_ACTUAL_COMPILER_DEPENDENCY_CLOSURE" and
                 cpp["libraries"] == 97 and
                 cpp["source_commit"] == gate.head and
                 len(cpp["files"]) == 812 and
                 len(cpp["dependency_reports"]) == 332, "all current compiler dependencies bound")
    gate.require(proof["result"] == "PASS_ACTUAL_FULLSEQ_SOURCE_AND_LINKED_FIRMWARE_BINDING"
                 and proof["source_commit"] == gate.head and
                 proof["phone_actions"] == 0 and
                 proof["storage_write_enabled"] is False and
                 proof["no_unknown_FDT_RAM"] is True, "compiled ownership and storage guards")
    gate.require(stack["result"] == "PASS_ACTUAL_AARCH64_OBJECTS_AND_STACK_FRAMES"
                 and stack["source_commit"] == gate.head and
                 stack["maximum_new_function_stack_bytes"] < 0x4000,
                 "bounded AArch64 stack frame evidence")
    gate.require(binding["result"] == "PASS_ACTUAL_FULL_RAW_LAYOUT_AND_V3_BINDING"
                 and transition["result"] == "PASS_STANDALONE_OBJECT_AND_LINKED_PE_IDENTICAL"
                 and transition["linked_pe"]["pe_sha256"] == release["uefi_pe_sha256"],
                 "full Mach-O layout and exact native transition linked")
    layout = binding["handoff_layout"]
    gate.require(layout["Args"] == asset["loaded_span_bytes"] and
                 layout["Used"] == 113606656 and
                 layout["Used"] + 0x4000 <= layout["MinimumGrant"] <= 0x7000000,
                 "112 MiB retained grant includes guarded metadata")
    gate.require(reproducible["same_actual_fd_and_pe"] is True and
                 reproducible["source_commit"] == gate.head and
                 reproducible["final_fd_sha256"] == release["uefi_fd_sha256"] and
                 reproducible["final_pe_sha256"] == release["uefi_pe_sha256"] and
                 reproducible["link_input_archive_changed"] == 4,
                 "independent clean rebuild matches FD/PE; 4 archive drifts recorded")
    index = gate.document(l2/"public-transfer/PUBLIC_TRANSFER_INDEX.json")
    entries = validate_source_index(gate,index,audit,cpp)
    summary = gate.document(l2/"host-suite-summary.json")
    gate.require(summary["result"] == "PASS_FULLSEQ02_HOST_SUITES_AND_SOURCE_IDENTITIES"
                 and summary["total_tests_passed"] == 304 and
                 summary["source_commit"] == gate.head and
                 summary["phone_actions"] == 0, "304 genuine host cases; zero hardware actions")
    hostpins = gate.document(l2/"host-evidence-pins.json")
    gate.require(set(hostpins) == set(release["host_audits_sha256"]) ==
                 {"mac_suite","linux_proof","linux_adapter","mac_handoff"},
                 "exact four host-receipt provenance roles")
    for name,ref in hostpins.items():
        actual = gate.document(ref["path"],ref["sha256"])
        gate.require(ref["sha256"] == release["host_audits_sha256"][name] and
                     actual.get("phone_actions",0) == 0, "host receipt matches linked release")
        source_map = actual.get("source_files_sha256",{})
        for src,digest_value in source_map.items():
            location = source / src if (source/src).is_file() else root / src
            gate.freeze(location,digest_value)
        if name == "linux_proof":
            gate.require(actual["result"] == "PASS_FULLSEQ_SERVICE_FREE_PROOF_REAL_MMU"
                         and actual["case_count"] == actual["passed_cases"] == 96
                         and all(c["passed"] for c in actual["cases"]), "Linux proof: 96/96")
        if name == "linux_adapter":
            gate.require(actual["result"] == "PASS_PRODUCTION_ADAPTER_ENTRY_AND_EBS_CONTROL_FLOW"
                         and actual["total"] == actual["passed"] == 33
                         and all(c["passed"] for c in actual["cases"]), "Linux EBS adapter: 33/33")
    for s,count in [("core",49),("adt",62),("handoff",64)]:
        log = l2/"host-fresh"/(s+".log")
        gate.freeze(log)
        gate.require(f"Ran {count} tests" in log.read_text() and
                     re.search(r"(?m)^OK\s*$",log.read_text()) is not None,
                     "fresh fullseq02 native host test: "+s)
    for name in ("fullseq02_pack.py","fullseq_asset.py","full_loader_asset.py",
                 "package_host.py","verify_fullseq02.py"):
        gate.freeze(pathlib.Path(__file__).with_name(name))
    gate.freeze(root/"toolchains/android-boot-tools/package-tools.json")
    _,_,toolpins = load_tools(root,pathlib.Path("toolchains/android-boot-tools/package-tools.json"))
    for row in toolpins["scripts"].values():
        gate.freeze(row["path"],row["sha256"])
    for name in ("BootShim.bin","thor-stock-vendor-dtb.bin"):
        gate.freeze(root/"artifacts/thor-smoke-01"/name)
    return asset,release,entries

def preflight(gate,out):
    asset,release,entries=run_preflight(gate)
    return dict(schema="thor-fullseq02-public-preflight-v1",result="PASS_PUBLIC_FULLSEQ02",
        run_id=RUN,ready_for_packaging=True,seed_generated=False,phone_actions=0,
        full_kernel_entry_executed=False,hardware_test_attempts=0,
        source_commit=gate.head,full_kernel=asset,fd_sha256=release["uefi_fd_sha256"],
        public_compilation_inputs=entries,
        checks=gate.rows,frozen_input_sha256=gate.pins,frozen_input_bytes=gate.bytes,
        verifier_path=str(pathlib.Path(__file__).resolve()),verifier_sha256=digest(__file__),
        checked_at=datetime.datetime.now(datetime.timezone.utc).isoformat())

def verify_package(gate):
    root, l2 = gate.root, gate.l2
    asset, release, entries = run_preflight(gate)
    public_path = l2 / "public-preflight-final-02.json"
    public = gate.document(public_path)
    gate.require(public["schema"] == "thor-fullseq02-public-preflight-v1" and
                 public["result"] == "PASS_PUBLIC_FULLSEQ02" and
                 public["ready_for_packaging"] is True and
                 public["seed_generated"] is False and
                 public["run_id"] == RUN and public["phone_actions"] == 0,
                 "exact completed FULLSEQ02 public gate before seed creation")
    gate.require(public["full_kernel"] == asset and
                 public["fd_sha256"] == release["uefi_fd_sha256"] and
                 public["verifier_sha256"] == digest(__file__) and
                 all(row["passed"] for row in public["checks"]),
                 "preflight pins same complete asset and immutable verifier")
    gate.require(len(public["frozen_input_sha256"]) >= entries and
                 set(public["frozen_input_sha256"]) == set(public["frozen_input_bytes"]),
                 "full pre-entropy input inventory bound")
    for name,digest_value in public["frozen_input_sha256"].items():
        gate.freeze(name,digest_value)
        gate.require(pathlib.Path(name).stat().st_size ==
                     public["frozen_input_bytes"][name],
                     "unchanged public source/input size after packaging")
    package = l2 / "package"
    gate.require(package.is_dir() and
                 not (package/"INVALID-NOT-FOR-PHONE").exists() and
                 not (package/"ONE_SHOT_USED-DO-NOT-BOOT-AGAIN").exists(),
                 "one-use private output complete and unconsumed")
    metadata = gate.document(package/"manifest.json")
    gate.require(metadata["schema"] == "thor-fullseq02-one-use-boot-package-v1" and
                 metadata["run_id"] == RUN and
                 metadata["state"] == "HOST_PACKAGED_AWAITING_INDEPENDENT_L2" and
                 metadata["entry_mode"] == "darwin22-full" and
                 metadata["full_kernel_entry_executed"] is False and
                 metadata["hardware_tested"] is False and
                 metadata["phone_attempts"] == metadata["phone_actions"] == 0,
                 "fresh private package has no hardware execution")
    gate.require(metadata["pre_generation_preflight_sha256"] == digest(public_path) and
                 metadata["full_kernel"] == asset and
                 metadata["kernel_sha256"] == asset["raw_sha256"] and
                 metadata["uefi_commit"] == gate.head and
                 metadata["fd_sha256"] == release["uefi_fd_sha256"],
                 "private image bound to independent L2 inputs")
    gate.require(metadata["seed_source"] == "darwin-os.urandom" and
                 metadata["seed_bytes"] == 64 and
                 metadata["seed_raw_logged"] is False and
                 metadata["seed_reuse_allowed"] is False and
                 metadata["packet_version"] == 3 and metadata["packet_header_bytes"] == 128,
                 "fresh single-use OS entropy remains private")
    bundle_path = package/"kernel-bundle.bin"
    bundle = bundle_path.read_bytes()
    gate.freeze(bundle_path,metadata["bundle_sha256"])
    inner = decode_v3(bundle)
    gate.require(len(bundle) == metadata["bundle_bytes"] and
                 hashlib.sha256(bundle[64:128]).hexdigest() == metadata["seed_sha256"] and
                 inner["raw_sha256"] == asset["raw_sha256"] and
                 inner["raw_bytes"] == asset["raw_bytes"] and
                 inner["loaded_span_sha256"] == asset["loaded_span_sha256"],
                 "private v3 decoded only in offline verifier, seed bytes never logged")
    compressed = (root / asset["header_path"]).read_bytes() + (root / asset["compressed_path"]).read_bytes()
    gate.require(hashlib.sha256(compressed).hexdigest() == metadata["payload_sha256"] and
                 metadata["payload_bytes"] == len(compressed) and
                 bundle[128:128+len(compressed)] == compressed,
                 "full Darwin22 payload packed unmodified")
    for name,digest_value in [("SILICIUM_UEFI.fd",release["uefi_fd_sha256"]),
                              ("BootShim.bin",None),("thor-stock-vendor-dtb.bin",None)]:
        gate.freeze(package/name,digest_value)
        source_file=(gate.source/FD if name.endswith(".fd")
                     else root/"artifacts/thor-smoke-01"/name)
        gate.require((package/name).read_bytes() == source_file.read_bytes(),
                     "private image preserves real Thor firmware/stock components")
    kernel = package/"kernel.gz-dtb"
    gate.freeze(kernel)
    shim = (package/"BootShim.bin").read_bytes()
    fd = (package/"SILICIUM_UEFI.fd").read_bytes()
    dtb = (package/"thor-stock-vendor-dtb.bin").read_bytes()
    import gzip
    image_kernel = kernel.read_bytes()
    gate.require(image_kernel.endswith(dtb) and
                 gzip.decompress(image_kernel[:-len(dtb)]) == shim + fd,
                 "bootshim/FD/FDT container exactly decodes to current firmware")
    image_path = package/"thor-boot.img"
    gate.freeze(image_path,metadata["image_sha256"])
    gate.require(image_path.stat().st_size == metadata["image_bytes"] and
                 0 < metadata["image_bytes"] < 64 * 1024 * 1024 and
                 metadata["image_sha256"] != "801c439981d126d89ac52721d059e0b8245f50991b448089f2dd2ec7d262ce21",
                 "new bounded Android v4 image differs from consumed FULLSEQ01")
    gate.require((package/"unpacked/kernel").read_bytes() == image_kernel and
                 (package/"unpacked/ramdisk").read_bytes() == bundle,
                 "independently unpacked boot v4 identity")
    for previous in (root/"state/consumed-images").glob("*.json"):
        receipt=gate.document(previous)
        gate.require(previous.stem != metadata["image_sha256"] and
                     receipt.get("seed_sha256") != metadata["seed_sha256"],
                     "no image/entropy collision with consumed historical attempts")
    python,scripts,pins = load_tools(root,pathlib.Path("toolchains/android-boot-tools/package-tools.json"))
    verify_command = package_commands(python,scripts,package)[1]
    replay=subprocess.run(verify_command,capture_output=True,text=True,timeout=180)
    gate.require(replay.returncode == 0 and len(replay.stderr) < 32000,
                 "independent Android boot v4 parse + strict padding + stock geometry")
    gate.require(metadata["package_tools"]["fullseq02_pack.py"] ==
                 digest(pathlib.Path(__file__).with_name("fullseq02_pack.py")),
                 "image uses checked FULLSEQ02-specific packer")
    return dict(schema="thor-fullseq02-independent-offline-v1",result="PASS",
        run_id=RUN,ready_for_one_use_temporary_boot=True,
        hardware_test_attempts=0,phone_actions=0,
        full_kernel_entry_executed=False,
        actual_kernel_executed=False,ios_interface_reached=False,
        full_kernel=asset,
        inputs=dict(full_kernel=asset,
                    native_xnu=dict(sha256=asset["raw_sha256"],bytes=asset["raw_bytes"]),
                    android_boot_v4=dict(sha256=metadata["image_sha256"],
                                        bytes=metadata["image_bytes"]),
                    uefi_fd=dict(sha256=release["uefi_fd_sha256"],bytes=3145728)),
        image_verifier=dict(tool_sha256=digest(scripts["image_verifier"]),
                            successful=True,stdout_sha256=hashlib.sha256(replay.stdout.encode()).hexdigest()),
        package_sha256=digest(package/"manifest.json"),
        public_preflight_sha256=digest(public_path),
        checks=gate.rows,checked_at=datetime.datetime.now(datetime.timezone.utc).isoformat())


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=pathlib.Path,required=True)
    group=p.add_mutually_exclusive_group(required=True)
    group.add_argument("--preflight-public",action="store_true")
    group.add_argument("--verify-package",action="store_true")
    p.add_argument("--out",type=pathlib.Path,required=True)
    a=p.parse_args()
    g=Gate(a.root)
    out=rooted(g.root,a.out)
    if out.exists():raise SystemExit("Refusing to overwrite immutable verification receipt")
    try:
        if a.preflight_public:
            receipt=preflight(g,out)
        else:
            receipt=verify_package(g)
    except Exception as exc:
        receipt=dict(schema="thor-fullseq02-independent-offline-v1",result="FAIL",
                     error_type=type(exc).__name__,error=str(exc),run_id=RUN,
                     checks=g.rows,phone_actions=0)
    out.parent.mkdir(parents=True,exist_ok=True)
    with out.open("x") as f:json.dump(receipt,f,indent=2);f.write("\n")
    print(json.dumps(dict(result=receipt["result"],checks=len(g.rows),receipt=str(out),
                          sha256=digest(out),error=receipt.get("error"))))
    if receipt["result"]=="FAIL":raise SystemExit(1)

if __name__=="__main__":
    main()
