#!/usr/bin/env python3
"""Run the isolated ENTRY10 normal-XNU-VM-preparation build stages."""
from pathlib import Path
import argparse, datetime, hashlib, json, os, re, signal, subprocess, tomllib, traceback

TREE = Path(__file__).resolve().parents[1]
ROOT = TREE.parent.parent
RUN = ROOT / 'analysis/thor-entry10-kernel-build-20261024'
BASE = ROOT / 'sources/UniXNU'
TARGET = TREE / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837'
BASE_KERNEL = BASE / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837.unstripped.noctf'
BASE_SHA = 'c262f87c0718a0ccf72bd8dea288ac1dbc7504b3649c66810c2f276d23386908'

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def stamp():
    return datetime.datetime.now().astimezone().isoformat()

def capture(argv):
    return subprocess.check_output([str(x) for x in argv], text=True, timeout=30,
                                   env=globals().get('env')).strip()

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('stage', choices=('bootstrap', 'providers', 'link', 'audit'))
args = parser.parse_args()
RUN.mkdir(exist_ok=True)
number = 1
while (RUN / f'{args.stage}-{number:02d}.json').exists():
    number += 1
ident = f'{args.stage}-{number:02d}'
state_path = RUN / f'{ident}.json'
state = {'stage': args.stage, 'attempt': number, 'phase': 'preflight', 'started_at': stamp(),
         'tree': str(TREE), 'jobs': 4, 'purpose': 'host-only ENTRY10 normal XNU VM preparation; no loader or phone image',
         'phone_commands': False, 'prelink_attempted': False, 'runtime_crypto_selftests_executed': False,
         'commands': []}

def save():
    for path in (state_path, RUN / 'status.json'):
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(state, indent=2) + '\n')
        temp.replace(path)

def run(name, argv, extra=None, timeout=1800):
    record = {'name': name, 'argv': [str(x) for x in argv], 'cwd': str(TREE),
              'environment_overrides': extra or {}, 'started_at': stamp(),
              'log': str(RUN / f'{ident}-{name}.log')}
    state['commands'].append(record)
    state['phase'] = name
    save()
    with Path(record['log']).open('x') as log:
        child = subprocess.Popen(argv, cwd=TREE, env={**env, **(extra or {})},
                                 stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                 start_new_session=True)
        record['pid'] = child.pid
        save()
        try:
            rc = child.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=10)
            raise RuntimeError(f'{name}: exceeded {timeout} seconds')
    record.update(returncode=rc, finished_at=stamp(), log_sha256=sha(Path(record['log'])))
    save()
    return rc

