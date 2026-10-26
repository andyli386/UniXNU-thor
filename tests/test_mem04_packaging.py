#!/usr/bin/env python3
"""MEM04 pre-seed profile and renderer regressions; no real entropy or boot image."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("mem03_fixture", HERE / "test_mem03_packaging.py")
old = importlib.util.module_from_spec(spec)
spec.loader.exec_module(old)
pack, host = old.pack, old.host

NAMES = ["initial", "read_permit", "kernel_hash_pass", "kernel_hash_refused",
         "loader_handoff_ready", "loader_error_max_values"]


class ProfileFixture:
    """Reuse fixture construction only, without re-running/inheriting 31 old tests."""
    def __init__(self, tag="MEM04"):
        self.base = old.PreflightOrderTests(
            "test_dry_run_creates_nothing_and_never_requests_entropy")
        self.base.setUp()
        self.root, self.work, self.candidate = self.base.root, self.base.source, self.base.candidate
        self.tag, self.prefix = tag, tag.lower()
        self.lock = json.loads((self.candidate / "manifest.json").read_text())
        self.build = json.loads((self.candidate / "build-manifest.json").read_text())
        self.linked = {
            "schema": "thor-mem04-grid-pmap-machinecode-v1",
            "result": "PASS_MEM04_LINKED_READABLE_GRID_BOUNDED_NATIVE_PMAP",
            "frontier": "pmap-return", "kernel_sha256": self.base.kernel_pin,
            "native_pmap_bootstrap_call_compiled": True, "bounded_after_pmap_return": True,
            "pmap_bootstrap_executed": False, "arm_vm_init_returned": False,
            "hardware_tested": False, "phone_actions": 0,
            "checkpoint_contract": {"compiled_stage_prefix": list(range(14)),
                                    "reserved_slots": [14, 15], "number_base": 16}}
        if tag == "MEM04":
            self.build["schema"] = "thor-mem04-build-manifest-v1"
            self.lock.pop("mem03_coverage_machinecheck_sha256")
            self.lock.update(native_pmap_bootstrap_call_compiled=True,
                             bounded_after_pmap_return=True, pmap_bootstrap_executed=False,
                             arm_vm_init_returned=False, hardware_tested=False)
        self.artifact_profile = host.ARTIFACT_PROFILES[self.build["schema"]]
        self.sync_artifact()
        self.profile = next(value for value in host.RELEASE_PROFILES.values()
                            if value["tag"] == tag)
        self.release_schema = next(key for key, value in host.RELEASE_PROFILES.items()
                                   if value["tag"] == tag)
        self.adt = self.work / "Platforms/Xiaomi/thorPkg/Drivers/ThorEntryBdsDxe"
        relative = [
            "Platforms/Xiaomi/thorPkg/Drivers/ThorEntryBdsDxe/" + name
            for name in ("ThorEntryBdsDxe.c", "ThorReportHud.inc.c",
                         "VM_POST_TTBR1_PROVENANCE.json", "ThorEntry06PayloadExpected.h",
                         "thor_fdt_gate.c", "thor_fdt_gate.h", "thor_payload_read_permit.c",
                         "thor_payload_read_permit.h", "thor_payload04.c", "thor_payload04.h",
                         "thor_payload09.h", "thor_entry06_uefi_adapter.c",
                         "thor_entry06_uefi_adapter.h", "thor_mem03_detail.h")]
        relative += ["Platforms/Xiaomi/thorPkg/Library/MemoryMapLib/MemoryMapLib.c",
                     "Platforms/Xiaomi/thorPkg/Tests/test_mem04_stage_render.py"]
        for item in relative:
            path = self.work / item
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text("unit source fixture: " + item)
        (self.adt / "VM_POST_TTBR1_PROVENANCE.json").write_bytes(
            (self.adt / "provenance.json").read_bytes())
        pins = {item: host.sha(self.work / item) for item in relative}
        adt_prefix = "Platforms/Xiaomi/thorPkg/Drivers/ThorEntryBdsDxe/"
        self.receipt_path, self.audit_path = self.root / "release.json", self.root / "audit.json"
        self.proof_path = self.root / (self.prefix + "-uefi-proof.json")
        self.ui_path, self.render_path = self.root / "ui-stage-grid-audit.json", self.root / "stage-render-audit.json"
        self.trace_path, self.harness_path = self.root / "stage-render-events.jsonl", self.root / "stage-render-harness.c"
        self.harness_path.write_text("unit-only host harness; not compiled or phone eligible")
        self.events, frames = [], []
        for number, name in enumerate(NAMES, 1):
            rows = [{"frame": number, "kind": "begin", "name": name},
                    {"frame": number, "kind": "clear", "rect": [0, 0, 1440, 3200]},
                    {"frame": number, "kind": "text", "rect": [60, 352, 100, 14],
                     "text": "unit trace", "scale": 2},
                    {"frame": number, "kind": "pixels", "glyph_pixels": 42,
                     "forbidden_glyphs": 0}]
            self.events.extend(rows)
            frames.append({"id": number, "name": name, "event_count": 4,
                           "texts": 1, "glyph_pixels": 42, "forbidden_glyphs": 0})
        self.render = {
            "schema": "thor-mem04-executed-stage-render-event-audit-v1",
            "result": "PASS_ALL_STAGE_FRAMES_NO_OVERLAP",
            "renderer_source_sha256": pins[adt_prefix + "ThorEntryBdsDxe.c"],
            "hud_source_sha256": pins[adt_prefix + "ThorReportHud.inc.c"],
            "test_source_sha256": pins["Platforms/Xiaomi/thorPkg/Tests/test_mem04_stage_render.py"],
            "host_harness_sha256": host.sha(self.harness_path),
            "source_files_sha256": {key: pins[key] for key in relative[:13]},
            "frames": frames, "frame_count": 6, "event_count": len(self.events),
            "unexpected_overlap_count": 0, "text_bounds_valid": True,
            "xnu_reserved_preserved": True, "xnu_y_reserved": [0, 319],
            "render_functions_from_production_C": True, "font_pixels_executed": True,
            "host_service_and_loader_state_stubs": True, "phone_actions": 0}
        self.ui = {
            "schema": self.profile["ui_schema"], "result": self.profile["ui_result"],
            "hud_sha256": pins[adt_prefix + "ThorReportHud.inc.c"],
            "bds_source_sha256": pins[adt_prefix + "ThorEntryBdsDxe.c"],
            "fd_sha256": self.base.fd_pin, "pe_sha256": "unit-pe",
            "grid_machinecode_sha256": self.lock["machinecheck_sha256"],
            "gop_y_reserved": [0, 319], "uefi_first_postclear_draw_y": 352,
            "never_calls_GOP_after_EBS": True,
            "last_fullscreen_clear_followed_by_five_FDT_rows": True,
            "completed_stage_slots": list(range(14)), "reserved_stage_slots": [14, 15],
            "native_pmap_bootstrap_call_compiled": True, "bounded_after_pmap_return": True,
            "phone_actions": 0}
        tests = {"mem01": 12, "mem02": 10, "mem03": 12}
        if tag == "MEM04":
            tests["mem04"] = 11
        test_result_path = self.root / (self.prefix + "-source-test-results.json")
        test_result_path.write_text('{"unit_fixture_only":true}\n')
        self.proof = {
            "schema": self.profile["proof_schema"], "result": self.profile["proof_result"],
            "firmware": {"sha256": self.base.fd_pin}, "kernel_sha256": self.base.kernel_pin,
            "pe_sha256": "unit-pe", "transition": {
                "sha256": "29ee4adbbb30fe06bcebf9f9af9723f98698c6170636b2bb24e52a7a17fe9d24",
                "bytes": 500, "relocations": 0},
            "source_files_sha256": pins, "tests": tests,
            "test_results_sha256": host.sha(test_result_path),
            "policy_unchanged": dict.fromkeys(
                ["fdt_gate", "fdt_gate_header", "memory_map", "payload_read_permit"], True),
            "same_immutable_snapshot": True, "per_window_dynamic_count": True,
            "final_postclear_overlay": True, "status_and_padding_reported": True,
            "new_allocatable_memory_pages": 0, "memory_ownership_proven": False,
            "hardware_tested": False, "phone_actions": 0}
        audit = {"schema": 1, "status": "PASS", "failures": [],
                 "fd": {"sha256": self.base.fd_pin, "bytes": 3145728},
                 "volumes": [None] * 2, "actual_modules": [None] * 20,
                 "unique_link_inputs": [None] * 96}
        self.audit_path.write_text(json.dumps(audit))
        self.receipt = {
            "schema": self.release_schema, "board": "thor", "uefi_commit": "uefi-test",
            "uefi_fd_sha256": self.base.fd_pin, "uefi_fd_bytes": 3145728,
            "kernel_sha256": self.base.kernel_pin, "kernel_bytes": len(b"unit-test-kernel"),
            "kernel_source_commit": "a" * 40, "loaded_span_bytes": 4096,
            "firmware_audit_sha256": host.sha(self.audit_path), "firmware_audit_result": "PASS",
            "firmware_volume_count": 2, "executable_modules": 20, "linker_inputs": 96,
            "linked_transition_sha256": self.proof["transition"]["sha256"],
            "transition_pe_relocations_in_interval": 0, "transition_sha_matches_expected_header": True,
            "actual_kernel_binding_sha256": pins[adt_prefix + "VM_POST_TTBR1_PROVENANCE.json"],
            "new_ui_source_sha256": pins[adt_prefix + "ThorReportHud.inc.c"],
            "new_bds_source_sha256": pins[adt_prefix + "ThorEntryBdsDxe.c"],
            "unchanged_fdt_validator_sha256": pins[adt_prefix + "thor_fdt_gate.c"],
            "unchanged_memorymap_source_sha256":
                pins["Platforms/Xiaomi/thorPkg/Library/MemoryMapLib/MemoryMapLib.c"],
            "source_level_regressions_passed": sum(tests.values()), "phone_actions": 0}
        self.receipt.update(dict.fromkeys(
            ["no_write_to_candidate_windows", "no_memory_grant_or_heap_extension",
             "gop_rows_for_all_five", "UEFI_reserved_top_y0_through_319",
             "pre_linux_FDT_validation_only", "temporary_boot_only"], True))
        self.receipt.update(dict.fromkeys(
            ["actual_UEFI_GetMemoryMap_contiguous_256MiB_verified", "pmap_bootstrap_executed",
             "arm_vm_init_returned", "hardware_tested", "xnu_runtime_qualified"], False))
        self.sync_trace()
        self.sync_release()

    def close(self):
        self.base.doCleanups()

    def sync_artifact(self):
        proof_path = self.candidate / self.artifact_profile["proof"]
        proof_path.write_text(json.dumps(self.linked))
        value = host.sha(proof_path)
        self.lock[self.artifact_profile["lock_key"]] = value
        self.build["coverage_machinecode_sha256"] = value
        (self.candidate / "manifest.json").write_text(json.dumps(self.lock))
        (self.candidate / "build-manifest.json").write_text(json.dumps(self.build))

    def sync_trace(self):
        self.trace_path.write_text("".join(json.dumps(event) + "\n" for event in self.events))
        self.render["host_trace_sha256"] = host.sha(self.trace_path)

    def sync_release(self):
        for path, value in ((self.proof_path, self.proof), (self.ui_path, self.ui),
                            (self.render_path, self.render)):
            path.write_text(json.dumps(value))
        self.receipt[self.prefix + "_uefi_proof_sha256"] = host.sha(self.proof_path)
        self.receipt["new_ui_layout_audit_sha256"] = host.sha(self.ui_path)
        self.receipt["stage_render_audit_sha256"] = host.sha(self.render_path)
        self.receipt_path.write_text(json.dumps(self.receipt))

    def run(self, dry=True, diff=""):
        tools = (Path("/pinned/python"), {}, {"scripts": {}})
        with mock.patch.object(pack.subprocess, "check_output",
                               side_effect=["a" * 40, "", diff, "uefi-test", ""]):
            with mock.patch.object(pack, "load_tools", return_value=tools):
                with mock.patch.object(pack, "private_seed",
                                       side_effect=AssertionError("real entropy forbidden")) as rng:
                    self.rng = rng
                    with contextlib.redirect_stdout(io.StringIO()) as output:
                        pack.main(self.base.argv + (["--dry-run"] if dry else []))
        return json.loads(output.getvalue())


class Mem04PackagingTests(unittest.TestCase):
    def setUp(self):
        self.f = ProfileFixture()
        self.addCleanup(self.f.close)

    def reject(self, pattern, dry=False):
        with self.assertRaisesRegex(RuntimeError, pattern):
            self.f.run(dry=dry)
        self.f.rng.assert_not_called()
        self.assertFalse(self.f.base.out.exists())

    def test_current_MEM04_interface_dry_run_has_no_output_or_entropy(self):
        result = self.f.run()
        self.assertEqual(result["result"], "PASS_NO_ARTIFACT_CREATED")
        self.f.rng.assert_not_called()
        self.assertFalse(self.f.base.out.exists())

    def test_only_exact_recognized_artifact_schema_is_allowed(self):
        self.f.build["schema"] = "thor-mem04-build-manifest-v0"
        self.f.sync_artifact()
        self.reject("build manifest schema")

    def test_legacy_linked_proof_cannot_masquerade_as_MEM04(self):
        self.f.linked["schema"] = "thor-mem03-framebuffer-machinecode-v1"
        self.f.sync_artifact()
        self.reject("schema/result/frontier")

    def test_pre_pmap_result_cannot_masquerade_as_pmap_return(self):
        self.f.linked.update(result="PASS_MEM04_LINKED_READABLE_GRID_PRE_PMAP",
                             frontier="pre-pmap")
        self.f.sync_artifact()
        self.reject("schema/result/frontier")

    def test_legacy_release_profile_cannot_bypass_new_render_checks(self):
        self.f.receipt["schema"] = "thor-mem03-fdt-detail-and-xnu-fb-coverage-v1"
        self.f.receipt["mem03_uefi_proof_sha256"] = self.f.receipt["mem04_uefi_proof_sha256"]
        self.f.sync_release()
        self.reject("artifact/release profile mismatch")

    def test_unapproved_compiler_or_audit_files_rejected(self):
        for path in ("patches/xnu/unsafe.patch", "scripts/audit-other.py"):
            with self.subTest(path=path):
                self.f.lock["post_build_changes"] = [path]
                self.f.build["post_build_changes"] = [path]
                self.f.sync_artifact()
                self.reject("Only reviewed audit/test")

    def test_compiled_pmap_flag_missing_before_entropy(self):
        self.f.lock.pop("native_pmap_bootstrap_call_compiled")
        self.f.sync_artifact()
        self.reject("compiled bounded pmap")

    def test_noreturn_guard_missing_before_entropy(self):
        self.f.linked["bounded_after_pmap_return"] = False
        self.f.sync_artifact()
        self.reject("compiled bounded pmap")

    def test_false_runtime_claim_rejected(self):
        for field in ("pmap_bootstrap_executed", "arm_vm_init_returned", "hardware_tested"):
            with self.subTest(field=field):
                self.f.linked[field] = True
                self.f.sync_artifact()
                self.reject("unexecuted runtime scope")
                self.f.linked[field] = False

    def test_linked_checkpoint_prefix_must_have_fourteen_real_integer_ids(self):
        self.f.linked["checkpoint_contract"]["compiled_stage_prefix"][0] = False
        self.f.sync_artifact()
        self.reject("fourteen numbered stages")

    def test_release_schema_and_UEFI_proof_schema_cannot_be_mixed(self):
        self.f.proof["schema"] = "thor-mem03-snapshot-detail-host-proof-v1"
        self.f.sync_release()
        self.reject("proof schemas/results")

    def test_UEFI_legacy_grid_contract_cannot_claim_fourteen_stages(self):
        self.f.ui["completed_stage_slots"] = list(range(11))
        self.f.sync_release()
        self.reject("fourteen bounded stages")

    def test_boolean_slot_ID_does_not_equal_a_real_number(self):
        self.f.ui["completed_stage_slots"][0] = False
        self.f.sync_release()
        self.reject("fourteen bounded stages")

    def test_fewer_than_eleven_renderer_tests_rejected(self):
        self.f.proof["tests"]["mem04"] = 1
        self.f.receipt["source_level_regressions_passed"] = 35
        self.f.sync_release()
        self.reject("eleven renderer regressions")

    def test_top_level_zero_overlap_cannot_hide_real_trace_overlap(self):
        event = {"frame": 1, "kind": "text", "rect": [65, 354, 100, 14],
                 "text": "overlapping text", "scale": 2}
        self.f.events.insert(3, event)
        self.f.render["frames"][0].update(event_count=5, texts=2)
        self.f.render["event_count"] += 1
        self.f.sync_trace()
        self.f.sync_release()
        self.reject("text overlap")

    def test_empty_trace_cannot_pass_with_six_frame_summaries(self):
        self.f.events = []
        self.f.render["event_count"] = 0
        self.f.sync_trace()
        self.f.sync_release()
        self.reject("nonempty executed renderer")

    def test_six_empty_frame_dicts_are_not_executed_frames(self):
        self.f.render["frames"] = [{} for _ in range(6)]
        self.f.sync_release()
        self.reject("required renderer stages")

    def test_forged_text_bounds_flag_cannot_hide_out_of_bounds_rect(self):
        self.f.events[2]["rect"] = [1400, 352, 100, 14]
        self.f.sync_trace()
        self.f.sync_release()
        self.reject("text/fill bounds")

    def test_renderer_summary_counts_must_match_trace(self):
        self.f.render["frames"][0]["event_count"] = 999
        self.f.sync_release()
        self.reject("summary/pixel identity")

    def test_renderer_source_hash_must_bind_real_BDS(self):
        self.f.render["renderer_source_sha256"] = "0" * 64
        self.f.sync_release()
        self.reject("executed renderer source identity")

    def test_old_renderer_source_key_aliases_do_not_hide_missing_new_keys(self):
        self.f.render["bds_source_sha256"] = self.f.render.pop("renderer_source_sha256")
        self.f.sync_release()
        self.reject("executed renderer source identity")

    def test_renderer_pinned_source_tamper_rejected(self):
        path = next(iter(self.f.render["source_files_sha256"]))
        self.f.render["source_files_sha256"][path] = "0" * 64
        self.f.sync_release()
        self.reject("renderer pinned source")

    def test_harness_trace_missing_or_tampered_rejected_before_entropy(self):
        self.f.harness_path.write_text("changed after proof")
        self.reject("trace/harness identity")

    def test_MEM03_still_uses_its_own_full_preseed_release_contract(self):
        fixture = ProfileFixture("MEM03")
        self.addCleanup(fixture.close)
        result = fixture.run()
        self.assertEqual(result["result"], "PASS_NO_ARTIFACT_CREATED")
        fixture.rng.assert_not_called()
        self.assertFalse(fixture.base.out.exists())


if __name__ == "__main__":
    unittest.main()
