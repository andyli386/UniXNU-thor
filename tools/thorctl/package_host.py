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
    require(build.get("schema") == "thor-mem03-build-manifest-v1",
            "MEM03 build manifest schema")
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
            and set(changes) <= COMPILE_EQUIVALENCE_NONBUILD_FILES,
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
            ("mem03-fb-contract-machinecode-final.json", "mem03_coverage_machinecheck_sha256",
             "coverage_machinecode_sha256")):
        require(lock.get(lock_key) == build.get(build_key) ==
                sha(candidate / file), "Pinned build proof: " + file)
    require(lock.get("L2_linked_and_audited") is True,
            "Artifact must be linked and audited")
    return lock, build, layout


def validate_mem03_release(root, work, receipt_path, audit_path, lock):
    """Check actual MEM03 release/proof/source contracts before a seed exists."""
    receipt = json.loads(receipt_path.read_text())
    audit = json.loads(audit_path.read_text())
    required = ("schema", "uefi_commit", "uefi_fd_sha256", "uefi_fd_bytes",
                "kernel_sha256", "kernel_bytes", "kernel_source_commit",
                "loaded_span_bytes", "firmware_audit_sha256",
                "firmware_audit_result", "linked_transition_sha256",
                "actual_kernel_binding_sha256", "new_ui_source_sha256",
                "new_bds_source_sha256", "unchanged_fdt_validator_sha256",
                "unchanged_memorymap_source_sha256", "new_ui_layout_audit_sha256",
                "mem03_uefi_proof_sha256")
    require(all(key in receipt for key in required),
            "Required MEM03 firmware release fields")
    require(receipt["schema"] == "thor-mem03-fdt-detail-and-xnu-fb-coverage-v1",
            "MEM03 firmware release schema")
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
    proof_path = receipt_path.parent / "mem03-uefi-proof.json"
    ui_path = receipt_path.parent / "ui-stage-grid-audit.json"
    require(receipt["mem03_uefi_proof_sha256"] == sha(proof_path) and
            receipt["new_ui_layout_audit_sha256"] == sha(ui_path),
            "MEM03 release proof hashes")
    proof, ui = json.loads(proof_path.read_text()), json.loads(ui_path.read_text())
    require(proof.get("schema") == "thor-mem03-snapshot-detail-host-proof-v1"
            and proof.get("result") == "PASS_MEM03_FDT_DETAIL_AND_FINAL_OVERLAY"
            and ui.get("schema") == "thor-mem03-UEFI-HUD-grid-linked-proof-v1"
            and ui.get("result") == "PASS_LINKED_MEM03_AND_RESERVED_GOP_Y0_319",
            "MEM03 proof schemas/results")
    require(proof["firmware"]["sha256"] == ui["fd_sha256"] ==
            receipt["uefi_fd_sha256"] and proof["kernel_sha256"] ==
            receipt["kernel_sha256"] and ui["grid_machinecode_sha256"] ==
            lock["machinecheck_sha256"] and proof["pe_sha256"] == ui["pe_sha256"],
            "MEM03 UEFI/XNU/PE proof identity")
    transition = "29ee4adbbb30fe06bcebf9f9af9723f98698c6170636b2bb24e52a7a17fe9d24"
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
    require(proof.get("tests") == {"mem01": 12, "mem02": 10, "mem03": 12} and
            receipt.get("source_level_regressions_passed") == 34 and
            proof["test_results_sha256"] ==
            sha(receipt_path.parent / "mem03-source-test-results.json"),
            "MEM03 source regression identity")
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
    return receipt, audit
