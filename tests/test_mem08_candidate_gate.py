#!/usr/bin/env python3
"""Rejection tests for independent MEM08 contracts; no USB or phone access."""
import base64
import copy
import hashlib
import importlib.util
from pathlib import Path
import struct
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'tools/thorctl/verify_mem08_candidate.py'
spec = importlib.util.spec_from_file_location('mem08_gate_under_test', SCRIPT)
gate = importlib.util.module_from_spec(spec); spec.loader.exec_module(gate)


def header(asset=b'compressed-test-data', **changes):
    fields = [gate.FULL_MAGIC, 1, 128, len(asset), hashlib.sha256(asset).digest(),
              gate.RAW_BYTES, gate.FULL_SPAN, bytes.fromhex(gate.RAW_SHA), 1, 15, 0]
    for index, value in changes.items(): fields[int(index)] = value
    return gate.FULL_HEADER.pack(*fields)


def macho(segments=None):
    if segments is None:
        segments = [('__TEXT', 0x100000, 4096, 0, 4096), ('__DATA', 0x102000, 4096, 4096, 1024),
                    ('__ZERO', 0x103000, 0, 0, 0)]
    command = b''.join(struct.pack('<II16sQQQQIIII', 0x19, 72, n.encode(), va, vm, off, size, 7, 3, 0, 0)
                       for n, va, vm, off, size in segments)
    result = struct.pack('<8I', 0xfeedfacf, 0x100000c, 0, 2, len(segments), len(command), 0, 0) + command
    return bytearray(result + b'\x5a' * (8192 - len(result)))


def geometry():
    return dict(rows=4, columns=4, fb_existing_owned_pages=391, new_page_table_allocations=0,
        new_high_alias_L3_indices=[16, 406], old_hw07_alias_L3_indices=[480, 499],
        tile_width_pixels=328, tile_height_pixels=42,
        cells=[dict(slot=i, relative_xywh=[16 + 352*(i&3), 62 + 54*(i>>2), 328, 42],
            physical_first_pixel=hex(gate.FB_START+((62+54*(i>>2))*1440+16+352*(i&3))*4)) for i in range(16)])


MEM07_TRANSITION_FIXTURE = base64.b64decode('XyQD1d9PA9XfPwPVSEI41R8RAPHhDgBUCICg0n8ACOuBDQBUCAC/0l8ACOsjDQBUCHi/0l8ACOvIDABUSFBAkh8RQPFhDABUyP3/EAkAkNKJJqDyKQECix8BCeuhCwBUCACI0ogmoPIIAQKLHwAI6wELAFQIkYDSCAig8ggBAos/AAjrYQoAVAgAgtIICKDyCAECi58ACOvBCQBUCACA0ggnoPIIAQKLvwAI6yEJAFQIEDjVHwEG68EIAFSpAILSCgEJil8BCetBCABUKAjINwgImDcJ/WDTyQcAtSgAO9UfAQfrYQcAVOkMQJI/KQDxCAcAVOlMUNM/KQDxqAYAVIoAgNJKIcmaBMAY1d8/A9W/AACR/QMfqv4DH6roAwKqSQADiyh+C9UIAQqLHwEJ66P//1QIAJTSqAC38gkAiNJpAbfyKH4L1QgBCosfAQnro///VJ8/A9UfdQjVnz8D1d8/A9XoAwaqCPl/kgj5fZII+XOSnz8D1QgQGNXfPwPVH4cI1Z8/A9XfPwPVCRA41T8BCOvhAQBUSUI41T8RAPGBAgBUCcA41T8BBOshAQBU6QMAkT8BBevBAABUKUI71SkNepI/AQ/xQQAAVCAAH9bfTwPVnz8D1SABgFIAgLBy4QMfquIDH6rjAx+qAwAA1AAAABQ=')

