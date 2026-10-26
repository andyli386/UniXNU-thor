"""Host-only packaging primitives. No device operations or raw entropy logging."""
from contextlib import contextmanager
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys

TOOL_KEYS = ("mkbootimg", "unpack_bootimg", "image_verifier",
             "gki_certificate", "inspect_thor_fdt")


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def rooted(root, path):
    value = (root / path).resolve()
    require(value.is_relative_to(root), "Path escapes workspace")
    return value


def seed_source(platform):
    if platform.startswith("linux"):
        return "linux-os.getrandom(flags=0)"
    if platform == "darwin":
        return "darwin-os.urandom"
    raise RuntimeError("Unsupported OS CSPRNG platform")


def private_seed(platform=None):
    """One blocking OS CSPRNG call; the caller must never log the bytes."""
    platform = sys.platform if platform is None else platform
    source = seed_source(platform)
    value = os.getrandom(64, 0) if platform.startswith("linux") else os.urandom(64)
    require(isinstance(value, bytes) and len(value) == 64 and any(value),
            "Invalid OS CSPRNG result")
    return value, source


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_tools(root, manifest_path):
    """Pin interpreter and all non-stdlib script imports before entropy."""
    manifest = rooted(root, manifest_path)
    data = json.loads(manifest.read_text())
    require(data.get("schema") == "thor-package-tools-v1", "Tool manifest schema")
    python = Path(data["python"]["path"])
    python = (root / python).resolve()
    require(python.is_file() and sha(python) == data["python"]["sha256"],
            "Pinned Python identity")
    version_run = subprocess.run(
        [str(python), "-E", "-s", "-c",
         "import json,sys;print(json.dumps(list(sys.version_info[:3])))"],
        capture_output=True, text=True, check=True, timeout=15)
    version = json.loads(version_run.stdout)
    require(version == data["python"]["version"] and version[:2] >= [3, 11],
            "Pinned Python version")
    scripts = {}
    records = {}
    for key in TOOL_KEYS:
        entry = data["scripts"][key]
        path = rooted(root, Path(entry["path"]))
        require(path.is_file() and sha(path) == entry["sha256"],
                "Pinned packaging script: " + key)
        scripts[key] = path
        records[key] = {"path": str(path), "sha256": entry["sha256"]}
    require(scripts["gki_certificate"] ==
            scripts["mkbootimg"].parent / "gki/generate_gki_certificate.py",
            "Pinned GKI import must be beside mkbootimg")
    require(scripts["inspect_thor_fdt"] ==
            scripts["image_verifier"].parent / "inspect_thor_fdt.py",
            "Pinned FDT import must be beside image verifier")
    gki_init = scripts["gki_certificate"].parent / "__init__.py"
    require(not gki_init.exists(), "Unpinned GKI package initializer")
    metadata = {"manifest_sha256": sha(manifest),
                "python": {"path": str(python), "sha256": sha(python),
                           "version": version}, "scripts": records}
    return python, scripts, metadata


def package_commands(python, scripts, out):
    def run(key):
        return [str(python), "-E", "-s", str(scripts[key])]
    image = out / "thor-boot.img"
    return [
        run("mkbootimg") + [
            "--header_version", "4", "--kernel", str(out / "kernel.gz-dtb"),
            "--ramdisk", str(out / "kernel-bundle.bin"),
            "--os_version", "12.0.0", "--os_patch_level", "2024-09",
            "--cmdline", "", "-o", str(image)],
        run("image_verifier") + [
            str(image), "--expected-header-version", "4",
            "--kernel-format", "gzip-bootshim-fd-dtb",
            "--shim", str(out / "BootShim.bin"),
            "--fd", str(out / "SILICIUM_UEFI.fd"),
            "--dtb", str(out / "thor-stock-vendor-dtb.bin"),
            "--ramdisk", str(out / "kernel-bundle.bin"),
            "--fd-base", "0xA7100000", "--fd-size", "0x300000",
            "--max-image-size", "201326592",
            "--strict-padding", "--require-exact-size"],
        run("unpack_bootimg") + [
            "--boot_img", str(image), "--out", str(out / "unpacked")],
    ]


