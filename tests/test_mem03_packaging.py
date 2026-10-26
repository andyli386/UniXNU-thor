#!/usr/bin/env python3
"""Host-only packaging regressions; entropy is mocked and no boot image is built."""
from pathlib import Path
from unittest import mock
import contextlib
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools/thorctl"))
import package as pack
import package_host as host


def digest(value):
    return hashlib.sha256(value).hexdigest()


class EntropyTests(unittest.TestCase):
    def test_linux_uses_one_blocking_getrandom(self):
        with mock.patch.object(host.os, "getrandom", return_value=b"L" * 64,
                               create=True) as call:
            value, source = host.private_seed("linux")
        self.assertEqual(value, b"L" * 64)
        self.assertEqual(source, "linux-os.getrandom(flags=0)")
        call.assert_called_once_with(64, 0)

    def test_darwin_uses_one_os_urandom(self):
        with mock.patch.object(host.os, "urandom", return_value=b"D" * 64) as call:
            value, source = host.private_seed("darwin")
        self.assertEqual(value, b"D" * 64)
        self.assertEqual(source, "darwin-os.urandom")
        call.assert_called_once_with(64)

    def test_unknown_platform_rejected_without_entropy_call(self):
        with mock.patch.object(host.os, "urandom") as call:
            with self.assertRaisesRegex(RuntimeError, "Unsupported"):
                host.private_seed("win32")
        call.assert_not_called()

    def test_short_zero_or_nonbytes_entropy_rejected(self):
        for value in (b"x" * 63, bytes(64), bytearray(b"x" * 64)):
            with self.subTest(kind=type(value).__name__, size=len(value)):
                with mock.patch.object(host.os, "urandom", return_value=value):
                    with self.assertRaisesRegex(RuntimeError, "Invalid"):
                        host.private_seed("darwin")

    def test_rng_failure_propagates_without_fallback_or_output(self):
        output = io.StringIO()
        with mock.patch.object(host.os, "urandom", side_effect=OSError("rng")):
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                with self.assertRaises(OSError):
                    host.private_seed("darwin")
        self.assertEqual(output.getvalue(), "")


