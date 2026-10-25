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

if __name__ == "__main__":
    unittest.main()