@contextmanager
def private_output(out, timestamp):
    """No overwrite/retry; every interrupted package stays quarantined."""
    out.mkdir(parents=True, mode=0o700, exist_ok=False)
    try:
        yield out
    except BaseException:
        with (out / "INVALID-NOT-FOR-PHONE").open("x") as stream:
            stream.write(timestamp + "\n")
        raise


COMPILE_EQUIVALENCE_NONBUILD_FILES = {
    "scripts/audit-thor-mem03-linked.py",
    "tests/test_thor_mem03_fb_contract.py",
}
MEM04_NONBUILD_FILES = {
    "scripts/audit-thor-mem04-linked.py",
    "tests/test_thor_mem04_telemetry.py",
    "scripts/audit-thor-mem04-pmap.py",
    "tests/test_thor_mem04_pmap.py",
}
ARTIFACT_PROFILES = {
    "thor-mem03-build-manifest-v1": {
        "tag": "MEM03", "allowed": COMPILE_EQUIVALENCE_NONBUILD_FILES,
        "proof": "mem03-fb-contract-machinecode-final.json",
        "lock_key": "mem03_coverage_machinecheck_sha256",
    },
    "thor-mem04-build-manifest-v1": {
        "tag": "MEM04", "allowed": MEM04_NONBUILD_FILES,
        "proof": "mem04-grid-pmap-machinecode-final.json",
        "lock_key": "mem04_machinecheck_sha256",
        "proof_schema": "thor-mem04-grid-pmap-machinecode-v1",
        "proof_result": "PASS_MEM04_LINKED_READABLE_GRID_BOUNDED_NATIVE_PMAP",
    },
}
RELEASE_PROFILES = {
    "thor-mem03-fdt-detail-and-xnu-fb-coverage-v1": {
        "tag": "MEM03", "prefix": "mem03",
        "proof_schema": "thor-mem03-snapshot-detail-host-proof-v1",
        "proof_result": "PASS_MEM03_FDT_DETAIL_AND_FINAL_OVERLAY",
        "ui_schema": "thor-mem03-UEFI-HUD-grid-linked-proof-v1",
        "ui_result": "PASS_LINKED_MEM03_AND_RESERVED_GOP_Y0_319",
    },
    "thor-mem04-stage-safe-hud-and-pmap-v1": {
        "tag": "MEM04", "prefix": "mem04",
        "proof_schema": "thor-mem04-stage-safe-hud-host-proof-v1",
        "proof_result": "PASS_MEM04_STAGE_SAFE_HUD_AND_PMAP_BINDING",
        "ui_schema": "thor-mem04-UEFI-HUD-grid-linked-proof-v1",
        "ui_result": "PASS_LINKED_MEM04_AND_RESERVED_GOP_Y0_319",
    },
}


