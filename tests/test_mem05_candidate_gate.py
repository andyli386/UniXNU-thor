#!/usr/bin/env python3
"""Risk regressions for MEM05 independent offline contracts; no phone access."""
import copy
import importlib.util
import struct
import unittest
from pathlib import Path

HERE=Path(__file__).resolve()
SCRIPT=HERE.with_name("verify_mem05_candidate.py")
if not SCRIPT.exists():
    SCRIPT=HERE.parents[1]/"tools/thorctl/verify_mem05_candidate.py"
spec=importlib.util.spec_from_file_location("mem05_gate",SCRIPT)
gate=importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
REPLAY=SCRIPT.with_name("firmware_audit_replay.py")
rspec=importlib.util.spec_from_file_location("firmware_replay",REPLAY)
replay=importlib.util.module_from_spec(rspec);rspec.loader.exec_module(replay)


def boot_fixture():
    kernel,bundle=b"kernel"*107,b"transport"*97
    header=bytearray(4096)
    header[:8]=b"ANDROID!"
    struct.pack_into("<II",header,8,len(kernel),len(bundle))
    struct.pack_into("<I",header,20,1584)
    struct.pack_into("<I",header,40,4)
    return bytearray(header+kernel+bytes(gate.align(len(kernel))-len(kernel))+
                     bundle+bytes(gate.align(len(bundle))-len(bundle))),kernel,bundle


def macho_fixture():
    header=struct.pack("<8I",0xfeedfacf,0x100000c,0,2,1,72,0,0)
    segment=struct.pack("<II16sQQQQIIII",0x19,72,b"__TEXT",0x100000,4096,0,4096,7,5,0,0)
    data=bytearray(header+segment+bytes(4096-len(header+segment)))
    struct.pack_into("<I",data,256,0x52802770) # MOVZ w16,#315
    return data


def detail_fixture():
    return dict(schema="thor-mem05-stage-safe-hud-host-proof-v1",
                result="PASS_MEM05_STAGE_SAFE_HUD_AND_EARLY_VM_RETURN_BINDING",same_immutable_snapshot=True,
                per_window_dynamic_count=True,unresolved_truncation_explicit=True,
                status_and_padding_reported=True,final_postclear_overlay=True,
                unresolved_detail_limit=2,framebuffer_reserved_y=[0,319],
                detail_overlay_xywh=[30,1925,1380,1153],
                policy_unchanged=dict(fdt_gate=True,fdt_gate_header=True,memory_map=True,payload_read_permit=True),
                new_allocatable_memory_pages=0,phone_actions=0)


class CoverageTests(unittest.TestCase):
    def validate(self,pages=315,consumer=315,start=0xb805a000,end=0xb8195000,flags=0x006000000000070b):
        return gate.coverage_contract(pages,consumer,start,end,flags)

    def test_all315_pages_and_edges(self):
        pages=self.validate()
        self.assertEqual((len(pages),pages[0],pages[-1]),(315,0xb805a000,0xb8194000))

    def test_historical90_producer315_consumer_rejected(self):
        with self.assertRaisesRegex(gate.GateError,"both cover315"):
            self.validate(pages=90)

    def test_missing_last_page_rejected(self):
        with self.assertRaises(gate.GateError): self.validate(pages=314)

    def test_extra_leaf_rejected(self):
        with self.assertRaises(gate.GateError): self.validate(pages=316)

    def test_consumer_extent_drift_rejected(self):
        with self.assertRaises(gate.GateError): self.validate(consumer=314)

    def test_shifted_physical_base_rejected(self):
        with self.assertRaises(gate.GateError): self.validate(start=0xb805b000)

    def test_alias_outside_extent_rejected(self):
        with self.assertRaises(gate.GateError): self.validate(end=0xb8196000)

    def test_write_cache_xn_attribute_mutations_rejected(self):
        for bit in (4,0x80,1<<54):
            with self.subTest(bit=bit),self.assertRaises(gate.GateError):
                self.validate(flags=0x006000000000070b^bit)


