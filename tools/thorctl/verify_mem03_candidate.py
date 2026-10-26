#!/usr/bin/env python3
"""Independent L2 verifier for an immutable, seed-bearing Thor VM boot candidate.

No USB, ADB or Fastboot access. Never prints raw entropy bytes.
"""
import argparse,datetime,hashlib,json,re,struct,subprocess,sys,tempfile,gzip,importlib.util,tomllib,zlib
from pathlib import Path


def sha(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1<<20),b""):h.update(chunk)
    return h.hexdigest()


class GateError(RuntimeError):
    """An offline contract failed; messages never contain entropy bytes."""


def check(condition, message):
    if not condition:
        raise GateError(message)


def align(value, size=4096):
    return (value + size - 1) // size * size


class MachO:
    """Bounded independent Mach-O segment/symbol parser, without shell tools."""
    def __init__(self, data):
        self.data = data
        check(len(data) >= 32, "truncated Mach-O header")
        magic, cpu, _, _, ncmds, sizeofcmds, _, _ = struct.unpack_from("<8I", data)
        check(magic == 0xfeedfacf and cpu == 0x100000c, "native arm64 Mach-O header")
        check(0 < ncmds < 4096 and 32 + sizeofcmds <= len(data), "Mach-O load-command bounds")
        self.segments, self.symbols = [], {}
        pos, symtab = 32, None
        for _ in range(ncmds):
            check(pos + 8 <= 32 + sizeofcmds, "truncated load command")
            cmd, size = struct.unpack_from("<II", data, pos)
            check(size >= 8 and not size % 8 and pos + size <= 32 + sizeofcmds,
                  "load command outside declared bounds")
            if cmd == 0x19:
                check(size >= 72, "truncated segment command")
                name = data[pos+8:pos+24].split(b"\0")[0].decode("ascii")
                va, vs, off, count = struct.unpack_from("<QQQQ", data, pos+24)
                check(count <= vs and off + count <= len(data), "Mach-O segment bounds")
                if vs:
                    self.segments.append((va, vs, off, count, name))
            elif cmd == 2:
                check(size == 24 and symtab is None, "invalid/duplicate symbol table")
                symtab = struct.unpack_from("<4I", data, pos+8)
            pos += size
        check(pos == 32 + sizeofcmds and self.segments, "complete load commands")
        ordered = sorted(self.segments)
        check(all(a[0]+a[1] <= b[0] for a,b in zip(ordered,ordered[1:])),
              "nonoverlapping Mach-O loaded segments")
        self.base = min(s[0] for s in ordered)
        self.end = max(s[0]+s[1] for s in ordered)
        check(0 < self.end-self.base < 64*1024*1024, "bounded diagnostic loaded span")
        loaded = bytearray(self.end-self.base)
        for va, vs, off, count, _ in ordered:
            loaded[va-self.base:va-self.base+count] = data[off:off+count]
        self.loaded_hash = hashlib.sha256(loaded).hexdigest()
        if symtab:
            off, count, strings, strbytes = symtab
            check(off+count*16 <= len(data) and strings+strbytes <= len(data), "symbol-table bounds")
            for i in range(count):
                index, typ, _, _, value = struct.unpack_from("<IBBHQ",data,off+i*16)
                if not index or index >= strbytes or not value or (typ & 0xe0):
                    continue
                end = data.find(b"\0",strings+index,strings+strbytes)
                check(end >= 0, "terminated symbol name")
                name = data[strings+index:end].decode("ascii")
                self.symbols[name] = value

    def word(self, va):
        check(not va % 4, "aligned AArch64 instruction address")
        matches = [s for s in self.segments if s[0] <= va and va+4 <= s[0]+s[3]]
        check(len(matches) == 1, "instruction in file-backed segment")
        start, _, off, _, _ = matches[0]
        return struct.unpack_from("<I",self.data,off+va-start)[0]

    def movz(self, va, register, bits=32):
        word = self.word(va)
        base = 0xd2800000 if bits == 64 else 0x52800000
        check(word & 0xff800000 == base and word & 31 == register,
              "actual MOVZ register and width")
        shift = ((word >> 21) & 3) * 16
        check(bits == 64 or shift < 32, "valid MOVZ shift")
        return ((word >> 5) & 0xffff) << shift

    def contains_function(self, name, va):
        check(name in self.symbols, "required function symbol: "+name)
        start = self.symbols[name]
        following = [v for v in self.symbols.values() if v > start]
        end = min(following) if following else self.end
        return start <= va < end


def validate_boot_bytes(image, packed_kernel, bundle):
    check(len(image) >= 4096 and image[:8] == b"ANDROID!", "Android boot magic/header")
    kernel_bytes, ramdisk_bytes = struct.unpack_from("<II",image,8)
    header_bytes = struct.unpack_from("<I",image,20)[0]
    version = struct.unpack_from("<I",image,40)[0]
    check(version == 4 and header_bytes == 1584, "Android Boot v4 header layout")
    check(kernel_bytes == len(packed_kernel) and ramdisk_bytes == len(bundle), "Boot v4 section lengths")
    signature = struct.unpack_from("<I",image,1580)[0]
    check(signature == 0 and not any(image[1584:4096]), "unsigned v4 exact zero header padding")
    ramdisk_start = 4096 + align(kernel_bytes)
    end = ramdisk_start + align(ramdisk_bytes)
    check(end == len(image) and len(image) < 64*1024*1024, "bounded exact Boot v4 total length")
    check(image[4096:4096+kernel_bytes] == packed_kernel, "Boot v4 embedded kernel exact")
    check(not any(image[4096+kernel_bytes:ramdisk_start]), "kernel section zero padding")
    check(image[ramdisk_start:ramdisk_start+ramdisk_bytes] == bundle, "Boot v4 embedded transport exact")
    check(not any(image[ramdisk_start+ramdisk_bytes:end]), "ramdisk section zero padding")