def validate_mem03_artifact(root, candidate):
    """Verify both honest freeze schemas and real source equivalence pre-seed."""
    lock_path = candidate / "manifest.json"
    build_path = candidate / "build-manifest.json"
    layout_path = candidate / "kernel-layout.json"
    lock = json.loads(lock_path.read_text())
    build = json.loads(build_path.read_text())
    layout = json.loads(layout_path.read_text())
    require(lock.get("schema") == "thor-artifact-lock-v2",
            "Artifact lock schema")
    profile = ARTIFACT_PROFILES.get(build.get("schema"))
    require(profile is not None, "MEM03/MEM04 build manifest schema")
    for key in ("kernel_sha256", "kernel_bytes", "loaded_span_bytes",
                "loaded_span_sha256"):
        require(key in lock and key in build and key in layout and
                lock[key] == build[key] == layout[key],
                "Artifact/build/layout agreement: " + key)
    final = lock.get("xnu_commit")
    actual = lock.get("build_source_commit")
    import re
    require(isinstance(final, str) and re.fullmatch(r"[0-9a-f]{40}", final)
            and isinstance(actual, str) and re.fullmatch(r"[0-9a-f]{40}", actual),
            "Artifact source revision fields")
    require(final == lock.get("audit_and_test_commit") == build.get("final_HEAD")
            and actual == build.get("actual_build_source_commit"),
            "Actual build and audit source revisions")
    require(lock.get("compiled_source_content_equivalent_to_final_HEAD") is True
            and build.get("compiled_source_content_equivalent_to_final_HEAD") is True
            and build.get("working_tree_clean") is True,
            "Compiled-source equivalence declarations")
    changes = lock.get("post_build_changes")
    require(isinstance(changes, list) and all(isinstance(x, str) for x in changes)
            and len(changes) == len(set(changes))
            and sorted(changes) == sorted(build.get("post_build_changes", []))
            and set(changes) <= profile["allowed"],
            "Only reviewed audit/test files may change after compilation")
    tree = rooted(root, Path(build["tree"]))
    def git(*arguments):
        return subprocess.check_output(
            ["git", "-C", str(tree), *arguments], text=True).strip()
    require(git("rev-parse", "HEAD") == final, "Live XNU source HEAD")
    require(not git("status", "--porcelain"), "Live XNU source unclean")
    require(sorted(git("diff", "--name-only", actual, final).splitlines()) ==
            sorted(changes), "Live build-to-audit source equivalence")
    require(build.get("layout_sha256") == sha(layout_path),
            "Build receipt layout identity")
    for file, lock_key, build_key in (
            ("machinecode.json", "machinecheck_sha256", "machinecode_sha256"),
            (profile["proof"], profile["lock_key"],
             "coverage_machinecode_sha256")):
        require(lock.get(lock_key) == build.get(build_key) ==
                sha(candidate / file), "Pinned build proof: " + file)
    require(lock.get("L2_linked_and_audited") is True,
            "Artifact must be linked and audited")
    if profile["tag"] == "MEM04":
        linked = json.loads((candidate / profile["proof"]).read_text())
        require(linked.get("schema") == profile["proof_schema"] and
                linked.get("result") == profile["proof_result"] and
                linked.get("frontier") == "pmap-return",
                "MEM04 linked pmap proof schema/result/frontier")
        require(linked.get("kernel_sha256") == lock["kernel_sha256"],
                "MEM04 linked pmap proof kernel identity")
        checkpoints = linked.get("checkpoint_contract", {})
        require(exact_stage_list(checkpoints.get("compiled_stage_prefix"), list(range(14)))
                and exact_stage_list(checkpoints.get("reserved_slots"), [14, 15])
                and type(checkpoints.get("number_base")) is int
                and checkpoints["number_base"] == 16,
                "MEM04 linked pmap proof fourteen numbered stages")
        for key in ("native_pmap_bootstrap_call_compiled", "bounded_after_pmap_return"):
            require(lock.get(key) is True and linked.get(key) is True,
                    "MEM04 compiled bounded pmap contract: " + key)
        for key in ("pmap_bootstrap_executed", "arm_vm_init_returned", "hardware_tested"):
            require(lock.get(key) is False and linked.get(key) is False,
                    "MEM04 unexecuted runtime scope: " + key)
    # This tag exists only in the validated in-memory copy. Never rewrite the
    # immutable artifact lock, and never infer profile from caller-supplied flags.
    return dict(lock, _validated_packaging_profile=profile["tag"],
                _validated_candidate_directory=str(candidate)), build, layout