class BootImageTests(unittest.TestCase):
    def test_independently_reads_valid_sections(self):
        gate.validate_boot_bytes(*boot_fixture())

    def test_wrong_header_version(self):
        image,k,b=boot_fixture();struct.pack_into("<I",image,40,3)
        with self.assertRaises(gate.GateError):gate.validate_boot_bytes(image,k,b)

    def test_changed_ramdisk_length(self):
        image,k,b=boot_fixture();struct.pack_into("<I",image,12,len(b)+1)
        with self.assertRaises(gate.GateError):gate.validate_boot_bytes(image,k,b)

    def test_nonzero_header_padding(self):
        image,k,b=boot_fixture();image[2000]=1
        with self.assertRaises(gate.GateError):gate.validate_boot_bytes(image,k,b)

    def test_nonzero_kernel_padding(self):
        image,k,b=boot_fixture();image[4096+len(k)]=1
        with self.assertRaises(gate.GateError):gate.validate_boot_bytes(image,k,b)

    def test_embedded_transport_tampering(self):
        image,k,b=boot_fixture();image[4096+gate.align(len(k))+2]^=1
        with self.assertRaises(gate.GateError):gate.validate_boot_bytes(image,k,b)

    def test_appended_bytes(self):
        image,k,b=boot_fixture()
        with self.assertRaises(gate.GateError):gate.validate_boot_bytes(image+b"\0",k,b)

    def test_truncated_image(self):
        image,k,b=boot_fixture()
        with self.assertRaises(gate.GateError):gate.validate_boot_bytes(image[:-1],k,b)


class MachOTests(unittest.TestCase):
    def test_parses_actual_instruction_not_receipt_count(self):
        data=macho_fixture();m=gate.MachO(data)
        self.assertEqual(m.movz(0x100100,16),315)
        struct.pack_into("<I",data,256,0x52800b50) # original broken90
        self.assertEqual(gate.MachO(data).movz(0x100100,16),90)

    def test_wrong_register_cannot_substitute_count(self):
        with self.assertRaises(gate.GateError):gate.MachO(macho_fixture()).movz(0x100100,22)

    def test_non_native_image(self):
        data=macho_fixture();struct.pack_into("<I",data,4,0x1000007)
        with self.assertRaises(gate.GateError):gate.MachO(data)

    def test_truncated_load_commands(self):
        with self.assertRaises(gate.GateError):gate.MachO(macho_fixture()[:64])

    def test_segment_file_extent(self):
        data=macho_fixture();struct.pack_into("<Q",data,32+48,8192)
        with self.assertRaises(gate.GateError):gate.MachO(data)

    def test_unmapped_instruction_va(self):
        with self.assertRaises(gate.GateError):gate.MachO(macho_fixture()).word(0x200000)


class ReuseTests(unittest.TestCase):
    def test_fresh_identity(self):gate.reject_reuse("new","newseed",[{"image_sha256":"old","seed_sha256":"oldseed"}])

    def test_consumed_MEM02_hash(self):
        with self.assertRaises(gate.GateError):gate.reject_reuse(next(iter(gate.CONSUMED_BASELINES)),"fresh",[],gate.CONSUMED_BASELINES)

    def test_old_seed_with_new_image(self):
        with self.assertRaises(gate.GateError):gate.reject_reuse("fresh","oldseed",[{"image_sha256":"old","seed_sha256":"oldseed"}])

    def test_old_image_even_if_unconsumed(self):
        with self.assertRaises(gate.GateError):gate.reject_reuse("old","freshseed",[{"image_sha256":"old","seed_sha256":"oldseed"}])


class FdtDetailTests(unittest.TestCase):
    def test_complete_same_snapshot_detail(self):gate.validate_uefi_detail_document(detail_fixture())

    def test_incomplete_fields_rejected(self):
        detail=detail_fixture();del detail["same_immutable_snapshot"]
        with self.assertRaises(KeyError):gate.validate_uefi_detail_document(detail)

    def test_global_count_without_window_count_rejected(self):
        detail=detail_fixture();detail["per_window_dynamic_count"]=False
        with self.assertRaises(gate.GateError):gate.validate_uefi_detail_document(detail)

    def test_unknown_node_truncation_must_be_explicit(self):
        detail=detail_fixture();detail["unresolved_truncation_explicit"]=False
        with self.assertRaises(gate.GateError):gate.validate_uefi_detail_document(detail)

    def test_display_cannot_claim_memory(self):
        detail=detail_fixture();detail["new_allocatable_memory_pages"]=65536
        with self.assertRaises(gate.GateError):gate.validate_uefi_detail_document(detail)

    def test_parser_policy_cannot_be_relaxed(self):
        for policy in detail_fixture()["policy_unchanged"]:
            detail=detail_fixture();detail["policy_unchanged"][policy]=False
            with self.subTest(policy=policy),self.assertRaises(gate.GateError):gate.validate_uefi_detail_document(detail)

    def test_cannot_cover_XNU_reserved_area(self):
        detail=detail_fixture();detail["framebuffer_reserved_y"]=[0,127]
        with self.assertRaises(gate.GateError):gate.validate_uefi_detail_document(detail)

    def test_details_cannot_be_erased_after_render(self):
        detail=detail_fixture();detail["final_postclear_overlay"]=False
        with self.assertRaises(gate.GateError):gate.validate_uefi_detail_document(detail)