class TransitionTests(unittest.TestCase):
    def setUp(self):
        self.layout={'loaded_span_bytes':21000192,'entry_offset':0x402488,'vectors_offset':0x403000}
        self.new=bytearray(MEM07_TRANSITION_FIXTURE)
        pairs=[(76,self.layout['loaded_span_bytes']+0x4000),(96,self.layout['loaded_span_bytes']),
               (116,self.layout['entry_offset']),(136,self.layout['vectors_offset']),
               (156,self.layout['loaded_span_bytes']+0x3c000)]
        for offset,value in pairs:
            for at,immediate in [(offset,value&0xffff),(offset+4,value>>16)]:
                word=struct.unpack_from('<I',self.new,at)[0]
                struct.pack_into('<I',self.new,at,(word&~(0xffff<<5))|(immediate<<5))
    def validate(self):return gate.transition_words_contract(MEM07_TRANSITION_FIXTURE,self.new,self.layout)
    def test_all_five_actual_instruction_pairs_decoded(self):
        self.assertEqual(self.validate()['new_decoded_args_offset'],21000192)
    def test_other_instruction_cannot_change(self):
        self.new[180]^=1
        with self.assertRaises(gate.GateError):self.validate()
    def test_mov_register_cannot_change(self):
        self.new[76]^=1
        with self.assertRaises(gate.GateError):self.validate()
    def test_mov_shift_cannot_change(self):
        self.new[82]^=0x20
        with self.assertRaises(gate.GateError):self.validate()
    def test_actual_entry_offset_must_match(self):
        self.layout['entry_offset']+=4
        with self.assertRaises(gate.GateError):self.validate()
    def test_old_transition_pin_is_required(self):
        old=bytearray(MEM07_TRANSITION_FIXTURE);old[180]^=1
        with self.assertRaises(gate.GateError):gate.transition_words_contract(old,self.new,self.layout)


class HeaderTests(unittest.TestCase):
    def test_exact_full08_wire(self):
        self.assertEqual(gate.full08_header_contract(header(), b'compressed-test-data')['nonzero_segment_count'], 15)

    def test_old_or_wrong_identity_rejected(self):
        with self.assertRaises(gate.GateError): gate.full08_header_contract(header(**{'0': b'THOR-FULL07'+b'\0'*5}), b'compressed-test-data')

    def test_wrong_version_rejected(self):
        with self.assertRaises(gate.GateError): gate.full08_header_contract(header(**{'1': 2}), b'compressed-test-data')

    def test_wrong_header_length_rejected(self):
        with self.assertRaises(gate.GateError): gate.full08_header_contract(header(**{'2': 120}), b'compressed-test-data')

    def test_truncated_header_rejected(self):
        with self.assertRaises(gate.GateError): gate.full08_header_contract(header()[:-1], b'compressed-test-data')

    def test_compressed_length_rejected(self):
        with self.assertRaises(gate.GateError): gate.full08_header_contract(header(**{'3': 8}), b'compressed-test-data')

    def test_compressed_hash_rejected(self):
        with self.assertRaises(gate.GateError): gate.full08_header_contract(header(**{'4': bytes(32)}), b'compressed-test-data')

    def test_raw_length_rejected(self):
        with self.assertRaises(gate.GateError): gate.full08_header_contract(header(**{'5': gate.RAW_BYTES-1}), b'compressed-test-data')

    def test_loaded_span_rejected(self):
        with self.assertRaises(gate.GateError): gate.full08_header_contract(header(**{'6': gate.FULL_SPAN+4096}), b'compressed-test-data')

    def test_raw_hash_rejected(self):
        with self.assertRaises(gate.GateError): gate.full08_header_contract(header(**{'7': bytes(32)}), b'compressed-test-data')

    def test_tiano_or_other_algorithm_rejected(self):
        with self.assertRaises(gate.GateError): gate.full08_header_contract(header(**{'8': 2}), b'compressed-test-data')

    def test_total_records_cannot_replace_nonzero_count(self):
        with self.assertRaises(gate.GateError): gate.full08_header_contract(header(**{'9': 18}), b'compressed-test-data')

    def test_reserved_bits_rejected(self):
        with self.assertRaises(gate.GateError): gate.full08_header_contract(header(**{'10': 1}), b'compressed-test-data')


