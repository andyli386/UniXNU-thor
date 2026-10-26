import hashlib
import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'tools/thorctl/verify_fullseq_candidate.py'
spec = importlib.util.spec_from_file_location('fullseq_gate', SOURCE)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
BASE = 0xfffffff007004000


def macho_fixture():
    raw = bytearray(0x10000)
    struct.pack_into('<8I', raw, 0, 0xfeedfacf, 0x100000c, 0, 2, 3, 72 + 288 + 24, 1, 0)
    struct.pack_into('<II16s4Q4I', raw, 32, 0x19, 72, b'__TEXT',
                     BASE, 0x10000, 0, 0x10000, 7, 5, 0, 0)
    thread = 32 + 72
    struct.pack_into('<4I', raw, thread, 5, 288, 6, 68)
    struct.pack_into('<Q', raw, thread + 272, BASE + 0x1000)
    table = thread + 288
    strings = b'\0' + b'\0'.join(name.encode() for name in gate.SYMBOL_NAMES) + b'\0'
    struct.pack_into('<6I', raw, table, 2, 24, 0xf000, 3, 0xf100, len(strings))
    raw[0xf100:0xf100 + len(strings)] = strings
    at = 1
    for index, (name, offset) in enumerate(zip(gate.SYMBOL_NAMES, (0x2000, 0x4000, 0x8000))):
        struct.pack_into('<IBBHQ', raw, 0xf000 + index * 16, at, 0xf, 1, 0, BASE + offset)
        at += len(name) + 1
    struct.pack_into('<I', raw, 0x1000, 0xd65f03c0)
    return raw


def packet_fixture():
    compressed = struct.pack('<II', 1, 65536) + b'X'
    inner = gate.INNER.pack(gate.INNER_MAGIC, 1, 128, len(compressed),
                            hashlib.sha256(compressed).digest(), 65536, 65536,
                            bytes([7]) * 32, 1, 1, 0) + compressed
    outer = gate.OUTER.pack(gate.OUTER_MAGIC, 3, 128, len(inner),
                            hashlib.sha256(inner).digest(), bytes([8]) * 64) + inner
    return bytearray(outer + bytes(-len(outer) % 4096))


def boot_fixture():
    kernel, bundle = b'selected gzip firmware and dtb', bytes(packet_fixture())
    header = bytearray(4096)
    header[:8] = b'ANDROID!'
    struct.pack_into('<4I', header, 8, len(kernel), len(bundle), 0x18000189, 1584)
    struct.pack_into('<I', header, 40, 4)
    body = header + kernel + bytes(-len(kernel) % 4096) + bundle + bytes(-len(bundle) % 4096)
    return body, kernel, bundle