def validate_mem03_release(root, work, receipt_path, audit_path, lock):
    """Check actual MEM03 release/proof/source contracts before a seed exists."""
    receipt = json.loads(receipt_path.read_text())
    audit = json.loads(audit_path.read_text())
    profile = RELEASE_PROFILES.get(receipt.get("schema"))
    require(profile is not None,
            "Required MEM03 firmware release schema or recognized MEM04 firmware release schema")
    prefix = profile["prefix"]
    proof_hash_key = prefix + "_uefi_proof_sha256"
    required = ("schema", "uefi_commit", "uefi_fd_sha256", "uefi_fd_bytes",
                "kernel_sha256", "kernel_bytes", "kernel_source_commit",
                "loaded_span_bytes", "firmware_audit_sha256",
                "firmware_audit_result", "linked_transition_sha256",
                "actual_kernel_binding_sha256", "new_ui_source_sha256",
                "new_bds_source_sha256", "unchanged_fdt_validator_sha256",
                "unchanged_memorymap_source_sha256", "new_ui_layout_audit_sha256",
                proof_hash_key)
    require(all(key in receipt for key in required),
            "Required " + profile["tag"] + " firmware release fields")
    require(lock.get("_validated_packaging_profile") == profile["tag"],
            "Validated artifact/release profile mismatch")
    require(receipt.get("board") == "thor" and
            receipt["kernel_sha256"] == lock["kernel_sha256"] and
            receipt["kernel_bytes"] == lock["kernel_bytes"] and
            receipt["kernel_source_commit"] == lock["xnu_commit"] and
            receipt["loaded_span_bytes"] == lock["loaded_span_bytes"],
            "MEM03 release kernel/source/layout")
    require(receipt["firmware_audit_sha256"] == sha(audit_path) and
            receipt["firmware_audit_result"] == audit.get("status") == "PASS"
            and audit.get("failures") == [] and audit.get("schema") == 1
            and receipt["uefi_fd_sha256"] == audit["fd"]["sha256"]
            and receipt["uefi_fd_bytes"] == audit["fd"]["bytes"] == 3145728,
            "MEM03 complete firmware audit identity")
    for field, key, count in (("firmware_volume_count", "volumes", 2),
                              ("executable_modules", "actual_modules", 20),
                              ("linker_inputs", "unique_link_inputs", 96)):
        require(receipt.get(field) == len(audit.get(key, [])) == count,
                "MEM03 firmware structure: " + field)
    proof_path = receipt_path.parent / (prefix + "-uefi-proof.json")
    ui_path = receipt_path.parent / "ui-stage-grid-audit.json"
    require(receipt[proof_hash_key] == sha(proof_path) and
            receipt["new_ui_layout_audit_sha256"] == sha(ui_path),
            "MEM03 release proof hashes")
    proof, ui = json.loads(proof_path.read_text()), json.loads(ui_path.read_text())
    require(proof.get("schema") == profile["proof_schema"]
            and proof.get("result") == profile["proof_result"]
            and ui.get("schema") == profile["ui_schema"]
            and ui.get("result") == profile["ui_result"],
            "MEM03 proof schemas/results")
    require(proof["firmware"]["sha256"] == ui["fd_sha256"] ==
            receipt["uefi_fd_sha256"] and proof["kernel_sha256"] ==
            receipt["kernel_sha256"] and ui["grid_machinecode_sha256"] ==
            lock["machinecheck_sha256"] and proof["pe_sha256"] == ui["pe_sha256"],
            "MEM03 UEFI/XNU/PE proof identity")
    transition = "29ee4adbbb30fe06bcebf9f9af9723f98698c6170636b2bb24e52a7a17fe9d24"
    if prefix == "mem04":
        transition = validate_mem04_transition_binding(
            root, work, receipt_path, receipt, proof, lock)
    require(receipt["linked_transition_sha256"] ==
            proof["transition"]["sha256"] == transition and
            proof["transition"]["bytes"] == 500 and
            proof["transition"]["relocations"] ==
            receipt.get("transition_pe_relocations_in_interval") == 0 and
            receipt.get("transition_sha_matches_expected_header") is True,
            "MEM03 exact unchanged EBS transition")
    source_pins = proof["source_files_sha256"]
    require(len(source_pins) >= 15, "MEM03 source proof completeness")
    for relative, expected in source_pins.items():
        require(sha(rooted(work, Path(relative))) == expected,
                "MEM03 proof source: " + relative)
    adt = "Platforms/Xiaomi/thorPkg/Drivers/ThorEntryBdsDxe/"
    links = (("actual_kernel_binding_sha256", adt + "VM_POST_TTBR1_PROVENANCE.json"),
             ("new_ui_source_sha256", adt + "ThorReportHud.inc.c"),
             ("new_bds_source_sha256", adt + "ThorEntryBdsDxe.c"),
             ("unchanged_fdt_validator_sha256", adt + "thor_fdt_gate.c"),
             ("unchanged_memorymap_source_sha256",
              "Platforms/Xiaomi/thorPkg/Library/MemoryMapLib/MemoryMapLib.c"))
    for field, relative in links:
        require(receipt[field] == source_pins[relative],
                "MEM03 release source pin: " + field)
    require(ui["hud_sha256"] == receipt["new_ui_source_sha256"] and
            ui["bds_source_sha256"] == receipt["new_bds_source_sha256"],
            "MEM03 linked HUD source identity")
    tests = proof.get("tests")
    expected = {"mem01": 12, "mem02": 10, "mem03": 12}
    require(isinstance(tests, dict) and
            all(type(value) is int and value > 0 for value in tests.values()),
            profile["tag"] + " source regression counts")
    if prefix == "mem04":
        require(tests == dict(expected, mem04=11),
                "MEM04 retains baseline and eleven renderer regressions")
    else:
        require(tests == expected, "MEM03 baseline regression contracts")
    require(receipt.get("source_level_regressions_passed") == sum(tests.values()) and
            proof["test_results_sha256"] ==
            sha(receipt_path.parent / (prefix + "-source-test-results.json")),
            profile["tag"] + " source regression identity")
    require(all(proof.get("policy_unchanged", {}).get(key) is True
                for key in ("fdt_gate", "fdt_gate_header", "memory_map",
                            "payload_read_permit")),
            "MEM03 existing memory policy retained")
    require(all(receipt.get(key) is True for key in
                ("no_write_to_candidate_windows", "no_memory_grant_or_heap_extension",
                 "gop_rows_for_all_five", "UEFI_reserved_top_y0_through_319",
                 "pre_linux_FDT_validation_only", "temporary_boot_only")) and
            all(receipt.get(key) is False for key in
                ("actual_UEFI_GetMemoryMap_contiguous_256MiB_verified",
                 "pmap_bootstrap_executed", "arm_vm_init_returned",
                 "hardware_tested", "xnu_runtime_qualified")) and
            receipt.get("phone_actions") == proof.get("phone_actions") ==
            ui.get("phone_actions") == 0,
            "MEM03 honest unexecuted bounded scope")
    require(proof.get("new_allocatable_memory_pages") == 0 and
            proof.get("memory_ownership_proven") is False and
            proof.get("hardware_tested") is False and
            proof.get("same_immutable_snapshot") is True and
            proof.get("per_window_dynamic_count") is True and
            proof.get("final_postclear_overlay") is True and
            proof.get("status_and_padding_reported") is True and
            ui.get("gop_y_reserved") == [0, 319] and
            ui.get("uefi_first_postclear_draw_y", 0) >= 320 and
            ui.get("never_calls_GOP_after_EBS") is True and
            ui.get("last_fullscreen_clear_followed_by_five_FDT_rows") is True,
            "MEM03 diagnostic/ownership boundary")
    if prefix == "mem04":
        require(lock.get("native_pmap_bootstrap_call_compiled") is True and
                lock.get("bounded_after_pmap_return") is True,
                "MEM04 release requires bounded pmap artifact")
        render_path = receipt_path.parent / "stage-render-audit.json"
        require(receipt.get("stage_render_audit_sha256") == sha(render_path),
                "MEM04 stage render audit identity")
        render = json.loads(render_path.read_text())
        require(render.get("schema") == "thor-mem04-executed-stage-render-event-audit-v1"
                and render.get("result") == "PASS_ALL_STAGE_FRAMES_NO_OVERLAP"
                and render.get("unexpected_overlap_count") == 0
                and render.get("text_bounds_valid") is True
                and render.get("xnu_reserved_preserved") is True,
                "MEM04 all-stage renderer nonoverlap and bounds")
        frames = render.get("frames")
        require(isinstance(frames, list) and len(frames) >= 6,
                "MEM04 executed renderer frame coverage")
        require(render.get("renderer_source_sha256") == receipt["new_bds_source_sha256"]
                and render.get("hud_source_sha256") == receipt["new_ui_source_sha256"],
                "MEM04 executed renderer source identity")
        validate_mem04_render_trace(work, render_path, render)
        checkpoints = ui.get("checkpoint_contract", {})
        candidate = rooted(root, Path(lock["_validated_candidate_directory"]))
        linked_path = candidate / ARTIFACT_PROFILES["thor-mem04-build-manifest-v1"]["proof"]
        require(sha(linked_path) == lock["mem04_machinecheck_sha256"],
                "MEM04 release numbered checkpoint proof identity")
        linked = json.loads(linked_path.read_text())
        require(exact_stage_list(checkpoints.get("compiled_stage_prefix"), list(range(14)))
                and exact_stage_list(checkpoints.get("reserved_slots"), [14, 15])
                and type(checkpoints.get("number_base")) is int
                and checkpoints["number_base"] == 16
                and checkpoints == linked.get("checkpoint_contract")
                and ui.get("native_pmap_bootstrap_call_compiled") is True
                and ui.get("bounded_after_pmap_return") is True,
                "MEM04 pmap checkpoint scope is fourteen bounded stages")
    return receipt, audit


