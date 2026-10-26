#!/usr/bin/env python3
"""Risk regressions for MEM03 independent offline contracts; no phone access."""
import copy
import importlib.util
import struct
import unittest
from pathlib import Path

HERE=Path(__file__).resolve()
SCRIPT=HERE.with_name("verify_mem03_candidate.py")
if not SCRIPT.exists():
    SCRIPT=HERE.parents[1]/"tools/thorctl/verify_mem03_candidate.py"
spec=importlib.util.spec_from_file_location("mem03_gate",SCRIPT)
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
    return dict(schema="thor-mem03-snapshot-detail-host-proof-v1",
                result="PASS_MEM03_FDT_DETAIL_AND_FINAL_OVERLAY",same_immutable_snapshot=True,
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
        return replay.PublicPathMap(Path("/new/root/worktrees/uefi/vm-mem03"),
                                    Path("/old/root/worktrees/uefi/vm-mem03"),Path("/old/root"))

    def test_only_explicit_source_prefix_relocated(self):
        self.assertEqual(self.mapper()("/old/root/worktrees/uefi/vm-mem03/Build/file.lib"),
                         Path("/new/root/worktrees/uefi/vm-mem03/Build/file.lib"))

    def test_other_original_paths_fail_instead_of_searching(self):
        with self.assertRaises(replay.ReplayError):self.mapper()("/old/root/private/file")

    def test_existing_local_paths_unchanged(self):
        self.assertEqual(self.mapper()("/new/root/worktrees/uefi/vm-mem03/Build/file.lib"),
                         Path("/new/root/worktrees/uefi/vm-mem03/Build/file.lib"))

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


if __name__=="__main__":
    unittest.main()
