import hashlib
import importlib.util
import io
import json
from pathlib import Path
import plistlib
import struct
import tempfile
import unittest
import zipfile
import zlib

SOURCE = Path(__file__).resolve().parents[1] / 'tools/thorctl/fullseq_system_assets.py'
spec = importlib.util.spec_from_file_location('fullseq_system_assets', SOURCE)
assets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assets)


def identity():
    return {'ProductVersion': '16.6.1', 'ProductBuildVersion': '20G81',
            'SupportedProductTypes': ['iPad7,1'], 'BuildIdentities': [{
                'Info': {'DeviceClass': 'j120ap', 'RestoreBehavior': 'Erase'},
                'ApChipID': '0x8011', 'ApBoardID': '0x0c',
                'Manifest': {'OS': {'Info': {'Path': 'os.dmg'}}}}]}


class FullseqAssets(unittest.TestCase):
    def test_exact_identity(self):
        self.assertEqual(assets.select_identity(identity())[0], 0)

    def test_wrong_build_rejected(self):
        doc = identity()
        doc['ProductBuildVersion'] = '20G80'
        with self.assertRaisesRegex(assets.AssetError, 'version/build'):
            assets.select_identity(doc)

    def test_wrong_product_rejected(self):
        doc = identity()
        doc['SupportedProductTypes'] = ['iPhone10,3']
        with self.assertRaisesRegex(assets.AssetError, 'product'):
            assets.select_identity(doc)

    def test_duplicate_identity_rejected(self):
        doc = identity()
        doc['BuildIdentities'] *= 2
        with self.assertRaisesRegex(assets.AssetError, 'one exact'):
            assets.select_identity(doc)

    def test_wrong_board_rejected(self):
        doc = identity()
        doc['BuildIdentities'][0]['ApBoardID'] = '0x0d'
        with self.assertRaises(assets.AssetError):
            assets.select_identity(doc)

    def test_update_identity_rejected(self):
        doc = identity()
        doc['BuildIdentities'][0]['Info']['RestoreBehavior'] = 'Update'
        with self.assertRaises(assets.AssetError):
            assets.select_identity(doc)

    def test_range_must_match_exact_request(self):
        assets.check_range(206, 'bytes 3-9/10', 7, 3, 9, 10)
        for status, header, count in [(200, 'bytes 3-9/10', 7),
                                      (206, 'bytes 0-6/10', 7),
                                      (206, 'bytes 3-9/11', 7),
                                      (206, 'bytes 3-9/10', 6)]:
            with self.subTest(status=status, header=header, count=count):
                with self.assertRaises(assets.AssetError):
                    assets.check_range(status, header, count, 3, 9, 10)

    def test_unsafe_member_paths_rejected(self):
        self.assertEqual(assets.safe_basename('Firmware/test.dmg'), 'test.dmg')
        for path in ('../os.dmg', '/os.dmg', 'Firmware/../../os', 'a\\b'):
            with self.subTest(path=path), self.assertRaises(assets.AssetError):
                assets.safe_basename(path)

    def make_archive(self, names=('os.dmg',), method=zipfile.ZIP_STORED):
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w', compression=method) as archive:
            for name in names:
                archive.writestr(name, b'checked original input' * 17)
        data.seek(0)
        return zipfile.ZipFile(data)

    def test_inventory_binds_real_zip_member(self):
        archive = self.make_archive()
        report = assets.inventory(archive, plistlib.dumps(identity()), ('OS',))
        row = report['components'][0]
        self.assertEqual(row['bytes'], len(b'checked original input' * 17))
        self.assertEqual(row['method'], 0)
        self.assertFalse(report['whole_ipsw_downloaded'])
        self.assertEqual(report['phone_actions'], 0)

    def test_missing_component_rejected(self):
        with self.assertRaisesRegex(assets.AssetError, 'absent'):
            assets.inventory(self.make_archive(), plistlib.dumps(identity()), ('Data',))

    def test_destination_collision_rejected(self):
        doc = identity()
        doc['BuildIdentities'][0]['Manifest']['Second'] = {'Info': {'Path': 'second/os.dmg'}}
        with self.assertRaisesRegex(assets.AssetError, 'collision'):
            assets.inventory(self.make_archive(('os.dmg', 'second/os.dmg')),
                             plistlib.dumps(doc), ('OS', 'Second'))

    def test_local_central_header_disagreement_rejected(self):
        archive = self.make_archive()
        data = bytearray(archive.fp.getvalue())
        data[8:10] = b'\x08\x00'
        changed = zipfile.ZipFile(io.BytesIO(data))
        with self.assertRaisesRegex(assets.AssetError, 'disagreement'):
            assets.inventory(changed, plistlib.dumps(identity()), ('OS',))

    def extract_fixture(self, method, mutate=None):
        source = (b'APFS host input\0' * 1000) + bytes(range(256))
        compressor = zlib.compressobj(wbits=-15)
        packed = compressor.compress(source) + compressor.flush() if method == 8 else source
        row = dict(bytes=len(source), method=method,
                   zip_crc32=f'{zlib.crc32(source):08x}')
        if mutate:
            mutate(row)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'packed').write_bytes(packed)
            measured = assets.extract_member(row, root / 'packed', root / 'output')
            self.assertEqual((root / 'output').read_bytes(), source)
            self.assertEqual(measured['sha256'], hashlib.sha256(source).hexdigest())
            self.assertTrue(measured['zip_crc_checked'])
        return row

    def test_real_stored_member_extract(self):
        self.extract_fixture(0)

    def test_real_deflate_member_extract(self):
        self.extract_fixture(8)

    def test_crc_failure_does_not_publish(self):
        with self.assertRaisesRegex(assets.AssetError, 'size/CRC'):
            self.extract_fixture(8, lambda row: row.update(zip_crc32='00000000'))

    def test_declared_size_bounds_decode(self):
        with self.assertRaisesRegex(assets.AssetError, 'declared size'):
            self.extract_fixture(8, lambda row: row.update(bytes=1))

    def test_trailing_deflate_bytes_rejected(self):
        source = b'file'
        compressor = zlib.compressobj(wbits=-15)
        packed = compressor.compress(source) + compressor.flush() + b'junk'
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'packed').write_bytes(packed)
            with self.assertRaisesRegex(assets.AssetError, 'trailing'):
                assets.extract_member(dict(bytes=4, method=8, zip_crc32=f'{zlib.crc32(source):08x}'),
                                      root / 'packed', root / 'output')
            self.assertFalse((root / 'output').exists())

    def test_der_payload_preserves_exact_original_bytes(self):
        fields = b'\x16\x04IM4P\x16\x04trst\x16\x011\x04\x04file'
        self.assertEqual(assets.im4p_payload(b'\x30' + bytes([len(fields)]) + fields, b'trst'), b'file')

    def test_der_type_mismatch_rejected(self):
        fields = b'\x16\x04IM4P\x16\x04trst\x16\x011\x04\x04file'
        with self.assertRaisesRegex(assets.AssetError, 'fields/type'):
            assets.im4p_payload(b'\x30' + bytes([len(fields)]) + fields, b'trcs')

    def test_indefinite_der_length_rejected(self):
        with self.assertRaisesRegex(assets.AssetError, 'length'):
            assets.der_item(b'\x30\x80\x00\x00', 0)

    def test_der_field_cannot_exceed_parent(self):
        with self.assertRaisesRegex(assets.AssetError, 'enclosing'):
            assets.der_item(b'\x04\x10abc', 0)

    def test_v2_trustcache_hashes_checked(self):
        raw = struct.pack('<I16sI', 2, bytes(16), 2)
        raw += bytes([1]) * 20 + bytes([2, 0, 0, 0])
        raw += bytes([2]) * 20 + bytes([2, 0, 0, 0])
        self.assertEqual(assets.validate_trustcache(raw)['entries'], 2)

    def test_v1_trustcache_rejected_for_v2_loader(self):
        with self.assertRaisesRegex(assets.AssetError, 'v2'):
            assets.validate_trustcache(struct.pack('<I16sI', 1, bytes(16), 0))

    def test_trustcache_unsorted_or_duplicate_hash_rejected(self):
        raw = struct.pack('<I16sI', 2, bytes(16), 2)
        raw += bytes([2]) * 20 + bytes([2, 0, 0, 0])
        raw += bytes([1]) * 20 + bytes([2, 0, 0, 0])
        with self.assertRaisesRegex(assets.AssetError, 'type/order'):
            assets.validate_trustcache(raw)

    def test_trustcache_count_rejected(self):
        with self.assertRaisesRegex(assets.AssetError, 'size/count'):
            assets.validate_trustcache(struct.pack('<I16sI', 2, bytes(16), 1))

    def test_attachment_requires_exact_host_disk(self):
        with self.assertRaisesRegex(assets.AssetError, 'host image disk'):
            assets.image_devices({'system-entities': [{'dev-entry': '/dev/disk6s1'}]})
        self.assertEqual(assets.image_devices({'system-entities': [{'dev-entry': '/dev/disk6'}]}), '/dev/disk6')

    def test_coalesced_ranges_preserve_all_missing_chunks(self):
        pending = list(range(20))
        groups = assets.group_missing_ranges(pending)
        self.assertEqual([item for group in groups for item in group], pending)
        self.assertEqual([len(group) for group in groups], [8, 8, 4])

    def test_coalesced_ranges_do_not_refetch_completed_holes(self):
        self.assertEqual(assets.group_missing_ranges([1, 2, 5, 6, 8]), [[1, 2], [5, 6], [8]])


if __name__ == '__main__':
    unittest.main()
