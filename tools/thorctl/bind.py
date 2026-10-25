#!/usr/bin/env python3
"""Derive exact Thor UEFI kernel + handoff geometry from a linked Mach-O.

Changes *only* a clean, explicit isolated task worktree after --apply.
Never edits original source, never fetches remote dependencies, never accesses a phone.
"""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess


def sha(data):
    return hashlib.sha256(data).hexdigest()


def array(h):
    assert len(h) == 64
    return "{ " + ", ".join("0x" + h[i:i+2] for i in range(0, 64, 2)) + " }"


def identity(root, path):
    proc = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                          check=True, capture_output=True, text=True)
    status = subprocess.check_output(["git", "-C", str(path), "status",
                                      "--porcelain"], text=True)
    assert not status, "UEFI worktree must be clean before binding"
    assert path.resolve().is_relative_to((root/"worktrees/uefi").resolve())
    return proc.stdout.strip()


def geometry(layout, old):
    original = {}
    for line in old.splitlines():
        m = re.match(r"^#define\s+([A-Z0-9_]+)\s+(0x[0-9a-f]+)$", line)
        if m:
            original[m[1]] = int(m[2], 16)
    names = [
        "E09_KERNEL_SPAN", "E06_ARGS_OFFSET", "E06_TRANSITION_OFFSET",
        "E06_CONTEXT_OFFSET", "E06_MAP_OFFSET", "E06_WALK_OFFSET",
        "E06_STACK_OFFSET", "E06_STACK_TOP_OFFSET", "E06_FINAL_USED",
        "E06_ENTRY_OFFSET", "E06_VECTOR_OFFSET", "E06_ENTRY_SYNC_OFFSET",
        "E06_ENTRY_SYNC_BYTES", "E09_ADT_OFFSET"]
    assert set(original) == set(names), "Unexpected legacy layout schema"
    oldspan = original["E09_KERNEL_SPAN"]
    assert oldspan == original["E06_ARGS_OFFSET"] == 0x1340000
    assert layout["loaded_span_bytes"] in (0x133c000, 0x1340000)
    span = layout["loaded_span_bytes"]
    assert span % 0x4000 == 0 and oldspan - span in (0, 0x4000)
    d = dict(original)
    d["E09_KERNEL_SPAN"] = span
    shifted = names[1:9] + ["E09_ADT_OFFSET"]
    for name in shifted:
        d[name] = original[name] + span - oldspan
    assert d["E06_ARGS_OFFSET"] == span
    assert d["E06_TRANSITION_OFFSET"] == d["E06_ARGS_OFFSET"] + 0x4000
    assert d["E06_CONTEXT_OFFSET"] == d["E06_TRANSITION_OFFSET"] + 0x4000
    assert d["E06_MAP_OFFSET"] == d["E06_CONTEXT_OFFSET"] + 0x4000
    assert d["E06_WALK_OFFSET"] == d["E06_MAP_OFFSET"] + 0x10000
    assert d["E06_STACK_OFFSET"] == d["E06_WALK_OFFSET"] + 0x10000
    assert d["E06_STACK_TOP_OFFSET"] == d["E06_STACK_OFFSET"] + 0x10000
    assert d["E06_FINAL_USED"] == d["E06_TRANSITION_OFFSET"] + 0x44000
    assert d["E09_ADT_OFFSET"] == d["E06_FINAL_USED"] - 0x4000
    assert d["E06_FINAL_USED"] < 0x4000000 - 0x200000
    assert (d["E06_ENTRY_OFFSET"], d["E06_VECTOR_OFFSET"],
            d["E06_ENTRY_SYNC_OFFSET"], d["E06_ENTRY_SYNC_BYTES"]) == (
                layout["entry_offset"], layout["vectors_offset"],
                layout["entry_sync_offset"], layout["entry_sync_bytes"])
    return names, d


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--kernel", type=Path, required=True)
    ap.add_argument("--layout", type=Path, required=True)
    ap.add_argument("--xnu-commit", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    root = args.root.resolve()
    src = (root / args.source).resolve()
    k = (root / args.kernel).resolve()
    lp = (root / args.layout).resolve()
    for p in (src, k, lp):
        assert p.is_relative_to(root) and p.exists()
    assert re.fullmatch(r"[0-9a-f]{40}", args.xnu_commit)
    assert re.fullmatch(r"thor-[a-z0-9-]{6,96}", args.run_id)
    head = identity(root, src)
    raw = k.read_bytes()
    info = json.loads(lp.read_text())
    digest = sha(raw)
    assert digest == info["kernel_sha256"] and len(raw) == info["kernel_bytes"]
    assert info["virtual_base"] == 0xfffffff007004000
    assert info["segment_count"] == 10
    assert info["virtual_end"] - info["virtual_base"] == info["loaded_span_bytes"]
    assert info["entry_offset"] == 0x3fc488 and info["vectors_offset"] == 0x3fd000
    assert info["entry_sync_offset"] == 0x3fc000 and info["entry_sync_bytes"] == 0x3000
    assert info["bootstrap_pagetables_offset"] == 1527808
    assert info["excepstack_offset"] == 12517376
    board = src/"Platforms/Xiaomi/thorPkg/Drivers/ThorEntryBdsDxe"
    legacy = board/"ThorEntry09Layout.h"
    expected = board/"thor_entry06_expected.h"
    transport = board/"ThorEntry06PayloadExpected.h"
    assert "ENTRY10G exact" in expected.read_text(), "Not the reviewed source base"
    names, derived = geometry(info, legacy.read_text())
    prefix = struct.pack("<16sIIQ32s", b"THOR-PAYLOAD09\x00\x00",
                         2, 128, len(raw), bytes.fromhex(digest))
    assert len(prefix) == 64
    bind_id = "VM_POST_TTBR1_PROVENANCE.json"
    provenance = board/bind_id
    assert not provenance.exists()
    layout_header = (
        "/* Generated from frozen XNU Mach-O layout: run "+args.run_id+". */\n"
        "#ifndef THOR_ENTRY09_LAYOUT_H\n#define THOR_ENTRY09_LAYOUT_H\n"
        + "".join("#define "+name+" "+hex(derived[name])+"\n" for name in names)
        + "#endif\n")
    new_transport = (
        "/* Generated exact linked-kernel identity; seed belongs only to one-use image. */\n"
        "#ifndef THOR_ENTRY06_PAYLOAD_EXPECTED_H\n#define THOR_ENTRY06_PAYLOAD_EXPECTED_H\n"
        "#include <Uefi.h>\n"
        "#define THOR_ENTRY06_PAYLOAD_BYTES "+str(len(raw))+"ULL\n"
        "STATIC CONST UINT8 mEntry06PayloadHeader[64] = { "
        + ", ".join("0x%02x"%x for x in prefix) + " };\n"
        "STATIC CONST UINT8 mEntry06PayloadSha256[32] = "+array(digest)+";\n"
        "#endif\n")
    new_expected = (
        "/* Generated: actual XNU segment span/VA binding, unchanged handoff ABI. */\n"
        "#ifndef THOR_ENTRY06_EXPECTED_H\n#define THOR_ENTRY06_EXPECTED_H\n"
        "#include \"thor_entry06.h\"\n"
        "STATIC CONST THOR_ENTRY06_EXPECTED mThorEntry06Expected = {\n"
        "  THOR_ENTRY06_VERSION, "+str(info["segment_count"])+"u,\n"
        "  "+str(len(raw))+"ULL, "+hex(info["virtual_base"])+"ULL, "
        +hex(info["virtual_end"])+"ULL,\n"
        "  "+info["entry_va_hex"]+"ULL, "+str(info["loaded_span_bytes"])+"ULL,\n"
        "  "+array(digest)+",\n"
        "  "+array(info["loaded_span_sha256"])+",\n"
        "  { 0xb8000000ULL, 1ULL, 5760ULL, 1440ULL, 3200ULL, 32ULL },\n"
        "  "+hex(info["vectors_offset"])+"ULL\n};\n#endif\n")
    outputs = {
        legacy:layout_header.encode(),transport:new_transport.encode(),
        expected:new_expected.encode()
    }
    report = {
        "schema":"thor-unified-kernel-uefi-binding-v1",
        "generated_at":dt.datetime.now(dt.timezone.utc).isoformat(),
        "run_id":args.run_id,
        "xnu_commit":args.xnu_commit,"uefi_base_commit":head,
        "kernel_sha256":digest,"kernel_bytes":len(raw),
        "loaded_span_sha256":info["loaded_span_sha256"],
        "loaded_span_bytes":info["loaded_span_bytes"],
        "layout":{key:val for key,val in info.items() if key!="kernel_path"},
        "source_files_before_sha256":{p.name:sha(p.read_bytes()) for p in outputs},
        "generated_files_sha256":{p.name:sha(d) for p,d in outputs.items()},
        "generated_layout":derived,
        "header_sha256":sha(prefix),
        "payload_prefix_sha256":sha(prefix),
        "adt_template_untouched":True,
        "kernel_worktree_untouched":True,
        "UEFI_handoff_abi_unchanged":True,
        "new_memory_grant_pages":0,
        "old_offset_offset_delta":derived["E06_ARGS_OFFSET"]-0x1340000,
        "phone_actions":0
    }
    out = root/"runs"/args.run_id/"l2"/"binding"
    if args.apply:
        assert not out.exists(), "Cannot overwrite an existing run"
        out.mkdir(parents=True)
        for p,data in outputs.items():
            tmp=out/(p.name+".new")
            with tmp.open("xb") as f:f.write(data)
        with (out/"binding-result.json").open("x") as f:
            json.dump(report,f,indent=2);f.write("\n")
        # Exchanging only generated files in an isolated worktree: Git tracks
        # the original, enabling exact rollback without mutating verified tag.
        for p in outputs:
            p.write_bytes((out/(p.name+".new")).read_bytes())
        with provenance.open("x") as f:
            json.dump(report,f,indent=2);f.write("\n")
        print("APPLIED_GENERATED_BINDING", out, digest, derived["E06_ARGS_OFFSET"])
    else:
        print(json.dumps(report,indent=2))


if __name__=="__main__":
    main()
