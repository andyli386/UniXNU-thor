"""Malformed full-only wire inputs; no phone, compressor or entropy calls."""
from pathlib import Path
import hashlib
import importlib.util
import struct
import sys
import unittest

TOOLS = Path(__file__).resolve().parents[1] / 'tools/thorctl'
sys.path.insert(0, str(TOOLS))
import fullseq_asset as asset


class WireTests(unittest.TestCase):
    def setUp(self):
        self.body = struct.pack('<II', 32, 4096) + bytes(range(32))
        self.fields = [asset.MAGIC, 1, 128, len(self.body), hashlib.sha256(self.body).digest(),
                       4096, 8192, bytes.fromhex('b' * 64), 1, 1, 0]
        self.payload = asset.HEADER.pack(*self.fields) + self.body
        self.seed = b'\x23' * 64
        self.bundle = asset.encode_v3(self.payload, self.seed)

    def inner(self, index, value):
        fields = list(self.fields); fields[index] = value
        return asset.HEADER.pack(*fields) + self.body

    def test_exact_full_only_packet(self):
        parsed = asset.decode_v3(self.bundle)
        self.assertEqual(parsed['raw_sha256'], 'b' * 64)
        self.assertEqual(len(self.bundle), 4096)
        self.assertEqual(self.bundle[64:128], self.seed)
        self.assertEqual(self.bundle[128:128 + len(self.payload)], self.payload)

    def test_outer_header_rejects_old_diagnostic_role(self):
        data = bytearray(self.bundle); data[:16] = b'THOR-PAYLOAD09\0\0\0'
        with self.assertRaises(RuntimeError): asset.decode_v3(data)

    def test_bad_outer_digest(self):
        data = bytearray(self.bundle); data[32] ^= 1
        with self.assertRaisesRegex(RuntimeError, 'outer body SHA'): asset.decode_v3(data)

    def test_zero_seed(self):
        data = bytearray(self.bundle); data[64:128] = bytes(64)
        with self.assertRaises(RuntimeError): asset.decode_v3(data)
        with self.assertRaises(RuntimeError): asset.encode_v3(self.payload, bytes(64))

    def test_padding_cannot_hide_trailing_frame(self):
        data = bytearray(self.bundle); data[-1] = 1
        with self.assertRaisesRegex(RuntimeError, 'padding'): asset.decode_v3(data)
        with self.assertRaisesRegex(RuntimeError, 'padding'): asset.decode_v3(self.bundle + bytes(4096))

    def test_payload_length_is_not_a_pointer_permission(self):
        data = bytearray(self.bundle); struct.pack_into('<Q', data, 24, 1 << 63)
        with self.assertRaises(RuntimeError): asset.decode_v3(data)

    def test_inner_version_algorithm_and_reserved(self):
        for index, value in [(1, 3), (2, 64), (8, 2), (10, 1)]:
            with self.subTest(index=index), self.assertRaises(RuntimeError):
                asset.read_inner(self.inner(index, value))

    def test_codec_file_and_span_bounds(self):
        for index, value in [(3, len(self.body) - 1), (5, 0), (5, 1 << 32), (6, 2048),
                             (6, 117440513), (9, 0), (9, 33)]:
            with self.subTest(index=index), self.assertRaises(RuntimeError):
                asset.read_inner(self.inner(index, value))

    def test_inner_compressed_sha(self):
        with self.assertRaisesRegex(RuntimeError, 'body SHA'):
            asset.read_inner(self.inner(4, bytes(32)))

    def test_standard_codec_size_words(self):
        wrong = bytearray(self.body); struct.pack_into('<I', wrong, 4, 4095)
        fields = list(self.fields); fields[4] = hashlib.sha256(wrong).digest()
        with self.assertRaisesRegex(RuntimeError, 'compression header'):
            asset.read_inner(asset.HEADER.pack(*fields) + wrong)

    def test_outer_version_and_header_size(self):
        for at, value in [(16, 2), (20, 64)]:
            data = bytearray(self.bundle); struct.pack_into('<I', data, at, value)
            with self.subTest(at=at), self.assertRaises(RuntimeError): asset.decode_v3(data)

    def test_truncation(self):
        for length in [0, 16, 127, 128, 263, 4095]:
            with self.subTest(length=length), self.assertRaises(RuntimeError): asset.decode_v3(self.bundle[:length])


if __name__ == '__main__':
    unittest.main()
