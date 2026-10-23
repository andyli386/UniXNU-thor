#!/usr/bin/env python3
"""Build pinned upstream ArmDTPlatform/ArmCpu using this tree's real XNU C++ ABI.

The core pin expects these archives but omits the old device-overlay builder.
No source shim, binary binding, prelink or synthetic driver is introduced here.
"""
from pathlib import Path
import argparse, hashlib, json, struct, subprocess

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837'
SPECS = {
    'ArmDTPlatform': ('0d544705c2e89725caabc0542bb96bc8febd8c70',
                      'IOPlatformExpert', 'armdt-platform', '_armdt_platform_configured'),
    'ArmCpu': ('1951cc8444b340d7d8879e9418220cf29eb711d7',
               'IOCPU', 'armcpu', '_armcpu_registered'),
}

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def capture(argv):
    return subprocess.check_output([str(x) for x in argv], text=True).strip()

def build(name):
    pin, template, directory, observation = SPECS[name]
    source_dir = ROOT / 'drivers' / name
    if capture(['git', '-C', source_dir, 'rev-parse', 'HEAD']) != pin:
        raise RuntimeError(name + ': wrong source revision')
    if capture(['git', '-C', source_dir, 'status', '--porcelain']):
        raise RuntimeError(name + ': source must be unchanged')
    source = source_dir / (name + '.cpp')
    record_path = TARGET / 'iokit/DEVELOPMENT' / (template + '.cpo.json')
    raw = record_path.read_text().strip().rstrip(',')
    record = json.loads(raw)
    cwd = Path(record['directory']).resolve()
    if not cwd.is_relative_to(ROOT.resolve()):
        raise RuntimeError('Compiler record is from another build tree')
    if not Path(record['file']).resolve().is_relative_to((ROOT / '.cache/xnu-work').resolve()):
        raise RuntimeError('Compiler record input is not this XNU source')
    arguments = record['arguments']
    if arguments.count(record['file']) != 1:
        raise RuntimeError('Unexpected compiler source operands')
    if '-mkernel' not in arguments or '-fapple-kext' not in arguments or '-c' not in arguments:
        raise RuntimeError('Expected the real kernel C++ compile contract')
    if not any(x.startswith('--target=arm64-apple-ios17.') for x in arguments):
        raise RuntimeError('Unexpected compiler target')
    out = ROOT / 'build' / directory
    out.mkdir(parents=True, exist_ok=True)
    obj = out / (name + '.o')
    archive = out / ('lib' + name + '.a')
    command = []
    skip = False
    removed = []
    for arg in arguments:
        if skip:
            removed.append(arg)
            skip = False
            continue
        if arg in ('-o', '-MJ', '-MF', '-MT', '-MQ'):
            removed.append(arg)
            skip = True
        elif arg == record['file']:
            command.append(str(source))
        else:
            command.append(arg)
    command.extend(['-o', str(obj), '-fno-vectorize', '-fno-slp-vectorize', '-mno-implicit-float'])
    report = {'module': name, 'source_commit': pin, 'source_path': str(source),
              'source_sha256': sha(source), 'template_compile_record': str(record_path),
              'template_record_sha256': sha(record_path), 'cwd': str(cwd),
              'command': command, 'removed_output_record_arguments': removed,
              'purpose': 'host-only source-built diagnostic provider',
              'hardware_execution_tested': False}
    (out / 'compile-command.json').write_text(json.dumps(report, indent=2) + '\n')
    with (out / 'compile.log').open('w') as log:
        result = subprocess.run(command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT)
    report['compiler_returncode'] = result.returncode
    if result.returncode:
        (out / 'build-report.json').write_text(json.dumps(report, indent=2) + '\n')
        raise RuntimeError(name + ': compile failed; see ' + str(out / 'compile.log'))
    data = obj.read_bytes()
    if struct.unpack_from('<4I', data) != (0xfeedfacf, 0x100000c, 0, 1):
        raise RuntimeError('Provider is not ordinary ARM64 Mach-O MH_OBJECT')
    position, versions = 32, []
    for _ in range(struct.unpack_from('<I', data, 16)[0]):
        cmd, length = struct.unpack_from('<II', data, position)
        if cmd == 0x32:
            versions.append(struct.unpack_from('<II', data, position + 8))
        position += length
    if versions != [(2, 0x110000)]:
        raise RuntimeError('Provider deployment differs from XNU iOS 17.0 ABI')
    defined = set(capture(['xcrun', 'nm', '-g', '-U', '-j', obj]).splitlines())
    if observation not in defined:
        raise RuntimeError('Real provider observation definition absent')
    archive.unlink(missing_ok=True)
    archive_command = ['xcrun', 'ar', 'rcs', str(archive), str(obj)]
    subprocess.run(archive_command, check=True)
    report.update(object_sha256=sha(obj), archive_path=str(archive), archive_sha256=sha(archive),
                  archive_bytes=archive.stat().st_size, archive_command=archive_command,
                  defined_global_symbols=sorted(defined), observation_symbol=observation,
                  deployment='iOS 17.0', source_unchanged=True)
    (out / 'build-report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(f'{name}: {archive.stat().st_size} bytes, SHA256 {sha(archive)}', flush=True)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('modules', nargs='*', choices=tuple(SPECS))
    args = parser.parse_args()
    for module in args.modules or SPECS:
        build(module)