class FullMachOTests(unittest.TestCase):
    def test_keeps_zero_record_distinct_from_loaded_segments(self):
        result = gate.full_macho_plan(macho())
        self.assertEqual((result['segment_record_count'], result['nonzero_segment_count']), (3, 2))
        self.assertEqual([x['destination_offset'] for x in result['sorted_nonzero_segments']], [0, 8192])

    def test_wrong_total_and_nonzero_profile_rejected(self):
        with self.assertRaises(gate.GateError): gate.full_profile_contract(gate.full_macho_plan(macho()))

    def test_truncated_file_segment_rejected(self):
        with self.assertRaises(gate.GateError): gate.full_macho_plan(macho()[:-4097])

    def test_non_arm64_rejected(self):
        raw=macho();struct.pack_into('<I',raw,4,0x1000007)
        with self.assertRaises(gate.GateError): gate.full_macho_plan(raw)

    def test_overlap_loaded_segments_rejected(self):
        raw=macho([('__TEXT',0x100000,4096,0,4096),('__DATA',0x100800,4096,4096,1024)])
        with self.assertRaises(gate.GateError): gate.full_macho_plan(raw)

    def test_file_cannot_exceed_virtual_capacity(self):
        raw=macho([('__TEXT',0x100000,2048,0,4096)])
        with self.assertRaises(gate.GateError): gate.full_macho_plan(raw)

    def test_reverse_expansion_cannot_move_below_source(self):
        raw=macho([('__TEXT',0x100000,4096,0,4096),('__DATA',0x101000,4096,6000,1024)])
        with self.assertRaises(gate.GateError): gate.full_macho_plan(raw)

    def test_va_overflow_rejected(self):
        raw=macho([('__TEXT',(1<<64)-4096,8192,0,4096)])
        with self.assertRaises(gate.GateError): gate.full_macho_plan(raw)

    def test_zero_record_cannot_have_file_bytes(self):
        raw=macho([('__TEXT',0x100000,4096,0,4096),('__ZERO',0x102000,0,4096,1)])
        with self.assertRaises(gate.GateError): gate.full_macho_plan(raw)

    def test_file_overlap_across_zero_file_segment_rejected(self):
        raw=macho([('__TEXT',0x100000,4096,0,4096),('__BSS',0x101000,4096,0,0),
                   ('__DATA',0x102000,4096,2048,1024)])
        with self.assertRaises(gate.GateError):gate.full_macho_plan(raw)


class GridTests(unittest.TestCase):
    def test_all391_pages(self):
        pages=gate.coverage_contract(391,391,gate.FB_START,gate.FB_END,gate.FB_FLAGS)
        self.assertEqual((len(pages),pages[-1]),(391,0xb81e0000))

    def test_old315_mapping_rejected(self):
        with self.assertRaises(gate.GateError):gate.coverage_contract(315,315,gate.FB_START,0xb8195000,gate.FB_FLAGS)

    def test_mismatched_consumer_rejected(self):
        with self.assertRaises(gate.GateError):gate.coverage_contract(391,390,gate.FB_START,gate.FB_END,gate.FB_FLAGS)

    def test_more_or_less_mapping_rejected(self):
        for count in (390,392):
            with self.subTest(count=count),self.assertRaises(gate.GateError):
                gate.coverage_contract(count,count,gate.FB_START,gate.FB_START+count*4096,gate.FB_FLAGS)

    def test_attribute_drift_rejected(self):
        with self.assertRaises(gate.GateError):gate.coverage_contract(391,391,gate.FB_START,gate.FB_END,gate.FB_FLAGS^0x80)

    def test_exact_one_row_shift(self):gate.geometry_contract(geometry())

    def test_old_y_position_rejected(self):
        value=geometry();value['cells'][0]['relative_xywh'][1]=8
        with self.assertRaises(gate.GateError):gate.geometry_contract(value)

    def test_false_physical_position_rejected(self):
        value=geometry();value['cells'][0]['physical_first_pixel']='0xb8065440'
        with self.assertRaises(gate.GateError):gate.geometry_contract(value)

    def test_alias_last_page_rejected(self):
        value=geometry();value['new_high_alias_L3_indices'][1]=405
        with self.assertRaises(gate.GateError):gate.geometry_contract(value)