def reject_reuse(image_hash, seed_hash, manifests, consumed_hashes=()):
    check(image_hash not in set(consumed_hashes), "consumed historical image cannot be reused")
    for old in manifests:
        check(old.get("image_sha256") != image_hash, "image differs from every historical package")
        check(old.get("seed_sha256") != seed_hash, "fresh seed differs from historical seed digests")


def coverage_contract(producer_pages, consumer_pages, start, end, flags):
    check(start == 0xb805a000 and end == 0xb8195000, "MEM03 exact framebuffer physical range")
    check(producer_pages == consumer_pages == 315, "bootstrap and consumer both cover315 pages")
    check(end-start == producer_pages*4096, "framebuffer exact page extent")
    check(90+producer_pages <= 512, "bootstrap framebuffer leaves fit existing L3")
    check(flags == 0x006000000000070b, "unchanged framebuffer leaf attributes")
    return [start+i*4096 for i in range(consumer_pages)]


def validate_uefi_detail_document(detail):
    check(detail["schema"]=="thor-mem03-snapshot-detail-host-proof-v1", "MEM03 detail schema")
    check(detail["result"]=="PASS_MEM03_FDT_DETAIL_AND_FINAL_OVERLAY", "MEM03 detail result")
    for key in ("same_immutable_snapshot","per_window_dynamic_count", "unresolved_truncation_explicit",
                "status_and_padding_reported","final_postclear_overlay"):
        check(detail[key] is True, "FDT detail contract: "+key)
    check(detail["unresolved_detail_limit"]==2, "bounded unresolved detail count")
    check(detail["framebuffer_reserved_y"]==[0,319], "XNU top region reserved")
    check(detail["detail_overlay_xywh"]==[30,1925,1380,1153], "bounded FDT detail canvas")
    check(set(detail["policy_unchanged"]) >= {"fdt_gate","fdt_gate_header","memory_map","payload_read_permit"}
          and all(detail["policy_unchanged"].values()), "unchanged strict memory permission policy")
    check(detail["new_allocatable_memory_pages"]==0 and detail["phone_actions"]==0,
          "host detail proof neither grants memory nor acts on phone")



CONSUMED_BASELINES = {
    "af5e84e72d6eeba3351ebdf2df9e8f55f74adaa786a0bbd9b0c627222733695c",
    "0239eb9a758a9d3f7695a7de0f86c66913d3fe6ad125afdb693eb14a9e85c8ca",
}
TRANSITION_SHA = "29ee4adbbb30fe06bcebf9f9af9723f98698c6170636b2bb24e52a7a17fe9d24"
OLD_KERNEL_SHA = "623b444dc57fc8981562277f1eade9f1ed25818badaa3bfd03ae7ff54a5333aa"


