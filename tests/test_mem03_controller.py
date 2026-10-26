"""One-use Mac controller tests use virtual files and fake subprocess only."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / 'tools/thorctl/prepare_mem03_mac.py'
spec = importlib.util.spec_from_file_location('mem03_mac_controller', MODULE)
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.run_id = 'thor-20261026-vm-mem03-test'
        self.source = self.root / 'uefi-source'
        self.source.mkdir()
        self.package = self.root / 'runs' / self.run_id / 'l2' / 'package'
        self.package.mkdir(parents=True)
        self.image = self.package / 'thor-boot.img'
        self.image.write_bytes(b'new-variable-sized-image' * 37)
        self.fd = self.package / 'SILICIUM_UEFI.fd'
        self.fd.write_bytes(b'fd')
        self.adb = self.root / 'adb'
        self.adb.write_bytes(b'fake-adb')
        self.fastboot = self.root / 'fastboot'
        self.fastboot.write_bytes(b'fake-fastboot')
        self.serial = 'FAKE-PRIVATE-SERIAL'
        self.props = dict(zip(controller.PROPERTIES,
            ('1', '_a', 'running', 'stopped', 'thor', 'baseline', 'vendor-baseline')))
        self.pins_data = {'schema': 'thor-mem03-controller-pins-v1', 'run_id': self.run_id,
            'image_sha256': controller.sha(self.image), 'image_bytes': self.image.stat().st_size,
            'fd_sha256': controller.sha(self.fd), 'uefi_commit': 'a' * 40,
            'kernel_sha256': 'b' * 64, 'kernel_source_revision': 'c' * 40}
        self.manifest_data = {key: value for key, value in self.pins_data.items() if key != 'schema'}
        self.manifest_data.update(phone_attempts=0, seed_reuse_allowed=False, seed_raw_logged=False)
        self.proof_data = {'result': 'PASS', 'ready_for_one_use_temporary_boot': True,
            'run_id': self.run_id, 'checks': [{'passed': True}],
            'hardware_test_attempts': 0, 'inputs': {
                'android_boot_v4': {'sha256': controller.sha(self.image), 'bytes': self.image.stat().st_size},
                'uefi_fd': {'sha256': controller.sha(self.fd)},
                'xnu_kernel': {'sha256': 'b' * 64}}}
        self.firmware_data = {'uefi_commit': 'a' * 40, 'uefi_fd_sha256': controller.sha(self.fd),
                              'transition_sha_matches_expected_header': True}
        self.device_data = {'schema': 'thor-mem03-mac-readonly-preflight-v1',
            'run_id': self.run_id, 'unique_USB_target': True,
            'serial_sha256': hashlib.sha256(self.serial.encode()).hexdigest(),
            'seven_expected_properties': self.props, 'seven_observed_properties': self.props,
            'adb_reboots': 0, 'fastboot_commands': 0, 'image_boot_attempts': 0}
        self.paths = {}
        for name, value in (('pins', self.pins_data), ('manifest', self.manifest_data),
                            ('offline_proof', self.proof_data), ('firmware_release', self.firmware_data),
                            ('device_proof', self.device_data)):
            self.paths[name] = self.package / (name + '.json')
            self.write(name, value)
        self.args = argparse.Namespace(root=self.root, run_id=self.run_id, source=self.source,
            image=self.image, fd=self.fd, adb=self.adb, fastboot=self.fastboot, **self.paths)
        self.calls = []
        self.fail_boot = False
        self.bad_fastboot_slot = False
        self.bad_serial = False
        self.bad_android_prop = False
        self.patch = patch.object(controller.subprocess, 'run', self.fake_run)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def write(self, name, value):
        self.paths[name].write_text(json.dumps(value))

    def fake_run(self, argv, **kwargs):
        self.calls.append(tuple(argv))
        if argv[0] == 'git':
            output = ('a' * 40 + '\n') if 'rev-parse' in argv else ''
            return subprocess.CompletedProcess(argv, 0, output, '')
        self.assertIs(kwargs.get('shell'), False)
        out, err = '', ''
        if argv[0] == str(self.adb):
            if argv[1:] == ['devices', '-l']:
                serial = 'wrong-serial' if self.bad_serial else self.serial
                out = 'List of devices attached\n' + serial + ' device product:thor model:test\n'
            elif 'getprop' in argv:
                out = ('wrong' if self.bad_android_prop else self.props[argv[-1]]) + '\n'
            else:
                self.assertEqual(argv[-2:], ['reboot', 'bootloader'])
        elif argv[0] == str(self.fastboot):
            if argv[1:] == ['devices']:
                out = self.serial + '\tfastboot\n'
            elif argv[-2:] == ['getvar', 'product']:
                err = 'product: thor\nFinished. Total time: 0.001s\n'
            elif argv[-2:] == ['getvar', 'current-slot']:
                err = 'current-slot: ' + ('b' if self.bad_fastboot_slot else 'a') + '\n'
            elif 'boot' in argv:
                if self.fail_boot:
                    raise subprocess.TimeoutExpired(argv, kwargs['timeout'])
                err = 'Sending boot.img OKAY\nBooting OKAY\n'
            else:
                self.fail('Unexpected fastboot command: ' + repr(argv))
        else:
            self.fail('Unexpected executable')
        kwargs['stdout'].write(out.encode())
        kwargs['stderr'].write(err.encode())
        return subprocess.CompletedProcess(argv, 0)

    def phone_calls(self):
        return [argv for argv in self.calls if argv[0] != 'git']

    def prepared(self):
        result = controller.prepare(self.args)
        self.preflight = Path(result['preflight'])
        return argparse.Namespace(preflight=self.preflight, recording_confirmed=True,
            confirm_run_id=self.run_id, confirm_image_sha256=self.pins_data['image_sha256'])

    def test_prepare_is_phone_free_sealed_and_uses_dynamic_size(self):
        self.prepared()
        record = controller.read(self.preflight)
        self.assertEqual(self.phone_calls(), [])
        self.assertEqual(record['status'], 'READY_NOT_EXECUTED')
        self.assertFalse(record['recording_confirmed'])
        self.assertEqual(record['image_bytes'], len(b'new-variable-sized-image' * 37))
        self.assertEqual(len(record['files']), 2)

    def test_no_recording_has_zero_subprocess_calls(self):
        args = self.prepared()
        self.calls.clear()
        args.recording_confirmed = False
        with self.assertRaisesRegex(RuntimeError, 'Recording'):
            controller.execute(args)
        self.assertEqual(self.calls, [])

    def test_existing_claim_refuses_all_phone_calls(self):
        args = self.prepared()
        (self.preflight.parent / 'EXECUTION_CLAIM.json').write_text('{}')
        with self.assertRaises(FileExistsError):
            controller.execute(args)
        self.assertEqual(self.phone_calls(), [])

    def test_consumed_refuses_all_phone_calls(self):
        args = self.prepared()
        (self.package / controller.CONSUMED).write_text('{}')
        with self.assertRaisesRegex(RuntimeError, 'consumed'):
            controller.execute(args)
        self.assertEqual(self.phone_calls(), [])

    def test_global_consumed_hash_refuses_new_copy(self):
        args = self.prepared()
        ledger = controller.registry(self.root, self.pins_data['image_sha256'])
        ledger.parent.mkdir(parents=True)
        ledger.write_text('{}')
        with self.assertRaisesRegex(RuntimeError, 'consumed'):
            controller.execute(args)
        self.assertEqual(self.phone_calls(), [])

    def test_wrong_explicit_hash_has_zero_phone_calls(self):
        args = self.prepared()
        args.confirm_image_sha256 = '0' * 64
        with self.assertRaisesRegex(RuntimeError, 'confirmation'):
            controller.execute(args)
        self.assertEqual(self.phone_calls(), [])

    def test_changed_image_is_rejected_before_phone(self):
        args = self.prepared()
        self.image.write_bytes(b'changed')
        with self.assertRaisesRegex(RuntimeError, 'altered'):
            controller.execute(args)
        self.assertEqual(self.phone_calls(), [])

    def test_changed_script_is_rejected_before_phone(self):
        args = self.prepared()
        with (self.preflight.parent / 'controller.py').open('a') as stream:
            stream.write('\n# changed\n')
        with self.assertRaisesRegex(RuntimeError, 'altered'):
            controller.execute(args)
        self.assertEqual(self.phone_calls(), [])

    def test_not_ready_l2_refuses_prepare(self):
        self.proof_data['ready_for_one_use_temporary_boot'] = False
        self.write('offline_proof', self.proof_data)
        with self.assertRaisesRegex(RuntimeError, 'PASS/ready'):
            controller.prepare(self.args)
        self.assertEqual(self.phone_calls(), [])

    def test_missing_seventh_property_refuses_prepare(self):
        self.device_data['seven_expected_properties'] = dict(self.props)
        self.device_data['seven_expected_properties'].pop(controller.PROPERTIES[-1])
        self.write('device_proof', self.device_data)
        with self.assertRaisesRegex(RuntimeError, 'Seven'):
            controller.prepare(self.args)

    def test_l2_kernel_missing_or_wrong_refuses_prepare(self):
        self.proof_data['inputs']['xnu_kernel']['sha256'] = 'd' * 64
        self.write('offline_proof', self.proof_data)
        with self.assertRaisesRegex(RuntimeError, 'kernel mismatch'):
            controller.prepare(self.args)

    def test_l2_pass_with_failed_check_refuses_prepare(self):
        self.proof_data['checks'][0]['passed'] = False
        self.write('offline_proof', self.proof_data)
        with self.assertRaisesRegex(RuntimeError, 'checks incomplete'):
            controller.prepare(self.args)

    def test_normal_attempt_is_exactly_one_reboot_and_one_boot(self):
        args = self.prepared()
        result = controller.execute(args)
        phones = self.phone_calls()
        self.assertEqual(sum(argv[-2:] == ('reboot', 'bootloader') for argv in phones), 1)
        self.assertEqual(sum('boot' in argv for argv in phones), 1)
        self.assertTrue((self.package / controller.CONSUMED).exists())
        self.assertEqual(result['status'], 'FASTBOOT_ACCEPTED_VISUAL_AND_ANDROID_PENDING')
        with self.assertRaises(RuntimeError):
            controller.execute(args)
        self.assertEqual(self.phone_calls(), phones)

    def test_boot_timeout_keeps_consumed_and_claimed(self):
        args = self.prepared()
        self.fail_boot = True
        with self.assertRaisesRegex(RuntimeError, 'fastboot-boot'):
            controller.execute(args)
        self.assertTrue((self.package / controller.CONSUMED).exists())
        self.assertTrue((self.preflight.parent / 'EXECUTION_CLAIM.json').exists())
        self.assertTrue((self.root / 'runs' / self.run_id / 'l3/STOPPED-DO-NOT-RETRY.json').exists())
        self.assertEqual(sum('boot' in argv for argv in self.phone_calls()), 1)

    def test_slot_mismatch_stops_before_boot_and_cannot_retry(self):
        args = self.prepared()
        self.bad_fastboot_slot = True
        with self.assertRaisesRegex(RuntimeError, 'current-slot'):
            controller.execute(args)
        self.assertFalse((self.package / controller.CONSUMED).exists())
        phones = list(self.phone_calls())
        with self.assertRaises(RuntimeError):
            controller.execute(args)
        self.assertEqual(self.phone_calls(), phones)
        self.assertEqual(sum('boot' in argv for argv in phones), 0)

    def test_wrong_android_identity_does_not_reboot(self):
        args = self.prepared()
        self.bad_serial = True
        with self.assertRaisesRegex(RuntimeError, 'serial'):
            controller.execute(args)
        self.assertEqual(len(self.phone_calls()), 1)
        self.assertEqual(sum('reboot' in argv for argv in self.phone_calls()), 0)

    def test_monitor_is_separate_readonly_and_redacts_result(self):
        args = self.prepared()
        controller.execute(args)
        self.calls.clear()
        result = controller.monitor(args)
        self.assertEqual(len(self.phone_calls()), 8)
        self.assertNotIn(self.serial, json.dumps(result))
        self.assertFalse(any('boot' in argv or 'reboot' in argv for argv in self.phone_calls()))


if __name__ == '__main__':
    unittest.main()
