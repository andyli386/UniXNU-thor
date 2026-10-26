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
    check(start == 0xb805a000 and end == 0xb8195000, "MEM04 exact framebuffer physical range")
    check(producer_pages == consumer_pages == 315, "bootstrap and consumer both cover315 pages")
    check(end-start == producer_pages*4096, "framebuffer exact page extent")
    check(90+producer_pages <= 512, "bootstrap framebuffer leaves fit existing L3")
    check(flags == 0x006000000000070b, "unchanged framebuffer leaf attributes")
    return [start+i*4096 for i in range(consumer_pages)]


def validate_uefi_detail_document(detail):
    check(detail["schema"]=="thor-mem04-stage-safe-hud-host-proof-v1", "MEM04 detail schema")
    check(detail["result"]=="PASS_MEM04_STAGE_SAFE_HUD_AND_PMAP_BINDING", "MEM04 detail result")
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
    "04802a8172f122f97fecd7f5da313212196cd2e4853dbf4243c56444029f12d2",
}
TRANSITION_SHA = "8ce4b52c64f2436845be90affac50ed0fe86f5f4b8072780ba8ffe1492962f36"
OLD_KERNEL_SHA = "623b444dc57fc8981562277f1eade9f1ed25818badaa3bfd03ae7ff54a5333aa"