try:
    save()
    if sha(BASE_KERNEL) != BASE_SHA:
        raise RuntimeError('Original baseline artifact changed')
    native_frozen = ROOT / 'artifacts/mainline-4k-native-arm-diagnostic-20261024/kernel.native-arm-diagnostic.macho'
    native_sha = '71953c3569fed461cd07ec8969b52ac0f78c297c06c111351e8a56b6df49e573'
    if sha(native_frozen) != native_sha:
        raise RuntimeError('Frozen native-ARM kernel changed')
    native_tree = ROOT / 'sources/UniXNU-native-arm-diagnostic'
    if capture(['git', '-C', native_tree, 'rev-parse', 'HEAD']) != 'f83004f5c8b8112a89f8244f51ea82ce5783d842':
        raise RuntimeError('Frozen native-ARM source HEAD changed')
    if capture(['git', '-C', native_tree, 'status', '--porcelain', '--untracked-files=no']):
        raise RuntimeError('Frozen native-ARM tracked source changed')
    state['native_baseline_sha256_before'] = native_sha
    state['baseline_sha256_before'] = BASE_SHA
    state['base_source_head'] = capture(['git', '-C', BASE, 'rev-parse', 'HEAD'])
    state['base_source_status_before'] = capture(['git', '-C', BASE, 'status', '--porcelain'])
    state['worktree_head'] = capture(['git', '-C', TREE, 'rev-parse', 'HEAD'])
    if capture(['git', '-C', TREE, 'merge-base', 'a7e270cb9e20fac230515126977ab29521362775', 'HEAD']) != 'a7e270cb9e20fac230515126977ab29521362775':
        raise RuntimeError('Diagnostic tree does not retain the reviewed mainline/guard fix')
    feeds = {x['name']: x for x in tomllib.loads((TREE / 'feeds.toml').read_text())['source']}
    state['pins'] = {}
    for name in ('xnu', 'libplatform', 'libdispatch', 'AvailabilityVersions', 'corecrypto-2023'):
        p = TREE / 'refer' / name
        pin = capture(['git', '-C', p, 'rev-parse', 'HEAD'])
        if pin != feeds[name]['commit'] or capture(['git', '-C', p, 'status', '--porcelain']):
            raise RuntimeError('Unexpected reference source state: ' + name)
        state['pins'][name] = pin
    for rel, pin in [('drivers/ArmCpu', '1951cc8444b340d7d8879e9418220cf29eb711d7'),
                     ('drivers/ArmDTPlatform', '0d544705c2e89725caabc0542bb96bc8febd8c70'),
                     ('kits/NativePrelink', '2b6ffb50117eee820b98aca9bd51b5c7dfb2970e')]:
        if capture(['git', '-C', TREE / rel, 'rev-parse', 'HEAD']) != pin:
            raise RuntimeError('Unexpected module revision: ' + rel)
        if capture(['git', '-C', TREE / rel, 'status', '--porcelain']):
            raise RuntimeError('Unexpected module change: ' + rel)
        state['pins'][rel] = pin
    # Clang also consumes implicit CPATH/LIBRARY_PATH even when its argv is exact.
    # Keep only ordinary process identity/locale/temp context, then construct all
    # build settings below. Never inherit toolchain, make or XNU search options.
    allowed_context = ('HOME', 'USER', 'LOGNAME', 'TMPDIR', 'LANG', 'LC_ALL',
                       'LC_CTYPE', 'TZ', 'SHELL')
    env = {k: os.environ[k] for k in allowed_context if k in os.environ}
    hidden_toolchain_keys = ('CPATH', 'C_INCLUDE_PATH', 'CPLUS_INCLUDE_PATH',
                            'OBJC_INCLUDE_PATH', 'OBJCPLUS_INCLUDE_PATH', 'LIBRARY_PATH',
                            'COMPILER_PATH', 'GCC_EXEC_PREFIX', 'SDKROOT', 'SDK_DIR',
                            'TOOLCHAINS', 'CCC_OVERRIDE_OPTIONS', 'CLANG_CONFIG_FILE_USER_DIR',
                            'CLANG_CONFIG_FILE_SYSTEM_DIR', 'DYLD_LIBRARY_PATH',
                            'DYLD_FALLBACK_LIBRARY_PATH', 'DYLD_INSERT_LIBRARIES',
                            'CMAKE_PREFIX_PATH', 'PKG_CONFIG_PATH', 'MAKEFLAGS', 'CPPFLAGS')
    state['environment_isolation'] = {
        'policy': 'allow ordinary identity/locale/temp context only, then explicit build environment',
        'inherited_context_keys': sorted(env),
        'excluded_implicit_toolchain_keys_found': sorted(k for k in hidden_toolchain_keys if k in os.environ),
        'all_other_inherited_variables_excluded': True,
    }
    selected = {
        'PATH': str(ROOT / 'toolchains/python313/bin') + ':/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin',
        'DEVELOPER_DIR': '/Applications/Xcode.app/Contents/Developer',
        'LC_ALL': 'C', 'GIT_TERMINAL_PROMPT': '0',
        'JOBS': '4', 'XNU_BOOTSTRAP': '0', 'XNU_BUILD_TARGET': 'build', 'XNU_BUILD_WERROR': '0',
        'XNU_NATIVE_ARCH_TIMER': '1', 'XNU_ARMCPU': '1', 'XNU_ARM_DT_PLATFORM': '1',
        'XNU_THOR_ENTRY_STOP': '0', 'XNU_THOR_EARLY_C': '1',
        'XNU_SOURCE_CORECRYPTO14': '1', 'XNU_CRYPTO_BOOTSTRAP': 'source', 'XNU_BOOT_PROGRESS': '0',
        'UNIXNU_XNU_TAG': feeds['xnu']['ref'], 'UNIXNU_XNU_COMMIT': feeds['xnu']['commit'],
        'UNIXNU_XNU_DARWIN': feeds['xnu']['darwin'], 'UNIXNU_XNU_VERSION': feeds['xnu']['version'],
        'UNIXNU_DRIVERKIT_TAG': feeds['xnu']['driverkit_tag'],
        'UNIXNU_TRUSTCACHE_TAG': feeds['xnu']['trustcache_tag'],
        'UNIXNU_LIBPLATFORM_TAG': feeds['libplatform']['ref'],
        'UNIXNU_LIBDISPATCH_TAG': feeds['libdispatch']['ref'],
        'UNIXNU_AVAILABILITYVERSIONS_TAG': feeds['AvailabilityVersions']['ref'],
    }
    env.update(selected)
    state['build_environment'] = selected
    script = TREE / 'scripts/build-xnu-4k-baseline.sh'
    state['core_build_script_sha256'] = sha(script)
    defaults = {x: selected.get(x, '0') for x in sorted(set(re.findall(r'\$\{(XNU_[A-Z0-9_]+):-0\}', script.read_text())))}
    state['effective_zero_default_options'] = defaults
    state['toolchain'] = {name: capture(argv) for name, argv in {
        'xcode': ['xcodebuild', '-version'], 'sdk': ['xcrun', '--sdk', 'iphoneos', '--show-sdk-version'],
        'sdk_path': ['xcrun', '--sdk', 'iphoneos', '--show-sdk-path'], 'clang': ['xcrun', 'clang', '--version'],
        'python': [str(ROOT / 'toolchains/python313/bin/python3'), '--version']}.items()}
    py = ROOT / 'toolchains/python313/bin/python3'
    if args.stage == 'bootstrap':
        rc = run('kernel', ['bash', str(script)], {'XNU_BOOTSTRAP': '1'})
        required = [TARGET / 'osfmk/DEVELOPMENT/startup.o.json', TARGET / 'link.filelist',
                    TARGET / 'iokit/DEVELOPMENT/IOPlatformExpert.cpo.json', TARGET / 'iokit/DEVELOPMENT/IOCPU.cpo.json']
        if not all(p.is_file() for p in required):
            raise RuntimeError('Bootstrap did not produce the required genuine compile records')
        inputs = [Path(x.strip()) for x in (TARGET / 'link.filelist').read_text().splitlines() if x.strip()]
        if not inputs or not all(p.is_file() for p in inputs):
            raise RuntimeError('Bootstrap kernel link inputs are incomplete')
        state['bootstrap_returncode'] = rc
        state['bootstrap_link_inputs'] = len(inputs)
        state['bootstrap_note'] = 'Compile records only; nonzero link result is reviewed before provider/link stages'
        state['phase'] = 'bootstrap-records-ready'
    elif args.stage == 'providers':
        for name, argv in [('crypto-plain', [py, TREE / 'scripts/build-corecrypto-14.py']),
                           ('crypto-namespaced', [py, TREE / 'scripts/build-corecrypto-14.py', '--namespace']),
                           ('arm-providers', [py, TREE / 'scripts/build-thor-arm-providers.py', 'ArmDTPlatform', 'ArmCpu'])]:
            if run(name, argv, timeout=1200):
                raise RuntimeError(name + ' failed')
        state['phase'] = 'providers-built'
    elif args.stage == 'link':
        if run('kernel', ['bash', str(script)]):
            raise RuntimeError('Final native ARM kernel link failed')
        kernel = TARGET / 'mach.development.bcm2837.unstripped.noctf'
        state['kernel'] = {'path': str(kernel), 'bytes': kernel.stat().st_size, 'sha256': sha(kernel)}
        state['phase'] = 'kernel-linked-and-sealed'
    elif args.stage == 'audit':
        kernel = TARGET / 'mach.development.bcm2837.unstripped.noctf'
        for name, argv in [('objects', [py, TREE / 'scripts/audit-xnu-objects.py']),
                           ('kernel', [py, TREE / 'scripts/audit-xnu-kernel.py', kernel])]:
            if run(name, argv, timeout=300):
                raise RuntimeError(name + ' audit failed')
        state['phase'] = 'host-audited'
    state['native_baseline_sha256_after'] = sha(native_frozen)
    if state['native_baseline_sha256_after'] != native_sha:
        raise RuntimeError('Frozen native-ARM kernel changed during stage')
    if capture(['git', '-C', native_tree, 'status', '--porcelain', '--untracked-files=no']):
        raise RuntimeError('Frozen native-ARM tracked source changed during stage')
    state['baseline_sha256_after'] = sha(BASE_KERNEL)
    if state['baseline_sha256_after'] != BASE_SHA:
        raise RuntimeError('Original baseline artifact changed')
    state['base_source_status_after'] = capture(['git', '-C', BASE, 'status', '--porcelain'])
    if state['base_source_status_after'] != state['base_source_status_before']:
        raise RuntimeError('Main source status changed during this stage')
except Exception:
    state.update(phase='failed', error=traceback.format_exc())
finally:
    state['finished_at'] = stamp()
    save()
    print(json.dumps(state, indent=2), flush=True)
if state['phase'] == 'failed':
    raise SystemExit(1)
