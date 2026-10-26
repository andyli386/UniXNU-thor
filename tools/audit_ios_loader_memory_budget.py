#!/usr/bin/env python3
"""Audit Thor iOS loader allocation lifetimes without changing policy or devices.

Source-only capacity is an upper bound, never live UEFI ownership. Unknown
ADT/cache/ramdisk/heap overhead remains unknown; quantified totals are lower
bounds. Hypothetical layouts are comparisons, not enabled boot configurations.
"""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import re
import struct

from darwinboot_thor_memory_gate import (
    parse_region_map, size_of_contiguous_conventional)

MIB = 1 << 20
PAGE = 4096
U64_MAX = (1 << 64) - 1
FAT_PROOF_SCHEMA = "thor-f1e-genuine-Apple20G81-apfs-kernel-in-fat56-thorfat1-hostonly-v1"


def need(condition, message):
    if not condition:
        raise ValueError(message)


def uint64(value, label):
    need(type(value) is int and 0 <= value <= U64_MAX, "invalid uint64: " + label)
    return value


def add(*values):
    result = 0
    for value in values:
        uint64(value, "addition operand")
        need(result <= U64_MAX - value, "uint64 addition overflow")
        result += value
    return result


def align_up(value, alignment):
    uint64(value, "aligned value")
    need(alignment > 0 and alignment & (alignment - 1) == 0,
         "alignment must be a power of two")
    return add(value, alignment - 1) & ~(alignment - 1)


def sha_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for data in iter(lambda: stream.read(1 << 20), b""):
            digest.update(data)
    return digest.hexdigest()


