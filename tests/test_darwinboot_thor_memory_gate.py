#!/usr/bin/env python3
"""Pure host-only memory authorisation regression tests; no phone interaction."""
import importlib.util
import pathlib
import unittest

ROOT=pathlib.Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('thor_memory',ROOT/'tools/darwinboot_thor_memory_gate.py')
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class TestDarwinBootThorMemory(unittest.TestCase):
    def setUp(self):
        self.minimal='''STATIC EFI_MEMORY_REGION_DESCRIPTOR gRegions[] = {
 {"RAM 1",0x10000000ULL,0x08000000ULL,AddMem,SYS_MEM,SYS_MEM_CAP,Conv,WRITE_BACK},
 {"reserved",0x18000000ULL,0x08000000ULL,AddMem,MEM_RES,SYS_MEM_CAP,Reserv,WRITE_BACK},
 {"RAM 2",0x20000000ULL,0x08000000ULL,AddMem,SYS_MEM,SYS_MEM_CAP,Conv,WRITE_BACK},
 };'''
    def test_parse_real_f1c(self):
        f=pathlib.Path('/Users/<user>/projects/thor-unixnu/evidence/build/thor-20261026-ios-full-f1c/F1C-MemoryMapLib.c')
        if not f.is_file():self.skipTest('F1c source unavailable on other hosts')
        rows=m.parse_region_map(f.read_text())
        self.assertEqual(len(rows),13)
        alloc=[x for x in rows if x['can_allocate']]
        self.assertEqual(len(alloc),1)
        self.assertEqual((alloc[0]['start'],alloc[0]['bytes']), (0xF8000000,124*m.MIB))
        self.assertFalse(any(x['can_allocate'] for x in rows if x['mode']=='AddDev'))
    def test_reserved_memory_breaks_join(self):
        rows=m.parse_region_map(self.minimal)
        runs=m.size_of_contiguous_conventional(rows)
        self.assertEqual(len(runs),2)
        self.assertTrue(all(x['bytes']==128*m.MIB for x in runs))
        self.assertEqual(m.largest_aligned_window(runs,256*m.MIB)['meets_minimum'],False)
    def test_conventional_physically_adjacent_join(self):
        s=self.minimal.replace(
         ' {"reserved",0x18000000ULL,0x08000000ULL,AddMem,MEM_RES,SYS_MEM_CAP,Reserv,WRITE_BACK},',
         ' {"RAM middle",0x18000000ULL,0x08000000ULL,AddMem,SYS_MEM,SYS_MEM_CAP,Conv,WRITE_BACK},')
        rows=m.parse_region_map(s)
        runs=m.size_of_contiguous_conventional(rows)
        self.assertEqual(len(runs),1)
        self.assertEqual(runs[0]['bytes'],384*m.MIB)
        self.assertTrue(m.largest_aligned_window(runs,256*m.MIB)['meets_minimum'])
    def test_conventional_disjoint_by_single_page(self):
        s=self.minimal.replace(
         '0x20000000ULL','0x20001000ULL')
        rows=m.parse_region_map(s)
        self.assertEqual(len(m.size_of_contiguous_conventional(rows)),2)
    def test_disallow_overlapping_memory_regions(self):
        s=self.minimal.replace(
         '0x20000000ULL','0x1ff00000ULL')
        with self.assertRaisesRegex(ValueError,'overlap'):m.parse_region_map(s)
    def test_reject_32bit_address_overflow(self):
        s=self.minimal.replace(
         '0x08000000ULL,AddMem,SYS_MEM','0xFFFFFFFFFFFFFFFFULL,AddMem,SYS_MEM',1)
        with self.assertRaisesRegex(ValueError,'invalid UEFI range'):m.parse_region_map(s)
    def test_mapped_mmio_is_never_conventional(self):
        s=self.minimal.replace('0x20000000ULL,0x08000000ULL,AddMem,SYS_MEM,SYS_MEM_CAP,Conv',
                               '0x20000000ULL,0x08000000ULL,AddDev,MMAP_IO,UNCACHEABLE,MmIO')
        rows=m.parse_region_map(s)
        self.assertEqual(sum(r['can_allocate'] for r in rows),1)
    def test_alignment_aware_window_fails_despite_nominal_length(self):
        # 256MiB physically conventional but starts just after 2MiB boundary.
        row={'start':0x30001000,'end':0x30001000+256*m.MIB}
        result=m.largest_aligned_window([row],256*m.MIB)
        self.assertLess(result['available'],256*m.MIB)
        self.assertFalse(result['meets_minimum'])
    def test_high_DDR_is_not_UEFI_allocation_evidence(self):
        text='''/ { memory { reg = <0 0x80000000 0 0x10000000
                8 0 0 0x20000000>; }; };'''
        banks=m.parse_vendor_banks(text)
        self.assertEqual(len(banks),2)
        self.assertEqual(banks[1]['start'],8*(1<<32))
        self.assertEqual(banks[1]['bytes'],512*m.MIB)
        # The existence of a DDR bank is not a UEFI conventional-memory descriptor.
        self.assertFalse(m.size_of_contiguous_conventional([]))
    def test_reject_duplicate_DDR_banks(self):
        source='/ { memory { reg = <0 0x80000000 0 0x30000000 0 0x90000000 0 0x10000000>; }; };'
        with self.assertRaisesRegex(ValueError,'overlap'):m.parse_vendor_banks(source)
if __name__=='__main__':
    unittest.main()
