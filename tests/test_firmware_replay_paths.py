"""Exercise real public-index relocation with canonical paths and compiler aliases."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/thorctl'))
import firmware_audit_replay as replay

class PublicPaths(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / 'source'
        self.source.mkdir()
        self.original = Path('/origin/project/worktrees/uefi/ios-full-seq01')
        self.mapper = replay.PublicPathMap(self.source, self.original, Path('/origin/project'))
        self.mapper.candidate_stage = 'fullseq01'
        rows = []
        for i in range(300):
            relative = 'Library/PEI/../../Include/header.h' if i == 0 else f'input-{i}.c'
            path = (self.source / relative).resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            data = str(i).encode()
            path.write_bytes(data)
            rows.append(dict(archive='fullseq01-uefi-main',
                relative_path='uefi-root/' + relative,
                origin_path=str(self.original / relative),
                bytes=len(data), sha256=hashlib.sha256(data).hexdigest()))
        self.document = dict(schema='thor-fullseq01-public-source-firmware-transfer-v1',
            contains_seed=False, contains_boot_image=False, phone_actions=0,
            actual_efi_count=20, actual_link_input_libraries=97,
            source_commit='frozen-head', audited_input_files=rows)

    def verify(self):
        path = self.root / 'index.json'
        path.write_text(json.dumps(self.document))
        with patch('subprocess.check_output', return_value='frozen-head\n'):
            return replay.verify_public_index(path, self.root, self.mapper)

    def test_compiler_alias_resolves_to_real_source(self):
        self.assertEqual(self.verify()['indexed_files_checked'], 300)

    def test_canonical_source_path(self):
        row = self.document['audited_input_files'][0]
        row.update(relative_path='uefi-root/Include/header.h',
                   origin_path=str(self.original / 'Include/header.h'))
        self.assertEqual(self.verify()['indexed_files_checked'], 300)

    def test_unbounded_and_mismatched_paths_are_rejected(self):
        row = self.document['audited_input_files'][0]
        saved = dict(row)
        cases = [
            (dict(relative_path='uefi-root/../outside'), 'resolved public input'),
            (dict(relative_path='/absolute'), 'explicit archive-relative'),
            (dict(relative_path=''), 'explicit archive-relative'),
            (dict(origin_path='/origin/escape/header.h'), 'bounded original'),
            (dict(origin_path=str(self.original / '../outside/header.h')), 'bounded original'),
            (dict(origin_path=str(self.original / 'Include/different.h')), 'agree exactly'),
        ]
        for change, reason in cases:
            with self.subTest(change=change):
                row.clear(); row.update(saved); row.update(change)
                with self.assertRaisesRegex(replay.ReplayError, reason):
                    self.verify()

    def test_symlink_escape_is_rejected(self):
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'header.h').write_bytes(b'0')
        (self.source / 'link').symlink_to(outside, target_is_directory=True)
        self.document['audited_input_files'][0].update(
            relative_path='uefi-root/link/header.h',
            origin_path=str(self.original / 'link/header.h'))
        with self.assertRaisesRegex(replay.ReplayError, 'resolved public input'):
            self.verify()

if __name__ == '__main__':
    unittest.main()