class FirmwareRelocationTests(unittest.TestCase):
    def mapper(self):
        return replay.PublicPathMap(Path("/new/root/worktrees/uefi/vm-mem05"),
                                    Path("/old/root/worktrees/uefi/vm-mem05"),Path("/old/root"))

    def test_only_explicit_source_prefix_relocated(self):
        self.assertEqual(self.mapper()("/old/root/worktrees/uefi/vm-mem05/Build/file.lib"),
                         Path("/new/root/worktrees/uefi/vm-mem05/Build/file.lib"))

    def test_other_original_paths_fail_instead_of_searching(self):
        with self.assertRaises(replay.ReplayError):self.mapper()("/old/root/private/file")

    def test_existing_local_paths_unchanged(self):
        self.assertEqual(self.mapper()("/new/root/worktrees/uefi/vm-mem05/Build/file.lib"),
                         Path("/new/root/worktrees/uefi/vm-mem05/Build/file.lib"))

    def test_normalization_only_discards_paths_and_timestamp(self):
        before={"audited_utc":"old","file":{"path":"/old/source/file","sha256":"abc"}}
        after={"audited_utc":"new","file":{"path":"/new/source/file","sha256":"abc"}}
        replacements=[("/old/source","$SOURCE"),("/new/source","$SOURCE")]
        self.assertEqual(replay.semantic(before,replacements),replay.semantic(after,replacements))
        after["file"]["sha256"]="changed"
        self.assertNotEqual(replay.semantic(before,replacements),replay.semantic(after,replacements))

    def test_semantic_PTE_or_PE_changes_are_never_normalized(self):
        before={"modules":[{"sha256":"abc","guid":"1"}],"attributes":0x70b}
        after=copy.deepcopy(before);after["attributes"]^=0x80
        self.assertNotEqual(replay.semantic(before,[]),replay.semantic(after,[]))


def pmap_fixture():
    return dict(page_shift=12,mem_size=64<<20,pt_desc_size=64,ptd_info_size=4,
                pt_index_max=4,pp_attr_size=2,pv_pointer_size=8,pte_size=8,
                max_l2_cline=6,pmap_max_asids=2048,bitmap_word_size=8,
                io_attr_table_size=0,io_filter_table_size=0,xnu_monitor=False,
                has_guarded_io_filter=False,has_16bit_asid=False)


class NativePmapBudgetTests(unittest.TestCase):
    def test_exact_native_ABI_budget(self):
        result=gate.pmap_budget_contract(pmap_fixture())
        self.assertEqual((result["metadata_bytes"],result["asid_bytes"],result["total_pages"]),(172032,4096,43))
        self.assertEqual(result["ptd_per_page"],51)

    def test_runtime_page_shift_must_be12(self):
        c=pmap_fixture();c["page_shift"]=14
        with self.assertRaises(gate.GateError):gate.pmap_budget_contract(c)

    def test_oversized_memory_changes_budget(self):
        c=pmap_fixture();c["mem_size"]*=2
        with self.assertRaises(gate.GateError):gate.pmap_budget_contract(c)

    def test_64bit_pointer_ABI_required(self):
        c=pmap_fixture();c["pv_pointer_size"]=4
        with self.assertRaises(gate.GateError):gate.pmap_budget_contract(c)

    def test_nondefault_ADT_IO_cannot_enter_fixed_budget(self):
        for key in ("io_attr_table_size","io_filter_table_size"):
            c=pmap_fixture();c[key]=4096
            with self.subTest(key=key),self.assertRaises(gate.GateError):gate.pmap_budget_contract(c)

    def test_unreviewed_ppl_filter_or_asid_feature_rejected(self):
        for key in ("xnu_monitor","has_guarded_io_filter","has_16bit_asid"):
            c=pmap_fixture();c[key]=True
            with self.subTest(key=key),self.assertRaises(gate.GateError):gate.pmap_budget_contract(c)