def exact_stage_list(value, expected):
    return (isinstance(value, list) and
            all(type(item) is int for item in value) and value == expected)


def validate_mem04_render_trace(work, render_path, render):
    """Bind frame summaries to the executed host trace and recheck its geometry."""
    trace_path = render_path.parent / "stage-render-events.jsonl"
    harness_path = render_path.parent / "stage-render-harness.c"
    require(render.get("host_trace_sha256") == sha(trace_path) and
            render.get("host_harness_sha256") == sha(harness_path),
            "MEM04 executed renderer trace/harness identity")
    test_path = work / "Platforms/Xiaomi/thorPkg/Tests/test_mem04_stage_render.py"
    require(render.get("test_source_sha256") == sha(test_path),
            "MEM04 executed renderer test source identity")
    pins = render.get("source_files_sha256")
    require(isinstance(pins, dict) and len(pins) >= 13,
            "MEM04 executed renderer source coverage")
    for relative, expected in pins.items():
        require(sha(rooted(work, Path(relative))) == expected,
                "MEM04 executed renderer pinned source: " + relative)
    require(render.get("render_functions_from_production_C") is True and
            render.get("font_pixels_executed") is True and
            render.get("host_service_and_loader_state_stubs") is True and
            render.get("xnu_y_reserved") == [0, 319],
            "MEM04 honest executed renderer scope")
    events = [json.loads(line) for line in trace_path.read_text().splitlines()
              if line.strip()]
    require(events and type(render.get("event_count")) is int and
            render["event_count"] == len(events),
            "MEM04 nonempty executed renderer event trace")
    groups, active = {}, None
    for event in events:
        number, kind = event.get("frame"), event.get("kind")
        require(type(number) is int and number > 0,
                "MEM04 renderer frame ID")
        if kind == "begin":
            require(active is None and number not in groups,
                    "MEM04 renderer frame ordering")
            active = number
            groups[number] = []
        require(active == number, "MEM04 renderer event outside active frame")
        groups[number].append(event)
        if kind == "pixels":
            active = None
    require(active is None, "MEM04 incomplete renderer frame")
    frames = render["frames"]
    require(type(render.get("frame_count")) is int and
            render["frame_count"] == len(frames) == len(groups) and
            exact_stage_list(list(groups), list(range(1, len(groups) + 1))),
            "MEM04 renderer frame-count identity")
    essential = {"initial", "read_permit", "kernel_hash_pass",
                 "kernel_hash_refused", "loader_handoff_ready", "loader_error_max_values"}
    require(essential <= {row.get("name") for row in frames},
            "MEM04 required renderer stages")
    def rectangle(event):
        value = event.get("rect")
        require(isinstance(value, list) and len(value) == 4 and
                all(type(item) is int for item in value),
                "MEM04 renderer rectangle shape")
        x, y, width, height = value
        require(x >= 0 and y >= 320 and width >= 0 and height >= 0 and
                x + width <= 1440 and y + height <= 3200,
                "MEM04 renderer text/fill bounds")
        return value
    def overlap(left, right):
        x, y, w, h = left
        xx, yy, ww, hh = right
        return min(x + w, xx + ww) > max(x, xx) and min(y + h, yy + hh) > max(y, yy)
    for index, frame in enumerate(frames, 1):
        require(type(frame.get("id")) is int and frame["id"] == index,
                "MEM04 renderer frame summary ID")
        rows = groups[index]
        require(len(rows) >= 4 and rows[0].get("kind") == "begin" and
                rows[0].get("name") == frame.get("name") and
                rows[1].get("kind") == "clear" and
                rows[1].get("rect") == [0, 0, 1440, 3200] and
                sum(row.get("kind") == "clear" for row in rows) == 1 and
                rows[-1].get("kind") == "pixels",
                "MEM04 renderer full-frame clear and completion")
        pixels = rows[-1]
        require(type(frame.get("event_count")) is int and
                frame["event_count"] == len(rows) and
                type(frame.get("texts")) is int and frame["texts"] > 0 and
                frame["texts"] == sum(row.get("kind") == "text" for row in rows) and
                type(frame.get("glyph_pixels")) is int and frame["glyph_pixels"] > 0 and
                frame["glyph_pixels"] == pixels.get("glyph_pixels") and
                frame.get("forbidden_glyphs") == pixels.get("forbidden_glyphs") == 0,
                "MEM04 renderer summary/pixel identity")
        live_text = []
        for row in rows[2:-1]:
            kind = row.get("kind")
            require(kind in ("text", "fill"), "MEM04 renderer event kind")
            rect = rectangle(row)
            if kind == "text":
                require(isinstance(row.get("text"), str), "MEM04 renderer text value")
                require(not any(overlap(rect, old) for old in live_text),
                        "MEM04 executed text overlap")
                if row["text"]:
                    live_text.append(rect)
            else:
                require(not any(overlap(rect, old) for old in live_text),
                        "MEM04 renderer fill erases live text")


