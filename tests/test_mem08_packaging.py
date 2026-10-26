"""Malformed-input and byte-layout regression tests for the MEM08 public asset."""
import hashlib
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/thorctl'))
import full_loader_asset as asset


def wire(**changes):
    fields = dict(magic=asset.MAGIC, version=1, header=128, compressed=16,
                  digest=b'c'*32, raw=asset.RAW_BYTES, span=asset.SPAN_BYTES,
                  rawsha=bytes.fromhex(asset.RAW_SHA256), algorithm=1, count=15, reserved=0)
    fields.update(changes)
    return asset.HEADER.pack(*fields.values())


BASE = 0xfffffff007004000


def image(records=None):
    records = records or [('__TEXT', BASE, 0x2000, 0, 0x2000),
                          ('__DATA', BASE+0x6000, 0x3000, 0x2000, 0x1000),
                          ('__ZERO', 0, 0, 0, 0)]
    raw = bytearray(b'\x5a' * 0x6000)
    struct.pack_into('<8I', raw, 0, 0xfeedfacf, 0x100000c, 0, 2, len(records), 72*len(records), 0, 0)
    for i, (name, va, vm, offset, size) in enumerate(records):
        struct.pack_into('<II16s4Q4I', raw, 32+72*i, 0x19, 72, name.encode(), va, vm, offset, size, 7, 3, 0, 0)
    return bytes(raw)


class HeaderTests(unittest.TestCase):
    def test_exact_standard_header(self):
        self.assertEqual(asset.parse_header(wire())['nonzero_segment_count'], 15)

    def test_reject_truncated_header(self):
        with self.assertRaises(RuntimeError): asset.parse_header(wire()[:-1])

    def test_reject_oversized_header(self):
        with self.assertRaises(RuntimeError): asset.parse_header(wire()+b'\0')

    def test_reject_tiano_variant(self):
        with self.assertRaises(RuntimeError): asset.parse_header(wire(algorithm=2))

    def test_reject_record_count_confusion(self):
        with self.assertRaises(RuntimeError): asset.parse_header(wire(count=18))

    def test_reject_header_version(self):
        with self.assertRaises(RuntimeError): asset.parse_header(wire(version=2))

    def test_reject_reserved_word(self):
        with self.assertRaises(RuntimeError): asset.parse_header(wire(reserved=1))

    def test_reject_empty_compressed_body(self):
        with self.assertRaises(RuntimeError): asset.parse_header(wire(compressed=0))

    def test_reject_body_above_source_budget(self):
        with self.assertRaises(RuntimeError): asset.parse_header(wire(compressed=asset.MAX_COMPRESSED_BYTES+1))

    def test_reject_different_raw_kernel(self):
        with self.assertRaises(RuntimeError): asset.parse_header(wire(rawsha=b'\0'*32))

    def test_reject_changed_loaded_span(self):
        with self.assertRaises(RuntimeError): asset.parse_header(wire(span=asset.SPAN_BYTES+4096))


class MachoTests(unittest.TestCase):
    def test_zero_fill_and_file_bytes_match_independent_expected_image(self):
        raw = image(); result = asset.macho_layout(raw)
        expected = raw[:0x2000] + bytes(0x4000) + raw[0x2000:0x3000] + bytes(0x2000)
        self.assertEqual(result['loaded_span_sha256'], hashlib.sha256(expected).hexdigest())
        self.assertEqual(result['loaded_span_bytes'], len(expected))

    def test_zero_sized_records_are_counted_separately(self):
        result = asset.macho_layout(image())
        self.assertEqual((result['segment_record_count'], result['nonzero_segment_count']), (3, 2))

    def test_scrambled_load_command_order_is_sorted(self):
        raw = image([('__ZERO',0,0,0,0), ('__DATA',BASE+0x6000,0x3000,0x2000,0x1000), ('__TEXT',BASE,0x2000,0,0x2000)])
        self.assertEqual([r['name'] for r in asset.macho_layout(raw)['sorted_nonzero_segments']], ['__TEXT','__DATA'])

    def test_reject_empty_header(self):
        with self.assertRaises(RuntimeError): asset.macho_layout(b'')

    def test_reject_non_arm64(self):
        raw = bytearray(image());struct.pack_into('<I',raw,4,0x1000007)
        with self.assertRaises(RuntimeError): asset.macho_layout(raw)

    def test_reject_fileset_as_executable(self):
        raw = bytearray(image());struct.pack_into('<I',raw,12,12)
        with self.assertRaises(RuntimeError): asset.macho_layout(raw)

    def test_reject_unbounded_command_count(self):
        raw = bytearray(image());struct.pack_into('<I',raw,16,513)
        with self.assertRaises(RuntimeError): asset.macho_layout(raw)

    def test_reject_load_command_truncation(self):
        raw = bytearray(image());struct.pack_into('<I',raw,20,len(raw))
        with self.assertRaises(RuntimeError): asset.macho_layout(raw)

    def test_reject_section_count_outside_command(self):
        raw = bytearray(image());struct.pack_into('<I',raw,32+64,1)
        with self.assertRaises(RuntimeError): asset.macho_layout(raw)

    def test_reject_file_range_outside_source(self):
        with self.assertRaises(RuntimeError): asset.macho_layout(image([('__TEXT',BASE,0x2000,0x5800,0x2000)]))

    def test_reject_file_bytes_larger_than_vm(self):
        with self.assertRaises(RuntimeError): asset.macho_layout(image([('__TEXT',BASE,0x1000,0,0x2000)]))

    def test_reject_overlapping_vm_segments(self):
        with self.assertRaises(RuntimeError): asset.macho_layout(image([('__TEXT',BASE,0x2000,0,0x2000),('__DATA',BASE+0x1000,0x2000,0x2000,0x1000)]))

    def test_reject_overlapping_file_segments(self):
        with self.assertRaises(RuntimeError): asset.macho_layout(image([('__TEXT',BASE,0x2000,0,0x2000),('__DATA',BASE+0x6000,0x2000,0x1000,0x1000)]))

    def test_reject_backward_inplace_move(self):
        with self.assertRaises(RuntimeError): asset.macho_layout(image([('__TEXT',BASE,0x2000,0,0x2000),('__DATA',BASE+0x3000,0x2000,0x4000,0x1000)]))

    def test_reject_reversed_source_destination_orders(self):
        rows=[('__TEXT',BASE,0x2000,0,0x2000),('__A',BASE+0x9000,0x2000,0x2000,0x1000),('__B',BASE+0x6000,0x1000,0x4000,0x1000)]
        with self.assertRaises(RuntimeError): asset.macho_layout(image(rows))

    def test_reject_va_overflow(self):
        with self.assertRaises(RuntimeError): asset.macho_layout(image([('__TEXT',(1<<64)-4096,0x2000,0,0x2000)]))

    def test_reject_span_above_grant(self):
        with self.assertRaises(RuntimeError): asset.macho_layout(image([('__TEXT',BASE,0x2000,0,0x2000),('__DATA',BASE+asset.WINDOW_BYTES,0x2000,0x2000,0x1000)]))

    def test_reject_no_loaded_segments(self):
        with self.assertRaises(RuntimeError): asset.macho_layout(image([('__ZERO',0,0,0,0)]))


if __name__ == '__main__':
    unittest.main()
