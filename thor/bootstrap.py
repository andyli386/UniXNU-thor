#!/usr/bin/env python3
"""Create the working copy of one version of the port from the sources and the inputs.

    python3.13 -I thor/bootstrap.py thor/versions/v247.json [--name NAME] [--libraries-only]

The working copy (work/<release>, or work/<name>) is generated; nothing is cloned from an older working copy:

  device/      the device repository (src/UniXNU-thor, THOR_PONG) through links, with a real copy of scripts/, so
               that the build outputs land here and not in the source tree
  build-tree/  made by the device repository's own build script (links and the three build scripts); the kernel
               source tree build-tree/.cache/xnu-work is then made from the pinned Apple XNU checkout, the recorded
               patch series and the kernel tree checkpoint of the version, and compared with the recorded tree hash
  prepare/     sources, build scripts and host tests of the injected libraries (thor/libraries)
  runtime/     the libraries: rebuilt from their sources and compared with the recorded digests; the signed ones
               copied from inputs/signed
  os-patch.bin when the version names an OS patch (thor/os_patch.py): the changed blocks of the system window,
               checked against the digest in the version file; package.py puts it into the image and the
               kernel streams it from boot_a at boot
  thor-gpu.defconfig, runtime/env.sh, runtime/final-inputs.json, recipe.json
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import os_patch as os_patch_format                     # noqa: E402

HERE = Path(__file__).resolve().parent                 # <root>/src/UniXNU-thor/thor
REPO = HERE.parent
ROOT = REPO.parent.parent                              # <root>
SRC, INPUTS, WORK = ROOT / 'src', ROOT / 'inputs', ROOT / 'work'
PONG = Path(os.environ.get('THOR_PONG') or SRC / 'UniXNU-thor')      # the device tree is this repository itself
XNU = PONG / 'refer/ipados18/xnu-11417.140.69'


def run(args, cwd=None, check=True, env=None):
    r = subprocess.run([str(a) for a in args], cwd=cwd and str(cwd), env=env, capture_output=True, text=True)
    if check and r.returncode:
        raise SystemExit(f'failed ({r.returncode}): {args[:4]}\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}')
    return r


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_env():
    """the variables thor/env.sh exports"""
    out = run(['bash', '-c', f'source "{HERE / "env.sh"}" && env -0']).stdout
    keep = ('THOR_', 'PONG_')
    return {k: v for k, v in (item.split('=', 1) for item in out.split('\0') if item) if k.startswith(keep)}


def device_tree(work):
    device = work / 'device'
    device.mkdir()
    for entry in sorted(os.listdir(PONG)):
        if entry in ('.git', 'scripts', 'mac-build', 'out', '__pycache__'):
            continue
        os.symlink(PONG / entry, device / entry)
    shutil.copytree(PONG / 'scripts', device / 'scripts', ignore=shutil.ignore_patterns('__pycache__'))


def prepare_build_tree(work, env):
    shutil.copy2(HERE / 'thor-gpu.defconfig', work / 'thor-gpu.defconfig')
    run([sys.executable, '-I', work / 'device/scripts/build-ipados18-pong-macos.py', '--prepare-only',
         '--tree', work / 'build-tree', '--volume-layout', INPUTS / 'private/volume-layout.json',
         '--config-path', work / 'thor-gpu.defconfig', '--artifact-dir', work / 'kernel'], env=env)


def kernel_tree(work, checkpoint, env):
    tree = work / 'build-tree'
    manifest = json.loads((REPO / f'patches/xnu-ipados18/checkpoints/{checkpoint}/manifest.json').read_text())
    head = run(['git', '-C', XNU, 'rev-parse', 'HEAD']).stdout.strip()
    if head != manifest['upstream_commit']:
        raise SystemExit(f'the pinned XNU checkout is at {head}, the checkpoint was made from {manifest["upstream_commit"]}')
    xnu = tree / '.cache/xnu-work'
    run(['git', 'clone', '--quiet', '--shared', XNU, xnu])
    series_dir = REPO / 'patches/xnu-ipados18'
    series = [l.strip() for l in (series_dir / 'series').read_text().splitlines() if l.strip() and not l.startswith('#')]
    rejected = []
    for name in series:
        r = run(['patch', '--batch', '--fuzz=3', '--forward', '-p1', '-i', series_dir / name], cwd=xnu, check=False)
        if r.returncode:
            rejected.append(name)
    leftovers = [p for p in xnu.rglob('*') if p.is_file() and p.name.endswith(('.orig', '.rej'))]
    for p in leftovers:
        p.unlink()
    run(['git', 'apply', '--whitespace=nowarn', REPO / f'patches/xnu-ipados18/checkpoints/{checkpoint}/xnu-after-series.patch'], cwd=xnu)
    # the tree the build will compile is the recorded one
    index = work / '.tree-index'
    env_index = dict(os.environ, GIT_INDEX_FILE=str(index))
    run(['git', 'add', '-A', '.'], cwd=xnu, env=env_index)
    tree_hash = run(['git', 'write-tree'], cwd=xnu, env=env_index).stdout.strip()
    index.unlink()
    recorded = manifest[[k for k in manifest if k.endswith('_tree') and k.startswith('v')][-1]]
    if tree_hash != recorded:
        raise SystemExit(f'the kernel source tree is {tree_hash}, the checkpoint recorded {recorded}')
    return dict(tree=tree_hash, series=len(series), patches_with_rejected_hunks=rejected, leftovers_removed=len(leftovers))


def console_header(blob, stage_path):
    lines = [f'#define THOR_CONSOLE_V172_STAGE_PATH "{stage_path}"', 'static const unsigned char thor_console_v172_payload[] = {']
    for off in range(0, len(blob), 16):
        lines.append('  ' + ''.join(f'0x{x:02x},' for x in blob[off:off + 16]) + ('' if off + 16 < len(blob) else ''))
    lines.append('};')
    return '\n'.join(lines) + '\n'


def metal_pool_header(work, table):
    bridge, turnip = 'PongMetalBridge.dylib', 'libvulkan_freedreno.dylib'
    sizes = {name: table['libraries'][name]['bytes'] for name in (bridge, turnip)}
    return f'''#pragma once
#define THOR_METAL_POOL_PAYLOAD_BYTES {sizes[bridge]}u
#define THOR_METAL_POOL_NAME "{table['metal_pool_name']}"
#define THOR_TURNIP_V162_PAYLOAD_BYTES {sizes[turnip]}u
extern const unsigned char thor_metal_pool_payload[THOR_METAL_POOL_PAYLOAD_BYTES];
extern const unsigned char thor_turnip_v162_payload[THOR_TURNIP_V162_PAYLOAD_BYTES];
__asm__(".section __TEXT,__const\\n.p2align 12\\n.globl _thor_metal_pool_payload\\n_thor_metal_pool_payload:\\n.incbin \\"{work / 'runtime' / bridge}\\"\\n.text\\n");
__asm__(".section __TEXT,__const\\n.p2align 12\\n.globl _thor_turnip_v162_payload\\n_thor_turnip_v162_payload:\\n.incbin \\"{work / 'runtime' / turnip}\\"\\n.text\\n");
'''


def libraries(work, env):
    table = json.loads((HERE / 'libraries.json').read_text())
    runtime = work / 'runtime'
    runtime.mkdir()
    for name, spec in sorted(table['libraries'].items()):
        if spec.get('script'):
            run(['/bin/bash', work / spec['script']], cwd=work, env=env)
        else:
            shutil.copy2(ROOT / spec['input'], runtime / name)
        blob = (runtime / name).read_bytes()
        if hashlib.sha256(blob).hexdigest() != spec['sha256'] or len(blob) != spec['bytes']:
            raise SystemExit(f'{name} does not have the recorded digest: rebuilt from {spec.get("script") or spec.get("input")}')
        uuid = run(['xcrun', 'dwarfdump', '--uuid', runtime / name]).stdout.split()[1]
        if uuid != spec['uuid']:
            raise SystemExit(f'{name}: UUID {uuid}, recorded {spec["uuid"]}')
        run(['codesign', '--verify', '--strict', runtime / name])
    shutil.copy2(INPUTS / 'signed/ui-tag.txt', runtime / 'ui-tag.txt')
    final = dict(pool_path=table['pool_path'], diagnostic_stage_path=table['console_stage_path'],
                 payloads={name: {k: spec[k] for k in ('sha256', 'uuid', 'bytes')} for name, spec in table['libraries'].items()})
    (runtime / 'final-inputs.json').write_text(json.dumps(final, indent=2) + '\n')
    lines = [f'export {spec["variable"]}="{runtime / name}"' for name, spec in sorted(table['libraries'].items()) if spec.get('variable')]
    (runtime / 'env.sh').write_text('\n'.join(lines) + '\n')
    kern = work / 'build-tree/.cache/xnu-work/bsd/kern'
    console = table['libraries']['libpong-console-v205.dylib']
    header = console_header((runtime / 'libpong-console-v205.dylib').read_bytes(), table['console_stage_path'])
    (kern / 'thor_console_v172_payload.h').write_text(header)
    pool = metal_pool_header(work, table)
    (runtime / 'thor_metal_pool_payload.h').write_text(pool)
    (kern / 'thor_metal_pool_payload.h').write_text(pool)
    return dict(libraries=len(table['libraries']), console_header_bytes=len(header.encode()),
                rebuilt=[name for name, spec in table['libraries'].items() if spec.get('script')],
                copied=[name for name, spec in table['libraries'].items() if not spec.get('script')])


def os_patch(work, version):
    """the changed blocks of the system window that the image carries (thor/os_patch.py), when the version names one"""
    if not version.get('os_patch'):
        return None
    source = ROOT / version['os_patch']['file']
    blob = source.read_bytes()
    if blob[:8] != b'THOROSP2':
        raise SystemExit(f'{source} is not an OS patch')
    if hashlib.sha256(blob).hexdigest() != version['os_patch']['sha256'] or len(blob) != version['os_patch']['bytes']:
        raise SystemExit(f'{source} is not the OS patch the version file records')
    try:
        patch = os_patch_format.load(source)
    except AssertionError as error:
        raise SystemExit(f'{source} is not a complete OS patch: {error}')
    if patch['window_bytes'] != version['os_patch']['window_bytes'] or len(patch['records']) != version['os_patch']['records']:
        raise SystemExit(f'{source} is not the OS patch the version file describes (window, records)')
    shutil.copy2(source, work / 'os-patch.bin')
    return dict(file=version['os_patch']['file'], sha256=version['os_patch']['sha256'], bytes=len(blob),
                records=len(patch['records']), window_bytes=patch['window_bytes'])


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('version', type=Path, help='thor/versions/vNNN.json')
    parser.add_argument('--name', help='name of the working copy (default: the release name)')
    args = parser.parse_args()
    version = json.loads(args.version.read_text())
    release = version['release']
    name = args.name or release
    work = WORK / name
    if work.exists():
        raise SystemExit(f'{work} already exists: a working copy is created once; remove it to create it again')
    env = dict(os.environ, **source_env())
    (work).parent.mkdir(exist_ok=True)
    work.mkdir()
    shutil.copytree(HERE / 'libraries', work / 'prepare', ignore=shutil.ignore_patterns('__pycache__'))
    device_tree(work)
    prepare_build_tree(work, env)
    tree = kernel_tree(work, version['checkpoint'], env)
    libs = libraries(work, env)
    patch = os_patch(work, version)
    record = dict(version=version['version'], release=release, checkpoint=version['checkpoint'], kernel_source_tree=tree, **libs,
                  os_patch=patch,
                  device_repository=run(['git', '-C', PONG, 'rev-parse', 'HEAD']).stdout.strip(),
                  repository=run(['git', '-C', REPO, 'rev-parse', 'HEAD']).stdout.strip())
    (work / 'recipe.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(record, indent=1))


if __name__ == '__main__':
    main()
