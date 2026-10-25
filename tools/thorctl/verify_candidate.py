#!/usr/bin/env python3
"""Independent L2 verifier for an immutable, seed-bearing Thor VM boot candidate.

No USB, ADB or Fastboot access. Never prints raw entropy bytes.
"""
import argparse,datetime,hashlib,json,re,struct,subprocess
from pathlib import Path


def sha(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1<<20),b""):h.update(chunk)
    return h.hexdigest()


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root",type=Path,required=True)
    ap.add_argument("--run-id",required=True)
    ap.add_argument("--candidate",type=Path,required=True)
    ap.add_argument("--source",type=Path,required=True)
    ap.add_argument("--firmware-release",type=Path,required=True)
    ap.add_argument("--firmware-audit",type=Path,required=True)
    args=ap.parse_args()
    root=args.root.resolve()
    assert re.fullmatch(r"thor-[0-9a-z-]{6,96}",args.run_id)
    def path(p):
        f=(root/p).resolve()
        assert f.is_relative_to(root)
        return f
    src=path(args.source)
    candidate=path(args.candidate)
    firmware_release=path(args.firmware_release)
    firmware_audit=path(args.firmware_audit)
    pkg=path(Path("runs")/args.run_id/"l2/package")
    proof= pkg/"independent-offline.json"
    assert pkg.is_dir() and not proof.exists()
    marks=[]
    def require(cond,why):
        marks.append({"contract":why,"passed":bool(cond)})
        if not cond:raise RuntimeError("ENTRY VM independent review FAILED: "+why)
    manifest=json.loads((pkg/"manifest.json").read_text())
    receipt=json.loads(firmware_release.read_text())
    audit=json.loads(firmware_audit.read_text())
    layout=json.loads((candidate/"kernel-layout.json").read_text())
    build=json.loads((candidate/"build-manifest.json").read_text())
    machine=json.loads((candidate/"machinecode.json").read_text())
    boot=pkg/"thor-boot.img"
    kernel=candidate/"kernel.macho"
    fd=src/"Build/thorEntry09/RELEASE_CLANGPDB/FV/SILICIUM_UEFI.fd"
    prov=src/"Platforms/Xiaomi/thorPkg/Drivers/ThorEntryBdsDxe/VM_POST_TTBR1_PROVENANCE.json"
    binding=json.loads(prov.read_text())
    require(manifest["schema"]=="thor-one-use-image-v2","correct one-use manifest schema")
    require(manifest["run_id"]==args.run_id,"unique immutable run ID")
    require(boot.is_file() and boot.stat().st_size==25894912,"expected Android Boot v4 length")
    image_hash=sha(boot)
    require(image_hash==manifest["image_sha256"],"boot image SHA256 matches manifest")
    require(sha(pkg/"SILICIUM_UEFI.fd")==receipt["uefi_fd_sha256"],"packed firmware FD identity")
    require(sha(fd)==receipt["uefi_fd_sha256"]==audit["fd"]["sha256"],"built FD matches independently audited FD")
    require(audit["status"]=="PASS" and not audit["failures"],"firmware audit has no failures")
    require(len(audit["volumes"])==2,"exactly two firmware volumes")
    require(len(audit["actual_modules"])==20,"20 executable UEFI modules")
    require(len(audit["unique_link_inputs"])==96,"96 source-linked UEFI inputs")
    require(sha(firmware_audit)==receipt["firmware_audit_sha256"],"auditor receipt SHA256 pinned")
    require(kernel.is_file() and kernel.stat().st_size==layout["kernel_bytes"],"Mach-O actual bytes and layout")
    kernel_hash=sha(kernel)
    require(kernel_hash==manifest["kernel_sha256"]==build["kernel_sha256"]==receipt["kernel_sha256"],"frozen source kernel SHA256")
    require(kernel_hash==machine["kernel_sha256"],"machine-code proof is for identical kernel")
    require(machine["result"]=="PASS_ACTUAL_VBAR_RELOCATION_CODE_AND_ORDER","linked TTBR1 and high VBAR machine-code path")
    require(machine["EL1_translation_probe_calls"]==3,"three EL1 translation probe calls")
    require(machine["VBAR_EL1_msr_count"]==1 and machine["VBAR_EL1_mrs_count"]==2,"VBAR register write and both reads")
    require(machine["msr_followed_by_isb_then_readback_then_white_marker"],"new high VBAR readback before white success")
    require(machine["low_exception_high_va"]=="0xfffffff007401000","linked high exception-vector address identity")
    require(not machine["ttbr0_invalidation_executed"],"original TTBR0 retained in bounded test")
    require(layout["loaded_span_bytes"]==0x133c000,"new 16KiB-reduced Mach-O loaded span")
    require(layout["loaded_span_bytes"]==build["loaded_span_bytes"]==receipt["loaded_span_bytes"],"loaded span comes from actual linked Mach-O")
    require(layout["loaded_span_sha256"]==binding["loaded_span_sha256"],"UEFI range binding to same loaded span")
    require(layout["kernel_sha256"]==binding["kernel_sha256"],"UEFI kernel identity from actual Mach-O")
    require(binding["payload_prefix_sha256"]==binding["header_sha256"],"transport prefix hash agreed")
    geometry=binding["generated_layout"]
    require(geometry["E09_KERNEL_SPAN"]==layout["loaded_span_bytes"],"UEFI loaded kernel span exact")
    require(geometry["E06_ARGS_OFFSET"]==geometry["E09_KERNEL_SPAN"],"BootArgs starts after kernel")
    require(geometry["E06_FINAL_USED"]==geometry["E06_TRANSITION_OFFSET"]+0x44000,"bounded handoff allocation budget")
    require(geometry["E09_ADT_OFFSET"]==geometry["E06_FINAL_USED"]-0x4000,"ADT isolated in final 16KiB")
    require(geometry["E06_FINAL_USED"]<0x4000000-0x200000,"owned 64MiB grant below 2MiB guard")
    require(geometry["E06_ENTRY_OFFSET"]==layout["entry_offset"] and geometry["E06_VECTOR_OFFSET"]==layout["vectors_offset"],"UEFI entry and exception vector geometry")
    require(geometry["E06_ENTRY_SYNC_OFFSET"]==layout["entry_sync_offset"] and geometry["E06_ENTRY_SYNC_BYTES"]==layout["entry_sync_bytes"],"4KiB prefetch/exception sync region")
    require(manifest["kernel_source_revision"]==receipt["kernel_source_commit"]==build["xnu_commit"],"exact XNU source revision")
    uefi_head=subprocess.check_output(["git","-C",str(src),"rev-parse","HEAD"],text=True).strip()
    require(not subprocess.check_output(["git","-C",str(src),"status","--porcelain"],text=True).strip(),"UEFI working tree clean")
    require(uefi_head==receipt["uefi_commit"]==manifest["uefi_commit"],"UEFI source commit exact")
    require(sha(prov)==receipt["actual_kernel_binding_sha256"],"UEFI source provenance unmodified")
    bundle=(pkg/"kernel-bundle.bin").read_bytes()
    require(sha(pkg/"kernel-bundle.bin")==manifest["bundle_sha256"],"kernel bundle SHA256 exact")
    require(len(bundle)==manifest["bundle_bytes"]==19292160,"complete one-time kernel bundle length")
    require(bundle[:16]==b"THOR-PAYLOAD09\x00\x00","original verified transport wire magic")
    version,header,bytes_=struct.unpack_from("<IIQ",bundle,16)
    require((version,header,bytes_)==(2,128,kernel.stat().st_size),"V2 transport header bounds")
    require(bundle[32:64]==bytes.fromhex(kernel_hash),"bundle SHA field matches Mach-O")
    require(len(bundle[64:128])==64 and any(bundle[64:128]),"one-use entropy 64 bytes, nonzero")
    require(hashlib.sha256(bundle[64:128]).hexdigest()==manifest["seed_sha256"],"seed digest checked without exposing seed")
    require(bundle[128:128+bytes_]==kernel.read_bytes(),"unpacked Mach-O equals frozen source")
    require(not any(bundle[128+bytes_:]),"transport tail padding all zero")
    require((pkg/"unpacked/ramdisk").read_bytes()==bundle,"unpacker agrees with original bundle")
    require((pkg/"unpacked/kernel").read_bytes()==(pkg/"kernel.gz-dtb").read_bytes(),"unpacked Android kernel equals packaged shims")
    v4=json.loads((pkg/"verify-image.stdout").read_text())
    require(v4["ok"] and v4["header_version"]==4 and v4["file_sha256"]==image_hash,"independent Android boot-v4 parser")
    require(not v4["warnings"],"boot image has no verifier warnings")
    require(all(c["returncode"]==0 for c in manifest["package_commands"]),"all packaging subprocesses succeeded")
    require(manifest["seed_bytes"]==64 and not manifest["seed_raw_logged"] and not manifest["seed_reuse_allowed"],"no raw seed logged and no reuse allowed")
    require(manifest["phone_attempts"]==0 and not manifest["full_vm_boot"] and not manifest["ttbr1_live_verified"],"honest untested boot scope")
    require(not (pkg/"ONE_SHOT_USED-DO-NOT-BOOT-AGAIN").exists(),"image not used before test")
    require(not (pkg/"INVALID-NOT-FOR-PHONE").exists(),"image not quarantined")
    old=root/"artifacts/mainline-4k-entry09-vmprep-20261024/run02/kernel.entry09-vmprep.macho"
    require(sha(old)=="5ae9caa966587357265011fa132377921ec6d6d96bf617b928f3da9326230647","ENTRY09 known-good kernel unaffected")
    previous=root/"artifacts/thor-entry-10g/firmware/ONE_SHOT_USED-DO-NOT-BOOT-AGAIN"
    require(previous.is_file(),"previous ENTRY10G one-use candidate is still retired")
    pe=src/"Build/thorEntry09/RELEASE_CLANGPDB/AARCH64/thorPkg/Drivers/ThorEntryBdsDxe/ThorEntryBdsDxe/OUTPUT/ThorEntryBdsDxe.efi"
    binary=pe.read_bytes()
    require(b"THOR / XNU" in binary and b"CURRENT RESULT" in binary and
            b"BOOT STAGE / UEFI" in binary,"structured diagnostic screen linked into real UEFI PE")
    require(b"ESR / ELR / FAR" in binary and b"NOT CAPTURED IN UEFI" in binary,
            "unavailable exception values explicitly identified")
    require(b"UEFI CHECKS FAILED" in binary and b"UEFI CHECKS PASS" in binary,
            "diagnostic screen distinguishes pass from failure by text")
    require(b"UEFI 1 INITRD READ" in binary and b"UEFI 2 KERNEL HASH PASS" in binary,
            "retained UEFI stage labels")
    final={"schema":"thor-vm-independent-offline-gate-v1",
           "checked_at":datetime.datetime.now().astimezone().isoformat(),
           "result":"PASS","conditions_count":len(marks),"checks":marks,
           "inputs":{"android_boot_v4":{"sha256":image_hash,"bytes":boot.stat().st_size},
                     "native_xnu":{"sha256":kernel_hash,"bytes":kernel.stat().st_size},
                     "uefi_fd":{"sha256":sha(fd),"bytes":fd.stat().st_size}},
           "run_id":args.run_id,"ready_for_one_use_temporary_boot":True,
           "device_commands":0,"runtime_ttbr0_retired":False,
           "hardware_test_attempts":0}
    with proof.open("x") as f:json.dump(final,f,indent=2);f.write("\n")
    print(json.dumps({"result":final["result"],"conditions_count":len(marks),
                      "image_sha256":image_hash,"one_time_only":True,"phone_actions":0},indent=2))


if __name__=="__main__":
    main()
