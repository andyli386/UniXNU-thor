#!/usr/bin/env python3
"""Provenance gate tests cannot accept host results as hardware pass."""
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[3]
script=ROOT/'repos/UniXNU-thor/tools/build_thor_ios_gate_matrix.py'
spec=importlib.util.spec_from_file_location('gates',script)
gates=importlib.util.module_from_spec(spec)
spec.loader.exec_module(gates)

FILES=[
 'evidence/build/thor-20261026-ios-full-f1e/f1e-independent-structural-audit.json',
 'runs/ios16-f1e-full-kernel-on-fat56-host-20261026/real-Apple20G81-f1e-envelope-proof.json',
 'runs/ios-f1d-qemu-host-20261026/qemu-visible-menu-proof.json',
 'evidence/build/thor-20261026-ios-full-f1d/source-bootchain-audit.json',
 'reports/thor-darwin22-memory-gate-20261026.json',
 'reports/thor-ddr-ownership-hypotheses-20261026.json',
 'reports/thor-ios16-storage-bound-native-host-20261026.json',
 'runs/ios16-apfs-full-closure-host-20261026/apfs-emission-proof.json',
 'reports/thor-pong-ufs-dt-diff-20261026.json'
]
class TestFullChainGates(unittest.TestCase):
    def setUp(self):
        if not all((ROOT/name).is_file() for name in FILES):
            self.skipTest('Explicit host reports not installed on this machine')
        self.tmp=tempfile.TemporaryDirectory(prefix='thor-f1e-gate-')
        self.repo=Path(self.tmp.name)
        for name in FILES:
            dest=self.repo/name
            dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(ROOT/name,dest)
    def tearDown(self):
        if hasattr(self,'tmp'):self.tmp.cleanup()
    def mutate(self,which,key,value):
        path=self.repo/which
        entry=json.loads(path.read_text());entry[key]=value
        path.write_text(json.dumps(entry))
    def test_real_14_gate_provenance(self):
        r=gates.make(self.repo)
        self.assertEqual(r['total_gates'],14)
        self.assertEqual(r['statuses'],{'PASS_HOST':6,'PASS_SOURCE':1,'BLOCKED':1,'PENDING':6})
        self.assertFalse(r['fullchain_hardware_ready'])
        self.assertFalse(r['original_iOS_SpringBoard_confirmed'])
        self.assertEqual(r['device_actions'],0)
        self.assertTrue(all(not v['phone_passed'] for v in r['gates']))
    def test_memory_cannot_be_forged_pass(self):
        self.mutate('reports/thor-darwin22-memory-gate-20261026.json',
            'available_MiB',256)
        with self.assertRaisesRegex(ValueError,'memory'):gates.make(self.repo)
    def test_apfs_kernel_fat_digest_must_match(self):
        self.mutate('runs/ios16-f1e-full-kernel-on-fat56-host-20261026/real-Apple20G81-f1e-envelope-proof.json',
            'kernel_sha256','0'*64)
        with self.assertRaisesRegex(ValueError,'FAT kernel differs'):gates.make(self.repo)
    def test_host_firmware_cannot_be_marked_phone_eligible(self):
        self.mutate('evidence/build/thor-20261026-ios-full-f1e/f1e-independent-structural-audit.json',
            'boot_eligible',True)
        with self.assertRaisesRegex(ValueError,'F1e full firmware'):gates.make(self.repo)
    def test_vendor_DTS_does_not_grant_DMA(self):
        self.mutate('reports/thor-pong-ufs-dt-diff-20261026.json',
            'hardware_MMIO_or_DMA_authorized',True)
        with self.assertRaisesRegex(ValueError,'vendor UFS'):gates.make(self.repo)
    def test_apfs_component_count_mutation_blocked(self):
        self.mutate('runs/ios16-apfs-full-closure-host-20261026/apfs-emission-proof.json',
            'component_count',10)
        with self.assertRaisesRegex(ValueError,'APFS'):gates.make(self.repo)
if __name__=='__main__':
    unittest.main()
