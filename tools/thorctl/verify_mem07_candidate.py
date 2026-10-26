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
    check(start == 0xb805a000 and end == 0xb8195000, "MEM07 exact framebuffer physical range")
    check(producer_pages == consumer_pages == 315, "bootstrap and consumer both cover315 pages")
    check(end-start == producer_pages*4096, "framebuffer exact page extent")
    check(90+producer_pages <= 512, "bootstrap framebuffer leaves fit existing L3")
    check(flags == 0x006000000000070b, "unchanged framebuffer leaf attributes")
    return [start+i*4096 for i in range(consumer_pages)]


def validate_uefi_detail_document(detail):
    check(detail["schema"]=="thor-mem07-native-startup-loader-host-proof-v1", "MEM07 detail schema")
    check(detail["result"]=="PASS_MEM07_NATIVE_STARTUP_AND_LOADER_PROBE_BINDING", "MEM07 detail result")
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
    "bd37e82ce63a6d1fc211407d0d8c5064da126a63282a056c011dc9eca67a04f7",
    "cebebd3f292d2fcee7c7b3d3511f704055543c860c93c2abde2f652069b8caa5",
    "ad9f7536e76def6a20f2e5329abce3a4ae674345cef41ff0f7ac8c9f73d2e8f9",
}
TRANSITION_SHA = "de0b5fd081759dde1f579f7741c2a6cab40feea95285e5c9eaee9832a87da195"
OLD_KERNEL_SHA = "623b444dc57fc8981562277f1eade9f1ed25818badaa3bfd03ae7ff54a5333aa"


def mem07_pe_identity(binary):
    return all(label in binary for label in (
        b"RUN MEM07 / THOR SM8475", b"MEM07 FDT WINDOW ONLY - NO MEMORY GRANT",
        b"THOR FDT MEMDIAG 07"))