def validate_mem04_inputs(root,pkg,src,xsrc,candidate,manifest,receipt,audit,layout,
                         build,machine,binding,extra,detail,boot,kernel,fd,prov,
                         xproof,uproof,firmware_audit_path,transfer_index,require,replay_xnu=True):
    def pin(path, expected, why):
        require(path.is_file() and sha(path)==expected, why)

    require(manifest["seed_source"] in {"linux-os.getrandom(flags=0)","darwin-os.urandom"},
            "supported OS CSPRNG with explicit source")
    require(receipt["linked_transition_sha256"]==TRANSITION_SHA and
            receipt["transition_pe_relocations_in_interval"]==0 and
            receipt["transition_sha_matches_expected_header"] is True,
            "reviewed immediate-rebound500-byte transition, no relocations")
    require(receipt["temporary_boot_only"] is True and receipt["phone_actions"]==0
            and receipt["hardware_tested"] is False and not receipt["pmap_bootstrap_executed"],
            "new firmware remains untested temporary bounded diagnostic")
    require(sha(kernel)!=OLD_KERNEL_SHA and manifest["uefi_commit"]!=
            "bb8382c6417adf779b89adfde295832e846cee93", "new MEM04 source/binary identities")
    require(subprocess.check_output(["git","-C",str(xsrc),"rev-parse","HEAD"],text=True).strip()
            ==build["xnu_commit"], "exact XNU source checkout revision")
    require(not subprocess.check_output(["git","-C",str(xsrc),"status","--porcelain"],text=True).strip(),
            "XNU source checkout clean")
    kernel_contract=validate_mem04_xnu(root,pkg,xsrc,candidate,kernel,extra,xproof,require,replay_xnu)
    transition_proof=uproof.parent/"postlink-transition/mem04-transition-binding.json"
    require(sha(transition_proof)==receipt["transition_binding_audit_sha256"]==detail["transition_binding_audit_sha256"],
            "actual immediate-rebind proof identity is pinned by both release and detail")
    transition_contract=validate_mem04_transition(root,src,layout,require,transition_proof)
    validate_uefi_detail_document(detail)
    require(sha(uproof)==receipt["mem04_uefi_proof_sha256"],"FDT detail proof immutable hash bound to firmware release")
    require(detail["schema"]=="thor-mem04-stage-safe-hud-host-proof-v1" and
            detail["result"]=="PASS_MEM04_STAGE_SAFE_HUD_AND_PMAP_BINDING",
            "dedicated new MEM04 FDT detail host proof")
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
            "MEM04 detail proof bound to actual final firmware bytes")
    require(detail["transition"]["bytes"]==500 and detail["transition"]["sha256"]==TRANSITION_SHA,
            "MEM04 detail proof preserves exact transition")
    source_pins=detail["source_files_sha256"]
    require(len(source_pins)>=7,"FDT detail source/helper/test identities all pinned")
    for relative,digest in source_pins.items():
        path=(src/relative).resolve()
        require(path.is_relative_to(src),"FDT source hash path bounded")
        pin(path,digest,"same final FDT source file: "+relative)
    adt=src/"Platforms/Xiaomi/thorPkg/Drivers/ThorEntryBdsDxe"
    pin(adt/"ThorEntryBdsDxe.c",receipt["new_bds_source_sha256"],"MEM04 final BDS source bound to firmware release")
    pin(adt/"thor_fdt_gate.c",receipt["unchanged_fdt_validator_sha256"],"strict original FDT validator unchanged")
    memory=src/"Platforms/Xiaomi/thorPkg/Library/MemoryMapLib/MemoryMapLib.c"
    # The release pins the real project filename, not a guessed new map.
    if not memory.is_file():
        memory=src/"Platforms/Xiaomi/thorPkg/Library/MemoryMapLib/ThorMemoryMapLib.c"
    pin(memory,receipt["unchanged_memorymap_source_sha256"],"original memory map unchanged")
    pe=src/"Build/thorEntry09/RELEASE_CLANGPDB/AARCH64/thorPkg/Drivers/ThorEntryBdsDxe/ThorEntryBdsDxe/OUTPUT/ThorEntryBdsDxe.efi"
    binary=pe.read_bytes()
    require(b"RUN MEM04 / THOR SM8475" in binary and
            b"MEM04 FDT WINDOW ONLY - NO MEMORY GRANT" in binary and b"THOR FDT MEMDIAG 04" in binary,
            "actual final linked PE carries unique MEM04 diagnostic identity")
    require(all(label not in binary for label in (b"RUN MEM01 / THOR SM8475",b"RUN MEM02 / THOR SM8475",b"RUN MEM03 / THOR SM8475",
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
    require(dependency["schema"]=="thor-mem04-pinned-libfdt-host-dependency-v1" and dependency["result"]=="PASS"
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
    require(detail["tests"]["mem04"]==11 and "Platforms/Xiaomi/thorPkg/Tests/test_mem04_stage_render.py" in source_pins,"executed renderer suite and its source hash pinned")
    render_contract=replay_mem04_stage_render(root,pkg,src,detail,receipt,uproof,require)
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
    require(not (root/"runs"/manifest["run_id"]/"l3").exists(),"no MEM04 L3 before recorded authorization")
    adapter=Path(__file__).with_name("firmware_audit_replay.py")
    spec=importlib.util.spec_from_file_location("mem04_firmware_replay",adapter)
    replay_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(replay_module)
    with tempfile.TemporaryDirectory(prefix="mem04-independent-firmware-",dir=pkg.parent) as directory:
        fresh_firmware=replay_module.replay(root,src,firmware_audit_path,transfer_index,Path(directory))
    require(fresh_firmware["result"]=="PASS" and fresh_firmware["modules"]==20
            and fresh_firmware["link_inputs"]==96 and fresh_firmware["volumes"]==2,
            "fresh full FV/FFS/LZMA, 20 PE/TE and96 linker-input audit matches original semantics")
    return {"fresh_firmware_replay":fresh_firmware,"libfdt_host_dependency_sha256":sha(dependency_path),"compiled_kernel_frontier":kernel_contract,"executed_stage_render":render_contract,"transition_rebind":transition_contract}


def pmap_budget_contract(config):
    """Recompute this bounded 64MiB native pmap allocation from exact ABI facts."""
    required={"page_shift":12,"mem_size":64<<20,"pt_desc_size":64,
              "ptd_info_size":4,"pt_index_max":4,"pp_attr_size":2,
              "pv_pointer_size":8,"pte_size":8,"max_l2_cline":6,
              "pmap_max_asids":2048,"bitmap_word_size":8,
              "io_attr_table_size":0,"io_filter_table_size":0,
              "xnu_monitor":False,"has_guarded_io_filter":False,"has_16bit_asid":False}
    for key,value in required.items():
        check(config[key]==value,"native pmap ABI/configuration: "+key)
    page=1<<config["page_shift"]
    cline=1<<config["max_l2_cline"]
    ptd_info=config["ptd_info_size"]*config["pt_index_max"]
    per_page=(page-(cline-1))//(config["pt_desc_size"]+ptd_info)
    increment=1
    while align((per_page+increment)*config["pt_desc_size"],cline)+(per_page+increment)*ptd_info<=page:
        increment+=1
        check(increment<page,"bounded ptd layout search")
    per_page+=increment-1
    leaf_tables=config["mem_size"]//((page//config["pte_size"])*page)
    root_ptds=per_page*((leaf_tables*2)//per_page)+per_page
    ptd_root_size=(root_ptds//per_page)*page
    npages=config["mem_size"]//page
    pp_attr=npages*config["pp_attr_size"]
    pv_head=align(npages*config["pv_pointer_size"],page)
    metadata=align(pp_attr,page)+pv_head+ptd_root_size
    asid_raw=config["bitmap_word_size"]*((config["pmap_max_asids"]+63)//64)
    asid=align(asid_raw,page)
    total=metadata+asid
    check(per_page==51 and metadata==172032 and asid_raw==256 and total==176128,
          "native pmap budget recomputes42 metadata pages plus1 ASID page")
    return {"page_size":page,"managed_pages":npages,"ptd_per_page":per_page,
            "pp_attr_bytes":pp_attr,"pv_head_bytes":pv_head,"ptd_root_bytes":ptd_root_size,
            "metadata_bytes":metadata,"asid_raw_bytes":asid_raw,"asid_bytes":asid,
            "total_bytes":total,"total_pages":total//page}


def numbered_display_contract(palette,labels,states,font):
    check(len(palette)==len(labels)==len(states)==16,"sixteen distinct numbered checkpoint cells")
    check(len(set(palette))==16 and all(type(rgb) is int and 0<=rgb<=0xffffff and rgb!=0x202020 for rgb in palette),
          "sixteen unique24-bit completed colors distinct from WAIT")
    check(all(isinstance(label,str) and label.strip() and label.isascii() and len(label)<16 for label in labels),
          "readable complete checkpoint label per slot")
    check(all(state in {"WAIT","PASS","CALL","RETURN"} for state in states),"known cell state words")
    check(states[7:9]==["CALL","RETURN"] and states[11:14]==["CALL","RETURN","PASS"] and states[14:]==["WAIT","WAIT"],
          "real AuxKC/pmap call-return-guard and reserved states remain distinct")
    check(labels[11:14]==["PMAP CALL","PMAP RETURN","PMAP GUARD"] and labels[14:]==["RESERVED","RESERVED"],"pmap labels identify real call/return/guard and reserved slots")
    check(len(font)==252 and all(type(row) is int and 0<=row<32 for row in font),
          "complete36-glyph5x7 font")
    glyphs=[tuple(font[i*7:i*7+7]) for i in range(16)]
    check(len(set(glyphs))==16 and all(any(glyph) for glyph in glyphs),"unambiguous00-through0F hex IDs")


def rectangles_intersect(first,second):
    x,y,w,h=first;a,b,c,d=second
    return min(x+w,a+c)>max(x,a) and min(y+h,b+d)>max(y,b)


def executed_draw_events_contract(events):
    """Independently validate actual host-C draw events, including operation order."""
    check(isinstance(events,list) and events,"actual draw event trace present")
    frames={}
    for event in events:
        check(type(event["frame"]) is int and event["frame"]>0,"positive frame identity")
        frames.setdefault(event["frame"],[]).append(event)
    check(sorted(frames)==list(range(1,len(frames)+1)),"complete ordered frame IDs")
    names=[]
    for frame,items in frames.items():
        check(items[0]["kind"]=="begin" and items[-1]["kind"]=="pixels","frame starts/finishes explicitly")
        name=items[0]["name"];names.append(name)
        check(sum(item["kind"]=="begin" for item in items)==1 and
              sum(item["kind"]=="pixels" for item in items)==1,"single frame begin/pixel summary")
        check(items[1]["kind"]=="clear" and
              sum(item["kind"]=="clear" for item in items)==1,"one leading full-canvas clear per stage")
        live=[]
        for item in items[1:-1]:
            kind=item["kind"]
            check(kind in {"clear","fill","text"},"known real draw operation")
            rect=item["rect"]
            check(len(rect)==4 and all(type(value) is int for value in rect),"integer actual draw rectangle")
            x,y,w,h=rect
            if kind=="clear":
                check(rect==[0,0,1440,3200],"exact initial GOP clear")
                live=[]
                continue
            check(0<=x<=x+w<=1440 and 320<=y<y+h<=3200 and w>=0 and h>0,
                  "actual text/fill outside XNU rows and inside GOP: "+name)
            if kind=="text":
                value=item["text"];scale=item["scale"]
                check(isinstance(value,str) and value.isascii() and type(scale) is int and scale>0,
                      "complete ASCII actual font input")
                check(w==((len(value)*6-1)*scale if value else 0) and h==7*scale,
                      "text rectangle matches executed font dimensions")
                check(not any(rectangles_intersect(rect,old) for old in live),
                      "executed text rectangles do not overlap: "+name)
                if value:live.append(rect)
            else:
                check(not any(rectangles_intersect(rect,old) for old in live),
                      "executed later fill does not erase text: "+name)
        summary=items[-1]
        check(summary["forbidden_glyphs"]==0 and summary["glyph_pixels"]>10000,
              "real glyph stores executed and stay within GOP/XNU reservation")
        if name=="kernel_hash_pass":
            lines=[item for item in items if item["kind"]=="text" and
                   re.fullmatch("[0-9A-F]{32}",item["text"])]
            check(len(lines)==4 and all(1460<=item["rect"][1]<1900 for item in lines),
                  "four complete SHA halves remain clear of FDT region")
    check({"initial","read_permit","kernel_hash_pass","kernel_hash_refused",
           "loader_handoff_ready","loader_error_max_values","read_refused"}<=set(names),
          "real transport/hash/handoff/refusal cases all exercised")
    return {"frame_count":len(frames),"event_count":len(events),
            "all_text_bounds_and_operation_order_checked":True,
            "no_text_overlap_or_late_background_erasure":True}


def macho_symbol_bytes(actual,name,length):
    check(name in actual.symbols and type(length) is int and length>0,"required complete compiled data symbol")
    address=actual.symbols[name]
    matches=[segment for segment in actual.segments if segment[0]<=address and address+length<=segment[0]+segment[3]]
    check(len(matches)==1,"data symbol lies wholly in a file-backed segment")
    base,_,offset,_,segment=matches[0]
    check(segment in {"__TEXT","__TEXT_EXEC","__DATA_CONST"},"telemetry table in immutable/code segment")
    start=offset+address-base
    return actual.data[start:start+length]


def validate_mem04_xnu(root,pkg,xsrc,candidate,kernel,extra,xproof,require,replay=True):
    def pin(path,expected,why):
        require(path.is_file() and sha(path)==expected,why)
    require(extra["schema"]=="thor-mem04-grid-pmap-machinecode-v1" and
            extra["result"]=="PASS_MEM04_LINKED_READABLE_GRID_BOUNDED_NATIVE_PMAP" and
            extra["frontier"]=="pmap-return" and extra["kernel_sha256"]==sha(kernel),
            "MEM04 new exact kernel proof identifies bounded native pmap frontier")
    require(extra["native_pmap_bootstrap_call_compiled"] is True and extra["bounded_after_pmap_return"] is True
            and extra["hardware_tested"] is False and extra["phone_actions"]==0
            and extra["arm_vm_init_returned"] is False and extra["full_VM_initialization_proven"] is False,
            "compiled native pmap call/return is distinct from untested hardware/fullVM success")
    actual=MachO(kernel.read_bytes())
    producer=actual.symbols["_thor_entry09_build_tables"]
    producer_end=min(value for value in actual.symbols.values() if value>producer)
    sites=[address for address in range(producer,producer_end,4)
           if actual.word(address)&0xff80001f==0x52800010 and actual.movz(address,16)==315]
    require(len(sites)==1,"actual bootstrap producer independently decodes315 pages")
    low=extra["bootstrap_to_grid_contract"]
    coverage_contract(low["producer_pages"],low["consumer_pages"],int(low["physical_range"][0],0),
                      int(low["physical_range"][1],0),0x006000000000070b)
    require(low["bootstrap_L3_indices"]==[90,404] and 8192<low["actual_table_stores"]<20000 and
            10000<low["executed_integer_instructions"]<100000,"actual bounded bootstrap execution and indices")
    for key in ("full_pool_zero_before_install","all_315_identity_PTEs_and_attributes_match",
                "framebuffer_neighbors_unmapped","owned_RAM_identity_and_KVA_edges_preserved","callee_saved_registers_preserved"):
        require(low[key] is True,"actual bootstrap PTE contract: "+key)
    aliases=extra["high_alias_PTE_execution"]
    require(aliases["actual_PTE_stores"]==335 and aliases["grid_L3_indices"]==[0,314] and
            aliases["legacy_L3_indices"]==[480,499],"actual335 independent grid/legacy high-alias stores")
    for key in ("all_315_grid_PA_and_attributes_match","all_20_legacy_PA_and_attributes_match",
                "all_other_L3_slots_remain_zero","DSB_TLBI_DSB_ISB_order_preserved"):
        require(aliases[key] is True,"actual high-alias PTE contract: "+key)
    diagnostics=extra["linked_diagnostics"]
    require(diagnostics["actual_AT_ISB_immediate_PAR_sample"] is True and
            diagnostics["raw_PAR_passed_to_reject_x3"] is True and
            diagnostics["fault_pixels_cleaned_before_bounded_stop"] is True,
            "pre-retirement failures preserve real PAR and safe bounded diagnostic output")
    entry=extra["reject_entry_guard"]
    require(all(entry[key] is True for key in ("actual_TTBR0_read_before_pixels",
            "kva_active_branch_to_reset_before_pixels","TTBR0_mismatch_branch_to_reset_before_pixels")),
            "old low-VA diagnostic rejects retired or mismatched root before pixels")
    boundary=extra["retirement_boundary"]
    require(boundary["no_low_diagnostic_calls_in_retired_native_interval"] is True,
            "new text/pmap paths cannot reach low-VA diagnostic in retired interval")
    display=extra["display_source_and_geometry"]
    geometry=display["geometry"]
    require(geometry["rows"]==geometry["columns"]==4 and len(geometry["cells"])==16 and
            geometry["tile_width_pixels"]==328 and geometry["tile_height_pixels"]==42 and
            geometry["fb_existing_owned_pages"]==315 and geometry["new_high_alias_L3_indices"]==[0,314] and
            geometry["old_hw07_alias_L3_indices"]==[480,499] and geometry["new_page_table_allocations"]==0,
            "sixteen readable tiles remain inside proven315-page framebuffer aperture")
    require(geometry["fb_existing_owned_window_physical"]==["0xb805a000","0xb8195000"] and
            geometry["gutter_horizontal_pixels"]==24 and geometry["gutter_vertical_pixels"]==12 and
            not geometry["overlap_with_old_alias_indices"] and geometry["all_grid_tiles_in_bounds"],
            "new IDs/text do not enlarge or overlap framebuffer mapping")
    for cell in geometry["cells"]:
        require(cell["relative_xywh"]==[16+352*cell["column"],8+54*cell["row"],328,42] and
                cell["row"]==cell["slot"]//4 and cell["column"]==cell["slot"]%4,
                "actual unique bounded cell geometry: "+str(cell["slot"]))
    palette=list(struct.unpack("<16I",macho_symbol_bytes(actual,"_thor_mem04_palette",64)))
    label_bytes=macho_symbol_bytes(actual,"_thor_mem04_labels",256)
    state_bytes=macho_symbol_bytes(actual,"_thor_mem04_states",128)
    font=list(macho_symbol_bytes(actual,"_thor_mem04_font",252))
    labels=[label_bytes[i*16:i*16+16].split(b"\0")[0].decode("ascii") for i in range(16)]
    states=[state_bytes[i*8:i*8+8].split(b"\0")[0].decode("ascii") for i in range(16)]
    numbered_display_contract(palette,labels,states,font)
    arrays={"thor_mem04_palette":struct.pack("<16I",*palette),"thor_mem04_labels":label_bytes,
            "thor_mem04_states":state_bytes,"thor_mem04_font":bytes(font)}
    for name,data in arrays.items():
        require(extra["compiled_telemetry_arrays"][name]["sha256"]==hashlib.sha256(data).hexdigest(),
                "independent Mach-O data bytes match compiled telemetry proof: "+name)
    checkpoint=extra["checkpoint_contract"]
    require(checkpoint["compiled_stage_prefix"]==list(range(14)) and checkpoint["reserved_slots"]==[14,15]
            and checkpoint["number_base"]==16,"native pmap frontier explicitly activates00-through0D only")
    require(len(checkpoint["slots"])==16,"all sixteen checkpoint contracts retained")
    for index,slot in enumerate(checkpoint["slots"]):
        require(slot["slot"]==index and slot["id"]=="%02X"%index and slot["label"]==labels[index]
                and slot["state"]==states[index] and slot["color_hex"]=="#%06X"%palette[index]
                and slot["active_compiled_checkpoint"]==(index<14),
                "number/label/state/color comes from actual compiled symbol: "+str(index))
    renderer=extra["linked_renderer"]
    require(renderer["actual_glyph_pixel_stores"] is True and renderer["text_before_tile_cache_clean_and_DSB"] is True
            and renderer["no_low_framebuffer_constants"] is True,"compiled high-only glyph pixel writes are visible before stage completion")
    require(display["minimum_active_text_contrast"]>=4.5 and display["WAIT_text_contrast"]>=4.5 and
            display["hex_IDs_00_to_0F"] is True and display["labels_states_and_IDs_fit_each_328x42_tile"] is True and
            display["runtime_slot_palette_and_sequential_mask_gates"] is True,
            "readable text/contrast and monotonic slot-palette gating")
    sites=[event for values in extra["compiled_stage_sites"].values() for event in values]
    require(sorted(event["slot"] for event in sites)==list(range(14)),"all14 stages have unique real compiled calls")
    require(all(event["color"]=="#%06X"%palette[event["slot"]] for event in sites),
            "every compiled stage call selects its own actual palette color")
    pmap=extra["bounded_pmap_audit"]
    require(pmap["schema"]=="thor-mem04-bounded-native-pmap-v1" and
            pmap["result"]=="PASS_MEM04_BOUNDED_NATIVE_PMAP_METADATA" and pmap["kernel_sha256"]==sha(kernel),
            "native pmap arithmetic/write/control-flow audit belongs to exact kernel")
    budget=pmap["native_budget"];config=pmap["actual_compile_configuration"]
    normalized=dict(page_shift=budget["PAGE_SHIFT"],mem_size=64<<20,pt_desc_size=budget["pt_desc_bytes"],
                    ptd_info_size=budget["ptd_info_bytes"],pt_index_max=budget["PT_INDEX_MAX"],pp_attr_size=2,
                    pv_pointer_size=8,pte_size=8,max_l2_cline=budget["MAX_L2_CLINE"],pmap_max_asids=2048,
                    bitmap_word_size=8,io_attr_table_size=0,io_filter_table_size=0,
                    xnu_monitor=not config["non_MONITOR"],has_guarded_io_filter=not config["no_guarded_IO_filter"],has_16bit_asid=bool(config["HAS_16BIT_ASID"]))
    own_budget=pmap_budget_contract(normalized)
    require(own_budget["total_bytes"]==budget["total_bytes"] and own_budget["metadata_bytes"]==budget["metadata_bytes"]
            and own_budget["ptd_per_page"]==budget["ptd_per_page"] and own_budget["asid_bytes"]==budget["ASID_alloc_bytes"],
            "independent real-ABI computation proves42 metadata pages plus1 ASID page")
    require(config["MAX_L2_CLINE"]==6 and config["DWARF_pt_desc_bytes"]==64 and config["DWARF_pp_attr_uint16"] is True
            and config["no_KASAN"] is True,"actual preprocessed native pmap configuration matches bounded ABI")
    require(config["HAS_16BIT_ASID"]==0 and config["ASID_SHIFT"]==11 and config["MAX_ASIDS"]==2048,
            "actual compiler selects reviewed non16-bit ASID default2048")
    require(config["CPU_pmap_cpu_data_offset"]==608 and config["CPU_nested_number_offset"]==32
            and config["CPU_number_store_offset"]==640 and config["CPU_number_store_bytes"]==4,
            "actual DWARF CPU target is nested four-byte pmap number at offset0x280")
    cpu=pmap["helper_proofs"]
    require(cpu["current_CPUData_exact_VA_write_probe_before_CALL11"] is True and
            cpu["nested_member"]=="cpu_data_t.cpu_pmap_cpu_data.cpu_number",
            "exact actual CPU write VA is writable before native pmap call")
    linkage=cpu["BootCpuData_linkage"]
    require(linkage in actual.symbols and int(cpu["actual_original_CPU_VA"],0)==actual.symbols[linkage]+640,
            "actual checked CPU write VA matches exact linked static CPU object plus nested offset")
    at_word=actual.word(int(cpu["actual_writable_AT"],0))
    require(at_word&0xffffffe0==0xd5087820,"independent A64 decode confirms actual AT S1E1W instruction")
    store_word=actual.word(int(cpu["actual_native_STRW"],0))
    require(store_word&0xffc00000==0xb9000000 and ((store_word>>10)&0xfff)*4==640,
            "independent A64 decode confirms actual native four-byte CPU store at offset0x280")
    pin(xsrc/".cache/xnu-work/osfmk/arm/cpu_data_internal.h",cpu["cpu_data_header_sha256"],"actual nested CPU-data linkage header pinned")
    prefix=pmap["actual_native_PTD_geometry"]
    require(prefix["PAGE_SHIFT_input"]==12 and prefix["actual_ptd_per_page"]==51 and prefix["actual_integer_instructions"]>0,
            "actual native integer prefix independently yields51 PTDs per page")
    for phase,key in ((1,"phase1_guarded_zero"),(2,"phase2_guarded_zero")):
        require(pmap[key]["phase"]==phase and pmap[key]["guard_dominates_dynamic_zero"] is True,
                "actual metadata memset dominated by correct phase guard: "+str(phase))
    require(pmap["native_frontier"]["native_pmap_calls"]==1 and
            pmap["native_frontier"]["no_disable_preemption_or_alloc_ptpage_after_return"] is True and
            pmap["bounded_after_pmap_return"] is True and pmap["arm_vm_init_return_allowed"] is False and
            pmap["pmap_map_globals_allowed"] is False,"native pmap return cannot enter incompatible allocator or slot2 global mapping")
    require(pmap["source_checks"]["all_live_inputs_protected_before_slab"] is True and
            pmap["source_checks"]["all512_alias_slots_and335_RW_probes_checked_after_pmap"] is True and
            pmap["helper_proofs"]["bounded_finish_has_no_ret"] is True and
            pmap["helper_proofs"]["preflight_protected_object_calls"]>=5 and
            pmap["helper_proofs"]["static_lock_group_slab_budget_checked"] is True and
            pmap["helper_proofs"]["reviewed_overflow_failure_is_BRK_to_stackless_vector_reset"] is True,
            "pmap allocation protects live inputs and validates alias lifecycle before0D")
    require(all(pmap["fault_injection_rejected"].values()) and
            set(pmap["fault_injection_rejected"])>={"remove_phase1_write_guard","remove_phase2_write_guard"},
            "removing either actual native write guard is rejected")
    final=extra["pmap_return_boundary"]
    require(final["finish_has_no_ret"] is True and final["guard_has_high_only_reset_continuation"] is True,
            "post-pmap GUARD is high-only and ends in bounded reset")
    required_mutations={"producer_page_count_90","producer_page_count_314","producer_page_count_316",
                        "producer_page_count_423","producer_wrong_PA_stride","wrong_RW_attributes",
                        "wrong_cache_attributes","wrong_XN_attributes","last_page_missing"}
    require(set(extra["producer_fault_injection_rejected"])>=required_mutations and
            all(extra["producer_fault_injection_rejected"].values()),"all inherited bootstrap/PTE negative contracts remain active")
    auditor=xsrc/"scripts/audit-thor-mem04-linked.py";binding=extra["source_binding"]
    pin(auditor,binding["script_sha256"],"exact current MEM04 linked auditor source")
    pin(xsrc/"scripts/audit-thor-mem04-pmap.py",binding["pmap_audit_script_sha256"],"exact native pmap audit source")
    revision=binding["git_commit"]
    require(subprocess.run(["git","-C",str(xsrc),"merge-base","--is-ancestor",revision,"HEAD"],capture_output=True).returncode==0,
            "MEM04 proof source revision is an ancestor of frozen source")
    require(not subprocess.check_output(["git","-C",str(xsrc),"diff",revision,"HEAD","--","patches/xnu","scripts/prepare-thor-mem04-source.py"],text=True),
            "no prepared-kernel source changes after frozen proof")
    for relative,digest in binding["prepared_source_sha256"].items():
        path=Path(relative)
        check(not path.is_absolute() and ".." not in path.parts,"bounded prepared source hash path")
        pin(xsrc/".cache/xnu-work"/path,digest,"exact current prepared source: "+relative)
    if replay:
        with tempfile.TemporaryDirectory(prefix="mem04-independent-kernel-",dir=pkg.parent) as temporary:
            replay=Path(temporary)/"linked.json"
            process=subprocess.run([sys.executable,str(auditor),"--kernel",str(kernel),"--frontier","pmap-return","--out",str(replay)],
                                   capture_output=True,text=True,timeout=240)
            require(process.returncode==0 and replay.is_file(),"fresh actual MEM04 PTE/telemetry/pmap/DWARF/control-flow audit succeeds")
            current=json.loads(replay.read_text())
            def semantic_report(value):
                result={key:item for key,item in value.items() if key!="created_at"}
                result["source_binding"]={key:item for key,item in value["source_binding"].items() if key!="git_commit"}
                return result
            require(semantic_report(current)==semantic_report(extra),"fresh complete MEM04 machine-code proof matches frozen semantics")
    return {"native_pmap_call_compiled":True,"bounded_after_native_pmap_return":True,
            "independent_pmap_budget":own_budget,"runtime_pmap_execution_verified":False}


def replay_mem04_stage_render(root,pkg,src,detail,receipt,uproof,require):
    def pin(path,expected,why):
        require(path.is_file() and sha(path)==expected,why)
    base=uproof.parent
    audit_path=base/"stage-render-audit.json"
    trace_path=base/"stage-render-events.jsonl"
    harness_path=base/"stage-render-harness.c"
    pin(audit_path,detail["stage_render_audit_sha256"],"executed stage-render audit immutable identity")
    require(sha(audit_path)==receipt["stage_render_audit_sha256"],"same executed stage-render audit in firmware release")
    frozen=json.loads(audit_path.read_text())
    require(frozen["schema"]=="thor-mem04-executed-stage-render-event-audit-v1" and
            frozen["result"]=="PASS_ALL_STAGE_FRAMES_NO_OVERLAP" and frozen["frame_count"]==19,
            "nineteen actual production-C rendered frames verified")
    pin(trace_path,detail["stage_render_events_sha256"],"actual stage draw event trace hash")
    require(sha(trace_path)==frozen["host_trace_sha256"],"draw trace hash belongs to executed audit")
    pin(harness_path,frozen["host_harness_sha256"],"exact executed production rendering harness bytes")
    adt=src/"Platforms/Xiaomi/thorPkg/Drivers/ThorEntryBdsDxe"
    test=src/"Platforms/Xiaomi/thorPkg/Tests/test_mem04_stage_render.py"
    pin(adt/"ThorEntryBdsDxe.c",frozen["renderer_source_sha256"],"executed renderer is final bound BDS C source")
    pin(adt/"ThorReportHud.inc.c",frozen["hud_source_sha256"],"executed HUD is final production HUD source")
    pin(test,frozen["test_source_sha256"],"actual renderer test source pinned")
    require(frozen["unexpected_overlap_count"]==0 and frozen["text_bounds_valid"] is True and
            frozen["xnu_reserved_preserved"] is True and frozen["font_pixels_executed"] is True and
            frozen["render_functions_from_production_C"] is True and frozen["xnu_y_reserved"]==[0,319],
            "all actual transport/hash/handoff frames preserve text and XNU aperture")
    require("MEM03_LEGACY_UEFI2_OVERLAY" in frozen["negative_controls_rejected"],
            "actual previous UEFI2 overwrite is rejected by executed negative control")
    events=[json.loads(line) for line in trace_path.read_text().splitlines() if line]
    independent=executed_draw_events_contract(events)
    require(independent["frame_count"]==frozen["frame_count"] and independent["event_count"]==frozen["event_count"],
            "independent draw-order/font-geometry/overlap validation matches full trace")
    with tempfile.TemporaryDirectory(prefix="mem04-stage-render-",dir=pkg.parent) as temporary:
        output=Path(temporary)/"stage-render-audit.json"
        env=__import__("os").environ.copy()
        env["THOR_MEM04_RENDER_AUDIT"]=str(output)
        env["PATH"]="/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin"
        run=subprocess.run([sys.executable,"-B",str(test),"-q"],env=env,capture_output=True,text=True,timeout=120)
        require(run.returncode==0 and "Ran 11 tests" in run.stderr and output.is_file(),
                "fresh host compiler executes all11 actual renderer tests and nineteen frames")
        current=json.loads(output.read_text())
        require(current==frozen,"fresh executed C renderer/font/trace proof exactly equals frozen stage proof")
        pin(output.parent/"stage-render-events.jsonl",frozen["host_trace_sha256"],"fresh actual draw trace matches frozen bytes")
        pin(output.parent/"stage-render-harness.c",frozen["host_harness_sha256"],"fresh production rendering harness matches frozen bytes")
        executed_draw_events_contract([json.loads(line) for line in (output.parent/"stage-render-events.jsonl").read_text().splitlines() if line])
    symbols_path=base/"native-trace-symbol-audit.json"
    pin(symbols_path,detail["native_trace_symbol_audit_sha256"],"native trace callback symbol audit immutable identity")
    require(sha(symbols_path)==receipt["native_trace_symbol_audit_sha256"],"same native symbol audit bound to release")
    symbols=json.loads(symbols_path.read_text())
    require(symbols["schema"]=="thor-mem04-native-trace-symbol-audit-v1" and
            symbols["result"]=="PASS_NATIVE_NO_HOST_TRACE_CALLBACK" and
            symbols["host_trace_callback_referenced"] is False and detail["native_pe_without_host_trace_callback"] is True,
            "native firmware excludes all host-only draw callbacks")
    module=src/"Build/thorEntry09/RELEASE_CLANGPDB/AARCH64/thorPkg/Drivers/ThorEntryBdsDxe/ThorEntryBdsDxe/OUTPUT"
    pin(module/"ThorEntryBdsDxe.lib",symbols["native_bds_lib_sha256"],"actual native BDS library belongs to symbol proof")
    pin(module/"ThorEntryBdsDxe.efi",symbols["native_pe_sha256"],"actual native PE belongs to trace callback proof")
    font_source=(adt/"ThorEntryBdsDxe.c").read_text().split("STATIC CONST GLYPH mFont[]",1)[1].split("\n};",1)[0]
    glyphs=re.findall(r"\{'(.)',\{([0-9,]+)\}\}",font_source)
    font_bytes=b"".join(bytes([ord(character),*[int(value) for value in rows.split(",")]]) for character,rows in glyphs)
    require(len(glyphs)>40 and len(font_bytes)==len(glyphs)*8 and
            (module/"ThorEntryBdsDxe.efi").read_bytes().count(font_bytes)==1,
            "complete actual production UEFI font bitmap occurs exactly once in native PE")
    font_sha=hashlib.sha256(font_bytes).hexdigest()
    require(font_sha==frozen["font_bitmap_sha256"]==symbols["native_font_bitmap_sha256"]==detail["native_font_bitmap_sha256"]
            and len(font_bytes)==frozen["font_bitmap_bytes"]==symbols["native_font_bitmap_bytes"]==detail["native_font_bitmap_bytes"]
            and len(glyphs)==frozen["font_glyph_count"] and symbols["native_font_in_pe_exact_occurrences"]==1,
            "executed host font pixels are anchored to exact complete native PE font bytes")
    native=subprocess.run(["xcrun","llvm-nm","--undefined-only",str(module/"ThorEntryBdsDxe.lib")],capture_output=True,text=True,timeout=60)
    require(native.returncode==0 and "HudTraceText" not in native.stdout and
            b"HudTraceText" not in (module/"ThorEntryBdsDxe.efi").read_bytes(),
            "fresh native archive symbol query confirms no host trace callback dependency")
    return {"executed_frames":19,"stage_test_count":11,"actual_draw_trace_independently_checked":True,
            "fresh_production_C_renderer_replayed":True,"native_host_callback_absence_rechecked":True}


def validate_mem04_frozen_artifact(root,candidate,xsrc,xproof,require,replay_regressions=True):
    locked=json.loads((candidate/"manifest.json").read_text())
    built=json.loads((candidate/"build-manifest.json").read_text())
    layout=json.loads((candidate/"kernel-layout.json").read_text())
    extra=json.loads(xproof.read_text());kernel=candidate/"kernel.macho"
    actual=MachO(kernel.read_bytes())
    require(locked["schema"]=="thor-artifact-lock-v2" and built["schema"]=="thor-mem04-build-manifest-v1",
            "exact MEM04 build/lock schemas")
    require(sha(kernel)==locked["kernel_sha256"]==built["kernel_sha256"]==layout["kernel_sha256"]==extra["kernel_sha256"]
            and kernel.stat().st_size==locked["kernel_bytes"]==built["kernel_bytes"]==layout["kernel_bytes"],
            "actual new kernel bytes agree across every frozen receipt")
    require(actual.base==layout["virtual_base"] and actual.end==layout["virtual_end"] and
            actual.end-actual.base==layout["loaded_span_bytes"]==built["loaded_span_bytes"]==locked["loaded_span_bytes"]
            and actual.loaded_hash==layout["loaded_span_sha256"]==built["loaded_span_sha256"]==locked["loaded_span_sha256"],
            "independent Mach-O loaded geometry and bytes agree with all freeze files")
    require(built["final_HEAD"]==locked["xnu_commit"] and built["actual_build_source_commit"]==locked["build_source_commit"]
            and built["compiled_source_content_equivalent_to_final_HEAD"] is True
            and locked["compiled_source_content_equivalent_to_final_HEAD"] is True,
            "actual compilation source is distinguished from later audit/test commit")
    head=subprocess.check_output(["git","-C",str(xsrc),"rev-parse","HEAD"],text=True).strip()
    require(head==locked["xnu_commit"] and not subprocess.check_output(["git","-C",str(xsrc),"status","--porcelain"],text=True).strip(),
            "current XNU source checkout equals clean frozen final HEAD")
    delta=subprocess.check_output(["git","-C",str(xsrc),"diff","--name-only",locked["build_source_commit"],head],text=True).splitlines()
    allowed={"scripts/audit-thor-mem04-linked.py","scripts/audit-thor-mem04-pmap.py",
             "tests/test_thor_mem04_pmap.py","tests/test_thor_mem04_telemetry.py"}
    require(delta==built["post_build_changes"]==locked["post_build_changes"] and set(delta)<=allowed,
            "actual post-build Git changes consist only of declared auditor/test files")
    require(sha(candidate/"kernel-layout.json")==built["layout_sha256"] and
            sha(candidate/"machinecode.json")==built["machinecode_sha256"]==locked["machinecheck_sha256"]
            and sha(xproof)==built["coverage_machinecode_sha256"]==locked["mem04_machinecheck_sha256"],
            "exact layout and complete linked-machine-code proofs pinned")
    specialized=candidate/"mem04-pmap-machinecode-final.json"
    require(sha(specialized)==built["pmap_machinecode_sha256"]==locked["pmap_machinecheck_sha256"],
            "specialized native pmap proof identity pinned")
    domain=json.loads(specialized.read_text())
    require({key:value for key,value in domain.items() if key not in {"created_at","script_sha256"}}==extra["bounded_pmap_audit"],
            "separate specialized proof exactly agrees with main native pmap proof")
    require(domain["script_sha256"]==extra["source_binding"]["pmap_audit_script_sha256"],
            "main and specialized native pmap auditor source hashes agree")
    require(locked["frontier"]==built["frontier"]=="pmap-return" and
            locked["MEM04_compiled_stage_prefix"]==list(range(14)) and locked["MEM04_reserved_slots"]==[14,15] and
            locked["native_pmap_bootstrap_call_compiled"] is True and locked["bounded_after_pmap_return"] is True,
            "frozen artifact explicitly compiles bounded00-through0D native pmap frontier")
    require(locked["pmap_bootstrap_executed"] is False and built["hardware_tested"] is False and
            locked["arm_vm_init_returned"] is False and locked["full_vm_boot"] is False and
            locked["RAM_grant_increased"] is False and locked["phone_actions"]==0,
            "kernel freeze does not claim hardware or complete VM or expanded RAM")
    reproducible=(root/Path(built["receipts"]["link"]).parent.parent/"mem04-reproducible-link.json").resolve()
    require(reproducible.is_relative_to(root) and sha(reproducible)==built["reproducible_link_sha256"],
            "actual two-link reproducibility receipt pinned")
    repeat=json.loads(reproducible.read_text())
    require(repeat["result"]=="PASS_TWO_REAL_LINKS_IDENTICAL" and repeat["kernel_sha256"]==sha(kernel) and
            repeat["source_commit"]==locked["build_source_commit"] and repeat["second_link_output_removed_before_build"] is True,
            "two fresh real links compile actual source and produce identical new kernel")
    for key in ("first_link","second_link"):
        path=(reproducible.parent/repeat[key]).resolve()
        require(path.is_relative_to(root),"bounded actual link receipt path")
        linked=json.loads(path.read_text())
        require(linked["kernel"]["sha256"]==sha(kernel) and linked["worktree_head"]==locked["build_source_commit"]
                and linked["phase"]=="kernel-linked-and-sealed" and all(item["returncode"]==0 for item in linked["commands"]),
                "real sealed link output matches frozen kernel and build source")
        for command in linked["commands"]:
            log=Path(command["log"])
            require(log.is_relative_to(root) and sha(log)==command["log_sha256"],"actual link log bytes pinned")
    suites=(("telemetry","tests/test_thor_mem04_telemetry.py",46),("pmap","tests/test_thor_mem04_pmap.py",29))
    require(built["regression_tests"]=={"telemetry_passed":46,"pmap_passed":29,"passed":75,"failed":0},
            "actual frozen XNU regression inventory is46 telemetry plus29 pmap")
    if replay_regressions:
        for name,relative,count in suites:
            result=subprocess.run([sys.executable,"-B",str(xsrc/relative),"-q"],capture_output=True,text=True,timeout=120)
            require(result.returncode==0 and ("Ran %d tests"%count) in result.stderr,
                    "actual current XNU regression suite independently replayed: "+name)
    return {"kernel_sha256":sha(kernel),"build_source_commit":locked["build_source_commit"],
            "audited_source_commit":head,"regression_tests":75,"full_L2_ready":False}


def precheck_xnu_main():
    parser=argparse.ArgumentParser(description="MEM04 public XNU-only precheck; no seed/image or phone actions")
    parser.add_argument("--precheck-xnu",action="store_true",required=True)
    for name in ("root","candidate","xnu-source","xnu-proof","out"):
        parser.add_argument("--"+name,type=Path,required=True)
    args=parser.parse_args();root=args.root.resolve()
    def path(value):
        result=(root/value).resolve();check(result.is_relative_to(root),"bounded public precheck path");return result
    candidate=path(args.candidate);source=path(args.xnu_source);proof=path(args.xnu_proof);output=path(args.out)
    check(output.is_relative_to(root/"runs") and not output.exists(),"new immutable public precheck receipt")
    output.parent.mkdir(parents=True,exist_ok=True);marks=[]
    def require(condition,message):
        marks.append({"contract":message,"passed":bool(condition)})
        check(condition,message)
    report={"schema":"thor-mem04-public-xnu-precheck-v1","seed_generated":False,"phone_actions":0,
            "full_L2_ready":False,"verifier_sha256":sha(Path(__file__))}
    try:
        report["frozen_artifact"]=validate_mem04_frozen_artifact(root,candidate,source,proof,require)
        report["compiled_frontier"]=validate_mem04_xnu(root,output,source,candidate,candidate/"kernel.macho",
                                                     json.loads(proof.read_text()),proof,require)
        report["result"]="PASS_PUBLIC_XNU_PRECHECK"
    except Exception as error:
        report.update(result="FAIL",error_type=type(error).__name__,error=str(error))
    report.update(checks=marks,conditions_count=len(marks))
    with output.open("x") as handle:json.dump(report,handle,indent=2);handle.write("\n")
    print(json.dumps({"result":report["result"],"conditions_count":len(marks),"out":str(output),"phone_actions":0}))
    return 0 if report["result"]=="PASS_PUBLIC_XNU_PRECHECK" else 1


def validate_mem04_transition(root,src,layout,require,proof_path=None):
    """Independently constrain the legal MEM03->MEM04 transition immediate rebind."""
    spec=importlib.util.spec_from_file_location("mem04_transition_bind",Path(__file__).with_name("transition_bind.py"))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    def actual_code(source):
        pe,_=module.pe_paths(source)
        # The audited CLANGPDB build exports this map beside its final PE.
        map_path=pe.with_suffix(".map")
        start,end=module.transition_symbols(map_path)
        code,metadata=module.load_pe(pe,start,end-start)
        require(len(code)==500 and not metadata["relocations_in_transition"],"real transition remains exactly500 bytes without interval relocations")
        return code
    previous=root/"worktrees/uefi/vm-mem03"
    old=actual_code(previous);current=actual_code(src)
    require(hashlib.sha256(old).hexdigest()=="29ee4adbbb30fe06bcebf9f9af9723f98698c6170636b2bb24e52a7a17fe9d24",
            "historical MEM03 transition baseline still matches immutable reviewed bytes")
    require(hashlib.sha256(current).hexdigest()==TRANSITION_SHA,"actual MEM04 transition matches newly reviewed immediate-only bytes")
    changed=[offset for offset in range(0,500,4) if old[offset:offset+4]!=current[offset:offset+4]]
    require(changed==[116,136,140],"only three legal entry/vector immediate words changed")
    immediate_mask=0xffff<<5
    for offset in changed:
        before,after=struct.unpack_from("<I",old,offset)[0],struct.unpack_from("<I",current,offset)[0]
        require(before&~immediate_mask==after&~immediate_mask,"transition opcode/register/branch bits unchanged at word "+str(offset))
    def pair(offset,code=current):
        first,second=struct.unpack_from("<II",code,offset)
        require(first&0x7f80001f==0x52800008 and second&0x7f80001f==0x72800008,
                "reviewed transition MOVZ/MOVK pair still writes x8/w8")
        require(((first>>21)&3)==0 and ((second>>21)&3)==1 and first>>31==second>>31,
                "reviewed transition immediate pair shifts and register width unchanged")
        return ((first>>5)&0xffff)|(((second>>5)&0xffff)<<16)
    require(pair(116)==layout["entry_offset"] and pair(136)==layout["vectors_offset"],
            "actual encoded transition entry/vector offsets equal actual Mach-O symbols")
    require(not subprocess.check_output(["git","-C",str(src),"diff","bb8382c6417adf779b89adfde295832e846cee93","HEAD","--","*.S"],text=True),
            "UEFI transition assembly source unchanged from consumed MEM03 baseline")
    folder=src/"Platforms/Xiaomi/thorPkg/Drivers/ThorEntryBdsDxe"
    pin,_,_=module.header_expected(folder/"ThorEntry06TransitionExpected.h")
    require(pin.hex()==TRANSITION_SHA,"current expected transition header pins actual new code")
    if proof_path is not None:
        proof=json.loads(proof_path.read_text())
        require(proof["schema"]=="thor-mem04-transition-entry-vector-immediate-proof-v1" and
                proof["result"]=="PASS_ONLY_ACTUAL_ENTRY_VECTOR_IMMEDIATES_CHANGED" and proof["phone_actions"]==0,
                "dedicated immutable transition proof reports the bounded immediate rebind")
        require(proof["old_code_sha256"]==hashlib.sha256(old).hexdigest() and
                proof["new_code_sha256"]==hashlib.sha256(current).hexdigest() and
                proof["code_bytes"]==500 and proof["relocations"]==0 and
                proof["changed_instruction_word_offsets"]==changed and proof["unchanged_bytes"]==488 and
                proof["opcode_register_branches_unchanged"] is True,
                "transition proof exact bytes/word deltas agree with independent PE extraction")
        require(proof["old_decoded_entry_offset"]==pair(116,old) and
                proof["old_decoded_vector_offset"]==pair(136,old) and
                proof["new_decoded_entry_offset"]==pair(116) and proof["new_decoded_vector_offset"]==pair(136),
                "transition proof old/new targets agree with independently decoded instructions")
        require(proof["kernel_sha256"]==layout["kernel_sha256"] and
                proof["new_expected_header_sha256"]==sha(folder/"ThorEntry06TransitionExpected.h") and
                proof["unchanged_assembly_source_sha256"]==sha(folder/"thor_entry06_arch.S"),
                "transition proof belongs to exact current kernel and assembly/header sources")
    return {"transition_bytes":500,"changed_word_offsets":changed,"only_immediate_bits_changed":True,
            "actual_entry_offset":pair(116),"actual_vectors_offset":pair(136),"relocations":0,
            "binding_proof_sha256":sha(proof_path) if proof_path else None}


def validate_cached_xnu_precheck(path,expected,candidate,xsrc,xproof,require):
    require(path.is_file() and sha(path)==expected,"explicit previously completed XNU precheck SHA pinned")
    # This completed receipt predates input hash inventory.  Reuse is limited
    # to its reviewed exact proof pair; future receipts use full replay unless
    # explicitly reviewed here.  A coordinated JSON edit cannot borrow PASS.
    reviewed={"0a1ee1d659a394e8ab013861a71dfe4e81d72e19cd3f5a2e38a951cf767abaed":
              ("ee3be080de0689aec0cc317a9bb2042478c6b6e4158a8258818c2e70cadccff0",
               "a4c37aa12240928aca89d8ec3464a88df1413efe5cfd7d0767289d68a5025d99")}
    require(expected in reviewed and sha(xproof)==reviewed[expected][0] and
            sha(candidate/"mem04-pmap-machinecode-final.json")==reviewed[expected][1],
            "completed precheck is bound to its exact reviewed linked and pmap proof bytes")
    cached=json.loads(path.read_text())
    require(cached["schema"]=="thor-mem04-public-xnu-precheck-v1" and cached["result"]=="PASS_PUBLIC_XNU_PRECHECK"
            and cached["conditions_count"]==len(cached["checks"]) and all(item["passed"] for item in cached["checks"])
            and cached["seed_generated"] is False and cached["phone_actions"]==0,
            "completed public XNU precheck succeeded without seed or phone actions")
    lock=json.loads((candidate/"manifest.json").read_text())
    require(cached["frozen_artifact"]["kernel_sha256"]==lock["kernel_sha256"] and
            cached["frozen_artifact"]["build_source_commit"]==lock["build_source_commit"] and
            cached["frozen_artifact"]["audited_source_commit"]==lock["xnu_commit"],
            "reused XNU result belongs to same exact frozen kernel/build/audit source")
    proof=json.loads(xproof.read_text());config=proof["bounded_pmap_audit"]["actual_compile_configuration"]
    object_directory=xsrc/"build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/osfmk/DEVELOPMENT"
    for name in ("pmap","pmap_data","lock_group"):
        require(sha(object_directory/(name+".o.json"))==config[name+"_compile_record_sha256"],
                "same native compile metadata as completed precheck: "+name)
    require(sha(object_directory/"pmap_data.o")==config["DWARF_object_sha256"],
            "same actual native DWARF object as completed precheck")
    require(cached["compiled_frontier"]["bounded_after_native_pmap_return"] is True and
            cached["compiled_frontier"]["independent_pmap_budget"]["total_bytes"]==176128,
            "reused precheck contains actual bounded pmap and independently computed43-page budget")
    return {"path":str(path),"sha256":expected,"conditions":cached["conditions_count"],
            "same_frozen_inputs_rechecked":True,"fresh_reexecution_claimed":False}


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
    ap.add_argument("--reuse-xnu-precheck",type=Path)
    ap.add_argument("--xnu-precheck-sha256")
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
    cached_xnu=None
    if args.reuse_xnu_precheck or args.xnu_precheck_sha256:
        check(args.reuse_xnu_precheck and args.xnu_precheck_sha256,"cache path and exact SHA must both be provided")
        cached_xnu=validate_cached_xnu_precheck(path(args.reuse_xnu_precheck),args.xnu_precheck_sha256,candidate,xsrc,xproof,require)
    validate_mem04_frozen_artifact(root,candidate,xsrc,xproof,require,replay_regressions=cached_xnu is None)
    require(manifest["kernel_build_source_revision"]==build["build_source_commit"]
            and manifest["kernel_artifact_manifest_sha256"]==sha(candidate/"manifest.json")
            and manifest["kernel_build_manifest_sha256"]==sha(candidate/"build-manifest.json"),
            "packed kernel traces exact frozen build and audited source metadata")
    require("mem04" in args.run_id,"MEM04 unique run identity")
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
            all(label in binary for label in (b"01 INITRD",b"02 KERNEL",b"03 MEMORY",b"04 HANDOFF")) and
            b"UEFI PROGRESS  %u / 4 VALIDATED  /  %a" in binary,
            "structured four-card diagnostic progress screen linked into real MEM04 UEFI PE")
    require(b"ESR / ELR / FAR" in binary and b"NOT CAPTURED IN UEFI" in binary,
            "unavailable exception values explicitly identified")
    require(b"UEFI CHECKS FAILED" in binary and b"UEFI CHECKS PASS" in binary,
            "diagnostic screen distinguishes pass from failure by text")
    require(b"UEFI 1 INITRD READ" in binary and b"UEFI 2 KERNEL HASH PASS" in binary,
            "retained UEFI stage labels")
    require(b"RUN MEM04 / THOR SM8475" in binary,
        "final UEFI linked PE identifies MEM04 candidate")
    hud=src/"Platforms/Xiaomi/thorPkg/Drivers/ThorEntryBdsDxe/ThorReportHud.inc.c"
    require(sha(hud)==receipt["new_ui_source_sha256"],
        "final MEM04 UEFI HUD SHA equals release source")
    ui_receipt=firmware_release.parent/"ui-stage-grid-audit.json"
    require(ui_receipt.is_file() and
        sha(ui_receipt)==receipt["new_ui_layout_audit_sha256"],
        "MEM04 independent XNU 4x4 grid provenance attached to exact UEFI build")
    ui_meta=json.loads(ui_receipt.read_text())
    require(ui_meta["result"]=="PASS_LINKED_MEM04_AND_RESERVED_GOP_Y0_319"
        and ui_meta["grid_size"]==[4,4]
        and ui_meta["tile_size_pixels"]==[328,42]
        and ui_meta["xnu_mapped_screen_y"]==[64,287],
        "MEM04 42px 4x4 grid drawn by XNU in y64..287")
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
    require(receipt["schema"]=="thor-mem04-stage-safe-hud-and-pmap-v1" and
          receipt["source_level_regressions_passed"]==45 and
          receipt["no_write_to_candidate_windows"] is True and
          receipt["no_memory_grant_or_heap_extension"] is True and
          receipt["UEFI_reserved_top_y0_through_319"] is True,
          "MEM04 retains read-only candidate policy with all45 source regressions")
    extended=validate_mem04_inputs(root,pkg,src,xsrc,candidate,manifest,receipt,audit,layout,build,machine,binding,extra,detail,boot,kernel,fd,prov,xproof,uproof,firmware_audit,transfer_index,require,replay_xnu=cached_xnu is None)

    final={"schema":"thor-vm-independent-offline-gate-v1",
           "checked_at":datetime.datetime.now().astimezone().isoformat(),
           "result":"PASS","conditions_count":len(marks),"checks":marks,
           "inputs":{"android_boot_v4":{"sha256":image_hash,"bytes":boot.stat().st_size},
                     "native_xnu":{"sha256":kernel_hash,"bytes":kernel.stat().st_size},
                     "uefi_fd":{"sha256":sha(fd),"bytes":fd.stat().st_size}},
           "run_id":args.run_id,"verifier_sha256":sha(Path(__file__)),
           "xnu_mem04_proof_sha256":sha(xproof),"uefi_mem04_proof_sha256":sha(uproof),
           "requires_new_recording_authorization":True,"L3_authorized":False,
           "fresh_firmware_replay":extended["fresh_firmware_replay"],
           "libfdt_host_dependency_sha256":extended["libfdt_host_dependency_sha256"],
           "compiled_kernel_frontier":extended["compiled_kernel_frontier"],
           "executed_stage_render":extended["executed_stage_render"],
           "transition_rebind":extended["transition_rebind"],
           "reused_completed_XNU_precheck":cached_xnu,
           "mem04_final_fdt_report_persists":True,
           "mem04_five_readonly_FDT_windows":True,
           "consumed_MEM01_MEM02_MEM03_images_never_reused":True,
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
    if "--precheck-xnu" in sys.argv:raise SystemExit(precheck_xnu_main())
    main()
