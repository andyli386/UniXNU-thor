#!/usr/bin/env python3
"""Tamper and provenance regressions for FULLSEQ02 public source closure."""
import copy
import hashlib
import importlib.util
import json
import os
import pathlib
import sys
import unittest

HERE = pathlib.Path(__file__).resolve()
THORCTL = HERE.parent.parent / "tools/thorctl"
sys.path.insert(0, str(THORCTL))
from verify_fullseq02 import Gate, validate_source_index
ROOT = HERE.parents[4]
L2 = ROOT / "runs/thor-20261027-ios-full-seq02/l2"

class SourceInventoryNegatives(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.good = json.loads((L2 / "public-transfer/PUBLIC_TRANSFER_INDEX.json").read_text())
        cls.audit = json.loads((L2 / "firmware-audit-repro/thor_fd_dc0fb92a528b-audit.json").read_text())
        cls.cpp = json.loads((L2 / "native-cpp-inputs.json").read_text())
    def invoke(self, index):
        gate = Gate(ROOT)
        gate.head = "57819387818c891de778ead7331626e6ada9c9f7"
        return validate_source_index(gate,index,self.audit,self.cpp)
    def test_exact_original_index_passes(self):
        self.assertEqual(self.invoke(self.good),1523)
    def check_rejected(self, updater):
        changed=copy.deepcopy(self.good)
        updater(changed)
        with self.assertRaises((AssertionError,ValueError,OSError)):
            self.invoke(changed)
    def test_reject_wrong_run_schema(self):
        self.check_rejected(lambda d:d.__setitem__("schema","thor-fullseq01-public-source-firmware-transfer-v1"))
    def test_reject_wrong_commit(self):
        self.check_rejected(lambda d:d.__setitem__("source_commit","0"*40))
    def test_reject_embedded_seed_claim(self):
        self.check_rejected(lambda d:d.__setitem__("contains_seed",True))
    def test_reject_missing_compiler_input(self):
        self.check_rejected(lambda d:d["audited_input_files"].pop())
    def test_reject_fake_fd_digest(self):
        self.check_rejected(lambda d:d["audited_input_files"][0].__setitem__("sha256","0"*64))
    def test_reject_traversal_outside_workspace(self):
        self.check_rejected(lambda d:d["audited_input_files"][0].__setitem__(
            "relative_path","uefi-root/../../private/secret"))
    def test_reject_unexpected_canonical_member(self):
        self.check_rejected(lambda d:d["audited_input_files"][0].__setitem__(
            "archive_member","uefi-root/kernel-bundle.bin"))
    def test_reject_duplicate_alias(self):
        self.check_rejected(lambda d:d["audited_input_files"].__setitem__(
            1,copy.deepcopy(d["audited_input_files"][0])))
    def test_reject_unverified_uefi_archive_size(self):
        self.check_rejected(lambda d:d.__setitem__("public_source_archive_bytes",1))

if __name__ == "__main__":
    unittest.main(verbosity=2)