MEM03_TRANSITION_PIN = "29ee4adbbb30fe06bcebf9f9af9723f98698c6170636b2bb24e52a7a17fe9d24"
MEM04_TRANSITION_PIN = "8ce4b52c64f2436845be90affac50ed0fe86f5f4b8072780ba8ffe1492962f36"


def validate_mem04_transition_words(old, new, layout):
    """Three reviewed x8 MOV-wide immediates; every other byte is immutable."""
    import struct
    require(len(old) == len(new) == 500, "MEM04 transition raw length")
    changed = [offset for offset in range(0, 500, 4)
               if old[offset:offset + 4] != new[offset:offset + 4]]
    require(changed == [116, 136, 140], "MEM04 transition changed word set")
    mask = 0xffff << 5
    def immediate(blob, offset, opcode, shift):
        word = struct.unpack_from("<I", blob, offset)[0]
        require(word & 0xff800000 == opcode and word & 31 == 8
                and (word >> 21) & 3 == shift,
                "MEM04 transition opcode/register/shift")
        return (word >> 5) & 0xffff
    for offset in changed:
        left, right = struct.unpack_from("<I", old, offset)[0], struct.unpack_from("<I", new, offset)[0]
        require(left & ~mask == right & ~mask,
                "MEM04 transition non-immediate bits changed")
    old_values = [immediate(old, at, op, shift) for at, op, shift in
                  [(116, 0xd2800000, 0), (136, 0xd2800000, 0), (140, 0xf2800000, 1)]]
    new_values = [immediate(new, at, op, shift) for at, op, shift in
                  [(116, 0xd2800000, 0), (136, 0xd2800000, 0), (140, 0xf2800000, 1)]]
    require(old_values == [0xd488, 0xe000, 0x3f] and
            new_values == [0xf488, 0, 0x40], "MEM04 transition immediate values")
    old_high = immediate(old, 120, 0xf2800000, 1)
    new_high = immediate(new, 120, 0xf2800000, 1)
    require(old_high == new_high == 0x3f, "MEM04 transition entry high immediate")
    require(layout.get("entry_offset") == new_values[0] | (new_high << 16) and
            layout.get("vectors_offset") == new_values[1] | (new_values[2] << 16) and
            layout.get("entry_sync_offset") == 0x3ff000 and
            layout.get("entry_sync_bytes") == 0x3000,
            "MEM04 transition actual kernel layout offsets")
    return {"old_decoded_entry_offset": old_values[0] | (old_high << 16),
            "new_decoded_entry_offset": new_values[0] | (new_high << 16),
            "old_decoded_vector_offset": old_values[1] | (old_values[2] << 16),
            "new_decoded_vector_offset": new_values[1] | (new_values[2] << 16)}


