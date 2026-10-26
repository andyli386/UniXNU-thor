#!/usr/bin/env python3
"""Fail-closed static DDR investigation only; no authority granted."""
import importlib.util
import json
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
S=ROOT/'tools/analyze_thor_ddr_ownership_candidates.py'
def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
m=load(S,'Ddr')
h=load(ROOT/'tools/darwinboot_thor_memory_gate.py','Mem')

class TestThorDdrHypotheses(unittest.TestCase):
    def test_disjoint_subtraction(self):
        source=[{'start':0,'end':500}]
        stops=[{'start':100,'end':140},
               {'start':300,'end':350}]
        rem=m.subtract(source,stops)
        self.assertEqual({(x['start'],x['end']) for x in rem},
                         {(0,100),(140,300),(350,500)})
    def test_reserved_interval_not_reclaimable(self):
        source=[{'start':0,'end':800}]
        stops=[{'start':128,'end':600}]
        gaps=m.subtract(source,stops)
        self.assertEqual(max(x['bytes'] for x in gaps),200)
    def test_misaligned_candidate_fails_closed(self):
        row={'start':0x80001000,'end':0x90001000}
        self.assertIsNone(m.aligned_prefix(row,256*m.MIB))
    def test_aligned_candidate_only_hypothesis(self):
        row={'start':0xBAB00000,'end':0xE0000000}
        candidate=m.aligned_prefix(row,256*m.MIB)
        self.assertEqual(candidate['start'],0xBAC00000)
        self.assertEqual(candidate['end'],0xCAC00000)
    def test_real_vendor_and_uefi_sources(self):
        project=ROOT.parents[1]
        vendor=project/'references/sm8450-vendor-fdt/thor.dts'
        mem=project/'evidence/build/thor-20261026-ios-full-f1c/F1C-MemoryMapLib.c'
        if not vendor.exists() or not mem.exists():
            self.skipTest('Strict local original vendor snapshot unavailable')
        proof=m.generate(vendor.read_text(),mem.read_text(),h,
                         '3e347601d9e91dd2f1f48e51499823225f7bc0bc')
        self.assertFalse(proof['none_candidates_UEFI_allocatable'] is False)
        self.assertTrue(proof['none_candidates_UEFI_allocatable'])
        self.assertEqual(proof['static_vendor_reserved_regions'],38)
        self.assertEqual(len(proof['dynamic_unknown_placement_reservations']),16)
        low=[x for x in proof['nominal_static_DDR_gaps_over_256M']
             if x['gap_start']=='0xbab00000'][0]
        self.assertEqual(low['whole_public_DDR_gap_MiB'],597)
        self.assertEqual(low['overlaps_current_UEFI_policy'],['Other low RAM reserved'])
        self.assertFalse(low['may_allocate_or_write'])
        self.assertFalse(proof['runtime_device_tree_validated'])
        self.assertEqual(proof['phone_actions'],0)
if __name__=='__main__':
    unittest.main()
