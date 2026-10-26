#!/usr/bin/env python3
"""Memory-budget regressions. Synthetic kernels are never device candidates."""
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
spec = importlib.util.spec_from_file_location(
    "budget", ROOT / "tools/audit_ios_loader_memory_budget.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

BASE = 0xfffffff007004000
WINDOW_HEADER = """static inline int DarwinWindowSizeValid(unsigned long long size) {
 return size >= 0x10000000ULL && size <= 0x200000000ULL && !(size & 0x1fffffULL);
}"""
LOADER = """
#define WINDOW_SIZE SIZE_256MB
#define BLOCK_SIZE SIZE_2MB
*Data = AllocatePool (*Size);
ReadFile (Root, Request->KernelPath, SIZE_128MB, &Image, &ImageSize);
AllocateWindow (&Kernel, WindowSize, &Address);
ArgsAddress = ALIGN_VALUE (Address + Kernel.End - Kernel.Base, SIZE_16KB);
TreeAddress = ArgsAddress + EFI_PAGE_SIZE;
RamdiskAddress = ALIGN_VALUE (TreeAddress + TreeSize, EFI_PAGE_SIZE);
TrustCacheAddress = RamdiskAddress + RamdiskBytes;
Used = ALIGN_VALUE (TrustCacheAddress + TrustCacheSize, SIZE_16KB) - Address;
Used += EFI_PAGE_SIZE;
if (Used > WindowSize - BLOCK_SIZE) fail();
Candidate = ALIGN_VALUE (Descriptor->PhysicalStart, BLOCK_SIZE) + (Kernel->Base & (BLOCK_SIZE - 1));
FreePool (Image); Image = NULL;
"""
FAT_DRIVER = """
gBS->AllocatePages (AllocateAnyPages, EfiReservedMemoryType,
 EFI_SIZE_TO_PAGES (FatBytes), &Copy);
mRamBase = (UINT8 *)(UINTN)Copy;
CopyMem (mRamBase, Fat, FatBytes);
CopyMem (Buffer, mRamBase + Lba * 512, Size);
"""


def policy():
    return m.loader_contract(LOADER, WINDOW_HEADER, FAT_DRIVER)


def source_regions():
    return [{"start": 0xF8000000, "end": 0xFFC00000, "bytes": 124 * m.MIB,
             "can_allocate": True}]


def measured_kernel_geometry():
    # Regression of measured a798... artifact dimensions; actual CLI separately
    # reads and hashes the real artifact before producing a source-only report.
    return {"virtual_base": BASE, "loaded_span_bytes": 113229824,
            "file_bytes": 50659328}


def kernel_blob():
    def segment(name, va, size, offset, file_size, protection):
        return struct.pack("<II16sQQQQiiII", 0x19, 72, name.encode(), va, size,
                           offset, file_size, 7, protection, 0, 0)
    thread = bytearray(288)
    struct.pack_into("<4I", thread, 0, 5, 288, 6, 68)
    struct.pack_into("<Q", thread, 272, BASE + 0x200)
    commands = (segment("__TEXT", BASE, 0x2000, 0, 0x800, 5) +
                segment("__DATA", BASE + 0x8000, 0x4000, 0x800, 0x800, 3) +
                thread)
    header = struct.pack("<8I", 0xfeedfacf, 0x100000c, 0, 2, 3, len(commands), 0, 0)
    return bytearray(header + commands + bytes(4096 - len(header) - len(commands)))


class MachOTests(unittest.TestCase):
    def test_file_size_is_not_loaded_virtual_span(self):
        geometry = m.macho_geometry(kernel_blob())
        self.assertEqual(geometry["file_bytes"], 4096)
        self.assertEqual(geometry["loaded_span_bytes"], 49152)
        self.assertEqual(geometry["virtual_base"] & 0x1fffff, 0x4000)

    def test_truncated_header_rejected(self):
        with self.assertRaisesRegex(ValueError, "truncated"):
            m.macho_geometry(bytes(31))

    def test_truncated_load_command_area_rejected(self):
        data = kernel_blob()
        struct.pack_into("<I", data, 20, len(data))
        with self.assertRaisesRegex(ValueError, "load commands"):
            m.macho_geometry(data)

    def test_overlapping_segments_rejected(self):
        data = kernel_blob()
        struct.pack_into("<Q", data, 32 + 32, 0x10000)
        with self.assertRaisesRegex(ValueError, "overlapping"):
            m.macho_geometry(data)

    def test_virtual_extent_integer_overflow_rejected(self):
        data = kernel_blob()
        struct.pack_into("<Q", data, 32 + 24, m.U64_MAX - 4095)
        with self.assertRaisesRegex(ValueError, "overflow"):
            m.macho_geometry(data)

    def test_file_extent_integer_overflow_rejected(self):
        data = kernel_blob()
        struct.pack_into("<Q", data, 32 + 40, m.U64_MAX - 1)
        with self.assertRaisesRegex(ValueError, "overflow"):
            m.macho_geometry(data)

    def test_segment_file_data_larger_than_VM_rejected(self):
        data = kernel_blob()
        struct.pack_into("<Q", data, 32 + 32, 0x100)
        with self.assertRaisesRegex(ValueError, "segment file"):
            m.macho_geometry(data)

    def test_entry_in_nonexecutable_segment_rejected(self):
        data = kernel_blob()
        struct.pack_into("<Q", data, 32 + 144 + 272, BASE + 0x8010)
        with self.assertRaisesRegex(ValueError, "executable"):
            m.macho_geometry(data)

    def test_unaligned_command_length_rejected(self):
        data = kernel_blob()
        struct.pack_into("<I", data, 32 + 4, 73)
        with self.assertRaisesRegex(ValueError, "command length"):
            m.macho_geometry(data)

    def test_truncated_section_table_rejected(self):
        data = kernel_blob()
        struct.pack_into("<I", data, 32 + 64, 1)
        with self.assertRaisesRegex(ValueError, "section table"):
            m.macho_geometry(data)


class LifetimeAndPolicyTests(unittest.TestCase):
    def test_current_parser_minimum_must_not_be_silently_reduced(self):
        with self.assertRaisesRegex(ValueError, "policy changed"):
            m.loader_contract(LOADER, WINDOW_HEADER.replace("0x10000000", "0x7000000"),
                              FAT_DRIVER)

    def test_missing_Image_pool_contract_rejected(self):
        with self.assertRaisesRegex(ValueError, "loader contract"):
            m.loader_contract(LOADER.replace("*Data = AllocatePool (*Size);", ""),
                              WINDOW_HEADER, FAT_DRIVER)

    def test_free_Image_before_Window_changes_reviewed_lifetime(self):
        changed = LOADER.replace("FreePool (Image); Image = NULL;", "")
        changed = changed.replace("AllocateWindow", "FreePool (Image); Image = NULL; AllocateWindow", 1)
        with self.assertRaisesRegex(ValueError, "lifetime changed"):
            m.loader_contract(changed, WINDOW_HEADER, FAT_DRIVER)

    def test_removing_retained_FAT_copy_is_not_accepted_as_existing_code(self):
        with self.assertRaisesRegex(ValueError, "FAT retained-copy"):
            m.loader_contract(LOADER, WINDOW_HEADER,
                              FAT_DRIVER.replace("CopyMem (mRamBase, Fat, FatBytes);", ""))

    def test_actual_profile_three_simultaneous_allocations(self):
        report = m.calculate_budget(measured_kernel_geometry(), policy(), 56 * m.MIB,
                                    source_regions())
        peaks = [row["simultaneous_known_allocation_peak_MiB_lower_bound"]
                 for row in report["scenarios"]]
        self.assertEqual(peaks, [360.3125, 216.3125, 168.0])
        self.assertTrue(all(item["overlaps_AllocateWindow"]
                            for item in report["allocation_lifetimes"]))
        self.assertFalse(any(row["total_heap_fits_static_capacity"]
                             for row in report["scenarios"]))

    def test_hypothetical_112MiB_is_not_a_lowered_current_policy(self):
        report = m.calculate_budget(measured_kernel_geometry(), policy(), 56 * m.MIB,
                                    source_regions())
        self.assertEqual(report["layout"]["minimum_window_bytes_by_layout"], 112 * m.MIB)
        self.assertFalse(report["scenarios"][1]["window_meets_current_parser_policy"])
        self.assertFalse(report["scenarios"][2]["implemented_in_current_loader"])

    def test_uint64_overflow_in_peak_rejected(self):
        with self.assertRaisesRegex(ValueError, "overflow"):
            m.calculate_budget(measured_kernel_geometry(), policy(), 56 * m.MIB,
                               source_regions(), heap_overhead_bytes=m.U64_MAX)

    def test_negative_or_bool_size_rejected(self):
        for value in (-1, True):
            with self.assertRaisesRegex(ValueError, "uint64"):
                m.calculate_budget(measured_kernel_geometry(), policy(), value,
                                   source_regions())

    def test_unknown_ADT_cache_heap_never_means_ready(self):
        for sizes in ({}, {"adt_bytes": 100, "ramdisk_bytes": 0},
                      {"adt_bytes": 100, "trustcache_bytes": 0, "ramdisk_bytes": 0}):
            report = m.calculate_budget(measured_kernel_geometry(), policy(), 56 * m.MIB,
                                        source_regions(), **sizes)
            self.assertTrue(report["unknown_budget_inputs"])
            self.assertFalse(report["budget_sizes_complete"])
            self.assertFalse(report["ready"])
            self.assertFalse(report["phone_boot_allowed"])

    def test_even_known_sizes_cannot_replace_GetMemoryMap_ownership(self):
        report = m.calculate_budget(measured_kernel_geometry(), policy(), 56 * m.MIB,
                                    source_regions(), adt_bytes=100, trustcache_bytes=0,
                                    ramdisk_bytes=0, heap_overhead_bytes=0)
        self.assertTrue(report["budget_sizes_complete"])
        self.assertFalse(report["live_GetMemoryMap_known"])
        self.assertFalse(report["memory_ownership_proven"])
        self.assertFalse(report["ready"])

    def test_empty_or_excess_ADT_rejected(self):
        for value in (0, 16 * m.MIB + 1):
            with self.assertRaisesRegex(ValueError, "ADT"):
                m.layout_lower_bound(measured_kernel_geometry(), policy(), adt_bytes=value)

    def test_excess_trustcache_rejected(self):
        with self.assertRaisesRegex(ValueError, "trustcache"):
            m.layout_lower_bound(measured_kernel_geometry(), policy(),
                                 trustcache_bytes=16 * m.MIB + 1)


class AlignmentAndCapacityTests(unittest.TestCase):
    def test_kernel_base_offset_prevents_nominal_exact_fit(self):
        runs = [{"start": 0xF8000000, "end": 0xF8000000 + 256 * m.MIB}]
        corrected = m.window_candidates(runs, 256 * m.MIB, 0x4000, 2 * m.MIB)[0]
        naive = m.window_candidates(runs, 256 * m.MIB, 0, 2 * m.MIB)[0]
        self.assertFalse(corrected["window_fits_static_run"])
        self.assertEqual(corrected["available_bytes"], 256 * m.MIB - 16384)
        self.assertTrue(naive["window_fits_static_run"])

    def test_Metadata_alignment_uses_physical_base_low_bits(self):
        geometry = {"virtual_base": BASE + 4096, "loaded_span_bytes": 65536}
        layout = m.layout_lower_bound(geometry, policy(), adt_bytes=100,
                                      trustcache_bytes=0, ramdisk_bytes=0)
        anchor = geometry["virtual_base"] & 0x1fffff
        self.assertEqual((anchor + layout["boot_args_offset"]) % 16384, 0)
        self.assertGreater(layout["boot_args_offset"], geometry["loaded_span_bytes"])

    def test_disjoint_total_heap_is_not_a_contiguous_window(self):
        runs = [{"start": 0x10000000, "end": 0x14000000},
                {"start": 0x18000000, "end": 0x1c000000}]
        self.assertEqual(sum(row["end"] - row["start"] for row in runs), 128 * m.MIB)
        result = m.window_candidates(runs, 112 * m.MIB, 0x4000, 2 * m.MIB)
        self.assertFalse(any(row["window_fits_static_run"] for row in result))

    def test_alignment_overflow_rejected(self):
        with self.assertRaisesRegex(ValueError, "overflow"):
            m.window_candidates([{"start": m.U64_MAX - 1000, "end": m.U64_MAX}],
                                4096, 0x4000, 2 * m.MIB)

    def test_124MiB_source_capacity_is_only_123_984375MiB_window_upper_bound(self):
        result = m.calculate_budget(measured_kernel_geometry(), policy(),
                                    56 * m.MIB, source_regions())
        self.assertEqual(result["total_static_conventional_MiB_upper_bound"], 124)
        self.assertEqual(result["largest_static_offset_corrected_window_bytes_upper_bound"],
                         124 * m.MIB - 16384)


class InputIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        data = kernel_blob()
        (self.root / "kernel").write_bytes(data)
        fat = bytearray(512)
        struct.pack_into("<H", fat, 11, 512)
        struct.pack_into("<H", fat, 19, 1)
        fat[510:512] = b"\x55\xaa"
        (self.root / "fat").write_bytes(fat)
        self.kernel_sha = hashlib.sha256(data).hexdigest()
        proof = {"schema": m.FAT_PROOF_SCHEMA,
                 "result": "PASS_ACTUAL_APFS_KERNEL_FAT56_THORFAT1_ENVELOPE_FITS_64M_INITRD",
                 "kernel_sha256": self.kernel_sha, "kernel_bytes": len(data),
                 "fat_image_sha256": hashlib.sha256(fat).hexdigest(), "fat_bytes": len(fat),
                 "volume_contains_real_Apple_prelinked_MachO": True,
                 "volume_contains_valid_Thor_ADT": False}
        (self.root / "proof").write_text(json.dumps(proof))
        for name, source in (("loader", LOADER), ("window", WINDOW_HEADER),
                             ("fat-driver", FAT_DRIVER)):
            (self.root / name).write_text(source)
        (self.root / "map").write_text(
            '{"DXE Heap",0xF8000000ULL,0x07C00000ULL,AddMem,SYS_MEM,SYS_MEM_CAP,Conv,WRITE_BACK},')

    def audit(self):
        return m.audit(*(self.root / name if name != "hash" else self.kernel_sha
                         for name in ("kernel", "hash", "fat", "proof", "loader",
                                      "window", "fat-driver", "map")))

    def test_valid_synthetic_inputs_still_report_static_blocked(self):
        report = self.audit()
        self.assertEqual(report["schema"], "thor-ios-loader-memory-budget-v1")
        self.assertEqual(report["result"], "BLOCKED_STATIC_BUDGET_NOT_LIVE_MEMORY_OWNERSHIP")
        self.assertFalse(report["ready"])
        self.assertFalse(report["phone_boot_allowed"])
        self.assertEqual(report["device_actions"], 0)

    def test_wrong_kernel_hash_rejected(self):
        self.kernel_sha = "0" * 64
        with self.assertRaisesRegex(ValueError, "kernel SHA"):
            self.audit()

    def test_fat_manifest_mismatch_rejected(self):
        (self.root / "fat").write_bytes(bytes(512))
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            self.audit()

    def test_unrecognized_proof_schema_rejected(self):
        proof = json.loads((self.root / "proof").read_text())
        proof["schema"] = "unknown"
        (self.root / "proof").write_text(json.dumps(proof))
        with self.assertRaisesRegex(ValueError, "schema"):
            self.audit()


if __name__ == "__main__":
    unittest.main()