def validate_mem04_transition_binding(root, work, receipt_path, receipt, uefi_proof, lock):
    folder = receipt_path.parent / "postlink-transition"
    audit_path = folder / "mem04-transition-binding.json"
    require(audit_path.is_file(), "MEM04 transition semantic proof missing")
    require(receipt.get("transition_binding_audit_sha256") ==
            uefi_proof.get("transition_binding_audit_sha256") == sha(audit_path),
            "MEM04 transition semantic proof identity")
    audit = json.loads(audit_path.read_text())
    require(audit.get("schema") == "thor-mem04-transition-entry-vector-immediate-proof-v1"
            and audit.get("result") == "PASS_ONLY_ACTUAL_ENTRY_VECTOR_IMMEDIATES_CHANGED"
            and audit.get("code_bytes") == 500 and audit.get("relocations") == 0
            and exact_stage_list(audit.get("changed_instruction_word_offsets"), [116, 136, 140])
            and audit.get("unchanged_bytes") == 488
            and audit.get("opcode_register_branches_unchanged") is True
            and audit.get("phone_actions") == 0,
            "MEM04 transition semantic proof contract")
    old_path, new_path = folder / "old-transition.bin", folder / "new-transition.bin"
    require(old_path.is_file() and new_path.is_file(), "MEM04 transition raw files missing")
    require(audit.get("old_code_sha256") == sha(old_path) == MEM03_TRANSITION_PIN and
            audit.get("new_code_sha256") == sha(new_path) == MEM04_TRANSITION_PIN,
            "MEM04 transition raw pins")
    candidate = rooted(root, Path(lock["_validated_candidate_directory"]))
    layout_path = candidate / "kernel-layout.json"
    layout = json.loads(layout_path.read_text())
    require(audit.get("kernel_layout_sha256") == sha(layout_path) and
            audit.get("kernel_sha256") == layout.get("kernel_sha256") == lock["kernel_sha256"],
            "MEM04 transition actual kernel layout identity")
    decoded = validate_mem04_transition_words(old_path.read_bytes(), new_path.read_bytes(), layout)
    require(all(audit.get(key) == value for key, value in decoded.items()),
            "MEM04 transition decoded offsets disagree with proof")
    adt = work / "Platforms/Xiaomi/thorPkg/Drivers/ThorEntryBdsDxe"
    for field, name in (("unchanged_assembly_source_sha256", "thor_entry06_arch.S"),
                        ("new_expected_header_sha256", "ThorEntry06TransitionExpected.h")):
        require(audit.get(field) == sha(adt / name), "MEM04 transition source pin: " + name)
    import re
    header = (adt / "ThorEntry06TransitionExpected.h").read_text()
    values = re.search(r"mEntry06TransitionSha256\[32\]\s*=\s*\{([^}]+)\}", header)
    require(values is not None and
            re.search(r"#define\s+THOR_ENTRY06_TRANSITION_BYTES\s+500u", header) is not None,
            "MEM04 transition expected header structure")
    expected = bytes(int(value, 16) for value in
                     re.findall(r"0x([0-9a-fA-F]{1,2})", values.group(1)))
    require(len(expected) == 32 and expected.hex() == MEM04_TRANSITION_PIN,
            "MEM04 transition expected header pin")
    return MEM04_TRANSITION_PIN