def validate_mem03_inputs(root,pkg,src,xsrc,candidate,manifest,receipt,audit,layout,
                         build,machine,binding,extra,detail,boot,kernel,fd,prov,
                         xproof,uproof,firmware_audit_path,transfer_index,require):
    def pin(path, expected, why):
        require(path.is_file() and sha(path)==expected, why)

    require(manifest["seed_source"] in {"linux-os.getrandom(flags=0)","darwin-os.urandom"},
            "supported OS CSPRNG with explicit source")
    require(receipt["linked_transition_sha256"]==TRANSITION_SHA and
            receipt["transition_pe_relocations_in_interval"]==0 and
            receipt["transition_sha_matches_expected_header"] is True,
            "unchanged reviewed500-byte transition, no relocations")
    require(receipt["temporary_boot_only"] is True and receipt["phone_actions"]==0
            and receipt["hardware_tested"] is False and not receipt["pmap_bootstrap_executed"],
            "new firmware remains untested temporary bounded diagnostic")
    require(sha(kernel)!=OLD_KERNEL_SHA and manifest["uefi_commit"]!=
            "1044cb422ccce63cf382c4cc23d30c83e26c787e", "new MEM03 source/binary identities")
    require(subprocess.check_output(["git","-C",str(xsrc),"rev-parse","HEAD"],text=True).strip()
            ==build["xnu_commit"], "exact XNU source checkout revision")
    require(not subprocess.check_output(["git","-C",str(xsrc),"status","--porcelain"],text=True).strip(),
            "XNU source checkout clean")
    require(extra["schema"]=="thor-mem03-linked-bootstrap-grid-coverage-v1" and
            extra["result"]=="PASS_MEM03_LINKED_BOOTSTRAP_GRID_COVERAGE_DIAGNOSTICS"
            and extra["kernel_sha256"]==sha(kernel), "MEM03 proof bound to exact new kernel")
    actual_macho=MachO(kernel.read_bytes())
    producer=actual_macho.symbols["_thor_entry09_build_tables"]
    producer_end=min(value for value in actual_macho.symbols.values() if value>producer)
    count_sites=[address for address in range(producer,producer_end,4)
                 if actual_macho.word(address)&0xff80001f==0x52800010
                 and actual_macho.movz(address,16)==315]
    require(len(count_sites)==1,"independent binary decode finds one actual producer MOVZ w16,#315")
    contract=extra["bootstrap_to_grid_contract"]
    coverage_contract(contract["producer_pages"],contract["consumer_pages"],int(contract["physical_range"][0],0),int(contract["physical_range"][1],0),0x006000000000070b)
    require(contract["producer_pages"]==contract["consumer_pages"]==315
            and contract["physical_range"]==["0xb805a000","0xb8195000"]
            and contract["bootstrap_L3_indices"]==[90,404], "linked315-page producer/consumer geometry")
    for key in ("full_pool_zero_before_install","all_315_identity_PTEs_and_attributes_match",
                "framebuffer_neighbors_unmapped","owned_RAM_identity_and_KVA_edges_preserved",
                "callee_saved_registers_preserved"):
        require(contract[key] is True,"bootstrap execution/PTE contract: "+key)
    require(8192 < contract["actual_table_stores"] < 20000 and
            10000 < contract["executed_integer_instructions"] < 100000,
            "real bounded bootstrap integer execution, not receipt-only geometry")
    negative=extra["known_MEM02_negative"]
    require(negative["kernel_sha256"]==OLD_KERNEL_SHA and negative["rejected"] is True
            and negative["first_unmapped_page"]==90 and negative["first_unmapped_PA"]=="0xb80b4000"
            and negative["hardware_PC_sample"] is False, "historical90/315 defect actually rejected")
    require(extra["source_checks"]["shared_C_assembly_contract"] is True and
            extra["source_checks"]["diagnostic_pixels_within_original_90_pages"] is True,
            "shared contract and original90-page fault rendering source audited")
    aliases=extra["high_alias_PTE_execution"]
    require(aliases["actual_PTE_stores"]==335 and aliases["grid_L3_indices"]==[0,314]
            and aliases["legacy_L3_indices"]==[480,499]
            and all(aliases[key] is True for key in ("all_315_grid_PA_and_attributes_match",
                "all_20_legacy_PA_and_attributes_match","all_other_L3_slots_remain_zero",
                "DSB_TLBI_DSB_ISB_order_preserved")),
            "actual335 high-alias stores preserve every required and empty L3 slot plus barriers")
    entry_guard=extra["reject_entry_guard"]
    require(all(entry_guard[key] is True for key in ("actual_TTBR0_read_before_pixels",
               "kva_active_branch_to_reset_before_pixels","TTBR0_mismatch_branch_to_reset_before_pixels"))
            and int(entry_guard["failure_reset_address"],0)<int(entry_guard["first_fault_pixel_store"],0),
            "fault renderer rejects invalid/retired originalTTBR0 before any pixel write")
    diagnostics=extra["linked_diagnostics"]
    require(diagnostics["actual_AT_ISB_immediate_PAR_sample"] is True
            and diagnostics["raw_PAR_passed_to_reject_x3"] is True
            and diagnostics["fault_pixels_cleaned_before_bounded_stop"] is True,
            "actual failed PAR captured and preserved through bounded diagnostic display")
    require(extra["retirement_boundary"]["no_low_diagnostics_after_native_retirement"] is True
            and extra["retirement_boundary"]["custom_retired_helpers_checked"]>=4,
            "low address fault display unreachable in retiredTTBR0 interval")
    required_mutations={"producer_page_count_90","producer_page_count_314",
                        "producer_page_count_316","producer_page_count_423",
                        "producer_wrong_PA_stride","wrong_RW_attributes",
                        "wrong_cache_attributes","wrong_XN_attributes","last_page_missing"}
    require(set(extra["fault_injection_rejected"])>=required_mutations and
            all(extra["fault_injection_rejected"].values()),
            "all MEM03 coverage/permission/last-page unsafe mutations rejected")
    auditor=xsrc/"scripts/audit-thor-mem03-linked.py"
    source_binding=extra["source_binding"]
    pin(auditor,source_binding["script_sha256"],"exact reviewed MEM03 bootstrap audit source")
    proof_revision=source_binding["git_commit"]
    require(subprocess.run(["git","-C",str(xsrc),"merge-base","--is-ancestor",proof_revision,"HEAD"],
                           capture_output=True).returncode==0,"proof source revision is ancestor of frozen source")
    require(not subprocess.check_output(["git","-C",str(xsrc),"diff",proof_revision,"HEAD","--","patches/xnu"],text=True),
            "XNU build patches unchanged since frozen proof revision")
    for name,digest in source_binding["prepared_source_sha256"].items():
        check("/" not in name and ".." not in name,"bounded prepared source filename")
        pin(xsrc/".cache/xnu-work/osfmk/arm64"/name,digest,"prepared source bytes match compiled proof: "+name)
    oldkernel=root/"artifacts/candidates/thor-20261026-vmdev-010/kernel.macho"
    pin(oldkernel,OLD_KERNEL_SHA,"frozen real negative baseline present")
    with tempfile.TemporaryDirectory(prefix="mem03-independent-",dir=pkg.parent) as tmp:
        replay=Path(tmp)/"linked.json"
        process=subprocess.run([sys.executable,str(auditor),"--kernel",str(kernel),
                                "--old-kernel",str(oldkernel),"--out",str(replay)],
                               capture_output=True,text=True,timeout=120)
        require(process.returncode==0 and replay.is_file(),
                "independent re-execution of linked bootstrap interpreter and all mutations")
        rerun=json.loads(replay.read_text())
        def semantic_report(value):
            result={k:v for k,v in value.items() if k!="created_at"}
            result["source_binding"]={k:v for k,v in value["source_binding"].items() if k!="git_commit"}
            return result
        require(semantic_report(rerun)==semantic_report(extra),
                "freshly replayed bootstrap/PTE/diagnostic proof exactly matches frozen receipt")
    validate_uefi_detail_document(detail)
    require(sha(uproof)==receipt["mem03_uefi_proof_sha256"],"FDT detail proof immutable hash bound to firmware release")
    require(detail["schema"]=="thor-mem03-snapshot-detail-host-proof-v1" and
            detail["result"]=="PASS_MEM03_FDT_DETAIL_AND_FINAL_OVERLAY",
            "dedicated new MEM03 FDT detail host proof")
    for key in ("same_immutable_snapshot","per_window_dynamic_count",
                "unresolved_truncation_explicit","status_and_padding_reported","final_postclear_overlay"):
        require(detail[key] is True,"FDT bounded same-snapshot diagnostic: "+key)
    require(detail["unresolved_detail_limit"]==2 and detail["framebuffer_reserved_y"]==[0,319]
            and detail["detail_overlay_xywh"]==[30,1925,1380,1153],
            "bounded2-node detail stays below reserved XNU grid")
    require(detail["phone_actions"]==0 and detail["new_allocatable_memory_pages"]==0,
            "FDT detail proof grants no memory and ran no phone command")
    require(set(detail["policy_unchanged"])>={"fdt_gate","fdt_gate_header","memory_map","payload_read_permit"}
            and all(detail["policy_unchanged"].values()),"FDT parser/memory map/read permission unchanged")
    require(detail["firmware"]["sha256"]==sha(fd) and detail["firmware"]["bytes"]==fd.stat().st_size,
            "MEM03 detail proof bound to actual final firmware bytes")
    require(detail["transition"]["bytes"]==500 and detail["transition"]["sha256"]==TRANSITION_SHA,
            "MEM03 detail proof preserves exact transition")
    source_pins=detail["source_files_sha256"]
    require(len(source_pins)>=7,"FDT detail source/helper/test identities all pinned")
    for relative,digest in source_pins.items():
        path=(src/relative).resolve()
        require(path.is_relative_to(src),"FDT source hash path bounded")
        pin(path,digest,"same final FDT source file: "+relative)
    adt=src/"Platforms/Xiaomi/thorPkg/Drivers/ThorEntryBdsDxe"
    pin(adt/"ThorEntryBdsDxe.c",receipt["new_bds_source_sha256"],"MEM03 final BDS source bound to firmware release")
    pin(adt/"thor_fdt_gate.c",receipt["unchanged_fdt_validator_sha256"],"strict original FDT validator unchanged")
    memory=src/"Platforms/Xiaomi/thorPkg/Library/MemoryMapLib/MemoryMapLib.c"
    # The release pins the real project filename, not a guessed new map.
    if not memory.is_file():
        memory=src/"Platforms/Xiaomi/thorPkg/Library/MemoryMapLib/ThorMemoryMapLib.c"
    pin(memory,receipt["unchanged_memorymap_source_sha256"],"original memory map unchanged")
    pe=src/"Build/thorEntry09/RELEASE_CLANGPDB/AARCH64/thorPkg/Drivers/ThorEntryBdsDxe/ThorEntryBdsDxe/OUTPUT/ThorEntryBdsDxe.efi"
    binary=pe.read_bytes()
    require(b"RUN MEM03 / THOR SM8475" in binary and
            b"MEM03 FDT WINDOW ONLY - NO MEMORY GRANT" in binary and b"THOR FDT MEMDIAG 03" in binary,
            "actual final linked PE carries unique MEM03 diagnostic identity")
    require(all(label not in binary for label in (b"RUN MEM01 / THOR SM8475",b"RUN MEM02 / THOR SM8475",
                                                b"RUN VM-VBAR / THOR",b"RUN VM-HW11 / THOR SM8475")),
            "final PE excludes misleading inherited run identity")
    modules=[m for m in audit["actual_modules"] if m["name"]=="ThorEntryBdsDxe"]
    require(len(modules)==1 and modules[0]["build"]["build_output"]["sha256"]==sha(pe)==modules[0]["artifact"]["sha256"],
            "BDS executable bytes match independently audited final build module")
    code=(adt/"ThorEntryBdsDxe.c").read_text()
    require(code.count("AuditFiveFdtWindows (Snapshot, Size, Source);")==1 and
            "THOR_MEM_PROBE_BYTES 0x10000000ULL" in code,"one immutable snapshot feeding bounded5x256MiB queries")
    for address in ("0xBAC00000ULL","0xCAC00000ULL","0x800000000ULL","0x810000000ULL","0x820000000ULL"):
        require(address in code,"five candidate address remains bound: "+address)
    show=code[code.index("STATIC VOID ShowEntry ("):code.index("STATIC BOOLEAN TransportReady (")]
    require(show.count("ShowFiveFdtWindows (Gop);")==1 and
            show.index("HudTop (Gop, mEntry.Stage")<show.index("Entry10GCheckpoint (Gop, Status == EFI_SUCCESS ? 3 : 4)")<
            show.index("ShowFiveFdtWindows (Gop);"),"final full clear precedes checkpoint and all FDT details")
    retained=code[code.index("STATIC EFI_STATUS RunRetainedAndCommit ("):code.index("STATIC VOID CleanupBeforeCommit (")]
    require(retained.index("ShowEntry (Gop, EFI_SUCCESS);")<retained.index("ThorEntry06PreEbsProof"),
            "successful handoff re-renders final FDT before EBS proof")
    entry=code[code.index("STATIC VOID EFIAPI ThorEntryBdsEntry ("):]
    require("ShowEntry (Gop, Status);" in entry and "ResetAfter (Gop, Status);" in entry and
            "ShowFiveFdtWindows (Gop);" not in entry[entry.index("Done:"):],
            "failure path uses final canvas without a later erase")
    dependency_path=pkg.parent/"libfdt-host-dependency.json"
    dependency=json.loads(dependency_path.read_text())
    require(dependency["schema"]=="thor-mem03-pinned-libfdt-host-dependency-v1" and dependency["result"]=="PASS"
            and dependency["libfdt_gitlink"]=="cfff805481bdea27f900c32698171286542b8d3c"
            and dependency["Mu_Basecore_gitlink"]=="cfb93724a06ff86ff5ba971413368a7809ee42a5"
            and dependency["git_blobs_matched_i386_build_source"] is True,
            "host C regression dependency comes from exact nested libfdt Git pin used by real build")
    require(dependency["Mu_Basecore_gitlink"] in subprocess.check_output(
            ["git","-C",str(src),"ls-tree","HEAD","Mu_Basecore"],text=True),
            "materialized host test dependency matches actual UEFI submodule Git pin")
    required_host_sources={"fdt.c","fdt_ro.c","fdt_check.c","fdt.h","libfdt.h","libfdt_env.h","libfdt_internal.h"}
    require({Path(item["path"]).name for item in dependency["files"]}==required_host_sources,
            "complete seven-file host libfdt dependency set")
    for item in dependency["files"]:
        relative=Path(item["path"])
        require(not relative.is_absolute() and ".." not in relative.parts,"bounded host dependency path")
        path=src/relative
        require(path.stat().st_size==item["bytes"] and sha(path)==item["sha256"],
                "same pinned host libfdt source bytes: "+path.name)
    for tag,name,count in (("mem01","test_mem01_5_window_readonly.py",12),
                           ("mem02","test_mem02_final_overlay.py",10),
                           ("mem03","test_mem03_snapshot_detail.py",12)):
        test=src/"Platforms/Xiaomi/thorPkg/Tests"/name
        result=subprocess.run([sys.executable,str(test),"-q"],capture_output=True,text=True,timeout=60)
        require(result.returncode==0 and ("Ran %d tests"%count) in result.stderr and detail["tests"][tag]==count,
                "independent source regression replay: "+name)
        require(str(test.relative_to(src)) in source_pins,"test source hash pinned: "+name)
    bundle=(pkg/"kernel-bundle.bin").read_bytes()
    validate_boot_bytes(boot.read_bytes(),(pkg/"kernel.gz-dtb").read_bytes(),bundle)
    require(True,"independent Boot v4 binary section and exact padding reparse")
    others=[]
    for directory in (root/"runs",root/"evidence/build"):
        if directory.is_dir():
            for historical in directory.rglob("manifest.json"):
                if historical.resolve()==(pkg/"manifest.json").resolve():
                    continue
                try:
                    old=json.loads(historical.read_text())
                except (ValueError,UnicodeError):
                    continue
                if isinstance(old,dict):
                    if (historical.parent/"ONE_SHOT_USED-DO-NOT-BOOT-AGAIN").exists():
                        require(old.get("image_sha256")!=sha(boot),"consumed mirror cannot be reused")
                    if old.get("run_id")==manifest["run_id"]:
                        require(old.get("image_sha256")==sha(boot) and old.get("seed_sha256")==manifest["seed_sha256"],
                                "same-run manifest mirrors have identical new image and seed hashes")
                        continue
                    others.append(old)
    reject_reuse(sha(boot),manifest["seed_sha256"],others,CONSUMED_BASELINES)
    require(True,"new image and entropy digest differ from every historical package")
    tools=manifest["package_tools"]
    interpreter=tools["python"]
    require(interpreter["path"]==str(Path(sys.executable).resolve()) and
            interpreter["version"]==list(sys.version_info[:3]),"packaging exact Python executable/version pinned")
    pin(Path(interpreter["path"]),interpreter["sha256"],"packaging Python executable SHA256")
    pin(root/"toolchains/android-boot-tools/package-tools.json",tools["manifest_sha256"],"AOSP tool manifest immutable identity")
    require(set(tools["scripts"])=={"mkbootimg","unpack_bootimg","image_verifier","gki_certificate","inspect_thor_fdt"},
            "all AOSP boot parser/packer support sources pinned")
    for name,item in tools["scripts"].items():
        path=Path(item["path"]).resolve()
        require(path.is_relative_to(root),"boot utility source inside project")
        pin(path,item["sha256"],"boot utility source hash: "+name)
    for record in manifest["package_commands"]:
        argv=record["argv"]
        require(len(argv)>=4 and argv[:3]==[interpreter["path"],"-E","-s"] and
                argv[3] in {item["path"] for item in tools["scripts"].values()},
                "packaging commands use pinned interpreter and scripts without ambient PATH")
    pin(Path(__file__).with_name("package.py"),tools["packager_sha256"],"exact reviewed packager source identity")
    pin(Path(__file__).with_name("package_host.py"),tools["helper_sha256"],"exact reviewed CSPRNG/toolchain helper identity")
    stock=tomllib.loads((root/"thor-build.toml").read_text())["stock"]
    shim=(pkg/"BootShim.bin").read_bytes()
    dtb=(pkg/"thor-stock-vendor-dtb.bin").read_bytes()
    require(hashlib.sha256(shim).hexdigest()==stock["bootshim_sha256"] and len(shim)==112,"immutable stock boot shim independently checked")
    require(hashlib.sha256(dtb).hexdigest()==stock["dtb_sha256"] and len(dtb)==6443651,"immutable stock vendorDTB independently checked")
    decompressor=zlib.decompressobj(16+zlib.MAX_WBITS)
    compressed=(pkg/"kernel.gz-dtb").read_bytes()
    unpacked=decompressor.decompress(compressed)
    require(decompressor.eof and not decompressor.unconsumed_tail and unpacked==shim+fd.read_bytes() and decompressor.unused_data==dtb,"actual gzip shim/firmware and trailing stockDTB independently reconstructed")
    require(not (root/"runs"/manifest["run_id"]/"l3").exists(),"no MEM03 L3 before recorded authorization")
    adapter=Path(__file__).with_name("firmware_audit_replay.py")
    spec=importlib.util.spec_from_file_location("mem03_firmware_replay",adapter)
    replay_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(replay_module)
    with tempfile.TemporaryDirectory(prefix="mem03-independent-firmware-",dir=pkg.parent) as directory:
        fresh_firmware=replay_module.replay(root,src,firmware_audit_path,transfer_index,Path(directory))
    require(fresh_firmware["result"]=="PASS" and fresh_firmware["modules"]==20
            and fresh_firmware["link_inputs"]==96 and fresh_firmware["volumes"]==2,
            "fresh full FV/FFS/LZMA, 20 PE/TE and96 linker-input audit matches original semantics")
    return {"fresh_firmware_replay":fresh_firmware,"libfdt_host_dependency_sha256":sha(dependency_path)}


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root",type=Path,required=True)
    ap.add_argument("--run-id",required=True)
    ap.add_argument("--candidate",type=Path,required=True)
    ap.add_argument("--source",type=Path,required=True)
    ap.add_argument("--firmware-release",type=Path,required=True)
    ap.add_argument("--firmware-audit",type=Path,required=True)
    ap.add_argument("--xnu-source",type=Path,required=True)
    ap.add_argument("--xnu-proof",type=Path,required=True)
    ap.add_argument("--uefi-detail-proof",type=Path,required=True)
    ap.add_argument("--public-transfer-index",type=Path,required=True)
    ap.add_argument("--check-only",action="store_true")
    args=ap.parse_args()
    root=args.root.resolve()
    check(re.fullmatch(r"thor-[0-9a-z-]{6,96}",args.run_id),"valid unique run ID")
    def path(p):
        f=(root/p).resolve()
        check(f.is_relative_to(root),"input path inside project root")
        return f
    src=path(args.source)
    candidate=path(args.candidate)
    firmware_release=path(args.firmware_release)
    firmware_audit=path(args.firmware_audit)
    pkg=path(Path("runs")/args.run_id/"l2/package")
    proof= pkg/"independent-offline.json"
    check(pkg.is_dir() and (args.check_only or not proof.exists()),"new immutable review output")
    marks=[]
    def require(cond,why):
        marks.append({"contract":why,"passed":bool(cond)})
        if not cond:raise RuntimeError("ENTRY VM independent review FAILED: "+why)
    manifest=json.loads((pkg/"manifest.json").read_text())
    receipt=json.loads(firmware_release.read_text())
    audit=json.loads(firmware_audit.read_text())
    layout=json.loads((candidate/"kernel-layout.json").read_text())
    build_receipt=json.loads((candidate/"build-manifest.json").read_text())
    build=json.loads((candidate/"manifest.json").read_text())
    machine=json.loads((candidate/"machinecode.json").read_text())
    boot=pkg/"thor-boot.img"
    kernel=candidate/"kernel.macho"
    fd=src/"Build/thorEntry09/RELEASE_CLANGPDB/FV/SILICIUM_UEFI.fd"
    prov=src/"Platforms/Xiaomi/thorPkg/Drivers/ThorEntryBdsDxe/VM_POST_TTBR1_PROVENANCE.json"
    binding=json.loads(prov.read_text())
    xsrc=path(args.xnu_source)
    xproof=path(args.xnu_proof)
    uproof=path(args.uefi_detail_proof)
    transfer_index=path(args.public_transfer_index)
    extra=json.loads(xproof.read_text())
    detail=json.loads(uproof.read_text())
    require(build_receipt["schema"]=="thor-mem03-build-manifest-v1"
            and build_receipt["final_HEAD"]==build["xnu_commit"]
            and build_receipt["actual_build_source_commit"]==build["build_source_commit"]
            and build_receipt["kernel_sha256"]==build["kernel_sha256"]
            and build_receipt["compiled_source_content_equivalent_to_final_HEAD"] is True
            and build["compiled_source_content_equivalent_to_final_HEAD"] is True,
            "frozen build receipt and artifact lock identify actual build source and final audited source")
    require(manifest["kernel_build_source_revision"]==build["build_source_commit"]
            and manifest["kernel_artifact_manifest_sha256"]==sha(candidate/"manifest.json")
            and manifest["kernel_build_manifest_sha256"]==sha(candidate/"build-manifest.json"),
            "packed kernel source/build trace points to the exact two frozen candidate receipts")
    require(build_receipt["post_build_changes"]==build["post_build_changes"]==
            ["scripts/audit-thor-mem03-linked.py","tests/test_thor_mem03_fb_contract.py"],
            "post-build additions limited to exact audited script and its regressions")
    delta=subprocess.check_output(["git","-C",str(xsrc),"diff","--name-only",build["build_source_commit"],build["xnu_commit"]],text=True).splitlines()
    require(delta==build["post_build_changes"],"actual Git diff matches frozen no-code-change build equivalence")
    require(sha(candidate/"kernel-layout.json")==build_receipt["layout_sha256"]
            and sha(candidate/"machinecode.json")==build_receipt["machinecode_sha256"]==build["machinecheck_sha256"]
            and sha(xproof)==build_receipt["coverage_machinecode_sha256"]==build["mem03_coverage_machinecheck_sha256"],
            "all three exact layout/machinecode/newcoverage receipt digests pinned")
    reproducible=(root/Path(build_receipt["receipts"]["link"]).parent.parent/"mem03-reproducible-link.json").resolve()
    require(reproducible.is_relative_to(root),"repeat-link proof stays inside project")
    require(sha(reproducible)==build_receipt["reproducible_link_sha256"],"actual repeat-link proof identity pinned")
    repeated=json.loads(reproducible.read_text())
    require(repeated["result"]=="PASS_TWO_REAL_LINKS_IDENTICAL"
            and repeated["kernel_sha256"]==build["kernel_sha256"]
            and repeated["source_commit"]==build["build_source_commit"]
            and repeated["second_link_output_removed_before_build"] is True,
            "two independent real links used same actual build source and produced identical kernel")
    for relative in (repeated["first_link"],repeated["second_link"]):
        record_path=(reproducible.parent/relative).resolve()
        require(record_path.is_relative_to(reproducible.parent),"bounded link receipt path")
        linked=json.loads(record_path.read_text())
        require(linked["kernel"]["sha256"]==build["kernel_sha256"]
                and linked["worktree_head"]==build["build_source_commit"]
                and linked["phase"]=="kernel-linked-and-sealed"
                and all(c["returncode"]==0 for c in linked["commands"]),
                "actual sealed build link logs agree with reproducibility record")
        for record in linked["commands"]:
            log=Path(record["log"])
            require(log.is_relative_to(root) and sha(log)==record["log_sha256"],"actual link stdout/stderr log digest pinned")
    regression=subprocess.run([sys.executable,str(xsrc/"tests/test_thor_mem03_fb_contract.py"),"-q"],capture_output=True,text=True,timeout=60)
    require(regression.returncode==0 and "Ran 23 tests" in regression.stderr
            and build_receipt["regression_tests"]=={"passed":23,"failed":0},
            "23 actual XNU bootstrap/PTE/failure regression tests independently replayed")
    require(build.get("HW11_grid_4x4_compiled") is True,"MEM03 inherits bounded HW11 4x4 native path")
    require("mem03" in args.run_id,"MEM03 unique run identity")
    require(manifest["schema"]=="thor-one-use-image-v2","correct one-use manifest schema")
    require(manifest["run_id"]==args.run_id,"unique immutable run ID")
    require(boot.is_file() and boot.stat().st_size==manifest["image_bytes"] and boot.stat().st_size<64*1024*1024,"bounded actual Android Boot v4 length")
    image_hash=sha(boot)
    require(image_hash==manifest["image_sha256"],"boot image SHA256 matches manifest")
    require(sha(pkg/"SILICIUM_UEFI.fd")==receipt["uefi_fd_sha256"],"packed firmware FD identity")
    require(sha(fd)==receipt["uefi_fd_sha256"]==audit["fd"]["sha256"],"built FD matches independently audited FD")
    require(fd.stat().st_size==receipt["uefi_fd_bytes"]==audit["fd"]["bytes"]==3145728,"exact reviewed3MiB firmware region")
    require(audit["status"]=="PASS" and not audit["failures"],"firmware audit has no failures")
    require(len(audit["volumes"])==2,"exactly two firmware volumes")
    require(len(audit["actual_modules"])==20,"20 executable UEFI modules")
    require(len(audit["unique_link_inputs"])==96,"96 source-linked UEFI inputs")
    require(sha(firmware_audit)==receipt["firmware_audit_sha256"],"auditor receipt SHA256 pinned")
    require(kernel.is_file() and kernel.stat().st_size==layout["kernel_bytes"],"Mach-O actual bytes and layout")
    kernel_hash=sha(kernel)
    require(kernel_hash==manifest["kernel_sha256"]==build["kernel_sha256"]==receipt["kernel_sha256"],"frozen source kernel SHA256")
    require(kernel_hash==machine["kernel_sha256"],"machine-code proof is for identical kernel")
    if build.get("HW11_grid_4x4_compiled") is True:
        require(machine["result"]==
            "PASS_COMPILED_HW11_TRIPLE_HEIGHT_4X4_315PAGE_HIGH_VA_GRID",
            "HW11 real linked 4x4 high-VA tile grid and native monotonic checkpoints")
        require(sha(candidate/"machinecode.json")==build["machinecheck_sha256"],
            "HW11 full linked machine-code proof pinned to frozen kernel")
        require(build["HW11_grid_height_3x_compiled"]
            and build["HW11_315page_alias_compiled"]
            and build["HW11_16cells_11stages_compiled"],
            "11 checkpoint, 315-page triple-height grid and native AuxKC flags")
        geometry=machine["tile_geometry"]
        require(geometry["rows"]==4 and geometry["columns"]==4
            and len(geometry["cells"])==16 and
            geometry["tile_width_pixels"]==328
            and geometry["tile_height_pixels"]==42,
            "four by four independently separated display cells")
        require(geometry["fb_existing_owned_pages"]==315
            and geometry["fb_existing_owned_window_physical"]==
            ["0xb805a000","0xb8195000"]
            and geometry["new_high_alias_L3_indices"]==[0,314],
            "new grid aliases exactly y64..287 framebuffer pages and no DRAM")
        require(geometry["old_hw07_alias_L3_indices"]==[480,499]
            and not geometry["overlap_with_old_alias_indices"]
            and geometry["new_page_table_allocations"]==0
            and geometry["all_grid_tiles_in_bounds"],
            "no framebuffer tile escapes verified page bounds or reuses old L3")
        require(geometry["gutter_horizontal_pixels"]==24
            and geometry["gutter_vertical_pixels"]==12,
            "vertical 3x height with larger 12px row gap")
        require(sorted(e["slot"] for e in geometry["cells"])==list(range(16))
            and all(e["relative_xywh"][2:]==[328,42] for e in geometry["cells"]),
            "16 unique immutable tile footprints")
        require(all(e["relative_xywh"]==[
              16+352*e["column"],8+54*e["row"],328,42]
              for e in geometry["cells"]),
              "all 16 grid cell locations exactly bound to 3x height and 12px gaps")
        require(all(e["row"]==e["slot"]//4 and e["column"]==e["slot"]%4
              for e in geometry["cells"]),
              "unique 4x4 row/column/slot index semantics")
        require(geometry["tile_height_pixels"]==3*14,
              "cell height is exactly three times HW10")
        stage=machine["linked_native_arm_vm"]
        require(stage["real_direct_stage_slots"]==[4,5,7,8,9],
            "five direct native post-retirement checkpoints exist in linked code")
        require(stage["native_TTBR0_and_TLBI"]
            and stage["pointer_rebind_after_stage4_before_stage5"]
            and stage["native_auxkc_call_between_stage7_and_stage8"]
            and stage["no_pmap_call_before_bounded_stop"],
            "native TTBR0, pointer updates, real AuxKC and bounded pre-pmap order")
        require(len(stage["call_order"])==14 and
            [entry[1] for entry in stage["call_order"]]==
            sorted(entry[1] for entry in stage["call_order"]),
            "14 native stage/call entries ordered in linked Mach-O")
        linked=machine["linked_grid_helpers"]
        require(linked["grid_315_page_install_with_TLBI"]
            and linked["init_16_dark_cells_4_baselines"]
            and linked["per_cell_real_pixel_store_and_CVAC"]
            and linked["mask_monotonic_enforced_in_linked_machinecode"],
            "315 leaf PTE install, actual stores, cache clean, consecutive bitmask")
        require(linked["stage6_guard_after_8_EL1_checks_2_writable_probes"]
            and linked["stage10_after_7_EL1_checks_4_PA_probes"]
            and linked["no_overlap_with_previous_HW09_marker"],
            "guarded orange and white slot and no overlapping old marker")
        aux=machine["native_auxkc"]
        require(aux["native_noinline_auxkc"]
            and aux["null_boot_globals_fast_return_compiled"],
            "real native AuxKC no-addon fast exit remains in final executable")
        late=machine["linked_pre_pmap_CFG"]
        require(late["linked_cfg_states"]==243
            and late["white_only_after_7_EL1_4_PA_checks"]
            and late["abort_paths"]>=1
            and late["never_returns_to_unvalidated_pmap"],
            "243 pre-pmap CFG states, white only after all high translation guards")
        require(set(machine["fault_injection_rejected"])=={
            "skip_TTBR0_TLBI","skip_native_AuxKC","change_stage7_id",
            "unsafe_stage10_low_FB","wrong_FB_physical_window",
            "nonmonotonic_stage_mask",
            "unsafe_tile_height","unsafe_new_PA_extent","unsafe_L3_index"} and
            all(machine["fault_injection_rejected"].values()),
            "nine unsafe source/linked-machine mutations all rejected")
        require(machine["hardware_tested"] is False and
            machine["device_actions"]==0
            and machine["single_use_required"],
            "HW11 remains offline only until unique recorded hardware test")
        require(machine["pmap_bootstrap_executed"] is False
            and machine["arm_vm_init_returned"] is False
            and build["TTBR0_empty_until_PSCI_reboot"]
            and not build["old_TTBR0_restored_in_candidate"]
            and not build["full_vm_boot"],
            "no claim of pmap, full arm_vm_init or kernel userspace boot")
    actual=MachO(kernel.read_bytes())
    require(actual.base==layout["virtual_base"] and actual.end==layout["virtual_end"] and actual.end-actual.base==layout["loaded_span_bytes"],"Mach-O actual segment span independently parsed")
    require(actual.loaded_hash==layout["loaded_span_sha256"] and len(actual.segments)==layout["segment_count"],"actual loaded segment bytes and count independently reconstructed")
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
    require(len(bundle)==manifest["bundle_bytes"]==align(max(128+kernel.stat().st_size,19289048)),"complete one-time kernel bundle length derived from actual Mach-O")
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
    require(image_hash not in CONSUMED_BASELINES,"consumed MEM01/MEM02 historical images never reused")
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
    if build.get("HW11_grid_4x4_compiled") is True:
        require(b"RUN MEM03 / THOR SM8475" in binary,
            "final UEFI linked PE identifies VM-HW11 candidate")
        hud=src/"Platforms/Xiaomi/thorPkg/Drivers/ThorEntryBdsDxe/ThorReportHud.inc.c"
        require(sha(hud)==receipt["new_ui_source_sha256"],
            "final HW11 UEFI HUD SHA equals signed-by-receipt source")
        ui_receipt=firmware_release.parent/"ui-stage-grid-audit.json"
        require(ui_receipt.is_file() and
            sha(ui_receipt)==receipt["new_ui_layout_audit_sha256"],
            "HW11 independent XNU 4x4 grid provenance attached to exact UEFI build")
        ui_meta=json.loads(ui_receipt.read_text())
        require(ui_meta["result"]=="PASS_LINKED_MEM03_AND_RESERVED_GOP_Y0_319"
            and ui_meta["grid_size"]==[4,4]
            and ui_meta["tile_size_pixels"]==[328,42]
            and ui_meta["xnu_mapped_screen_y"]==[64,287],
            "HW11 42px 4x4 grid drawn by XNU in y64..287")
        require(ui_meta["tile_vertical_gap_pixels"]==12
              and ui_meta["gop_y_reserved"]==[0,319]
              and ui_meta["uefi_first_postclear_draw_y"]==352
              and ui_meta["detail_overlay_xywh"]==[30,1925,1380,1153],
              "all linked UEFI display components remain clear of XNU y0..319")
        require(ui_meta["hud_sha256"]==sha(hud) and ui_meta["pe_sha256"]==sha(pe)
              and ui_meta["fd_sha256"]==sha(fd) and
              ui_meta["grid_machinecode_sha256"]==sha(candidate/"machinecode.json"),
              "current UI/grid provenance binds actual new source, PE, FD and XNU proof")
        require(ui_meta["never_calls_GOP_after_EBS"] is True and
              ui_meta["last_fullscreen_clear_followed_by_five_FDT_rows"] is True,
              "bounded UEFI final redraw remains before EBS")
        require(binding["hw11_hud_reservation"]["xnu_y_end_inclusive"]==319 and
              binding["generated_files_sha256"]["ThorReportHud.inc.c"]==sha(hud),
              "new generated provenance pins actual current HUD and reserved top range")
        require(receipt["schema"]=="thor-mem03-fdt-detail-and-xnu-fb-coverage-v1" and
              receipt["source_level_regressions_passed"]==34 and
              receipt["no_write_to_candidate_windows"] is True and
              receipt["no_memory_grant_or_heap_extension"] is True and
              receipt["UEFI_reserved_top_y0_through_319"] is True,
              "MEM03 retains read-only candidate policy with all34 source regressions")
    extended=validate_mem03_inputs(root,pkg,src,xsrc,candidate,manifest,receipt,audit,layout,build,machine,binding,extra,detail,boot,kernel,fd,prov,xproof,uproof,firmware_audit,transfer_index,require)

    final={"schema":"thor-vm-independent-offline-gate-v1",
           "checked_at":datetime.datetime.now().astimezone().isoformat(),
           "result":"PASS","conditions_count":len(marks),"checks":marks,
           "inputs":{"android_boot_v4":{"sha256":image_hash,"bytes":boot.stat().st_size},
                     "native_xnu":{"sha256":kernel_hash,"bytes":kernel.stat().st_size},
                     "uefi_fd":{"sha256":sha(fd),"bytes":fd.stat().st_size}},
           "run_id":args.run_id,"verifier_sha256":sha(Path(__file__)),
           "xnu_mem03_proof_sha256":sha(xproof),"uefi_mem03_proof_sha256":sha(uproof),
           "requires_new_recording_authorization":True,"L3_authorized":False,
           "fresh_firmware_replay":extended["fresh_firmware_replay"],
           "libfdt_host_dependency_sha256":extended["libfdt_host_dependency_sha256"],
           "mem03_final_fdt_report_persists":True,
           "mem03_five_readonly_FDT_windows":True,
           "consumed_MEM01_MEM02_images_never_reused":True,
           "device_memory_ownership_granted":False,
           "parent_HW11_consumed":False,
           "ready_for_one_use_temporary_boot":True,
           "device_commands":0,"runtime_ttbr0_retired":False,
           "hardware_test_attempts":0}
    if not args.check_only:
        with proof.open("x") as f:json.dump(final,f,indent=2);f.write("\n")
    print(json.dumps({"result":final["result"],"conditions_count":len(marks),
                      "image_sha256":image_hash,"one_time_only":True,"phone_actions":0},indent=2))


if __name__=="__main__":
    main()