class ScratchTests(unittest.TestCase):
    def setUp(self):self.value=dict(window_bytes=gate.WINDOW_BYTES,minimum_layout_bytes=gate.MINIMUM_LAYOUT,
        scratch_offset=gate.SCRATCH_OFFSET,scratch_reserved_bytes=16384,scratch_bytes=13376)
    def test_exact_measured_scratch(self):gate.scratch_contract(self.value)
    def test_scratch_cannot_overlap_image(self):
        self.value['scratch_offset']=gate.FULL_SPAN-4096
        with self.assertRaises(gate.GateError):gate.scratch_contract(self.value)
    def test_measured_scratch_cannot_overrun_reservation(self):
        self.value['scratch_bytes']=16385
        with self.assertRaises(gate.GateError):gate.scratch_contract(self.value)
    def test_smaller_window_rejected(self):
        self.value['window_bytes']=64<<20
        with self.assertRaises(gate.GateError):gate.scratch_contract(self.value)


class PEIdentityTests(unittest.TestCase):
    def test_accepts_only_full_new_identity(self):
        self.assertTrue(gate.mem08_pe_identity(b'RUN MEM08 / THOR SM8475\0THOR FDT MEMDIAG 08\0MEM08 FDT WINDOW ONLY - NO MEMORY GRANT'))
    def test_old_mem07_cannot_pass(self):
        self.assertFalse(gate.mem08_pe_identity(b'RUN MEM07 / THOR SM8475\0THOR FDT MEMDIAG 07'))
    def test_mixed_old_and_new_rejected(self):
        self.assertFalse(gate.mem08_pe_identity(b'RUN MEM08 / THOR SM8475\0THOR FDT MEMDIAG 08\0MEM08 FDT WINDOW ONLY - NO MEMORY GRANT\0RUN MEM07 / THOR SM8475'))


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.kernel=b'diagnostic-test-kernel';self.asset=b'compressed-test-data';self.seed=bytes(range(64))
        self.head=header(self.asset);self.offset=gate.align(19289048)
        primary=struct.pack('<16sIIQ32s64s',b'THOR-PAYLOAD09\0\0',2,128,len(self.kernel),
                            hashlib.sha256(self.kernel).digest(),self.seed)+self.kernel
        raw=primary+bytes(self.offset-len(primary))+self.head+self.asset
        self.bundle=bytearray(raw+bytes(gate.align(len(raw))-len(raw)))
    def validate(self,value):
        return gate.full08_bundle_contract(value,self.kernel,self.asset,self.head,hashlib.sha256(self.seed).hexdigest())
    def test_separate_diagnostic_and_full_payload(self):
        self.assertEqual(self.validate(self.bundle)['full08_header_offset'],self.offset)
    def test_primary_bytes_changed_rejected(self):
        self.bundle[128]^=1
        with self.assertRaises(gate.GateError):self.validate(self.bundle)
    def test_primary_seed_digest_changed_rejected(self):
        self.bundle[64]^=1
        with self.assertRaises(gate.GateError):self.validate(self.bundle)
    def test_nonzero_primary_padding_rejected(self):
        self.bundle[4096]=1
        with self.assertRaises(gate.GateError):self.validate(self.bundle)
    def test_second_full08_magic_rejected(self):
        self.bundle[4096:4112]=gate.FULL_MAGIC
        with self.assertRaises(gate.GateError):self.validate(self.bundle)
    def test_aux_bytes_changed_rejected(self):
        self.bundle[self.offset+128]^=1
        with self.assertRaises(gate.GateError):self.validate(self.bundle)
    def test_nonzero_aux_padding_rejected(self):
        self.bundle[-1]=1
        with self.assertRaises(gate.GateError):self.validate(self.bundle)


class ObserverStrippingTests(unittest.TestCase):
    def test_only_scoped_new_mem08_observers_removed(self):
        old='int original(void) { return 7; }\n'
        added='#if defined(XNU_THOR_EARLY_C) && XNU_THOR_EARLY_C\nthor_mem08_log_begin();\n#endif\n'
        self.assertEqual(gate.strip_mem08_observers(old+added),old)
    def test_existing_mem07_block_is_preserved(self):
        old='#if defined(XNU_THOR_EARLY_C) && XNU_THOR_EARLY_C\nthor_mem07_original();\n#endif\n'
        self.assertEqual(gate.strip_mem08_observers(old),old)


if __name__ == '__main__':
    unittest.main()