class PathAndOutputTests(unittest.TestCase):
    def test_outside_and_symlink_path_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "root"
            root.mkdir()
            (root / "escape").symlink_to(Path(folder))
            for path in (Path("../outside"), root / "escape/file"):
                with self.assertRaisesRegex(RuntimeError, "escapes"):
                    host.rooted(root.resolve(), path)

    def test_existing_attempt_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            (out / "manifest.json").write_text("old")
            with self.assertRaises(FileExistsError):
                with host.private_output(out, "test"):
                    self.fail("existing output yielded")
            self.assertEqual((out / "manifest.json").read_text(), "old")

    def test_failure_is_quarantined_without_success_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "attempt"
            with self.assertRaisesRegex(RuntimeError, "tool failure"):
                with host.private_output(out, "test"):
                    raise RuntimeError("tool failure")
            self.assertTrue((out / "INVALID-NOT-FOR-PHONE").is_file())
            self.assertFalse((out / "manifest.json").exists())

    def test_interrupt_is_quarantined(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "attempt"
            with self.assertRaises(KeyboardInterrupt):
                with host.private_output(out, "test"):
                    raise KeyboardInterrupt()
            self.assertTrue((out / "INVALID-NOT-FOR-PHONE").is_file())

    def test_commands_use_pinned_python_and_literal_argv(self):
        python = Path("/private/Python Runtime/python3")
        scripts = {key: Path("/trusted tools") / (key + ".py")
                   for key in host.TOOL_KEYS}
        commands = host.package_commands(python, scripts, Path("/private/run path"))
        self.assertEqual(len(commands), 3)
        for command in commands:
            self.assertEqual(command[:3], [str(python), "-E", "-s"])
            self.assertTrue(all(isinstance(value, str) for value in command))
        self.assertIn("/private/run path/thor-boot.img", commands[1])
        self.assertIn("--strict-padding", commands[1])
        self.assertIn("--require-exact-size", commands[1])


class ToolPinTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / "gki").mkdir()
        locations = {"mkbootimg": "mkbootimg.py",
                     "unpack_bootimg": "unpack_bootimg.py",
                     "image_verifier": "verify_android_boot_v4.py",
                     "gki_certificate": "gki/generate_gki_certificate.py",
                     "inspect_thor_fdt": "inspect_thor_fdt.py"}
        self.data = {"schema": "thor-package-tools-v1",
                     "python": {"path": "python", "sha256": digest(b"python"),
                                "version": [3, 13, 0]}, "scripts": {}}
        (self.root / "python").write_bytes(b"python")
        for key, location in locations.items():
            (self.root / location).write_bytes(key.encode())
            self.data["scripts"][key] = {"path": location,
                                       "sha256": digest(key.encode())}
        self.manifest = self.root / "tools.json"
        self.save()

    def save(self):
        self.manifest.write_text(json.dumps(self.data))

    def load(self):
        process = subprocess.CompletedProcess([], 0, "[3, 13, 0]\n", "")
        with mock.patch.object(host.subprocess, "run", return_value=process):
            return host.load_tools(self.root, self.manifest)

    def test_valid_pins_resolve_all_dependencies(self):
        python, scripts, metadata = self.load()
        self.assertEqual(python, self.root / "python")
        self.assertEqual(set(scripts), set(host.TOOL_KEYS))
        self.assertEqual(metadata["manifest_sha256"], host.sha(self.manifest))

    def test_script_tampering_is_rejected(self):
        (self.root / "mkbootimg.py").write_bytes(b"changed")
        with self.assertRaisesRegex(RuntimeError, "Pinned packaging script"):
            self.load()

    def test_interpreter_tampering_is_rejected(self):
        (self.root / "python").write_bytes(b"changed")
        with self.assertRaisesRegex(RuntimeError, "Python identity"):
            self.load()

    def test_version_mismatch_is_rejected(self):
        self.data["python"]["version"] = [3, 12, 0]
        self.save()
        with self.assertRaisesRegex(RuntimeError, "Python version"):
            self.load()

    def test_unpinned_import_initializer_is_rejected(self):
        (self.root / "gki/__init__.py").write_text("unexpected")
        with self.assertRaisesRegex(RuntimeError, "initializer"):
            self.load()

    def test_dependency_path_escape_is_rejected(self):
        self.data["scripts"]["mkbootimg"]["path"] = "../outside"
        self.save()
        with self.assertRaisesRegex(RuntimeError, "escapes"):
            self.load()


class PreflightOrderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "source"
        adt = self.source / "Platforms/Xiaomi/thorPkg/Drivers/ThorEntryBdsDxe"
        adt.mkdir(parents=True)
        self.candidate = self.root / "candidate"
        self.candidate.mkdir()
        kernel = b"unit-test-kernel"
        fd = bytes(3145728)
        self.kernel_pin = digest(kernel)
        self.fd_pin = digest(fd)
        (self.candidate / "kernel.macho").write_bytes(kernel)
        (self.root / "firmware.fd").write_bytes(fd)
        build = {"kernel_sha256": self.kernel_pin, "kernel_bytes": len(kernel),
                 "loaded_span_bytes": 4096, "xnu_commit": "xnu-test"}
        layout = {"kernel_sha256": self.kernel_pin, "loaded_span_bytes": 4096,
                  "virtual_base": 4096, "virtual_end": 8192, "segment_count": 10,
                  "loaded_span_sha256": "loaded-span-test"}
        (self.candidate / "build-manifest.json").write_text(json.dumps(build))
        (self.candidate / "kernel-layout.json").write_text(json.dumps(layout))
        prefix = pack.struct.pack("<16sIIQ32s", b"THOR-PAYLOAD09\0\0", 2, 128,
                                  len(kernel), bytes.fromhex(self.kernel_pin))
        provenance = {"kernel_sha256": self.kernel_pin,
                      "loaded_span_sha256": "loaded-span-test",
                      "payload_prefix_sha256": digest(prefix)}
        (adt / "provenance.json").write_text(json.dumps(provenance))
        (adt / "ThorEntry06PayloadExpected.h").write_text(
            "#define THOR_ENTRY06_PAYLOAD_BYTES " + str(len(kernel)) +
            "ULL\nmEntry06PayloadHeader[64]\n")
        (self.root / "audit.json").write_text(json.dumps(
            {"status": "PASS", "fd": {"sha256": self.fd_pin}, "failures": []}))
        (self.root / "release.json").write_text(json.dumps(
            {"uefi_fd_sha256": self.fd_pin, "uefi_commit": "uefi-test",
             "kernel_sha256": self.kernel_pin}))
        stock = self.root / "artifacts/thor-smoke-01"
        stock.mkdir(parents=True)
        shim, dtb = b"S" * 112, b"T" * 6443651
        (stock / "BootShim.bin").write_bytes(shim)
        (stock / "thor-stock-vendor-dtb.bin").write_bytes(dtb)
        original = self.root / ("artifacts/mainline-4k-entry09-vmprep-20261024/"
                                "run02/kernel.entry09-vmprep.macho")
        original.parent.mkdir(parents=True)
        original.write_bytes(b"original")
        (self.root / "thor-build.toml").write_text(
            '[stock]\nverified_entry09_kernel_sha256="' + digest(b"original") +
            '"\nbootshim_sha256="' + digest(shim) + '"\ndtb_sha256="' +
            digest(dtb) + '"\n')
        self.argv = ["--root", str(self.root), "--source", "source",
                     "--kernel", "candidate/kernel.macho", "--fd", "firmware.fd",
                     "--fd-audit", "audit.json", "--firmware-receipt", "release.json",
                     "--provenance", str((adt / "provenance.json").relative_to(self.root)),
                     "--run-id", "thor-test-package"]
        self.out = self.root / "runs/thor-test-package/l2/package"

    def run_main(self, extra=(), entropy_error=None, git_outputs=None):
        tools = (Path("/pinned/python"), {}, {"scripts": {}})
        output = io.StringIO()
        with mock.patch.object(pack.subprocess, "check_output",
                               side_effect=git_outputs or ["uefi-test", ""]):
            with mock.patch.object(pack, "load_tools", return_value=tools):
                with mock.patch.object(pack, "private_seed",
                                       side_effect=entropy_error) as rng:
                    with contextlib.redirect_stdout(output):
                        pack.main(self.argv + list(extra))
        return output.getvalue(), rng

    def test_dry_run_creates_nothing_and_never_requests_entropy(self):
        output, rng = self.run_main(["--dry-run"])
        self.assertEqual(json.loads(output)["result"], "PASS_NO_ARTIFACT_CREATED")
        rng.assert_not_called()
        self.assertFalse(self.out.exists())

    def test_rng_error_quarantines_and_never_builds_image(self):
        with self.assertRaisesRegex(RuntimeError, "rng unavailable"):
            self.run_main(entropy_error=RuntimeError("rng unavailable"))
        self.assertTrue((self.out / "INVALID-NOT-FOR-PHONE").exists())
        self.assertFalse((self.out / "manifest.json").exists())
        self.assertFalse((self.out / "thor-boot.img").exists())

    def test_kernel_hash_mismatch_fails_before_output_or_entropy(self):
        (self.candidate / "kernel.macho").write_bytes(b"tamper")
        with self.assertRaisesRegex(RuntimeError, "Frozen kernel"):
            self.run_main(["--dry-run"])
        self.assertFalse(self.out.exists())

    def test_wrong_source_revision_fails_before_output(self):
        with self.assertRaisesRegex(RuntimeError, "source revision"):
            self.run_main(["--dry-run"], git_outputs=["wrong"])
        self.assertFalse(self.out.exists())

    def test_dirty_source_fails_before_output(self):
        with self.assertRaisesRegex(RuntimeError, "source unclean"):
            self.run_main(["--dry-run"], git_outputs=["uefi-test", " M file"])
        self.assertFalse(self.out.exists())


if __name__ == "__main__":
    unittest.main()
