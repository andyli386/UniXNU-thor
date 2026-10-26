#!/usr/bin/env python3
"""Prepare a sealed Mac one-use controller; preparation never contacts a phone.

Execution is separate and requires --recording-confirmed plus exact run/image pins.
Every started execution permanently claims its run, even if any command fails.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

PROPERTIES = ('sys.boot_completed', 'ro.boot.slot_suffix', 'init.svc.zygote',
              'init.svc.bootanim', 'ro.product.vendor.device',
              'ro.build.version.incremental', 'ro.vendor.build.version.incremental')
CONSUMED = 'ONE_SHOT_USED-DO-NOT-BOOT-AGAIN'


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_text())


def private_write(path, value):
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def rooted(root, path):
    value = (root / path).resolve()
    require(value.is_relative_to(root), 'Input path escapes project root')
    return value


def source_head(source):
    head = subprocess.run(['git', '-C', str(source), 'rev-parse', 'HEAD'],
                          check=True, capture_output=True, text=True, timeout=15).stdout.strip()
    dirty = subprocess.run(['git', '-C', str(source), 'status', '--porcelain'],
                           check=True, capture_output=True, text=True, timeout=15).stdout.strip()
    require(not dirty, 'UEFI source is not clean')
    return head


def validate_inputs(config):
    root = Path(config['root'])
    paths = {key: rooted(root, value) for key, value in config['inputs'].items()}
    for key, path in paths.items():
        require(path.is_dir() if key == 'source' else path.is_file(), 'Missing input: ' + key)
    pins, proof, manifest, firmware, device = [read(paths[key]) for key in
        ('pins', 'offline_proof', 'manifest', 'firmware_release', 'device_proof')]
    require(pins['schema'] == 'thor-mem03-controller-pins-v1', 'Controller pins schema')
    require(pins['run_id'] == config['run_id'] == manifest['run_id'], 'Run pin mismatch')
    require(proof['result'] == 'PASS' and proof['ready_for_one_use_temporary_boot'] is True,
            'Independent L2 is not PASS/ready')
    require(proof['run_id'] == config['run_id'], 'Independent L2 run mismatch')
    require(isinstance(proof['checks'], list) and proof['checks'] and
            all(check['passed'] is True for check in proof['checks']), 'Independent L2 checks incomplete')
    require(proof['hardware_test_attempts'] == 0 and manifest['phone_attempts'] == 0,
            'Package already has hardware attempts')
    require(manifest['seed_reuse_allowed'] is False and manifest['seed_raw_logged'] is False,
            'One-use seed contract missing')
    image_sha = sha(paths['image'])
    image_bytes = paths['image'].stat().st_size
    require(image_bytes > 0 and image_sha == pins['image_sha256'] == manifest['image_sha256'] ==
            proof['inputs']['android_boot_v4']['sha256'], 'Image SHA pin mismatch')
    require(image_bytes == pins['image_bytes'] == manifest['image_bytes'], 'Image length pin mismatch')
    require(image_bytes == proof['inputs']['android_boot_v4']['bytes'], 'L2 image length mismatch')
    require(sha(paths['fd']) == pins['fd_sha256'] == manifest['fd_sha256'] ==
            firmware['uefi_fd_sha256'] == proof['inputs']['uefi_fd']['sha256'], 'FD SHA pin mismatch')
    require(source_head(paths['source']) == pins['uefi_commit'] == manifest['uefi_commit'] ==
            firmware['uefi_commit'], 'UEFI source commit mismatch')
    for key in ('kernel_sha256', 'kernel_source_revision'):
        require(manifest[key] == pins[key], 'Kernel pin mismatch: ' + key)
    kernel = proof['inputs'].get('native_xnu', proof['inputs'].get('xnu_kernel', {}))
    require(kernel.get('sha256') == pins['kernel_sha256'], 'L2 kernel mismatch')
    require(firmware['transition_sha_matches_expected_header'] is True, 'Transition binding missing')
    require(device['schema'] == 'thor-mem03-mac-readonly-preflight-v1' and
            device['run_id'] == config['run_id'] and device['unique_USB_target'] is True,
            'Device proof schema/run/uniqueness mismatch')
    expected = device['seven_expected_properties']
    require(set(expected) == set(PROPERTIES) and device['seven_observed_properties'] == expected,
            'Seven Android properties must be present and equal')
    require(expected['sys.boot_completed'] == '1' and
            expected['ro.product.vendor.device'] == 'thor' and
            expected['ro.boot.slot_suffix'] in ('_a', '_b'), 'Invalid Thor Android baseline')
    require(re.fullmatch('[0-9a-f]{64}', device['serial_sha256']) is not None,
            'Missing or invalid serial hash')
    require(all(device[key] == 0 for key in ('adb_reboots', 'fastboot_commands', 'image_boot_attempts')),
            'Device preflight must be read-only')
    for name, binary in config['binaries'].items():
        path = Path(binary['path'])
        require(path.is_file() and sha(path) == binary['sha256'], 'Tool binary pin mismatch: ' + name)
    return paths, pins, device


def registry(root, image_sha):
    return root / 'state' / 'consumed-images' / (image_sha + '.json')


def unused(paths, root, pins):
    require(not (paths['image'].parent / CONSUMED).exists(), 'Image is consumed')
    require(not (paths['image'].parent / 'INVALID-NOT-FOR-PHONE').exists(), 'Image is quarantined')
    require(not registry(root, pins['image_sha256']).exists(), 'Image hash is consumed')


def prepare(args):
    root = args.root.resolve()
    require(re.fullmatch(r'thor-[a-z0-9-]{8,90}', args.run_id) is not None, 'Invalid run ID')
    names = ('offline_proof', 'manifest', 'firmware_release', 'source', 'fd', 'image', 'device_proof', 'pins')
    config = {'schema': 'thor-mem03-mac-controller-config-v1', 'root': str(root),
              'run_id': args.run_id, 'inputs': {}, 'binaries': {}}
    for key in names:
        config['inputs'][key] = str(rooted(root, getattr(args, key)))
    for key in ('adb', 'fastboot'):
        path = getattr(args, key).resolve()
        require(path.is_file(), 'Missing tool: ' + key)
        config['binaries'][key] = {'path': str(path), 'sha256': sha(path)}
    paths, pins, _ = validate_inputs(config)
    run = root / 'runs' / args.run_id
    out = run / 'l2' / 'controller'
    require(not (run / 'l3').exists(), 'L3 already exists')
    unused(paths, root, pins)
    config['input_sha256'] = {key: sha(path) for key, path in paths.items() if key != 'source'}
    config['source_commit'] = pins['uefi_commit']
    out.mkdir(parents=True, mode=0o700, exist_ok=False)
    script = out / 'controller.py'
    with script.open('x') as stream:
        stream.write(Path(__file__).read_text())
    script.chmod(0o600)
    private_write(out / 'sealed-inputs.json', config)
    record = {'schema': 'thor-mem03-mac-controller-preflight-v1', 'created_at': now(),
              'status': 'READY_NOT_EXECUTED', 'result': 'PASS_HOST_CONTROLLER_REVIEW',
              'run_id': args.run_id, 'image_sha256': pins['image_sha256'],
              'image_bytes': pins['image_bytes'], 'source_commit': pins['uefi_commit'],
              'files': {'controller.py': sha(script), 'sealed-inputs.json': sha(out / 'sealed-inputs.json')},
              'offline_proof_sha256': config['input_sha256']['offline_proof'],
              'pins_sha256': config['input_sha256']['pins'], 'phone_actions': 0,
              'recording_confirmed': False, 'max_adb_reboots': 1, 'max_fastboot_boots': 1,
              'automatic_retries': 0, 'no_flash_erase_format_or_slot': True,
              'android_monitor_separate': True}
    private_write(out / 'controller-preflight.json', record)
    return {'result': record['result'], 'status': record['status'], 'run_id': args.run_id,
            'preflight': str(out / 'controller-preflight.json'), 'phone_actions': 0}


def sealed(preflight):
    preflight = preflight.resolve()
    record = read(preflight)
    require(record['schema'] == 'thor-mem03-mac-controller-preflight-v1' and
            record['status'] == 'READY_NOT_EXECUTED' and record['recording_confirmed'] is False,
            'Invalid sealed controller preflight')
    for name, digest in record['files'].items():
        require(name in ('controller.py', 'sealed-inputs.json') and
                sha(preflight.parent / name) == digest, 'Sealed controller file altered')
    require(set(record['files']) == {'controller.py', 'sealed-inputs.json'}, 'Incomplete script seal')
    require(sha(Path(__file__)) == record['files']['controller.py'], 'Running controller differs from sealed script')
    config = read(preflight.parent / 'sealed-inputs.json')
    require(preflight.parent == Path(config['root']) / 'runs' / config['run_id'] / 'l2' / 'controller',
            'Controller copied outside sealed run')
    for key, digest in config['input_sha256'].items():
        require(sha(Path(config['inputs'][key])) == digest, 'Sealed input altered: ' + key)
    paths, pins, device = validate_inputs(config)
    require(record['run_id'] == pins['run_id'] and record['image_sha256'] == pins['image_sha256'],
            'Preflight pin mismatch')
    return record, config, paths, pins, device


def command(argv, directory, label, timeout=15):
    """Fixed argv, no shell. Raw stdout/stderr remain separate private local files."""
    stdout = directory / (label + '.stdout.log')
    stderr = directory / (label + '.stderr.log')
    code = None
    timed_out = False
    for path in (stdout, stderr):
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
    try:
        with stdout.open('wb') as out, stderr.open('wb') as err:
            result = subprocess.run(argv, stdout=out, stderr=err, timeout=timeout,
                                    check=False, shell=False)
            code = result.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
    finally:
        private_write(directory / (label + '.result.json'),
                      {'returncode': code, 'timed_out': timed_out, 'finished_at': now(),
                       'stdout_sha256': sha(stdout), 'stderr_sha256': sha(stderr)})
    require(not timed_out and code == 0, 'Command stopped: ' + label)
    return stdout.read_text(errors='replace'), stderr.read_text(errors='replace')


def adb_identity(config, device, directory, prefix='android'):
    adb = config['binaries']['adb']['path']
    output, _ = command([adb, 'devices', '-l'], directory, prefix + '-devices')
    rows = [line.split() for line in output.splitlines()
            if line.strip() and not line.startswith(('List of devices', '*'))]
    require(len(rows) == 1 and len(rows[0]) >= 2 and rows[0][1] == 'device' and
            'product:thor' in rows[0][2:], 'Unique Android Thor is required')
    serial = rows[0][0]
    require(hashlib.sha256(serial.encode()).hexdigest() == device['serial_sha256'], 'Android serial mismatch')
    for i, key in enumerate(PROPERTIES):
        output, _ = command([adb, '-s', serial, 'shell', 'getprop', key], directory,
                            prefix + '-property-' + str(i))
        require(output.strip() == device['seven_expected_properties'][key], 'Android property mismatch: ' + key)
    return serial


def fastboot_identity(config, serial, device, directory):
    fastboot = config['binaries']['fastboot']['path']
    output, _ = command([fastboot, 'devices'], directory, 'fastboot-devices')
    rows = [line.split() for line in output.splitlines() if line.strip()]
    require(len(rows) == 1 and len(rows[0]) >= 2 and rows[0][1] == 'fastboot' and
            rows[0][0] == serial, 'Unique matching Fastboot serial is required')
    require(hashlib.sha256(rows[0][0].encode()).hexdigest() == device['serial_sha256'], 'Fastboot serial hash mismatch')
    expected = {'product': 'thor', 'current-slot': device['seven_expected_properties']['ro.boot.slot_suffix'][1:]}
    for key, value in expected.items():
        out, err = command([fastboot, '-s', serial, 'getvar', key], directory, 'fastboot-' + key)
        pattern = r'(?m)^\s*(?:\(bootloader\)\s*)?' + re.escape(key) + r':\s*' + re.escape(value) + r'\s*$'
        require(re.search(pattern, out + '\n' + err) is not None, 'Fastboot identity mismatch: ' + key)


def execute(args):
    require(args.recording_confirmed, 'Recording confirmation is required; no commands executed')
    _, config, paths, pins, device = sealed(args.preflight)
    require(args.confirm_run_id == pins['run_id'] and args.confirm_image_sha256 == pins['image_sha256'],
            'Explicit run/image confirmation mismatch')
    root = Path(config['root'])
    controller = args.preflight.resolve().parent
    run = root / 'runs' / config['run_id']
    unused(paths, root, pins)
    require(not (run / 'l3').exists(), 'L3 already exists; no retries')
    # Claim is permanent on every exit, including preflight/USB/timeout failures.
    private_write(controller / 'EXECUTION_CLAIM.json', {'created_at': now(), 'run_id': pins['run_id'],
                  'image_sha256': pins['image_sha256'], 'automatic_retries': 0})
    l3 = run / 'l3'
    l3.mkdir(mode=0o700, exist_ok=False)
    try:
        serial = adb_identity(config, device, l3)
        command([config['binaries']['adb']['path'], '-s', serial, 'reboot', 'bootloader'],
                l3, 'adb-reboot-bootloader', timeout=15)
        # One bounded read-only wait; timeout aborts the run, with no reboot retry.
        out, err = command([config['binaries']['fastboot']['path'], '-s', serial, 'getvar', 'product'],
                           l3, 'fastboot-entry-product', timeout=45)
        require(re.search(r'(?m)^\s*(?:\(bootloader\)\s*)?product:\s*thor\s*$', out + '\n' + err)
                is not None, 'Fastboot entry product mismatch')
        fastboot_identity(config, serial, device, l3)
        # Re-check every byte and source pin immediately before consuming/booting.
        sealed(args.preflight)
        unused(paths, root, pins)
        consumed = {'status': 'CONSUMED', 'created_at': now(), 'run_id': pins['run_id'],
                    'image_sha256': pins['image_sha256'], 'boot_result': 'NOT_YET_KNOWN_DO_NOT_RETRY'}
        ledger = registry(root, pins['image_sha256'])
        ledger.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        private_write(ledger, consumed)
        private_write(paths['image'].parent / CONSUMED, consumed)
        out, err = command([config['binaries']['fastboot']['path'], '-s', serial, 'boot', str(paths['image'])],
                           l3, 'fastboot-boot', timeout=45)
        require(re.search(r'Sending[^\n]*OKAY', out + '\n' + err) is not None and
                re.search(r'Booting[^\n]*OKAY', out + '\n' + err) is not None,
                'Fastboot acceptance lines are incomplete; image remains consumed')
        result = {'status': 'FASTBOOT_ACCEPTED_VISUAL_AND_ANDROID_PENDING', 'image_consumed': True,
                  'run_id': pins['run_id'], 'image_sha256': pins['image_sha256'],
                  'automatic_retries': 0, 'android_monitor_executed': False}
    except BaseException:
        private_write(l3 / 'STOPPED-DO-NOT-RETRY.json', {'status': 'STOPPED_NO_AUTOMATIC_RETRY',
                      'finished_at': now(), 'image_consumed': (paths['image'].parent / CONSUMED).exists()})
        raise
    private_write(l3 / 'execution-result.json', result)
    return result


def monitor(args):
    _, config, _, pins, device = sealed(args.preflight)
    run = Path(config['root']) / 'runs' / config['run_id']
    require((run / 'l3').is_dir(), 'No started L3 run')
    out = run / 'l3' / ('android-readonly-' + datetime.datetime.now().strftime('%Y%m%dT%H%M%S%f'))
    out.mkdir(mode=0o700, exist_ok=False)
    adb_identity(config, device, out)
    result = {'result': 'PASS_ANDROID_READONLY_7_OF_7', 'run_id': pins['run_id'],
              'serial_sha256': device['serial_sha256'], 'phone_mutations': 0}
    private_write(out / 'result.json', result)
    return result


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    commands = p.add_subparsers(dest='action', required=True)
    prep = commands.add_parser('prepare', help='Seal files only; no phone I/O')
    prep.add_argument('--root', type=Path, required=True)
    prep.add_argument('--run-id', required=True)
    for key in ('offline-proof', 'manifest', 'firmware-release', 'source', 'fd', 'image', 'device-proof', 'pins'):
        prep.add_argument('--' + key, type=Path, required=True)
    prep.add_argument('--adb', type=Path, required=True)
    prep.add_argument('--fastboot', type=Path, required=True)
    run = commands.add_parser('execute', help='Explicit recorded one-use attempt')
    run.add_argument('--preflight', type=Path, required=True)
    run.add_argument('--recording-confirmed', action='store_true')
    run.add_argument('--confirm-run-id', required=True)
    run.add_argument('--confirm-image-sha256', required=True)
    mon = commands.add_parser('monitor', help='Separate Android read-only check; never reboots')
    mon.add_argument('--preflight', type=Path, required=True)
    return p


def main():
    args = parser().parse_args()
    try:
        result = {'prepare': prepare, 'execute': execute, 'monitor': monitor}[args.action](args)
    except (RuntimeError, OSError, KeyError, ValueError, subprocess.SubprocessError):
        # Raw command diagnostics stay local; never print serial-bearing exception text.
        print(json.dumps({'result': 'STOPPED', 'automatic_retries': 0,
                          'message': 'Controller validation/action failed; inspect private local receipts.'}))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
