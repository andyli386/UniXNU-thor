#!/usr/bin/env python3
"""Regression tests for thorctl L0/P0. Does not open an ADB or Fastboot socket."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "tools/thorctl.py"
spec = importlib.util.spec_from_file_location("thorctl_p0", SOURCE)
thorctl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(thorctl)


class WorkflowTests(unittest.TestCase):
    def test_impact_vm_calls_link_and_machine_code(self):
        a = thorctl.affected(["osfmk/arm64/arm_vm_init.c"])
        self.assertIn("L2:machine-code-controlflow", a["checks"])
        self.assertTrue(a["full_candidate_gate_required"])
        self.assertTrue(a["hardware_never_automatic"])

    def test_impact_uefi_no_xnu_rebuild(self):
        a = thorctl.affected(["uefi-thor/ThorEntryBdsDxe.c"])
        self.assertIn("L2:uefi-link", a["checks"])
        self.assertNotIn("L2:xnu-link", a["checks"])

    def test_tool_changes_host_only(self):
        a = thorctl.affected(["tools/thorctl.py"])
        self.assertIn("L1:host-unit", a["checks"])
        self.assertFalse(a["full_candidate_gate_required"])

    def test_safe_paths_block_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(RuntimeError):
                thorctl.path_under(root, "../etc", exists=False)

    def test_immutable_lock_refuses_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "runs/a-run-001/build.lock.json"
            thorctl.new_file(output, {"x": 1})
            with self.assertRaises(FileExistsError):
                thorctl.new_file(output, {"x": 2})
            self.assertEqual(json.loads(output.read_text()), {"x": 1})

    def test_run_id_rejects_path_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("../../../oops", ".unsafe", "a/a", "x"):
                with self.assertRaises(RuntimeError):
                    thorctl.run_root(Path(tmp), name)
            self.assertEqual(thorctl.run_root(Path(tmp), "thor-20261025-vm-001").name,
                             "thor-20261025-vm-001")

    def test_archive_has_separate_stages(self):
        root = SOURCE.parents[3]
        a = thorctl.verify_run(root, "20261025-i386-entry10g-01")
        self.assertTrue(a["fastboot_command_accepted"])
        self.assertTrue(a["android_return_verified"])
        self.assertTrue(a["source_has_compiled_ttbr1_call"])
        self.assertFalse(a["full_arm_vm_init_verified"])
        self.assertTrue(a["post_ttbr1_validated_by_visual_record"])

    def test_real_mac_baseline(self):
        root = SOURCE.parents[3]
        c = thorctl.config_at(root, "thor-build.toml")
        result = thorctl.build_lock(root, c, "macmini")
        self.assertEqual(result["kernel"]["sha256"],
                         "6982bef3adb76fc82b9adbded96b758734853b5464a041517ba14ccc059493e4")
        self.assertEqual(result["xnu_source"]["commit"],
                         "db2facaa97bfc314711795aa9414a2043aaffbc0")


    def test_vm_postlink_transition_crc_regression(self):
        root = SOURCE.parents[3]
        r = root / "evidence/build/thor-20261025-vm-hw02/receipts"
        fix = json.loads((r / "transition-verified.json").read_text())
        final = json.loads((r / "firmware-release.json").read_text())
        baseline = json.loads((r / "photo-root-cause.json").read_text())
        self.assertEqual(fix["result"], "PASS_LINKED_SHA_EQUALS_EMBEDDED_HEADER")
        self.assertEqual(fix["linked_transition_sha256"],
                         fix["source_expected_transition_sha256"])
        self.assertEqual(fix["in_transition_relocations"], 0)
        self.assertEqual(fix["instruction_differences_vs_entry10g"],
                         [76, 96, 100, 156])
        self.assertEqual(fix["linked_transition_sha256"],
                         final["linked_transition_sha256"])
        self.assertNotEqual(baseline["binary_root_cause"]["old_uefi_sha256_transition"],
                            fix["linked_transition_sha256"])

    def test_vm_failed_pre_ebs_not_xnu_milestone(self):
        root = SOURCE.parents[3]
        d = json.loads((root / "evidence/build/thor-20261025-vm-hw02/receipts/photo-root-cause.json").read_text())
        self.assertEqual(d["visual_scope"]["explicit_firmware_error"],
                         "EFI_CRC_ERROR, 0x800000000000001B")
        self.assertFalse(d["visual_scope"]["exit_boot_services_executed"])
        self.assertFalse(d["visual_scope"]["xnu_ttbr1_probe_executed"])

    def test_vm_hw03_report_is_real_uefi_not_xnu_claim(self):
        root = SOURCE.parents[3]
        d = root / "evidence/build/thor-20261025-vm-hw03/receipts"
        from hashlib import sha256
        ui = json.loads((d / "ui-layout-audit.json").read_text())
        release = json.loads((d / "firmware-release.json").read_text())
        host = json.loads((d / "final-host-gate.json").read_text())
        code = (d / "ThorReportHud.inc.c").read_bytes()
        self.assertEqual(ui["result"], "PASS_STATIC_UI_LAYOUT_AND_LINKED_PE_STRINGS")
        self.assertEqual(sha256(code).hexdigest(), release["new_ui_source_sha256"])
        self.assertEqual(host["result"], "PASS_L2_CANDIDATE_READY_UNTESTED")
        self.assertFalse(host["hardware_tested"])
        self.assertEqual(host["host_checks_passed"], 60)
        self.assertEqual(host["phone_actions"], 0)

    def test_vm_hw03_high_vbar_compiled_without_retiring_ttbr0(self):
        root = SOURCE.parents[3]
        d = root / "evidence/build/thor-20261025-vm-hw03/receipts"
        machine = json.loads((d / "vm-machine-code.json").read_text())
        self.assertEqual(machine["result"], "PASS_ACTUAL_VBAR_RELOCATION_CODE_AND_ORDER")
        self.assertEqual(machine["EL1_translation_probe_calls"], 3)
        self.assertEqual(machine["VBAR_EL1_msr_count"], 1)
        self.assertTrue(machine["msr_followed_by_isb_then_readback_then_white_marker"])
        self.assertFalse(machine["ttbr0_invalidation_executed"])

    def test_vmhw03_visual_checkpoint_is_bounded(self):
        root = SOURCE.parents[3]
        p = root / "evidence/hardware-tests/thor-20261025-vm-hw03"
        review = json.loads((p / "visual-milestone-review.json").read_text())
        host = json.loads((p / "technical-summary-awaiting-video.json").read_text())
        self.assertEqual(review["conclusion"],
                         "PASS_BOUNDED_REAL_XNU_TTBR1_AND_HIGH_VBAR_CHECKPOINT_VISUALLY_CONFIRMED")
        self.assertEqual(len(review["photos"]), 7)
        self.assertTrue(review["observed"]["xnu_white_high_vbar_pass"])
        self.assertEqual(host["image_sha256"], review["code_path_basis"]["image_sha256"])
        self.assertTrue(host["android_return_passed"])
        self.assertFalse(host["full_arm_vm_init_completed"])
        derived = thorctl.verify_run(root, "thor-20261025-vm-hw03")
        self.assertTrue(derived["source_has_compiled_ttbr1_call"])
        self.assertTrue(derived["post_ttbr1_validated_by_visual_record"])
        self.assertFalse(derived["raw_photo_bytes_sha256_verified"])
        self.assertFalse(derived["full_arm_vm_init_verified"])

    def test_vmhw04_new_root_gate_is_compiled_not_retired(self):
        root = SOURCE.parents[3]
        p = root / "runs/thor-20261025-vmdev-004/l2/ttbr0-root-machinecode.json"
        a = json.loads(p.read_text())
        self.assertEqual(a["result"], "PASS_LINKED_ZERO_ROOT_AND_HIGH_PHYSVA_GATE")
        self.assertEqual(a["mapped_phystokv_calls"], 2)
        self.assertEqual(a["EL1_AT_S1E1R_calls"], 5)
        self.assertTrue(a["zero_root_4KiB_check_emitted"])
        self.assertTrue(a["white_marker_after_all_root_guards"])
        self.assertTrue(a["no_TTBR0_renewal_or_retirement"])
        self.assertFalse(a["arm_vm_init_full_returned"])
        self.assertFalse(a["hardware_tested"])

    def test_vmhw04_crosshost_mirror_has_exact_provenance(self):
        root = SOURCE.parents[3]
        from hashlib import sha256
        from subprocess import check_output
        d = root / "evidence/build/thor-20261025-vm-hw04"
        p = d / "receipts"
        frozen = json.loads((d / "crosshost-verification-receipt-v2.json").read_text())
        firmware = json.loads((p / "firmware-release.json").read_text())
        gate = json.loads((p / "final-host-gate.json").read_text())
        tests = json.loads((p / "host-offline-62-checks.json").read_text())
        machine = json.loads((p / "xnu-ttbr0-root-machinecheck.json").read_text())
        hashfile = lambda path: sha256(path.read_bytes()).hexdigest()
        self.assertEqual(frozen["result"], "PASS_RECEIPTS_AND_FD_GIT_HISTORY_ARCHIVED")
        self.assertEqual(tests["result"], "PASS")
        self.assertEqual(len(tests["checks"]), 62)
        self.assertTrue(all(check["passed"] for check in tests["checks"]))
        self.assertEqual(gate["image_sha256"], frozen["boot_image_sha256_on_i386_only"])
        self.assertEqual(hashfile(d / "SILICIUM_UEFI.fd"), firmware["uefi_fd_sha256"])
        self.assertEqual(hashfile(root / "artifacts/candidates/thor-20261025-vmdev-004/kernel.macho"),
                         firmware["kernel_sha256"])
        self.assertEqual(machine["kernel_sha256"], firmware["kernel_sha256"])
        self.assertEqual(check_output(["git", "-C", str(root / "repos/uefi-thor"),
                           "rev-parse", "experimental/vm-ttbr0-root-readiness-unverified"],
                          text=True).strip(), firmware["uefi_commit"])
        self.assertEqual(check_output(["git", "-C", str(root / "repos/UniXNU-thor"),
                           "rev-parse", "experimental/vm-ttbr0-root-readiness-unverified"],
                          text=True).strip(), gate["xnu_commit"])
        self.assertTrue(frozen["one_use_seed_not_transferred"])
        self.assertTrue(frozen["needs_live_hardware_screen_review"])

    def test_vmhw04_build_only_not_claimed_to_retire_ttbr0(self):
        root = SOURCE.parents[3]
        d = root / "evidence/build/thor-20261025-vm-hw04"
        frozen = json.loads((d / "crosshost-verification-receipt-v2.json").read_text())
        controller = json.loads((d / "receipts/controller-preflight.json").read_text())
        code = json.loads((d / "receipts/xnu-ttbr0-root-machinecheck.json").read_text())
        self.assertTrue(frozen["first_hardware_attempt_not_executed"])
        self.assertTrue(frozen["ttbr0_still_active"])
        self.assertEqual(controller["status"], "READY_NOT_EXECUTED")
        self.assertEqual(controller["max_fastboot_boots"], 1)
        self.assertEqual(controller["automatic_retries"], 0)
        self.assertEqual(controller["phone_actions"], 0)
        self.assertTrue(code["zero_root_4KiB_check_emitted"])
        self.assertTrue(code["no_TTBR0_renewal_or_retirement"])
        self.assertFalse(code["arm_vm_init_full_returned"])
        self.assertFalse(code["hardware_tested"])

    def test_vmhw04_real_run_reports_linked_code_but_not_live_checkpoint(self):
        root = SOURCE.parents[3]
        s = thorctl.verify_run(root, "thor-20261025-vm-hw04")
        self.assertTrue(s["fastboot_command_accepted"])
        self.assertTrue(s["android_return_verified"])
        self.assertTrue(s["source_has_compiled_ttbr1_call"])
        self.assertTrue(s["linked_ttbr0_zero_root_gate_compiled"])
        self.assertFalse(s["ttbr0_root_gate_live_verified"])
        self.assertFalse(s["post_ttbr1_validated_by_visual_record"])
        self.assertFalse(s["full_arm_vm_init_verified"])

    def test_linked_root_gate_requires_pinned_machine_code(self):
        with tempfile.TemporaryDirectory() as dirname:
            root = Path(dirname)
            summary = {"kernel_sha256": "4" * 64,
                       "linked_zero_root_machineproof_passed": True}
            with self.assertRaises(RuntimeError):
                thorctl.linked_root_gate_proof(root, "thor-20261025-vm-hw04", summary)
            summary["linked_zero_root_machineproof_passed"] = False
            self.assertFalse(thorctl.linked_root_gate_proof(
                root, "thor-20261025-vm-hw04", summary))

if __name__ == "__main__":
    unittest.main()