def code_fixture():
    raw = macho_fixture()
    old_thread, old_table = bytes(raw[104:392]), bytes(raw[392:416])
    struct.pack_into('<II', raw, 16, 4, 152 + 288 + 24 + 24)
    struct.pack_into('<I', raw, 36, 152)
    struct.pack_into('<I', raw, 96, 1)
    struct.pack_into('<16s16sQQ8I', raw, 104, b'__text', b'__TEXT', BASE, 0x10000,
                     0, 2, 0, 0, 0x80000400, 0, 0, 0)
    raw[184:472], raw[472:496] = old_thread, old_table
    struct.pack_into('<II', raw, 496, 0x1b, 24)
    raw[504:520] = bytes(range(16))
    struct.pack_into('<I', raw, 0x2000, 0x94000000 | ((0x4000 - 0x2000) // 4))
    struct.pack_into('<I', raw, 0x4000, 0x14000000 | (((0x2000 - 0x4000) // 4) & 0x3ffffff))
    return raw


def adt_fixture():
    def prop(name, value):
        return name.encode().ljust(32, b'\0') + struct.pack('<I', len(value)) + value + bytes(-len(value) % 4)
    return (struct.pack('<II', 1, 1) + prop('name', b'device-tree\0')
            + struct.pack('<II', 2, 0) + prop('name', b'chosen\0') + prop('boot-uuid', b'actual-os-uuid\0'))


class FullseqGate(unittest.TestCase):
    def test_independent_adt_node_paths_and_value_offsets(self):
        data = adt_fixture()
        nodes, offsets = gate.parse_adt(data)
        self.assertEqual(set(nodes), {'/', '/chosen'})
        self.assertEqual(nodes['/chosen']['boot-uuid'], b'actual-os-uuid\0')
        offset, count = offsets['/chosen']['boot-uuid']
        self.assertEqual(data[offset:offset + count], b'actual-os-uuid\0')

    def test_independent_adt_rejects_unbounded_value(self):
        raw = bytearray(adt_fixture())
        struct.pack_into('<I', raw, 40, 0xffffffff)
        with self.assertRaisesRegex(gate.GateError, 'bounded ADT'):
            gate.parse_adt(raw)

    def test_independent_adt_rejects_trailing_hidden_nodes(self):
        with self.assertRaisesRegex(gate.GateError, 'exact canonical'):
            gate.parse_adt(adt_fixture() + bytes(8))

    def test_native_coff_actual_symbol_and_section(self):
        raw = bytearray(82)
        struct.pack_into('<HHIIIHH', raw, 0, 0xaa64, 1, 0, 60, 1, 0, 0)
        struct.pack_into('<I', raw, 20 + 16, 256)
        struct.pack_into('<8sIhHBB', raw, 60, b'mSeq', 32, 1, 0, 3, 0)
        struct.pack_into('<I', raw, 78, 4)
        self.assertEqual(gate.coff_symbols(raw), ([('mSeq', 32, 1)], 1))

    def test_native_coff_rejects_symbol_outside_actual_section(self):
        raw = bytearray(82)
        struct.pack_into('<HHIIIHH', raw, 0, 0xaa64, 1, 0, 60, 1, 0, 0)
        struct.pack_into('<8sIhHBB', raw, 60, b'mSeq', 32, 2, 0, 3, 0)
        struct.pack_into('<I', raw, 78, 4)
        with self.assertRaisesRegex(gate.GateError, 'section bounds'):
            gate.coff_symbols(raw)

    def test_real_code_symbol_extent_and_positive_bl_target(self):
        view = gate.CodeView(code_fixture())
        start, body = view.body('LowExceptionVectorBase')
        self.assertEqual((start, len(body)), (BASE + 0x2000, 0x2000))
        self.assertEqual(view.calls('LowExceptionVectorBase')[0]['target'], hex(BASE + 0x4000))
        self.assertEqual(view.calls('LowExceptionVectorBase')[0]['kind'], 'BL')

    def test_real_code_negative_branch_sign_extension(self):
        calls = gate.CodeView(code_fixture()).calls('ExceptionVectorsBase')
        self.assertEqual(calls[0]['target'], hex(BASE + 0x2000))
        self.assertEqual(calls[0]['kind'], 'B')

    def test_real_uuid_only_payload_normalization(self):
        first = code_fixture()
        second = bytearray(first)
        second[504:520] = bytes([73]) * 16
        self.assertNotEqual(first, second)
        self.assertEqual(gate.CodeView(first).normalized_uuid(), gate.CodeView(second).normalized_uuid())

    def test_real_uuid_normalization_does_not_hide_code_difference(self):
        first = code_fixture()
        second = bytearray(first)
        second[0x2000] ^= 1
        self.assertNotEqual(gate.CodeView(first).normalized_uuid(), gate.CodeView(second).normalized_uuid())

    def test_fake_uuid_exclusion_size_is_rejected(self):
        raw = code_fixture()
        struct.pack_into('<I', raw, 500, 16)
        with self.assertRaises(gate.GateError):
            gate.CodeView(raw)

    def test_stack_report_rejects_dynamic_unknown_frame(self):
        with self.assertRaisesRegex(gate.GateError, 'static native'):
            gate.parse_stack_report(b'path.c:10:frame\t64\tdynamic\n')

    def test_stack_report_rejects_duplicate_frame(self):
        with self.assertRaisesRegex(gate.GateError, 'unique'):
            gate.parse_stack_report(b'a.c:1:f\t64\tstatic\na.c:2:f\t32\tstatic\n')

    def test_native_archive_rejects_missing_or_duplicate_members(self):
        def member(name, data):
            header = (name + '/').ljust(16) + '0'.ljust(12) + '0'.ljust(6) + '0'.ljust(6) + '100644'.ljust(8) + str(len(data)).ljust(10) + '`\n'
            return header.encode() + data + (b'\n' if len(data) % 2 else b'')
        raw = b'!<arch>\n' + member('one.obj', b'actualbytes')
        self.assertEqual(gate.archive_member(raw, 'one.obj'), b'actualbytes')
        with self.assertRaises(gate.GateError):
            gate.archive_member(raw, 'absent.obj')
        with self.assertRaises(gate.GateError):
            gate.archive_member(raw + member('one.obj', b'other'), 'one.obj')

    def test_actual_pc_field_and_three_distinct_symbol_offsets(self):
        result = gate.parse_macho(macho_fixture())
        self.assertEqual(result['entry_offset'], 0x1000)
        self.assertEqual(result['low_vector_offset'], 0x2000)
        self.assertEqual(result['vector_offset'], 0x2000)
        self.assertEqual(result['runtime_vector_offset'], 0x4000)
        self.assertEqual(result['boot_pt_offset'], 0x8000)

    def test_sp_is_not_misread_as_thread_pc(self):
        raw = macho_fixture()
        struct.pack_into('<Q', raw, 32 + 72 + 264, BASE + 0x9000)
        self.assertEqual(gate.parse_macho(raw)['entry_offset'], 0x1000)

    def test_loaded_hash_matches_independent_dense_fixture(self):
        raw = macho_fixture()
        self.assertEqual(gate.parse_macho(raw)['loaded_span_sha256'], hashlib.sha256(raw).hexdigest())

    def test_wrong_unixthread_flavor_rejected(self):
        raw = macho_fixture()
        struct.pack_into('<I', raw, 32 + 72 + 8, 1)
        with self.assertRaisesRegex(gate.GateError, 'thread_state64'):
            gate.parse_macho(raw)

    def test_thread_pc_outside_executable_file_rejected(self):
        raw = macho_fixture()
        struct.pack_into('<Q', raw, 32 + 72 + 272, BASE + 0x20000)
        with self.assertRaisesRegex(gate.GateError, 'thread PC'):
            gate.parse_macho(raw)

    def test_unaligned_pc_rejected(self):
        raw = macho_fixture()
        struct.pack_into('<Q', raw, 32 + 72 + 272, BASE + 1)
        with self.assertRaisesRegex(gate.GateError, 'thread PC'):
            gate.parse_macho(raw)

    def test_nonexecuting_entry_segment_rejected(self):
        raw = macho_fixture()
        struct.pack_into('<I', raw, 32 + 60, 1)
        with self.assertRaisesRegex(gate.GateError, 'thread PC'):
            gate.parse_macho(raw)

    def test_low_runtime_vector_alias_rejected(self):
        raw = macho_fixture()
        struct.pack_into('<Q', raw, 0xf000 + 16 + 8, BASE + 0x2000)
        with self.assertRaisesRegex(gate.GateError, 'distinct aligned'):
            gate.parse_macho(raw)

    def test_bootstrap_unaligned_rejected(self):
        raw = macho_fixture()
        struct.pack_into('<Q', raw, 0xf000 + 32 + 8, BASE + 0x8001)
        with self.assertRaisesRegex(gate.GateError, 'page aligned'):
            gate.parse_macho(raw)

    def test_bootstrap_missing_symbol_rejected(self):
        raw = macho_fixture()
        raw[0xf000 + 32 + 4] = 1
        with self.assertRaisesRegex(gate.GateError, 'selected symbol'):
            gate.parse_macho(raw)

    def test_symbol_string_outside_table_rejected(self):
        raw = macho_fixture()
        struct.pack_into('<I', raw, 0xf000, 10000)
        with self.assertRaisesRegex(gate.GateError, 'string table'):
            gate.parse_macho(raw)

    def test_segment_vm_cannot_be_smaller_than_file(self):
        raw = macho_fixture()
        struct.pack_into('<Q', raw, 32 + 32, 4096)
        with self.assertRaisesRegex(gate.GateError, 'file-backed segment'):
            gate.parse_macho(raw)

    def test_declared_command_extent_rejected(self):
        raw = macho_fixture()
        struct.pack_into('<I', raw, 20, 0x100000)
        with self.assertRaisesRegex(gate.GateError, 'command count'):
            gate.parse_macho(raw)

    def test_arm64e_subtype_rejected(self):
        raw = macho_fixture()
        struct.pack_into('<I', raw, 8, 2)
        with self.assertRaisesRegex(gate.GateError, 'ordinary arm64'):
            gate.parse_macho(raw)

    def test_no_undefined_flag_required(self):
        raw = macho_fixture()
        struct.pack_into('<I', raw, 24, 0)
        with self.assertRaisesRegex(gate.GateError, 'undefined'):
            gate.parse_macho(raw)

    def test_v3_packet_reports_seed_digest_only(self):
        result = gate.parse_packet(packet_fixture())
        self.assertEqual(result['packet_version'], 3)
        self.assertEqual(result['seed_bytes'], 64)
        self.assertNotIn('seed', result)
        self.assertEqual(result['seed_sha256'], hashlib.sha256(bytes([8]) * 64).hexdigest())

    def test_old_v2_transport_rejected(self):
        packet = packet_fixture()
        struct.pack_into('<I', packet, 16, 2)
        with self.assertRaisesRegex(gate.GateError, 'v3 outer identity'):
            gate.parse_packet(packet)

    def test_old_magic_rejected(self):
        packet = packet_fixture()
        packet[:16] = b'THOR-MEM07' + bytes(6)
        with self.assertRaisesRegex(gate.GateError, 'v3 outer identity'):
            gate.parse_packet(packet)

    def test_packet_extra_zero_page_rejected(self):
        packet = packet_fixture() + bytes(4096)
        with self.assertRaisesRegex(gate.GateError, 'page-rounded'):
            gate.parse_packet(packet)

    def test_nonzero_tail_rejected(self):
        packet = packet_fixture()
        packet[-1] = 1
        with self.assertRaisesRegex(gate.GateError, 'zero tail'):
            gate.parse_packet(packet)

    def test_zero_seed_rejected(self):
        packet = packet_fixture()
        packet[64:128] = bytes(64)
        with self.assertRaisesRegex(gate.GateError, 'one-use'):
            gate.parse_packet(packet)

    def test_mutated_outer_payload_rejected(self):
        packet = packet_fixture()
        packet[256] ^= 1
        with self.assertRaisesRegex(gate.GateError, 'outer body SHA'):
            gate.parse_packet(packet)

    def test_inner_tiano_id_rejected(self):
        packet = packet_fixture()
        payload = bytearray(packet[128:265])
        struct.pack_into('<I', payload, 112, 2)
        with self.assertRaisesRegex(gate.GateError, 'EFI standard'):
            gate.parse_inner(payload)

    def test_inner_compressed_digest_rejected(self):
        packet = packet_fixture()
        payload = bytearray(packet[128:265])
        payload[-1] ^= 1
        with self.assertRaisesRegex(gate.GateError, 'compressed body SHA'):
            gate.parse_inner(payload)

    def test_v4_actual_sections_and_size(self):
        image, kernel, bundle = boot_fixture()
        result = gate.parse_boot_v4(image, kernel, bundle)
        self.assertEqual(result['header_version'], 4)
        self.assertTrue(result['strict_padding'])

    def test_v4_rejects_unexpected_signature_or_header_padding(self):
        for offset in (1580, 1600):
            image, kernel, bundle = boot_fixture()
            image[offset] = 1
            with self.subTest(offset=offset), self.assertRaisesRegex(gate.GateError, 'boot signature'):
                gate.parse_boot_v4(image, kernel, bundle)

    def test_v4_rejects_nonzero_kernel_padding(self):
        image, kernel, bundle = boot_fixture()
        image[4096 + len(kernel)] = 1
        with self.assertRaisesRegex(gate.GateError, 'zero kernel padding'):
            gate.parse_boot_v4(image, kernel, bundle)

    def test_v4_rejects_extra_pages(self):
        image, kernel, bundle = boot_fixture()
        with self.assertRaisesRegex(gate.GateError, 'extra pages'):
            gate.parse_boot_v4(image + bytes(4096), kernel, bundle)

    def test_v4_rejects_cmdline_or_reserved_fields(self):
        for offset in (24, 44):
            image, kernel, bundle = boot_fixture()
            image[offset] = 1
            with self.subTest(offset=offset), self.assertRaisesRegex(gate.GateError, 'wrapper Android'):
                gate.parse_boot_v4(image, kernel, bundle)

    def test_frozen_input_mutation_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'input'
            path.write_bytes(b'first')
            checks = gate.Checks(temporary)
            checks.freeze(path)
            path.write_bytes(b'second')
            with self.assertRaisesRegex(gate.GateError, 'one immutable identity'):
                checks.freeze(path)

    def test_frozen_input_cannot_escape_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(gate.GateError, 'project root'):
                gate.Checks(temporary).freeze('../outside')


if __name__ == '__main__':
    unittest.main()