class NumberedPaletteTests(unittest.TestCase):
    def fixture(self):
        palette=[0x888888+i*0x010101 for i in range(16)]
        labels=["STAGE "+str(i) for i in range(16)];labels[11:]=["PMAP CALL","PMAP RETURN","PMAP GUARD","L3 CALL","VM RETURN"]
        states=["PASS"]*16;states[7]="CALL";states[8]="RETURN";states[11]="CALL";states[12]="RETURN";states[14:]=["CALL","PASS"]
        font=sum(([(i+1)&31,((i+1)>>5)&31,0,0,0,0,1] for i in range(36)),[])
        return palette,labels,states,font

    def test_sixteen_numbered_cells(self):gate.numbered_display_contract(*self.fixture())

    def test_duplicate_completed_colors(self):
        p,l,s,f=self.fixture();p[4]=p[3]
        with self.assertRaises(gate.GateError):gate.numbered_display_contract(p,l,s,f)

    def test_pass_color_cannot_equal_WAIT_gray(self):
        p,l,s,f=self.fixture();p[4]=0x202020
        with self.assertRaises(gate.GateError):gate.numbered_display_contract(p,l,s,f)

    def test_pmap_call_and_return_cannot_be_swapped(self):
        p,l,s,f=self.fixture();s[11],s[12]=s[12],s[11]
        with self.assertRaises(gate.GateError):gate.numbered_display_contract(p,l,s,f)

    def test_blank_checkpoint_label(self):
        p,l,s,f=self.fixture();l[13]=" "
        with self.assertRaises(gate.GateError):gate.numbered_display_contract(p,l,s,f)

    def test_truncated_label(self):
        p,l,s,f=self.fixture();l[13]="A"*16
        with self.assertRaises(gate.GateError):gate.numbered_display_contract(p,l,s,f)

    def test_duplicate_hex_glyphs(self):
        p,l,s,f=self.fixture();f[7:14]=f[:7]
        with self.assertRaises(gate.GateError):gate.numbered_display_contract(p,l,s,f)

    def test_blank_hex_id(self):
        p,l,s,f=self.fixture();f[:7]=[0]*7
        with self.assertRaises(gate.GateError):gate.numbered_display_contract(p,l,s,f)

    def test_incomplete_font(self):
        p,l,s,f=self.fixture()
        with self.assertRaises(gate.GateError):gate.numbered_display_contract(p,l,s,f[:-1])


class ExecutedDrawTraceTests(unittest.TestCase):
    def events(self):
        result=[]
        names=["initial","read_permit","kernel_hash_pass","kernel_hash_refused",
               "loader_handoff_ready","loader_error_max_values","read_refused"]
        for frame,name in enumerate(names,1):
            items=[{"frame":frame,"kind":"begin","name":name},
                   {"frame":frame,"kind":"clear","rect":[0,0,1440,3200]},
                   {"frame":frame,"kind":"fill","rect":[30,736,1380,300]},
                   {"frame":frame,"kind":"text","text":"STATUS","scale":2,"rect":[40,750,70,14]}]
            if name=="kernel_hash_pass":
                items += [{"frame":frame,"kind":"text","text":"A"*32,"scale":2,
                           "rect":[40,y,382,14]} for y in (1570,1602,1685,1717)]
            items.append({"frame":frame,"kind":"pixels","glyph_pixels":10001,"forbidden_glyphs":0})
            result+=items
        return result

    def test_actual_operation_order_and_SHA_regions(self):
        checked=gate.executed_draw_events_contract(self.events())
        self.assertEqual(checked["frame_count"],7)

    def test_later_background_cannot_erase_existing_text(self):
        events=self.events();events.insert(4,{"frame":1,"kind":"fill","rect":[40,750,200,20]})
        with self.assertRaises(gate.GateError):gate.executed_draw_events_contract(events)

    def test_two_text_fields_cannot_overlap(self):
        events=self.events();events.insert(4,{"frame":1,"kind":"text","text":"SECOND","scale":2,"rect":[45,750,70,14]})
        with self.assertRaises(gate.GateError):gate.executed_draw_events_contract(events)

    def test_real_font_width_cannot_be_underreported(self):
        events=self.events();events[3]["rect"][2]=1
        with self.assertRaises(gate.GateError):gate.executed_draw_events_contract(events)

    def test_reserved_XNU_pixels_rejected(self):
        events=self.events();events[3]["rect"][1]=64
        with self.assertRaises(gate.GateError):gate.executed_draw_events_contract(events)

    def test_missing_or_partial_SHA_line_rejected(self):
        events=self.events();line=next(x for x in events if x.get("text")=="A"*32);line["text"]="A"*31;line["rect"][2]=370
        with self.assertRaises(gate.GateError):gate.executed_draw_events_contract(events)

    def test_font_pixels_must_actually_execute(self):
        events=self.events();events[4]["glyph_pixels"]=0
        with self.assertRaises(gate.GateError):gate.executed_draw_events_contract(events)

    def test_missing_transport_error_frame_rejected(self):
        events=[x for x in self.events() if x["frame"]!=7]
        with self.assertRaises(gate.GateError):gate.executed_draw_events_contract(events)

    def test_second_clear_rejected(self):
        events=self.events();events.insert(4,{"frame":1,"kind":"clear","rect":[0,0,1440,3200]})
        with self.assertRaises(gate.GateError):gate.executed_draw_events_contract(events)


if __name__=="__main__":
    unittest.main()