def macho_geometry(data):
    need(len(data) >= 32, "truncated Mach-O header")
    magic, cpu, subtype, kind, count, command_bytes, flags, reserved = (
        struct.unpack_from("<8I", data))
    need((magic, cpu, subtype, kind) == (0xfeedfacf, 0x100000c, 0, 2),
         "expected thin little-endian ARM64 kernel Mach-O")
    need(0 < count <= 4096 and command_bytes <= len(data) - 32,
         "truncated or excessive load commands")
    limit, offset, segments, entry = 32 + command_bytes, 32, [], None
    for _ in range(count):
        need(offset <= limit - 8, "truncated load command")
        command, length = struct.unpack_from("<II", data, offset)
        need(length >= 8 and length % 8 == 0 and length <= limit - offset,
             "invalid load command length")
        if command == 0x19:
            need(length >= 72, "truncated LC_SEGMENT_64")
            nsects = struct.unpack_from("<I", data, offset + 64)[0]
            need(nsects <= (length - 72) // 80, "truncated section table")
            name = data[offset + 8:offset + 24].split(b"\0", 1)[0].decode("ascii")
            address, size, file_offset, file_bytes = struct.unpack_from(
                "<4Q", data, offset + 24)
            protection = struct.unpack_from("<I", data, offset + 60)[0]
            end, file_end = add(address, size), add(file_offset, file_bytes)
            need(file_bytes <= size and file_end <= len(data),
                 "segment file extent outside kernel")
            if size:
                need(len(segments) < 64, "loader MAX_SEGMENTS exceeded")
                segments.append({"name": name, "va": address, "end": end,
                                 "bytes": size, "file_offset": file_offset,
                                 "file_bytes": file_bytes, "protection": protection})
        elif command == 5:
            need(entry is None and length == 288 and
                 struct.unpack_from("<II", data, offset + 8) == (6, 68),
                 "unsupported or duplicate ARM64 thread entry")
            entry = struct.unpack_from("<Q", data, offset + 272)[0]
        offset += length
    need(offset == limit and segments and entry is not None,
         "incomplete kernel commands or entry")
    ordered = sorted(segments, key=lambda item: item["va"])
    for left, right in zip(ordered, ordered[1:]):
        need(left["end"] <= right["va"], "overlapping virtual segments")
    base, end = ordered[0]["va"], max(item["end"] for item in ordered)
    need(base >> 48 == 0xffff and base % PAGE == 0 and entry % 4 == 0,
         "kernel address or entry alignment")
    need(any(item["va"] <= entry < add(item["va"], item["file_bytes"])
             and item["protection"] & 4 for item in ordered),
         "entry outside executable file-backed segment")
    return {"file_bytes": len(data), "virtual_base": base, "virtual_end": end,
            "loaded_span_bytes": end - base, "entry_va": entry,
            "segment_count": len(ordered), "segments": ordered}


def compact_c(source):
    source = re.sub(r"/\*.*?\*/|//[^\n]*", "", source, flags=re.S)
    return re.sub(r"\s+", "", source)


def loader_contract(loader, window_header, fat_driver):
    bounds = re.search(
        r"return\s+size\s*>=\s*(0x[0-9a-fA-F]+)ULL\s*&&\s*"
        r"size\s*<=\s*(0x[0-9a-fA-F]+)ULL", window_header)
    need(bounds is not None, "unreviewed WindowSize parser")
    minimum, maximum = (int(value, 16) for value in bounds.groups())
    need(minimum == 256 * MIB and maximum == 8192 * MIB,
         "current 256MiB parser policy changed; require a separate review")
    need("!(size&0x1fffffULL)" in compact_c(window_header),
         "unreviewed window granularity")
    text, fat = compact_c(loader), compact_c(fat_driver)
    mandatory = [
        "#defineWINDOW_SIZESIZE_256MB", "#defineBLOCK_SIZESIZE_2MB",
        "*Data=AllocatePool(*Size);",
        "ArgsAddress=ALIGN_VALUE(Address+Kernel.End-Kernel.Base,SIZE_16KB);",
        "TreeAddress=ArgsAddress+EFI_PAGE_SIZE;",
        "RamdiskAddress=ALIGN_VALUE(TreeAddress+TreeSize,EFI_PAGE_SIZE);",
        "TrustCacheAddress=RamdiskAddress+RamdiskBytes;",
        "Used=ALIGN_VALUE(TrustCacheAddress+TrustCacheSize,SIZE_16KB)-Address;",
        "Used+=EFI_PAGE_SIZE;", "if(Used>WindowSize-BLOCK_SIZE)",
        "Candidate=ALIGN_VALUE(Descriptor->PhysicalStart,BLOCK_SIZE)+(Kernel->Base&(BLOCK_SIZE-1));",
    ]
    for statement in mandatory:
        need(statement in text, "unreviewed loader contract: " + statement)
    read_at = text.find("ReadFile(Root,Request->KernelPath,SIZE_128MB,&Image,&ImageSize)")
    allocate_at = text.find("AllocateWindow(&Kernel,WindowSize,&Address)")
    free_at = text.find("FreePool(Image);Image=NULL;", allocate_at)
    need(0 <= read_at < allocate_at < free_at, "kernel Image lifetime changed")
    need("gBS->AllocatePages(AllocateAnyPages,EfiReservedMemoryType,EFI_SIZE_TO_PAGES(FatBytes),&Copy)"
         in fat and "mRamBase=(UINT8*)(UINTN)Copy;" in fat and
         "CopyMem(mRamBase,Fat,FatBytes);" in fat,
         "FAT retained-copy lifetime changed")
    need("CopyMem(Buffer,mRamBase+Lba*512,Size);" in fat,
         "unreviewed BlockIO source")
    return {"parser_minimum_bytes": minimum, "parser_maximum_bytes": maximum,
            "default_window_bytes": 256 * MIB, "block_bytes": 2 * MIB,
            "page_bytes": PAGE, "metadata_alignment_bytes": 16384,
            "end_guard_bytes": 2 * MIB, "trampoline_bytes_reserved": PAGE,
            "kernel_image_pool_lives_during_window_allocation": True,
            "fat_reserved_copy_lives_during_window_allocation": True,
            "changes_to_loader_or_policy": False}


def window_candidates(runs, window_bytes, base_offset, alignment):
    rows = []
    for run in runs:
        start, end = uint64(run["start"], "run start"), uint64(run["end"], "run end")
        need(end > start, "invalid conventional run")
        candidate = add(align_up(start, alignment), base_offset)
        available = max(0, end - candidate)
        rows.append({"run_start": start, "run_end": end,
                     "candidate_start": candidate, "kernel_base_offset": base_offset,
                     "available_bytes": available,
                     "window_fits_static_run": available >= window_bytes})
    return rows


def layout_lower_bound(kernel, policy, adt_bytes=None, trustcache_bytes=None,
                       ramdisk_bytes=None):
    for name, value in (("ADT", adt_bytes), ("trustcache", trustcache_bytes),
                        ("ramdisk", ramdisk_bytes)):
        if value is not None:
            uint64(value, name)
    need(adt_bytes is None or 0 < adt_bytes <= 16 * MIB,
         "ADT must be nonempty and within loader ReadFile limit")
    need(trustcache_bytes is None or trustcache_bytes <= 16 * MIB,
         "trustcache exceeds loader ReadFile limit")
    anchor = kernel["virtual_base"] & (policy["block_bytes"] - 1)
    # Kernel.Base and Candidate have the same low 21 bits. Model absolute
    # alignment with that anchor instead of assuming a zero physical offset.
    args = align_up(add(anchor, kernel["loaded_span_bytes"]), 16384)
    tree = add(args, PAGE)
    ramdisk = align_up(add(tree, 1 if adt_bytes is None else adt_bytes), PAGE)
    trustcache = add(ramdisk, align_up(0 if ramdisk_bytes is None else ramdisk_bytes, PAGE))
    used = add(align_up(add(trustcache, 0 if trustcache_bytes is None else trustcache_bytes),
                       16384) - anchor, PAGE)
    minimum = align_up(add(used, policy["end_guard_bytes"]), policy["block_bytes"])
    return {"physical_alignment_anchor": anchor,
            "boot_args_offset": args - anchor, "ADT_offset": tree - anchor,
            "ramdisk_offset": ramdisk - anchor, "trustcache_offset": trustcache - anchor,
            "used_bytes_lower_bound": used, "minimum_window_bytes_by_layout": minimum,
            "layout_sizes_complete": all(value is not None for value in
                                         (adt_bytes, trustcache_bytes, ramdisk_bytes)),
            "unknown_ADT_uses_one_byte_for_layout_lower_bound_only": adt_bytes is None}


def calculate_budget(kernel, policy, fat_bytes, regions, adt_bytes=None,
                     trustcache_bytes=None, ramdisk_bytes=None, heap_overhead_bytes=None):
    uint64(fat_bytes, "FAT bytes")
    need(fat_bytes > 0, "empty FAT")
    if heap_overhead_bytes is not None:
        uint64(heap_overhead_bytes, "firmware heap overhead")
    layout = layout_lower_bound(kernel, policy, adt_bytes, trustcache_bytes, ramdisk_bytes)
    runs = size_of_contiguous_conventional(regions)
    need(runs, "no conventional-memory source range")
    heap_capacity = add(*(run["bytes"] for run in runs))
    offset = kernel["virtual_base"] & (policy["block_bytes"] - 1)
    static_windows = window_candidates(runs, policy["default_window_bytes"],
                                      offset, policy["block_bytes"])
    unknowns = [name for name, value in (
        ("actual_ADT_bytes_and_memory_size_property", adt_bytes),
        ("actual_trustcache_bytes_or_proven_absence", trustcache_bytes),
        ("actual_ramdisk_size_storage_and_lifetime", ramdisk_bytes),
        ("DXE_FDT_filesystem_map_allocator_and_other_heap_overhead", heap_overhead_bytes))
        if value is None]
    input_buffers = add(0 if adt_bytes is None else adt_bytes,
                        0 if trustcache_bytes is None else trustcache_bytes)
    overhead = 0 if heap_overhead_bytes is None else heap_overhead_bytes
    fat_allocation = align_up(fat_bytes, PAGE)
    def scenario(name, window, image_copy, implemented):
        peak = add(fat_allocation, kernel["file_bytes"] if image_copy else 0,
                   window, input_buffers, overhead)
        candidates = window_candidates(runs, window, offset, policy["block_bytes"])
        return {"name": name, "implemented_in_current_loader": implemented,
                "window_bytes": window, "FAT_retained_copy_bytes": fat_allocation,
                "kernel_Image_pool_bytes": kernel["file_bytes"] if image_copy else 0,
                "known_ADT_and_trustcache_pool_bytes": input_buffers,
                "known_firmware_heap_overhead_bytes": overhead,
                "simultaneous_known_allocation_peak_bytes_lower_bound": peak,
                "simultaneous_known_allocation_peak_MiB_lower_bound": peak / MIB,
                "total_heap_fits_static_capacity": peak <= heap_capacity,
                "contiguous_window_fits_static_upper_bound":
                    any(row["window_fits_static_run"] for row in candidates),
                "window_meets_current_parser_policy":
                    policy["parser_minimum_bytes"] <= window <= policy["parser_maximum_bytes"],
                "layout_fits_with_end_guard": layout["used_bytes_lower_bound"] <=
                    max(0, window - policy["end_guard_bytes"]),
                "actual_window_allocation_or_runtime_verified": False}
    minimum = layout["minimum_window_bytes_by_layout"]
    scenarios = [
        scenario("current_default_256MiB_with_FAT_and_Image_copies",
                 policy["default_window_bytes"], True, True),
        scenario("hypothetical_layout_minimum_with_both_copies", minimum, True, False),
        scenario("hypothetical_stream_Image_only_keep_FAT_copy", minimum, False, False),
    ]
    return {"layout": layout, "source_conventional_runs": runs,
            "total_static_conventional_bytes_upper_bound": heap_capacity,
            "total_static_conventional_MiB_upper_bound": heap_capacity / MIB,
            "static_default_window_candidates": static_windows,
            "largest_static_offset_corrected_window_bytes_upper_bound":
                max(row["available_bytes"] for row in static_windows),
            "kernel_physical_offset_mod_2MiB": offset,
            "unknown_budget_inputs": unknowns, "budget_sizes_complete": not unknowns,
            "scenarios": scenarios,
            "allocation_lifetimes": [
                {"allocation": "FAT reserved copy", "begins": "ThorBootMemoryDxe.Publish",
                 "ends": "retained by published BlockIO; not freed on success",
                 "overlaps_AllocateWindow": True},
                {"allocation": "kernel Image pool", "begins": "DarwinBoot.ReadFile kernel",
                 "ends": "after segment copy, FreePool(Image)",
                 "overlaps_AllocateWindow": True},
                {"allocation": "owned kernel Window", "begins": "AllocateWindow",
                 "ends": "passed to kernel at EBS handoff",
                 "overlaps_AllocateWindow": True}],
            "live_GetMemoryMap_known": False,
            "static_runs_are_preallocation_source_upper_bounds": True,
            "allocated_buffer_placement_and_fragmentation_known": False,
            "kernel_runtime_page_table_and_VM_budget_proven": False,
            "memory_ownership_proven": False, "ready": False, "phone_boot_allowed": False}


def audit(kernel_path, expected_kernel_sha256, fat_image, fat_proof,
          loader_source, window_header, fat_driver_source, memory_map_source,
          **sizes):
    need(sha_file(kernel_path) == expected_kernel_sha256, "kernel SHA256 mismatch")
    kernel = macho_geometry(kernel_path.read_bytes())
    proof = json.loads(fat_proof.read_text())
    need(proof.get("schema") == FAT_PROOF_SCHEMA and
         proof.get("result") == "PASS_ACTUAL_APFS_KERNEL_FAT56_THORFAT1_ENVELOPE_FITS_64M_INITRD",
         "unsupported FAT proof schema or result")
    need(proof.get("kernel_sha256") == expected_kernel_sha256 and
         proof.get("kernel_bytes") == kernel["file_bytes"] and
         proof.get("fat_bytes") == fat_image.stat().st_size and
         proof.get("fat_image_sha256") == sha_file(fat_image) and
         proof.get("volume_contains_real_Apple_prelinked_MachO") is True,
         "FAT proof/image/kernel identity mismatch")
    fat_bytes = fat_image.stat().st_size
    need(fat_bytes >= 512 and fat_bytes % 512 == 0, "FAT BlockIO geometry")
    with fat_image.open("rb") as stream:
        header = stream.read(512)
    sectors = struct.unpack_from("<H", header, 19)[0] or struct.unpack_from("<I", header, 32)[0]
    need(header[510:512] == b"\x55\xaa" and
         struct.unpack_from("<H", header, 11)[0] == 512 and sectors * 512 == fat_bytes,
         "FAT BPB sector count/geometry")
    policy = loader_contract(loader_source.read_text(), window_header.read_text(),
                             fat_driver_source.read_text())
    need(kernel["file_bytes"] <= 128 * MIB and
         kernel["loaded_span_bytes"] <= 256 * MIB - 2 * MIB,
         "kernel exceeds current DarwinBoot ParseKernel/ReadFile bounds")
    regions = parse_region_map(memory_map_source.read_text())
    budget = calculate_budget(kernel, policy, fat_bytes, regions, **sizes)
    paths = {"kernel": kernel_path, "fat_image": fat_image, "fat_proof": fat_proof,
             "loader_source": loader_source, "window_header": window_header,
             "fat_driver_source": fat_driver_source, "memory_map_source": memory_map_source}
    return {"schema": "thor-ios-loader-memory-budget-v1",
            "recorded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "result": "BLOCKED_STATIC_BUDGET_NOT_LIVE_MEMORY_OWNERSHIP",
            "inputs": {key: {"path": str(path.resolve()), "bytes": path.stat().st_size,
                             "sha256": sha_file(path)} for key, path in paths.items()},
            "kernel_geometry": kernel, "loader_contract": policy,
            "FAT_proof_contains_valid_Thor_ADT":
                proof.get("volume_contains_valid_Thor_ADT") is True,
            "FAT_filesystem_contents_reparsed_by_this_budget_tool": False,
            "scope": "Allocation budget and source contracts only; existing FAT identity proof reused",
            "budget": budget,
            "limitations": ["256MiB remains the unchanged parser/default policy.",
                            "Hypothetical 112MiB/streaming scenarios are not enabled code.",
                            "Missing size inputs contribute zero to known heap totals, never to readiness.",
                            "The ADT layout lower bound is nonempty; a valid Thor ADT remains required.",
                            "UEFI allocations reduce and fragment source-map free memory.",
                            "Kernel page tables, VM and userspace capacity require separate evidence."],
            "device_actions": 0, "loader_or_policy_modified": False,
            "ready": False, "phone_boot_allowed": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("kernel", "fat-image", "fat-proof", "loader-source", "window-header",
                 "fat-driver-source", "memory-map-source", "out"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--expected-kernel-sha256", required=True)
    for name in ("adt-bytes", "trustcache-bytes", "ramdisk-bytes", "heap-overhead-bytes"):
        parser.add_argument("--" + name, type=lambda value: int(value, 0))
    args = parser.parse_args(argv)
    need(not args.out.exists(), "refuse to replace an existing budget report")
    result = audit(args.kernel, args.expected_kernel_sha256, args.fat_image,
                   args.fat_proof, args.loader_source, args.window_header,
                   args.fat_driver_source, args.memory_map_source,
                   adt_bytes=args.adt_bytes, trustcache_bytes=args.trustcache_bytes,
                   ramdisk_bytes=args.ramdisk_bytes,
                   heap_overhead_bytes=args.heap_overhead_bytes)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"result": result["result"],
                      "kernel_file_bytes": result["kernel_geometry"]["file_bytes"],
                      "kernel_loaded_span_bytes": result["kernel_geometry"]["loaded_span_bytes"],
                      "minimum_layout_window_bytes": result["budget"]["layout"]["minimum_window_bytes_by_layout"],
                      "scenario_peak_MiB_lower_bounds":
                          [row["simultaneous_known_allocation_peak_MiB_lower_bound"]
                           for row in result["budget"]["scenarios"]],
                      "unknown_budget_inputs": result["budget"]["unknown_budget_inputs"],
                      "ready": False, "phone_boot_allowed": False, "device_actions": 0}, indent=2))


if __name__ == "__main__":
    main()