def validate_mem07_inputs(root,pkg,src,xsrc,candidate,manifest,receipt,audit,layout,
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
            "bb8382c6417adf779b89adfde295832e846cee93", "new MEM07 source/binary identities")
    require(subprocess.check_output(["git","-C",str(xsrc),"rev-parse","HEAD"],text=True).strip()
            ==build["xnu_commit"], "exact XNU source checkout revision")
    require(not subprocess.check_output(["git","-C",str(xsrc),"status","--porcelain"],text=True).strip(),
            "XNU source checkout clean")
    kernel_contract=validate_mem07_xnu(root,pkg,xsrc,candidate,kernel,extra,xproof,require,replay_xnu)
    transition_proof=uproof.parent/"postlink-transition/mem07-transition-binding.json"
    require(sha(transition_proof)==receipt["transition_binding_audit_sha256"]==detail["transition_binding_audit_sha256"],
            "actual immediate-rebind proof identity is pinned by both release and detail")
    transition_contract=validate_mem07_transition(root,src,layout,require,transition_proof)
    validate_uefi_detail_document(detail)
    loader_contract=validate_mem07_loader(root,pkg,src,receipt,detail,uproof,require)
    require(sha(uproof)==receipt["mem07_uefi_proof_sha256"],"FDT detail proof immutable hash bound to firmware release")
    require(detail["schema"]=="thor-mem07-native-startup-loader-host-proof-v1" and
            detail["result"]=="PASS_MEM07_NATIVE_STARTUP_AND_LOADER_PROBE_BINDING",
            "dedicated new MEM07 FDT detail host proof")
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
            "MEM07 detail proof bound to actual final firmware bytes")
    require(detail["transition"]["bytes"]==500 and detail["transition"]["sha256"]==TRANSITION_SHA,
            "MEM07 detail proof preserves exact transition")
    source_pins=detail["source_files_sha256"]
    require(len(source_pins)>=7,"FDT detail source/helper/test identities all pinned")
    for relative,digest in source_pins.items():
        path=(src/relative).resolve()
        require(path.is_relative_to(src),"FDT source hash path bounded")
        pin(path,digest,"same final source file: "+relative)
    adt=src/"Platforms/Xiaomi/thorPkg/Drivers/ThorEntryBdsDxe"
    pin(adt/"ThorEntryBdsDxe.c",receipt["new_bds_source_sha256"],"MEM07 final BDS source bound to firmware release")
    pin(adt/"thor_fdt_gate.c",receipt["unchanged_fdt_validator_sha256"],"strict original FDT validator unchanged")
    memory=src/"Platforms/Xiaomi/thorPkg/Library/MemoryMapLib/MemoryMapLib.c"
    # The release pins the real project filename, not a guessed new map.
    if not memory.is_file():
        memory=src/"Platforms/Xiaomi/thorPkg/Library/MemoryMapLib/ThorMemoryMapLib.c"
    pin(memory,receipt["unchanged_memorymap_source_sha256"],"original memory map unchanged")
    pe=src/"Build/thorEntry09/RELEASE_CLANGPDB/AARCH64/thorPkg/Drivers/ThorEntryBdsDxe/ThorEntryBdsDxe/OUTPUT/ThorEntryBdsDxe.efi"
    binary=pe.read_bytes()
    require(mem07_pe_identity(binary),
            "actual final linked PE carries unique MEM07 diagnostic identity")
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
    require(dependency["schema"]=="thor-mem07-pinned-libfdt-host-dependency-v1" and dependency["result"]=="PASS"
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
    require(detail["tests"]["mem07"]==12 and "Platforms/Xiaomi/thorPkg/Tests/test_mem07_stage_render.py" in source_pins,"executed renderer suite and its source hash pinned")
    render_contract=replay_mem07_stage_render(root,pkg,src,detail,receipt,uproof,require)
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
    require(not (root/"runs"/manifest["run_id"]/"l3").exists(),"no MEM07 L3 before recorded authorization")
    adapter=Path(__file__).with_name("firmware_audit_replay.py")
    spec=importlib.util.spec_from_file_location("mem07_firmware_replay",adapter)
    replay_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(replay_module)
    with tempfile.TemporaryDirectory(prefix="mem07-independent-firmware-",dir=pkg.parent) as directory:
        fresh_firmware=replay_module.replay(root,src,firmware_audit_path,transfer_index,Path(directory))
    require(fresh_firmware["result"]=="PASS" and fresh_firmware["modules"]==20
            and fresh_firmware["link_inputs"]==96 and fresh_firmware["volumes"]==2,
            "fresh full FV/FFS/LZMA, 20 PE/TE and96 linker-input audit matches original semantics")
    return {"loader_contract":loader_contract,"fresh_firmware_replay":fresh_firmware,"libfdt_host_dependency_sha256":sha(dependency_path),"compiled_kernel_frontier":kernel_contract,"executed_stage_render":render_contract,"transition_rebind":transition_contract}


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
    check(states[7:9]==["CALL","RETURN"] and states[11:14]==["CALL","RETURN","PASS"] and states[14:]==["CALL","PASS"],
          "real AuxKC/pmap call-return-guard and reserved states remain distinct")
    check(labels[11:14]==["PMAP CALL","PMAP RETURN","PMAP GUARD"] and labels[14:]==["L3 CALL","NAT KPRINTF"],"pmap labels identify real call/return/guard and reserved slots")
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


def decode_mem07_global_flags(low,high,combine):
    check(low&0xff80001f==0xd2800009 and (low>>21)&3==0,'actual global MOVZ x9')
    check(high&0xff80001f==0xf2800009 and (high>>21)&3==3,'actual global MOVK x9 shift48')
    check(combine&0xffe0fc00==0xaa000000 and combine&31==20 and (combine>>5)&31==8 and (combine>>16)&31==9,'actual native PA/flags ORR registers')
    value=((low>>5)&0xffff)|(((high>>5)&0xffff)<<48)
    check(value==0x0060000000000683,'native global is RO, NX, WB and outer-shareable')
    return value

def decode_mem07_white(word):
    if word==0x32005fed:value=0x00ffffff
    else:
        check(word&0xff80001f==0x1280000d and (word>>21)&3<2,'native white initializer is ORR or MOVN w13')
        value=(~(((word>>5)&0xffff)<<(((word>>21)&3)*16)))&0xffffffff
    check(value==0x00ffffff,'actual native foreground is exact RGB white')
    return value

def data_at_va(actual,address,length):
    check(type(address) is int and type(length) is int and length>0,'bounded native data read')
    matches=[s for s in actual.segments if s[0]<=address and address+length<=s[0]+s[3]]
    check(len(matches)==1,'native bytes wholly in one file-backed segment')
    base,_,offset,_,_=matches[0]
    return actual.data[offset+address-base:offset+address-base+length]


def startup_table_contract(raw,callback):
    check(len(raw)>0 and len(raw)%24==0 and len(raw)//24<=4096,'bounded24-byte startup table')
    entries=[struct.unpack_from('<IIQQ',raw,i) for i in range(0,len(raw),24)]
    selected=[(i,e) for i,e in enumerate(entries) if e[0]==4]
    check(len(selected)==1 and selected[0][1]==(4,0,callback,0),
          'one native KPRINTF callback with rank FIRST and null argument')
    return selected[0][0],{str(sub):sum(e[0]==sub for e in entries) for sub in range(5) if any(e[0]==sub for e in entries)}


def branch_target(actual,address,kind):
    check(kind in {'BL','B'},'actual native branch kind')
    word=actual.word(address)
    check(word&0xfc000000==({'BL':0x94000000,'B':0x14000000}[kind]),'raw direct branch opcode')
    delta=word&0x3ffffff
    if delta&(1<<25):delta-=1<<26
    return address+delta*4


def startup_section(actual):
    data=actual.data;cursor=32;found=[]
    for _ in range(struct.unpack_from('<I',data,16)[0]):
        command,size=struct.unpack_from('<II',data,cursor)
        if command==0x19:
            count=struct.unpack_from('<I',data,cursor+64)[0]
            check(size==72+80*count,'complete Mach-O section array')
            for i in range(count):
                p=cursor+72+80*i
                if data[p:p+16].split(b'\0')[0]==b'__init_entry_set':
                    found.append(struct.unpack_from('<QQI',data,p+32))
        cursor+=size
    check(len(found)==1,'unique actual native startup section')
    address,length,offset=found[0]
    raw=data_at_va(actual,address,length)
    check(actual.data[offset:offset+length]==raw,'section file offset matches actual loaded segment')
    return address,length,offset,raw


def validate_mem07_startup_binary(actual,extra,xsrc,candidate,require,call):
    for key in ['native_arm_init_kprintf_startup_compiled','bounded_after_native_KPRINTF_startup']:
        require(extra[key] is True,'compiled natural native startup frontier: '+key)
    require(extra['native_pmap_map_globals_call_compiled'] is False and
            extra['normal_machine_init_reached'] is False and extra['native_globals']['not_called'] is True
            and extra['native_globals']['slot2_remains_empty'] is True,
            'remove early diagnostic globals call and keep original natural mapping slot empty')
    startup=extra['native_startup'];table=startup['dispatch_table']
    address,length,offset,raw=startup_section(actual)
    selected,counts=startup_table_contract(raw,actual.symbols['_PE_init_kprintf'])
    require(table['section_address']==hex(address) and table['bytes']==length and
            table['section_file_offset']==offset and table['entry_size']==24 and
            table['entries']==length//24 and table['table_sha256']==hashlib.sha256(raw).hexdigest(),
            'independently parse actual complete24-byte native startup table')
    record=table['unique_KPRINTF_record']
    require(record==dict(record_address=hex(address+selected*24),file_offset=offset+selected*24,
                        subsystem=4,rank=0,func=hex(actual.symbols['_PE_init_kprintf']),arg=0)
            and table['subsystems_at_or_below_KPRINTF']==counts,
            'exact native callback identity and all prior subsystem counts')
    getter=startup['callback_getter'];pc=actual.symbols['_thor_mem07_kprintf_callback']
    adrp,add,ret=struct.unpack('<III',data_at_va(actual,pc,12))
    require(adrp&0x9f00001f==0x90000000 and add&0xffc003ff==0x91000000 and ret==0xd65f03c0,
            'actual callback getter is only ADRP ADD RET')
    immediate=((adrp>>5)&0x7ffff)<<2|((adrp>>29)&3)
    if immediate&(1<<20):immediate-=1<<21
    callback=(pc&~4095)+(immediate<<12)+((add>>10)&4095)
    require(callback==actual.symbols['_PE_init_kprintf'] and getter['decoded_callback']==hex(callback),
            'raw getter address agrees with unique dispatch callback')
    sites=startup['call_sites']
    require(len(sites)>30 and len({s['address'] for s in sites})==len(sites),'complete unique native direct call/branch audit')
    for site in sites:
        pc=int(site['address'],0);target=branch_target(actual,pc,site['branch_kind'])
        require(target==actual.symbols[site['target']]==int(site['target_address'],0)
                and actual.contains_function(site['containing_function'],pc),
                'independent raw native '+site['branch_kind']+' target: '+site['target'])
    order=startup['natural_arm_init_order']
    expected=['_arm_vm_init','_thor_mem07_after_vm','_PE_consistent_debug_inherit','_thor_mem07_debug_returned',
              '_serial_init','_thor_mem07_serial_returned','_kernel_startup_initialize_upto','_thor_mem07_kprintf_returned']
    require([s['target'] for s in order]==expected and
            [int(s['address'],0) for s in order]==sorted(int(s['address'],0) for s in order),
            'natural arm_init order reaches serial and one native KPRINTF startup dispatch')
    for site in order:call(site['address'],site['target'],'_arm_init')
    require(extra['native_caller']['natural_order']==order and extra['native_caller']['native_order_and_guard_dominance'] is True,
            'actual branch dominance enforces complete natural call sequence')
    for function,guards in startup['guard_order'].items():
        addresses=[int(s['address'],0) for s in guards]
        require(addresses==sorted(addresses) and len(addresses)>=3,'ordered original-VA state guards: '+function)
        for guard in guards:
            matches=[s for s in sites if s['address']==guard['address'] and s['target']==guard['target']]
            require(len(matches)==1 and matches[0]['containing_function']==function,
                    'guard call encoded in exact native function: '+guard['target'])
    serial=startup['serial_no_mmio'];branch=int(serial['zero_SoC_branch'],0);word=actual.word(branch)
    require(word&0xff00001f==0xb4000000,'native serial CBZ x0 from zero SoC lookup')
    delta=(word>>5)&0x7ffff
    if delta&(1<<18):delta-=1<<19
    path=[int(p,0) for p in serial['no_SoC_path']]
    require(branch+delta*4==path[0] and len(path)<32 and actual.word(path[-1])==0xd65f03c0,
            'bounded native no-SoC path ends in actual RET')
    for pc in path:
        word=actual.word(pc)
        require(word&0xfc000000!=0x94000000 and word&0xfffffc1f not in {0xd61f0000,0xd63f0000},
                'no direct or indirect device/MMIO callback on guarded serial path')
    for key in ['consistent_debug_unmapped','no_early_pmap_map_globals','slot2_empty_all_checkpoints',
                'KPRINTF_only_sets_console_pointer_without_invoking_console']:
        require(startup[key] is True,'native bounded startup scope: '+key)
    reset=startup['reset']
    require(reset['PASS_hold_seconds']==12 and reset['RESET_NEXT_hold_seconds']==1 and
            reset['iteration_bound']==0x40000000 and reset['stalled_counter_bound']==4096 and
            reset['native_counter_hz']==19200000 and reset['all_wait_callers_are_constant_bounded'] is True,
            'actual native PASS12s plus RESET_NEXT1s with bounded counter/stall loops')
    for key,target in [('PASS_call','_thor_vm_grid_stage'),('RESET_NEXT_call','_thor_mem07_view'),('reset_call','_thor_entry09_reset')]:
        call(reset[key],target,'_thor_mem07_kprintf_returned')
    require(int(reset['PASS_call'],0)<int(reset['RESET_NEXT_call'],0)<int(reset['reset_call'],0)
            and reset['framebuffer_extent_unchanged'] is True and reset['no_lower_screen_stores_introduced'] is True,
            'visible final marker precedes actual reset within existing framebuffer aperture')
    whites=extra['native_globals']['native_white_glyph_stores']
    require(len(whites)==20 and len(set(whites))==20,'twenty actual native white glyph stores')
    for value in whites:
        pc=int(value,0);word=actual.word(pc)
        require(word&0xffc0001f==0xb900000d and actual.contains_function('_thor_mem04_grid_glyph',pc),
                'raw native glyph pixel store is STR w13')
    start=actual.symbols['_thor_mem04_grid_glyph'];first=min(int(v,0) for v in whites)
    initializers=[actual.word(pc) for pc in range(start,first,4) if actual.word(pc)==0x32005fed or
                  actual.word(pc)&0xff80001f==0x1280000d]
    require(len(initializers)==1 and decode_mem07_white(initializers[0])==0xffffff,
            'actual native foreground initializer is exact white')
    render=candidate/'mem07-grid-render.json';build=json.loads((candidate/'build-manifest.json').read_text());doc=json.loads(render.read_text())
    require(sha(render)==build['grid_render_sha256'] and doc['schema']=='thor-mem07-production-C-grid-render-v1'
            and doc['result']=='PASS_WHITE_ON_DARK_NATIVE_SOURCE_RENDER','new production-C render proof bound to final native source')
    require(doc['foreground_rgb']=='FFFFFF' and doc['background_rgb']=='080C12' and
            doc['hex_ID_pixels_exact'] is True and doc['canaries_intact'] is True and doc['edge_pixels']==19488,
            'native white-on-dark hexadecimal identifiers and aperture canaries verified')
    for relative,digest in doc['sources_sha256'].items():require(sha(xsrc/relative)==digest,'actual rendered native source: '+relative)
    return {'natural_KPRINTF_callback':hex(callback),'actual_startup_records':length//24,
            'actual_KPRINTF_records':1,'native_serial_MMIO_on_guarded_path':False,
            'early_globals_call_removed':True,'RESET_NEXT_marker_compiled':True,
            'natural_machine_init_or_iOS_proven':False,'pixel_render_sha256':sha(render)}

def mem07_native_reconstruction(root,xsrc):
    prior=root/'worktrees/xnu/vm-mem06';reference=prior/'refer/xnu'
    commit='00bb7db8f91265c84d3f2201f55f590e72d4ceea'
    apple='d8b80295118ef25ac3a784134bcf95cd8e88109f'
    base=root/'runs/thor-20261026-vmdev-016/l2/mem06-startup-source-reconstruction'
    proof=base/'reconstruction-proof.json';document=json.loads(proof.read_text())
    check(document['schema']=='thor-mem07-MEM06-native-startup-source-reconstruction-v1' and
          document['result']=='PASS_THREE_NATIVE_TU_SOURCES_RECONSTRUCTED_FROM_FROZEN_GIT_INPUTS' and
          document['MEM06_final_source_commit']==commit and document['pinned_apple_xnu_reference_commit']==apple,
          'three native baseline sources trace fixed MEM06 source and Apple Git revision')
    expected={
      'osfmk/kern/startup.c':('2b47b26f728a5c82378775a197936cdf306994c86fd879632798a2402d93d965','481d007bb27bb1443a976ae0e75653f7b073ab1cc2bcd07cb13d42dc60663184'),
      'pexpert/arm/pe_serial.c':('d8b175c57832b6a3c4668e40270a8192bf71677a8ee3a64b1fa40a2d37e44882','d8b175c57832b6a3c4668e40270a8192bf71677a8ee3a64b1fa40a2d37e44882'),
      'pexpert/arm/pe_kprintf.c':('b0e9eb33fcbec9c165c05f50a63010b5db95165f6cf9994903a5ced1921ad376','138dc10c3b379e8625bbe265c1b8cc1acc80e5cd4a1f0de7c890fbaaa44171a7')}
    patches=[('native-trust-cache-runtime.patch','19e000ca6f70a8ddfefa503d2936894dd8315fae3ca2fb3e50d666d65252de79'),
             ('zzz-boot-progress.patch','6e04d6ebe9035bb897920eceb3f6fddef1f5c6d0afa771a1f39dce741ee9b2be')]
    env=__import__('os').environ.copy();env['GIT_NO_LAZY_FETCH']='1'
    files={str(proof):proof};rebuilt={}
    with tempfile.TemporaryDirectory(prefix='mem07-baseline-reconstruct-') as temporary:
        target=Path(temporary)
        for relative,(original,result) in expected.items():
            raw=subprocess.check_output(['git','-C',str(reference),'show',apple+':'+relative],env=env)
            check(hashlib.sha256(raw).hexdigest()==original,'fixed original native Apple Gitblob: '+relative)
            destination=target/relative;destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(raw)
        for name,digest in patches:
            path=base/'patches'/name
            raw=subprocess.check_output(['git','-C',str(prior),'show',commit+':patches/xnu/'+name],env=env)
            check(hashlib.sha256(raw).hexdigest()==digest and path.read_bytes()==raw,'exact ordered frozen MEM06 patch: '+name)
            files[str(path)]=path
            subprocess.run(['git','apply',*['--include='+r for r in expected],str(path)],cwd=target,env=env,
                           check=True,capture_output=True)
        for relative,(original,result) in expected.items():
            path=base/'source'/relative
            check(sha(target/relative)==sha(path)==sha(prior/'.cache/xnu-work'/relative)==result,
                  'independent native source reconstruction matches frozen MEM06 compiled input: '+relative)
            item=document['files'][relative]
            check(item['original_blob_sha256']==original and item['reconstructed_source_sha256']==result,
                  'native reconstruction receipt agrees with independently derived bytes')
            files[str(path)]=path;rebuilt[relative]=result
    return files,rebuilt


def mem07_baseline_files(root,xsrc):
    files,reconstructed=mem07_native_reconstruction(root,xsrc)
    specifications=[
        ('thor-20261026-vmdev-014','7c26d2fa82704d404781e50616f65ad09878f676521424c7196b48f6971b29fa',
         '057b3f423882e9e0105698d15934316d80f2477306e610c2511069c18b922d5c','mem06-baseline',5),
        ('thor-20261026-vmdev-015','48df9ab3cc59a9e6b0d32408489985032b4c0fd84f80c1a71c528c12fe1d6c06',
         '107f8d9b32cb5b91802e4d4da7b4c833a2a6bc31e288605e1089be601def7848','mem07-baseline',8)]
    for run,proof_sha,build_sha,folder,count in specifications:
        prior=root/'artifacts/candidates'/run;proof=prior/'machinecode.json';build=prior/'build-manifest.json'
        check(sha(proof)==proof_sha and sha(build)==build_sha,'exact immutable historical native baseline: '+run)
        old=json.loads(proof.read_text());files[str(proof)]=proof;files[str(build)]=build
        baseline=xsrc/'.cache'/folder;members=[p for p in baseline.rglob('*') if p.is_file()]
        check(len(members)==count,'complete historical source baseline: '+folder)
        for p in members:
            rel=str(p.relative_to(baseline))
            expected=old['prepared_source_sha256'].get(rel,reconstructed.get(rel) if run.endswith('015') else None)
            check(expected is not None and sha(p)==expected,
                  'historical source matches frozen native proof: '+folder+'/'+rel)
            files[str(p)]=p
    check(len(files)==23,'all13 historical sources,4 manifests and6 fixed reconstruction inputs present')
    return files

def validate_mem07_xnu(root,pkg,xsrc,candidate,kernel,extra,xproof,require,replay=True):
    def pin(path,value,why):require(path.is_file() and sha(path)==value,why)
    require(extra['schema']=='thor-mem07-native-kprintf-machinecode-v1' and extra['result']=='PASS_MEM07_NATIVE_ARM_INIT_KPRINTF_STARTUP' and extra['kernel_sha256']==sha(kernel),'exact MEM07 native early VM return proof identity')
    require(extra['native_arm_vm_init_return_compiled'] is True and extra['bounded_after_native_VM_return'] is True and extra['hardware_tested'] is False and extra['arm_vm_init_returned_on_phone'] is False and extra['full_VM_initialization_proven'] is False and extra['phone_actions']==0,'compiled early return is separate from actual hardware/full-VM success')
    mem07_baseline_files(root,xsrc)
    actual=MachO(kernel.read_bytes());low=extra['bootstrap_coverage'];aliases=extra['high_alias'];display=extra['display_geometry']
    coverage_contract(low['producer_pages'],low['consumer_pages'],int(low['physical_range'][0],0),int(low['physical_range'][1],0),0x006000000000070b)
    for key in ['full_pool_zero_before_install','all_315_identity_PTEs_and_attributes_match','framebuffer_neighbors_unmapped','owned_RAM_identity_and_KVA_edges_preserved','callee_saved_registers_preserved']:
        require(low[key] is True,'fresh native bootstrap PTE execution contract: '+key)
    require(aliases['actual_PTE_stores']==335 and aliases['grid_L3_indices']==[16,330] and aliases['legacy_L3_indices']==[480,499],'all335 native high framebuffer aliases retained')
    for key in ['all_315_grid_PA_and_attributes_match','all_20_legacy_PA_and_attributes_match','all_other_L3_slots_remain_zero','DSB_TLBI_DSB_ISB_order_preserved']:
        require(aliases[key] is True,'actual high alias execution contract: '+key)
    require(extra['retirement_boundary']['no_low_diagnostic_calls_in_retired_native_interval'] is True,'retired native interval cannot reach low framebuffer diagnostics')
    geometry=display['geometry'];require(geometry['rows']==geometry['columns']==4 and geometry['fb_existing_owned_pages']==315 and geometry['new_page_table_allocations']==0,'numbered display retains existing315-page aperture')
    palette=list(struct.unpack('<16I',macho_symbol_bytes(actual,'_thor_mem04_palette',64)))
    label_bytes=macho_symbol_bytes(actual,'_thor_mem04_labels',256);state_bytes=macho_symbol_bytes(actual,'_thor_mem04_states',128);font=list(macho_symbol_bytes(actual,'_thor_mem04_font',252))
    labels=[label_bytes[i*16:i*16+16].split(b'\0')[0].decode() for i in range(16)];states=[state_bytes[i*8:i*8+8].split(b'\0')[0].decode() for i in range(16)]
    numbered_display_contract(palette,labels,states,font)
    checkpoint=extra['checkpoint_contract'];require(checkpoint['compiled_stage_prefix']==list(range(16)) and checkpoint['reserved_slots']==[] and type(checkpoint['number_base']) is int and checkpoint['number_base']==16,'sixteen actual active hexadecimal checkpoints')
    for index,slot in enumerate(checkpoint['slots']):
        require(slot['slot']==index and slot['id']=='%02X'%index and slot['label']==labels[index] and slot['state']==states[index] and slot['color_hex']=='#%06X'%palette[index] and slot['active_compiled_checkpoint'] is True,'actual linked numbered label/state/color: '+str(index))
    require(len(checkpoint['slots'])==16,'complete sixteen-slot checkpoint contract')
    events=[e for values in extra['compiled_stage_sites'].values() for e in values]
    require(sorted(e['slot'] for e in events)==list(range(16)) and all(e['color']=='#%06X'%palette[e['slot']] for e in events),'sixteen unique actual linked stage calls use their own palette')
    require(display['minimum_active_text_contrast']>=18 and display['WAIT_text_contrast']>=18 and display['runtime_slot_palette_and_sequential_mask_gates'] is True,'actual display text visibility and monotonic state gates')
    require(extra['renderer']['actual_glyph_pixel_stores'] is True and extra['renderer']['text_before_tile_cache_clean_and_DSB'] is True and extra['renderer']['no_low_framebuffer_constants'] is True,'native glyph stores finish and flush through high aliases')
    budget=extra['pmap_metadata_budget'];config=extra['compile_configuration']
    normalized=dict(page_shift=budget['PAGE_SHIFT'],mem_size=64<<20,pt_desc_size=budget['pt_desc_bytes'],ptd_info_size=budget['ptd_info_bytes'],pt_index_max=budget['PT_INDEX_MAX'],pp_attr_size=2,pv_pointer_size=8,pte_size=8,max_l2_cline=budget['MAX_L2_CLINE'],pmap_max_asids=2048,bitmap_word_size=8,io_attr_table_size=0,io_filter_table_size=0,xnu_monitor=not config['non_MONITOR'],has_guarded_io_filter=not config['no_guarded_IO_filter'],has_16bit_asid=bool(config['HAS_16BIT_ASID']))
    own=pmap_budget_contract(normalized)
    require(own['total_pages']==43 and own['total_bytes']==budget['total_bytes']==176128 and own['ptd_per_page']==budget['ptd_per_page']==51,'independent native metadata budget remains43pages and51 PTDs per pool page')
    require(config['DWARF_pt_desc_bytes']==64 and config['DWARF_pp_attr_uint16'] is True and config['no_KASAN'] is True and config['MAX_ASIDS']==2048 and config['CPU_number_store_offset']==640 and config['CPU_number_store_bytes']==4,'actual compiler/DWARF configuration selects exact native ABI')
    cpu=extra['exact_CPU_write_proof'];require(cpu['current_CPUData_exact_VA_write_probe_before_CALL11'] is True and cpu['nested_member']=='cpu_data_t.cpu_pmap_cpu_data.cpu_number','exact original CPU member write is probed before native pmap')
    require(int(cpu['actual_original_CPU_VA'],0)==actual.symbols[cpu['BootCpuData_linkage']]+640,'exact static CPU object plus nested field offset')
    require(actual.word(int(cpu['actual_writable_AT'],0))&0xffffffe0==0xd5087820,'independent A64 decode of actual writable AT')
    word=actual.word(int(cpu['actual_native_STRW'],0));require(word&0xffc00000==0xb9000000 and ((word>>10)&0xfff)*4==640,'independent native four-byte CPU store offset')
    require(extra['native_PTD_geometry']['actual_ptd_per_page']==51 and extra['native_PTD_geometry']['PAGE_SHIFT_input']==12,'actual native arithmetic yields51 PTDs per page')
    for key,phase in [('pmap_phase1',1),('pmap_phase2',2)]:require(extra[key]['phase']==phase and extra[key]['guard_dominates_dynamic_zero'] is True,'native metadata clear remains dominated by phase guard: '+str(phase))
    counts=[]
    for offset in range(0,8<<20,2<<20):
        first=0xfffffff040000000+offset;end=first+(12<<20)+1440*3200*4
        while end%(8<<20):end+=4096
        cursor=first;count=0
        while cursor<end:count+=1;cursor+=2<<20
        counts.append(count+2)
    require(min(counts)==17 and max(counts)==20 and 51*2-max(counts)==82,'independent native-loop worst case is20 pages and leaves82 initial PTDs')
    native=extra['native_tail'];require(native['two_native_L3_loops'] is True and native['each_registration_dominated_by_write_guard'] is True and native['native_return_dominated_by_pmap_and_allocation_preflight'] is True,'both native L3 loops and actual early-VM return are guarded')
    def call(address,target,function):
        pc=int(address,0);word=actual.word(pc);delta=word&0x3ffffff
        if delta&(1<<25):delta-=1<<26
        require(word&0xfc000000==0x94000000 and pc+delta*4==actual.symbols[target] and actual.contains_function(function,pc),'independent actual BL target: '+target)
    for symbol,address in native['native_tail_calls'].items():call(address,symbol,'_arm_vm_init')
    startup_contract=validate_mem07_startup_binary(actual,extra,xsrc,candidate,require,call)
    caller=extra['native_caller'];call(caller['native_call'],'_arm_vm_init','_arm_init');call(caller['noreturn_gate'],'_thor_mem07_kprintf_returned','_arm_init')
    require(caller['native_order_and_guard_dominance'] is True and caller['no_unchecked_caller_return'] is True,'caller immediately enters bounded native-return verification')
    for address in native['native_return_sites']:require(actual.word(int(address,0))==0xd65f03c0 and actual.contains_function('_arm_vm_init',int(address,0)),'actual native early VM RET instruction')
    require(extra['native_PTD_pop']['guard_dominates_PTDS_and_growth_branch'] is True and extra['return_reset_continuation']['framebuffer_extent_unchanged'] is True and extra['native_globals']['not_called'] is True,'PTD writes/growth branch guarded and final stage ends only in high reset')
    require(extra['source_checks']['old_pmap_guards_exactly_preserved_except_proven_alias_reindex'] is True and extra['source_checks']['old_low_VA_ledger_retained'] is True and extra['source_checks']['empty_PTDS_pool_growth_rejected'] is True,'MEM04 guards and pre-switch ledger retained; empty pool growth forbidden')
    require(set(extra['guard_removal_mutations_rejected'])=={'remove_thor_mem05_vm_enter','remove_thor_mem07_after_vm','remove_thor_mem05_ptd_guard','remove_thor_mem07_debug_returned','remove_thor_mem07_serial_returned','remove_kernel_startup_initialize_upto','remove_thor_mem07_kprintf_returned'} and all(extra['guard_removal_mutations_rejected'].values()),'all three critical native guard removals are rejected')
    require(all(extra['bootstrap_mutations_rejected'].values()) and len(extra['bootstrap_mutations_rejected'])>=9,'inherited real bootstrap PTE mutations remain rejected')
    require(len(extra['validated_noreturn_CFG_helpers'])>=20 and all(x['no_reachable_RET_or_indirect_tail'] for x in extra['validated_noreturn_CFG_helpers']),'outlined fatal-call CFG helpers independently checked for nonreturn')
    auditor=xsrc/'scripts/audit-thor-mem07-linked.py';pin(auditor,extra['auditor_sha256'],'exact reviewed native VM machine-code auditor source')
    for rel,digest in extra['prepared_source_sha256'].items():pin(xsrc/'.cache/xnu-work'/rel,digest,'exact frozen prepared native source: '+rel)
    objects=xsrc/'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/osfmk/DEVELOPMENT'
    for name in ['pmap','pmap_data','lock_group']:pin(objects/(name+'.o.json'),config[name+'_compile_record_sha256'],'actual native compiler metadata: '+name)
    pin(objects/'pmap_data.o',config['DWARF_object_sha256'],'actual native DWARF ABI object')
    if replay:
        with tempfile.TemporaryDirectory(prefix='mem07-native-replay-',dir=pkg.parent) as tmp:
            output=Path(tmp)/'linked.json';run=subprocess.run([sys.executable,'-B',str(auditor),'--kernel',str(kernel),'--out',str(output)],capture_output=True,text=True,timeout=180)
            require(run.returncode==0 and output.is_file(),'fresh native PTE/display/pmap/allocator/DWARF/cold-CFG/VM-return replay succeeds')
            fresh=json.loads(output.read_text());strip=lambda obj:{k:v for k,v in obj.items() if k!='created_at'}
            require(strip(fresh)==strip(extra),'fresh full native machine-code proof exactly matches frozen semantics')
    return {'native_startup':startup_contract,'native_pmap_call_compiled':True,'native_early_VM_return_compiled':True,'bounded_after_native_VM_return':True,
            'independent_pmap_budget':own,'maximum_post_pmap_pages':20,'initial_PTDS_pool':102,'minimum_remaining_PTDS':82,
            'runtime_pmap_execution_verified':False,'runtime_VM_return_verified':False}


def replay_mem07_stage_render(root,pkg,src,detail,receipt,uproof,require):
    def pin(path,expected,why):
        require(path.is_file() and sha(path)==expected,why)
    base=uproof.parent
    audit_path=base/"stage-render-audit.json"
    trace_path=base/"stage-render-events.jsonl"
    harness_path=base/"stage-render-harness.c"
    pin(audit_path,detail["stage_render_audit_sha256"],"executed stage-render audit immutable identity")
    require(sha(audit_path)==receipt["stage_render_audit_sha256"],"same executed stage-render audit in firmware release")
    frozen=json.loads(audit_path.read_text())
    require(frozen["schema"]=="thor-mem07-executed-stage-render-event-audit-v1" and
            frozen["result"]=="PASS_ALL_STAGE_FRAMES_NO_OVERLAP" and frozen["frame_count"]==22,
            "twenty-two actual production-C rendered frames verified")
    pin(trace_path,detail["stage_render_events_sha256"],"actual stage draw event trace hash")
    require(sha(trace_path)==frozen["host_trace_sha256"],"draw trace hash belongs to executed audit")
    pin(harness_path,frozen["host_harness_sha256"],"exact executed production rendering harness bytes")
    adt=src/"Platforms/Xiaomi/thorPkg/Drivers/ThorEntryBdsDxe"
    test=src/"Platforms/Xiaomi/thorPkg/Tests/test_mem07_stage_render.py"
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
    with tempfile.TemporaryDirectory(prefix="mem07-stage-render-",dir=pkg.parent) as temporary:
        output=Path(temporary)/"stage-render-audit.json"
        env=__import__("os").environ.copy()
        env["THOR_MEM07_RENDER_AUDIT"]=str(output)
        env["PATH"]="/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin"
        run=subprocess.run([sys.executable,"-B",str(test),"-q"],env=env,capture_output=True,text=True,timeout=120)
        require(run.returncode==0 and "Ran 12 tests" in run.stderr and output.is_file(),
                "fresh host compiler executes all12 actual renderer tests and22 frames")
        current=json.loads(output.read_text())
        require(current==frozen,"fresh executed C renderer/font/trace proof exactly equals frozen stage proof")
        pin(output.parent/"stage-render-events.jsonl",frozen["host_trace_sha256"],"fresh actual draw trace matches frozen bytes")
        pin(output.parent/"stage-render-harness.c",frozen["host_harness_sha256"],"fresh production rendering harness matches frozen bytes")
        executed_draw_events_contract([json.loads(line) for line in (output.parent/"stage-render-events.jsonl").read_text().splitlines() if line])
    symbols_path=base/"native-trace-symbol-audit.json"
    pin(symbols_path,detail["native_trace_symbol_audit_sha256"],"native trace callback symbol audit immutable identity")
    require(sha(symbols_path)==receipt["native_trace_symbol_audit_sha256"],"same native symbol audit bound to release")
    symbols=json.loads(symbols_path.read_text())
    require(symbols["schema"]=="thor-mem07-native-trace-symbol-audit-v1" and
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
    return {"executed_frames":22,"stage_test_count":12,"actual_draw_trace_independently_checked":True,
            "fresh_production_C_renderer_replayed":True,"native_host_callback_absence_rechecked":True}


def validate_mem07_frozen_artifact(root,candidate,xsrc,xproof,require,replay_regressions=True):
    lock=json.loads((candidate/'manifest.json').read_text());built=json.loads((candidate/'build-manifest.json').read_text())
    layout=json.loads((candidate/'kernel-layout.json').read_text());extra=json.loads(xproof.read_text());kernel=candidate/'kernel.macho'
    actual=MachO(kernel.read_bytes())
    require(lock['schema']=='thor-artifact-lock-v2' and built['schema']=='thor-mem07-build-manifest-v1','MEM07 exact frozen artifact schemas')
    require(sha(kernel)==lock['kernel_sha256']==built['kernel_sha256']==layout['kernel_sha256']==extra['kernel_sha256'] and
            kernel.stat().st_size==lock['kernel_bytes']==built['kernel_bytes']==layout['kernel_bytes'],'actual kernel bytes match every independent freeze file')
    require(actual.base==0xfffffff007004000 and actual.end-actual.base==layout['loaded_span_bytes']==lock['loaded_span_bytes']==built['loaded_span_bytes'] and
            actual.loaded_hash==layout['loaded_span_sha256']==lock['loaded_span_sha256']==built['loaded_span_sha256'],'independent bounded Mach-O loaded span and loaded byte hash')
    symbols={'entry_offset':'__start','vectors_offset':'_LowExceptionVectorBase','entry_sync_offset':'_LowResetVectorBase',
             'bootstrap_pagetables_offset':'_bootstrap_pagetables','intstack_offset':'_intstack','intstack_top_offset':'_intstack_top',
             'excepstack_offset':'_excepstack','excepstack_top_offset':'_excepstack_top'}
    for field,symbol in symbols.items():require(layout[field]==actual.symbols[symbol]-actual.base,'actual Mach-O symbol-derived geometry: '+field)
    require(layout['entry_sync_bytes']==((actual.symbols['_thor_entry09_asm_end']+4095)&~4095)-actual.symbols['_LowResetVectorBase'],'actual exception sync extent')
    head=subprocess.check_output(['git','-C',str(xsrc),'rev-parse','HEAD'],text=True).strip()
    require(head==lock['xnu_commit']==built['final_HEAD']==extra['source_commit'] and not subprocess.check_output(['git','-C',str(xsrc),'status','--porcelain'],text=True).strip(),'exact clean frozen MEM07 XNU source HEAD')
    require(lock['build_source_commit']==built['actual_build_source_commit'] and lock['compiled_source_content_equivalent_to_final_HEAD'] is True and built['compiled_source_content_equivalent_to_final_HEAD'] is True,'actual compilation distinguished from later audit-only changes')
    delta=subprocess.check_output(['git','-C',str(xsrc),'diff','--name-only',lock['build_source_commit'],head],text=True).splitlines()
    require(delta==lock['post_build_changes']==built['post_build_changes'] and set(delta)<={'scripts/audit-thor-mem07-linked.py','tests/test_thor_mem07_startup.py','tests/test_thor_mem07_render.py'},'only declared auditor/test files changed after native compilation')
    require(sha(candidate/'kernel-layout.json')==built['layout_sha256'] and sha(xproof)==sha(candidate/'machinecode.json')==lock['mem07_machinecheck_sha256']==lock['machinecheck_sha256']==built['machinecode_sha256']==built['coverage_machinecode_sha256'],'exact layout and full native VM machine-code proof pins')
    require(lock['frontier']==built['frontier']=='native-arm-init-kprintf-startup' and lock['MEM07_compiled_stage_prefix']==list(range(16)) and lock['MEM07_reserved_slots']==[],'MEM07 exact bounded native frontier and sixteen slots')
    repeat=candidate/'mem07-reproducible-link.json';document=json.loads(repeat.read_text())
    require(sha(repeat)==built['reproducible_link_sha256'] and document['result']=='PASS_TWO_FORCED_LINKS_BYTE_IDENTICAL' and document['kernel_sha256']==sha(kernel),'two forced real links prove identical kernel bytes')
    for rel in document['link_receipts']:
        rec=json.loads((root/rel).read_text());require(rec['phase']=='kernel-linked-and-sealed' and rec['kernel']['sha256']==sha(kernel) and all(x['returncode']==0 for x in rec['commands']),'genuine successful linked output receipt: '+rel)
    guards=candidate/'mem07-native-startup-guards.json';guard_doc=json.loads(guards.read_text())
    require(sha(guards)==built['native_startup_guards_sha256']==lock['native_startup_guards_sha256'] and
            guard_doc['schema']=='thor-mem07-production-C-startup-guards-v1' and
            guard_doc['result']=='PASS_NATIVE_READONLY_GUARDS_WITH_FAULTS' and guard_doc['phone_actions']==0,
            'actual native startup/serial/KPRINTF guard C execution proof')
    require(set(guard_doc['source_sha256'])=={'osfmk/kern/startup.c','pexpert/arm/pe_serial.c','pexpert/arm/pe_kprintf.c'},'complete three native startup guard C sources')
    for relative,digest in guard_doc['source_sha256'].items():
        require(sha(xsrc/'.cache/xnu-work'/relative)==digest==extra['prepared_source_sha256'][relative],
                'executed native guard source belongs to final compiled input: '+relative)
    details=built['regression_tests']['details']
    require(built['regression_tests']['passed']==55 and built['regression_tests']['fresh_count']==35 and built['regression_tests']['baseline_reused_count']==20 and built['regression_tests']['failed']==0,'native regression scope: 35 new plus 20 pinned unchanged baseline tests')
    for name,count in [('test_thor_mem05_budget.py',12),('test_thor_mem05_cfg.py',8),('test_thor_mem07_render.py',12),('test_thor_mem07_startup.py',23)]:
        detail=details[name];log=root/detail['log_path']
        require(detail['passed']==count and detail['returncode']==0 and sha(xsrc/'tests'/name)==detail['source_sha256'] and sha(log)==detail['log_sha256'] and re.search(r'Ran '+str(count)+r' tests',log.read_text()) is not None,'reuse completed unchanged native regression source/log pins: '+name)
    return {'kernel_sha256':sha(kernel),'build_source_commit':lock['build_source_commit'],'audited_source_commit':head,
            'regressions_passed':55,'regressions_reexecuted':False}


def precheck_xnu_main():
    parser=argparse.ArgumentParser(description="MEM07 public XNU-only precheck; no seed/image or phone actions")
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
    report={"schema":"thor-mem07-public-xnu-precheck-v1","seed_generated":False,"phone_actions":0,
            "full_L2_ready":False,"verifier_sha256":sha(Path(__file__))}
    try:
        report["frozen_artifact"]=validate_mem07_frozen_artifact(root,candidate,source,proof,require)
        report["compiled_frontier"]=validate_mem07_xnu(root,output,source,candidate,candidate/"kernel.macho",
                                                     json.loads(proof.read_text()),proof,require)
        report["result"]="PASS_PUBLIC_XNU_PRECHECK"
    except Exception as error:
        report.update(result="FAIL",error_type=type(error).__name__,error=str(error))
    if report.get("result")=="PASS_PUBLIC_XNU_PRECHECK":
        native=json.loads(proof.read_text())
        files={str(p):p for p in candidate.glob("*.json")}
        files[str(candidate/"kernel.macho")]=candidate/"kernel.macho"
        files[str(proof)]=proof
        for rel in native["prepared_source_sha256"]:
            files[str(source/".cache/xnu-work"/rel)]=source/".cache/xnu-work"/rel
        files[str(source/"scripts/audit-thor-mem07-linked.py")]=source/"scripts/audit-thor-mem07-linked.py"
        files.update(mem07_baseline_files(root,source))
        report["frozen_input_sha256"]={name:sha(path) for name,path in sorted(files.items())}
    report.update(checks=marks,conditions_count=len(marks))
    with output.open("x") as handle:json.dump(report,handle,indent=2);handle.write("\n")
    print(json.dumps({"result":report["result"],"conditions_count":len(marks),"out":str(output),"phone_actions":0}))
    return 0 if report["result"]=="PASS_PUBLIC_XNU_PRECHECK" else 1


def validate_mem07_transition(root,src,layout,require,proof_path=None):
    import package_host as host
    spec=importlib.util.spec_from_file_location('mem07_actual_transition',Path(__file__).with_name('transition_bind.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    def raw(tree):
        output=tree/'Build/thorEntry09/RELEASE_CLANGPDB/AARCH64/thorPkg/Drivers/ThorEntryBdsDxe/ThorEntryBdsDxe/OUTPUT'
        pe=output/'ThorEntryBdsDxe.efi';mp=output/'ThorEntryBdsDxe.map'
        start,end=module.transition_symbols(mp);code,meta=module.load_pe(pe,start,end-start)
        require(len(code)==500 and not meta['relocations_in_transition'],'actual500B PE transition has no interval relocation');return code
    old=raw(root/'worktrees/uefi/vm-mem06');new=raw(src);decoded=host.validate_mem07_transition_words(old,new,layout)
    require(True,'independent actual PE words match transition/args/entry/vector/stack-top offsets')
    proof=json.loads(proof_path.read_text());require(proof['schema']=='thor-mem07-transition-owned-layout-immediate-proof-v1' and proof['result']=='PASS_ACTUAL_OWNED_LAYOUT_AND_TRANSITION_UNCHANGED' and proof['phone_actions']==0,'MEM07 dedicated owned-layout transition proof')
    require(proof['old_code_sha256']==hashlib.sha256(old).hexdigest() and proof['new_code_sha256']==hashlib.sha256(new).hexdigest() and proof['changed_instruction_word_offsets']==[] and proof['unchanged_bytes']==500 and proof['relocations']==0,'actual raw PE bytes and exact zero instruction differences')
    require((proof_path.parent/'old-transition.bin').read_bytes()==old and (proof_path.parent/'new-transition.bin').read_bytes()==new,'proof raw bytes equal actual independent PE extraction')
    require(all(proof.get(k)==v for k,v in decoded.items()),'all five instruction pairs decoded from actual linked PE agree with proof')
    folder=src/'Platforms/Xiaomi/thorPkg/Drivers/ThorEntryBdsDxe';pin,_,_=module.header_expected(folder/'ThorEntry06TransitionExpected.h')
    require(pin.hex()==host.MEM07_TRANSITION_PIN and proof['new_expected_header_sha256']==sha(folder/'ThorEntry06TransitionExpected.h') and proof['unchanged_assembly_source_sha256']==sha(folder/'thor_entry06_arch.S'),'actual expected header and unchanged assembly bound to native transition')
    require(not subprocess.check_output(['git','-C',str(src),'diff','2d57aff0f01dc6b26b748ca51eb19283e5fa3a25','HEAD','--','*.S'],text=True),'all transition assembly source identical to reviewed MEM06')
    return {'transition_bytes':500,'changed_word_offsets':[],'only_immediate_bits_changed':True,
            'actual_entry_offset':decoded['new_decoded_entry_offset'],'actual_vectors_offset':decoded['new_decoded_vector_offset'],'relocations':0,'binding_proof_sha256':sha(proof_path)}


def validate_cached_xnu_precheck(path,expected,candidate,xsrc,xproof,require):
    require(path.is_file() and sha(path)==expected,'explicit completed MEM07 XNU precheck hash')
    cached=json.loads(path.read_text());require(cached['verifier_sha256']==sha(Path(__file__)),'precheck belongs to exact current verifier');require(cached['schema']=='thor-mem07-public-xnu-precheck-v1' and cached['result']=='PASS_PUBLIC_XNU_PRECHECK' and all(x['passed'] for x in cached['checks']) and cached['conditions_count']==len(cached['checks']) and cached['phone_actions']==0 and cached['seed_generated'] is False,'completed public XNU precheck has all checks PASS and no seed/phone action')
    inventory=cached['frozen_input_sha256']
    files={str(p):p for p in candidate.glob('*.json')};files[str(candidate/'kernel.macho')]=candidate/'kernel.macho'
    extra=json.loads(xproof.read_text());files[str(xproof)]=xproof
    for rel in extra['prepared_source_sha256']:files[str(xsrc/'.cache/xnu-work'/rel)]=xsrc/'.cache/xnu-work'/rel
    files[str(xsrc/'scripts/audit-thor-mem07-linked.py')]=xsrc/'scripts/audit-thor-mem07-linked.py'
    files.update(mem07_baseline_files(xsrc.parents[2],xsrc))
    require(set(files)==set(inventory) and all(sha(p)==inventory[n] for n,p in files.items()),'every frozen candidate/native source/auditor hash still equals completed precheck')
    return {'path':str(path),'sha256':expected,'conditions':cached['conditions_count'],'same_frozen_inputs_rechecked':True,'fresh_reexecution_claimed':False}


def validate_mem07_loader(root,pkg,src,receipt,detail,uproof,require):
    spec=importlib.util.spec_from_file_location('mem07_package_host',Path(__file__).with_name('package_host.py'))
    helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
    document=helper.validate_mem07_loader_proof(src,uproof,receipt,detail)
    base=uproof.parent;proof=base/'loader-memory-probe-audit.json'
    require(document['full_kernel_span_bytes']==113229824 and document['full_kernel_min_used_bytes']==113250304
            and document['full_kernel_alignment_mod']==0x4000,
            'full APFS layout needs112MiB-aligned owned span with actual0x4000 offset')
    require(document['full_kernel_sha256']=='a798cf368ab013e7389f3c5944cea9381859fb98238b346b33423b00bf8d3a37'
            and document['full_kernel_file_bytes']==50659328,
            'full kernel plan bound to previously audited actual APFS kernel identity')
    require(document['canary_endpoint_bytes']==16 and document['live_ranges_excluded']==8
            and document['FDT_grant_pages']==0 and document['full_kernel_booted'] is False,
            'bounded16-byte restored canary, eight live exclusions and no FDT-derived memory grant')
    for key in ['pre_post_free_fresh_maps','stale_map_key_refused','cleanup_after_deadline',
                'released_before_64m_prepare','free_failure_stops','no_candidate_displays_BLOCK',
                'GCD_WB_and_AT_identity_before_canary','canary_original_bytes_restored',
                'source_generation_checked_before_after_copy','exact_source_range_and_length']:
        require(document[key] is True,'production source/probe failure contract: '+key)
    require(document['saved_additional_heap_file_copy_bytes']==0,
            'existing borrowed initrd path is not claimed as a new full-file heap saving')
    require(document['test_names_and_counts']=={'test_mem07_loader.py':29,'test_mem07_canary.py':14,'test_mem07_stack.py':10},
            '53 production loader and hardware-adapter fault cases')
    require(detail['tests']=={'mem01':12,'mem02':10,'mem03':12,'mem07':12,'mem07_loader':29,'mem07_canary':14,'mem07_stack':10}
            and receipt['source_level_regressions_passed']==99,
            'baseline FDT and new renderer/loader suites all included in99 real cases')
    tests=base/'mem07-source-test-results.json'
    require(sha(tests)==document['test_results_sha256']==detail['test_results_sha256'],
            'loader regression receipt is the same aggregate source execution proof')
    canary=base/'canary-host-execution.json'
    require(sha(canary)==document['canary_host_execution_sha256'],'actual canary function/harness execution proof pinned')
    frozen=json.loads(canary.read_text())
    require(frozen['schema']=='thor-mem07-production-canary-host-execution-v1' and
            frozen['phone_actions']==0 and len(frozen['results'])==14,
            'canary adapter executes production function in14 host-substituted fault cases')
    for name,count in document['test_names_and_counts'].items():
        test=src/'Platforms/Xiaomi/thorPkg/Tests'/name
        relative=str(test.relative_to(src))
        require(relative in document['source_files_sha256'] and sha(test)==document['source_files_sha256'][relative],
                'exact production loader regression source: '+name)
        with tempfile.TemporaryDirectory(prefix='mem07-loader-replay-',dir=pkg.parent) as temporary:
            out=Path(temporary)/'canary-host-execution.json';env=__import__('os').environ.copy()
            env['THOR_MEM07_CANARY_AUDIT']=str(out)
            result=subprocess.run([sys.executable,'-B',str(test),'-q'],capture_output=True,text=True,env=env,timeout=120)
            require(result.returncode==0 and ('Ran %d tests'%count) in result.stderr,
                    'fresh actual production-C loader regression: '+name)
            if name=='test_mem07_canary.py':
                require(out.is_file() and json.loads(out.read_text())==frozen,
                        'fresh actual canary adapter execution exactly equals pinned production proof')
    source=(src/'Platforms/Xiaomi/thorPkg/Drivers/ThorEntryBdsDxe/thor_entry06.c').read_text()
    require('ThorMem07CopyExact' in source and 'ThorMem07Probe' in
            (src/'Platforms/Xiaomi/thorPkg/Drivers/ThorEntryBdsDxe/ThorMem07Probe.inc.c').read_text(),
            'actual handoff segment reader and pre64MiB probe production call sites compiled')
    pe=src/'Build/thorEntry09/RELEASE_CLANGPDB/AARCH64/thorPkg/Drivers/ThorEntryBdsDxe/ThorEntryBdsDxe/OUTPUT/ThorEntryBdsDxe.efi'
    binary=pe.read_bytes()
    require(all(label in binary for label in [b'LDR PLAN',b'LDR PROBE']),
            'real linked firmware exposes independent loader PLAN and PROBE results')
    return {'proof_sha256':sha(proof),'temporary_owned_probe_bytes':117440512,'probe_pages':28672,
            'persistent_XNU_grant_bytes':67108864,'production_fault_cases_replayed':53,
            'real_hardware_probe_result':'PENDING_USER_RECORDED_BOOT','full_APFS_kernel_booted':False}


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
    validate_mem07_frozen_artifact(root,candidate,xsrc,xproof,require,replay_regressions=cached_xnu is None)
    require(manifest["kernel_build_source_revision"]==build["build_source_commit"]
            and manifest["kernel_artifact_manifest_sha256"]==sha(candidate/"manifest.json")
            and manifest["kernel_build_manifest_sha256"]==sha(candidate/"build-manifest.json"),
            "packed kernel traces exact frozen build and audited source metadata")
    require("mem07" in args.run_id,"MEM07 unique run identity")
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
    require(actual.base==layout.get("virtual_base",0xfffffff007004000) and actual.end==layout.get("virtual_end",actual.base+layout["loaded_span_bytes"]) and actual.end-actual.base==layout["loaded_span_bytes"],"Mach-O actual segment span independently parsed")
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
            "structured four-card diagnostic progress screen linked into real MEM07 UEFI PE")
    require(b"ESR / ELR / FAR" in binary and b"NOT CAPTURED IN UEFI" in binary,
            "unavailable exception values explicitly identified")
    require(b"UEFI CHECKS FAILED" in binary and b"UEFI CHECKS PASS" in binary,
            "diagnostic screen distinguishes pass from failure by text")
    require(b"UEFI 1 INITRD READ" in binary and b"UEFI 2 KERNEL HASH PASS" in binary,
            "retained UEFI stage labels")
    require(b"RUN MEM07 / THOR SM8475" in binary,
        "final UEFI linked PE identifies MEM07 candidate")
    hud=src/"Platforms/Xiaomi/thorPkg/Drivers/ThorEntryBdsDxe/ThorReportHud.inc.c"
    require(sha(hud)==receipt["new_ui_source_sha256"],
        "final MEM07 UEFI HUD SHA equals release source")
    ui_receipt=firmware_release.parent/"ui-stage-grid-audit.json"
    require(ui_receipt.is_file() and
        sha(ui_receipt)==receipt["new_ui_layout_audit_sha256"],
        "MEM07 independent XNU 4x4 grid provenance attached to exact UEFI build")
    ui_meta=json.loads(ui_receipt.read_text())
    require(ui_meta["result"]=="PASS_LINKED_MEM07_AND_RESERVED_GOP_Y0_319"
        and ui_meta["grid_size"]==[4,4]
        and ui_meta["tile_size_pixels"]==[328,42]
        and ui_meta["xnu_mapped_screen_y"]==[64,287],
        "MEM07 42px 4x4 grid drawn by XNU in y64..287")
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
    require(receipt["schema"]=="thor-mem07-native-startup-and-loader-probe-v1" and
          receipt["source_level_regressions_passed"]==99 and
          receipt["no_write_to_candidate_windows"] is True and
          receipt["no_memory_grant_or_heap_extension"] is True and
          receipt["UEFI_reserved_top_y0_through_319"] is True,
          "MEM07 retains read-only candidate policy with all99 source regressions")
    extended=validate_mem07_inputs(root,pkg,src,xsrc,candidate,manifest,receipt,audit,layout,build,machine,binding,extra,detail,boot,kernel,fd,prov,xproof,uproof,firmware_audit,transfer_index,require,replay_xnu=cached_xnu is None)

    final={"schema":"thor-vm-independent-offline-gate-v1",
           "checked_at":datetime.datetime.now().astimezone().isoformat(),
           "result":"PASS","conditions_count":len(marks),"checks":marks,
           "inputs":{"android_boot_v4":{"sha256":image_hash,"bytes":boot.stat().st_size},
                     "native_xnu":{"sha256":kernel_hash,"bytes":kernel.stat().st_size},
                     "uefi_fd":{"sha256":sha(fd),"bytes":fd.stat().st_size}},
           "run_id":args.run_id,"verifier_sha256":sha(Path(__file__)),
           "xnu_mem07_proof_sha256":sha(xproof),"uefi_mem07_proof_sha256":sha(uproof),
           "requires_new_recording_authorization":True,"L3_authorized":False,
           "fresh_firmware_replay":extended["fresh_firmware_replay"],
           "libfdt_host_dependency_sha256":extended["libfdt_host_dependency_sha256"],
           "compiled_kernel_frontier":extended["compiled_kernel_frontier"],
           "loader_contract":extended["loader_contract"],
           "executed_stage_render":extended["executed_stage_render"],
           "transition_rebind":extended["transition_rebind"],
           "reused_completed_XNU_precheck":cached_xnu,
           "mem07_final_fdt_report_persists":True,
           "mem07_five_readonly_FDT_windows":True,
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
