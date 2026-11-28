#!/usr/bin/env python3
"""Package, verify and publish the boot image of a working copy.

    python3.13 -I thor/package.py thor/versions/v247.json --work work/v247-unified [--publish]

Everything comes from the new layout and nothing from an older working copy:

  the parent image        images/<version>/ (named in the version file, checked against its record)
  the template image      inputs/image-template/ (the firmware and the EFI tree every version keeps)
  the kernel              <work>/kernel/kernel-ipados18-thor.macho (thor/build-kernel.sh)
  the libraries           <work>/runtime/ (thor/bootstrap.py: rebuilt from thor/libraries, or inputs/signed)
  the device tree         the parent's, plus the /chosen properties the version file names ("chosen"),
                          minus the ones it takes away ("chosen_removed": [names])
  what the version checks thor/versions/vNNN.json: manifest changes, kernel messages that must be new, gone or carried

The image is written next to the working copy with its record (unified-verification.json); --publish copies both
to images/<release>/.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

assert __debug__, "verification needs assertions"

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import thorimage as image                                        # noqa: E402

REPO = HERE.parent
ROOT = REPO.parent.parent
INPUTS, WORK, IMAGES = ROOT / 'inputs', ROOT / 'work', ROOT / 'images'
PONG = Path(os.environ.get('THOR_PONG') or ROOT / 'src/UniXNU-thor')          # the device tree
TEMPLATE = INPUTS / 'image-template' / image.TEMPLATE
KERNEL = 'kernel/kernel-ipados18-thor.macho'
TARGET = {name: '::' + name for name in (image.KERNEL_MEMBER, image.KERNEL_MEMBER + '.adt', image.MANIFEST_MEMBER, image.MENU_MEMBER)}
ADT_MEMBER = image.KERNEL_MEMBER + '.adt'
os.environ.setdefault('THOR_PONG', str(PONG))


def run(args, cwd=None, env=None):
    return subprocess.run([str(a) for a in args], cwd=cwd, env=env, capture_output=True, text=True)


def uuid_of(path):
    return subprocess.check_output(['xcrun', 'dwarfdump', '--uuid', str(path)], text=True).split()[1]


def load_names(path):
    return [line.strip().split(' (compatibility')[0] for line in subprocess.check_output(['otool', '-L', str(path)], text=True).splitlines()[1:]]


class OsVolume:
    """read-only reader of the OS volume of the decrypted IPSW (the device repository's APFS reader)"""

    def __init__(self):
        sys.path.insert(0, str(PONG / 'tools/apfs'))
        import apfs
        self._volume = apfs.pick_volume(apfs.Container(str(INPUTS / 'ipsw/22H374/components/094-32804-038.dmg')),
                                        'CrystalUpdate22H374.J171J172OS')

    def data(self, path):
        oid = 2
        for part in [p for p in path.split('/') if p]:
            hit = next((fid for name, fid, kind in self._volume.dir_entries(oid) if name == part), None)
            if hit is None:
                raise ValueError(f'not in the OS volume: {path}')
            oid = hit
        return self._volume.inode(oid).contents()


def host_tests(work):
    """the host tests the library table names, compiled from the same sources as the libraries"""
    table = json.loads((HERE / 'libraries.json').read_text())
    results = []
    for name, spec in sorted(table['libraries'].items()):
        if not spec.get('host_test'):
            continue
        script = work / spec['host_test']
        r = run(['/bin/bash', script], cwd=work)
        results.append(dict(library=name, test=spec['host_test'], exit=r.returncode, output_bytes=len(r.stdout)))
        assert r.returncode == 0, (script, r.stderr[-800:])
    assert results, 'no host test is recorded'
    return results


def inputs(version, work):
    """check every input of the image and return what goes into it"""
    parent = ROOT / version['parent']
    receipt = json.loads(parent.with_name('unified-verification.json').read_text())
    assert image.sha_file(parent) == version['parent_sha256'] == receipt['image_sha256'], 'the parent image is not the recorded one'
    old, firmware, signature, parent_sha = image.decode(parent)
    assert parent_sha == receipt['image_sha256'], 'the parent record belongs to another image'
    # the menu: a new title, the command line unchanged
    menu, args = image.new_menu(old[image.MENU_MEMBER], {'title': version['title'], 'replace_args': {}})
    manifest = json.loads(old[image.MANIFEST_MEMBER])
    assert args == manifest['boot_args'] == receipt['boot_args'] and len(args.encode()) <= 1023
    for token in ('tmlow=1', 'tmfull=1'):
        assert args.split().count(token) == 1                       # loader options
    # the device tree: the parent's plus the /chosen properties of this version
    tree = image.parse_adt(old[ADT_MEMBER])
    assert bytes(image.emit(tree, '/')) == old[ADT_MEMBER]
    assert ('/wlan' in tree) == version['wlan'], 'the wlan node decides whether wifid is started'
    for name, value in version['chosen'].items():
        assert name not in tree['/chosen'] or tree['/chosen'][name] != value.to_bytes(4, 'little'), name
        tree['/chosen'][name] = value.to_bytes(4, 'little')
    # ... minus the /chosen properties the version file takes away ("chosen_removed": [names])
    removed = version.get('chosen_removed', [])
    for name in removed:
        assert name in tree['/chosen'] and name not in version['chosen'], f'/chosen {name} cannot be removed'
        del tree['/chosen'][name]
    dt = bytes(image.emit(tree, '/'))
    assert image.parse_adt(dt) == tree
    # the manifest: the parent's with what the version file changes, and what is computed here
    kernel = work / KERNEL
    kernel_blob = kernel.read_bytes()
    manifest.update(version['manifest'])
    manifest.update(version=version['version'], release_variant=version['release'], purpose=version['title'],
                    candidate_parent=parent_sha, kernel_source=KERNEL, kernel_sha256=image.sha(kernel_blob),
                    boot_args=args, graphics_runtime_active=True, gpu_private_boot=False,
                    hardware_validated=False, flash_performed=False, fastboot_boot_performed=False, adt_sha256=image.sha(dt))
    for name in version['chosen']:
        assert manifest['config_change']['chosen_selectors'].get(name) == version['chosen'][name], f'the manifest does not describe /chosen {name}'
    for name in removed:
        assert manifest['config_change']['chosen_selectors'].get(name) is None, f'the manifest still names /chosen {name}'
    # the libraries: each one is the recorded build, signed, and embedded by the kernel build from this directory
    current = json.loads((work / 'runtime/final-inputs.json').read_text())
    table = json.loads((HERE / 'libraries.json').read_text())
    assert current['pool_path'] == table['pool_path'] and current['diagnostic_stage_path'] == table['console_stage_path']
    for name, spec in current['payloads'].items():
        blob = (work / 'runtime' / name).read_bytes()
        assert image.sha(blob) == spec['sha256'] == table['libraries'][name]['sha256'], name
        assert len(blob) == spec['bytes'] and uuid_of(work / 'runtime' / name) == spec['uuid'], name
        assert run(['codesign', '--verify', '--strict', work / 'runtime' / name]).returncode == 0, name
    # every library the rebuilt ones load is named as Apple's own binaries of this system name it
    volume = OsVolume()
    apple = set()
    for path in ('/sbin/launchd', '/usr/sbin/wifid', '/usr/libexec/configd', '/usr/libexec/backboardd',
                 '/System/Library/CoreServices/SpringBoard.app/SpringBoard', '/Applications/PosterBoard.app/PosterBoard'):
        with tempfile.NamedTemporaryFile() as file:
            file.write(volume.data(path))
            file.flush()
            apple |= set(load_names(file.name))
    assert '/usr/lib/libobjc.A.dylib' in apple and '/System/Library/Frameworks/Foundation.framework/Foundation' in apple
    for name, spec in table['libraries'].items():
        if spec.get('variable'):
            for dependency in load_names(work / 'runtime' / name):
                assert dependency.startswith('/pong/') or dependency in apple, (name, dependency)
    # the start-up qsort frames are the boot-stack budget: the same as the parent's
    frame = image.prologue_frame(image.bytes_at(kernel_blob, image.symbol_address(kernel, '_qsort'), 64))
    frame_old = image.prologue_frame(image.bytes_at(old[image.KERNEL_MEMBER],
                                                      image.symbol_address(parent.with_name('kernel-ipados18-thor.macho'), '_qsort'), 64))
    assert frame == frame_old <= 0x100, (frame_old, frame)
    tests = host_tests(work)
    members = {image.KERNEL_MEMBER: kernel_blob, ADT_MEMBER: dt,
               image.MANIFEST_MEMBER: image.json_bytes(manifest), image.MENU_MEMBER: menu}
    changed = {image.KERNEL_MEMBER, image.MANIFEST_MEMBER, image.MENU_MEMBER} | ({ADT_MEMBER} if dt != old[ADT_MEMBER] else set())
    return dict(parent=receipt, old=old, firmware=firmware, signature=signature, parent_sha=parent_sha, members=members,
                changed=changed, qsort=frame, current=current, manifest=manifest, args=args, host_tests=tests)


def build(where, members):
    """the template with the members replaced, trimmed to the clusters the volume uses"""
    assert image.sha_file(TEMPLATE) == image.TEMPLATE_SHA256, 'the template image is not the recorded one'
    full, candidate = where / 'full.img', where / 'candidate.img'
    shutil.copyfile(TEMPLATE, full)
    env = dict(os.environ, TZ='UTC', SOURCE_DATE_EPOCH=str(image.PACK_EPOCH))
    for index, (name, content) in enumerate(members.items()):
        file = where / str(index)
        file.write_bytes(content)
        os.utime(file, (image.PACK_EPOCH, image.PACK_EPOCH))
        subprocess.run(['mcopy', '-m', '-o', '-i', image.volume(full), str(file), TARGET[name]], check=True, env=env)
    image.compact(full, candidate)
    return candidate


def verify(version, work, path, expected):
    data, firmware, signature, image_sha = image.decode(path)
    old = expected['old']
    assert old.keys() == data.keys() and (firmware, signature) == (expected['firmware'], expected['signature'])
    assert {name for name in old if old[name] != data[name]} == expected['changed']
    assert all(data[name] == content for name, content in expected['members'].items())
    config = tomllib.loads(data[image.MENU_MEMBER].decode())
    manifest = json.loads(data[image.MANIFEST_MEMBER])
    image.verify_abi()
    chosen = image.validate_menu(config, manifest)
    assert config['boot'] == {'default': 'xnu', 'timeout': 2}
    assert chosen[0]['argument_bytes'] == len(manifest['boot_args'].encode()) <= 1023
    assert manifest['release_variant'] == version['release'] and version['title'].encode() in data[image.MENU_MEMBER]
    payload, old_payload = data[image.KERNEL_MEMBER], old[image.KERNEL_MEMBER]
    assert image.prologue_frame(image.bytes_at(payload, image.symbol_address(work / KERNEL, '_qsort'), 64)) == expected['qsort']
    strings = version['kernel_strings']
    for needle in strings['carried']:                                  # once in this kernel and in the parent's
        assert payload.count(needle.encode()) == 1 and old_payload.count(needle.encode()) == 1, needle
    for needle in strings['always']:                                   # present in both (may be named more than once)
        assert needle.encode() in payload and needle.encode() in old_payload, needle
    for needle in strings['witness']:
        assert payload.count(needle.encode()) >= 1 and old_payload.count(needle.encode()) == payload.count(needle.encode()), needle
    for needle in strings['new']:
        assert payload.count(needle.encode()) == 1 and old_payload.count(needle.encode()) == 0, needle
    for needle in strings['gone']:
        assert payload.count(needle.encode()) == 0 and old_payload.count(needle.encode()) >= 1, needle
    # every library is embedded exactly once and is the one recorded for this version
    current = expected['current']
    for name, spec in current['payloads'].items():
        blob = (work / 'runtime' / name).read_bytes()
        assert payload.count(blob) == 1, name
    console = (work / 'runtime/libpong-console-v205.dylib').read_bytes()
    assert payload.count(console) == 1 and current['diagnostic_stage_path'].encode() + b'\0' in payload
    tree = image.parse_adt(data[ADT_MEMBER])
    assert ('/wlan' in tree) == version['wlan']
    for name, value in version['chosen'].items():
        assert tree['/chosen'][name] == value.to_bytes(4, 'little')
    for name in version.get('chosen_removed', []):
        assert name not in tree['/chosen'], f'/chosen {name} is still in the device tree'
    uuids = {uuid_of(work / 'kernel' / f) for f in ('kernel-ipados18-thor.macho', 'mach.development.pong', 'mach.development.pong.unstripped')}
    assert len(uuids) == 1 and uuids != {expected['parent']['kernel_uuid']}
    _, offset, size, _ = image.sections(path)
    with tempfile.NamedTemporaryFile(dir=work, suffix='.fat') as tmp, path.open('rb') as image_file:
        image_file.seek(offset)
        tmp.write(image_file.read(size))
        tmp.flush()
        assert run(['/sbin/fsck_msdos', '-n', tmp.name]).returncode == 0
    record = dict(version=version['version'], variant=version['release'].removeprefix(f"v{version['version']}-"),
                  embedded_release_id=version['release'], image=f"boot-thor-unixnu-{version['release']}.img",
                  image_bytes=path.stat().st_size, image_sha256=image_sha,
                  kernel_uuid=uuids.pop(), kernel_sha256=image.sha(payload), parent_sha256=expected['parent_sha'],
                  parent_kernel_uuid=expected['parent']['kernel_uuid'], adt_sha256=image.sha(data[ADT_MEMBER]),
                  chosen_selectors=manifest['config_change']['chosen_selectors'], boot_args=manifest['boot_args'],
                  boot_argument_bytes=len(manifest['boot_args'].encode()), qsort_frame_bytes=expected['qsort'],
                  boot_stack_bytes=image.BOOT_STACK, console_sha256=current['payloads']['libpong-console-v205.dylib']['sha256'],
                  console_uuid=current['payloads']['libpong-console-v205.dylib']['uuid'],
                  console_stage_path=current['diagnostic_stage_path'], pool_path=current['pool_path'],
                  diagnostic_path=current['diagnostic_stage_path'], runtime=current['payloads'],
                  firmware_sha256=firmware, signature_sha256=signature, filesystem_check=0,
                  changed_fat_members=sorted(expected['changed']), hardware_validated=False,
                  probe=manifest['witness'].get('probe'), display=manifest['witness'].get('display'),
                  wifi_daemon=('started and held (wlan node in the device tree)' if version['wlan'] else 'not started (no wlan node)'),
                  host_tests=expected['host_tests'])
    record.update(version['record'])
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('version', type=Path)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--publish', action='store_true', help='copy the image and its record to images/<release>/')
    args = parser.parse_args()
    version = json.loads(args.version.read_text())
    work = args.work.resolve()
    target = work / f"boot-thor-unixnu-{version['release']}.img"
    record_file = work / 'unified-verification.json'
    expected = inputs(version, work)
    if not target.exists():
        with tempfile.TemporaryDirectory(dir=work, prefix='.package-') as folder:
            candidate = build(Path(folder), expected['members'])
            record = verify(version, work, candidate, expected)
            (Path(folder) / 'receipt').write_bytes(image.json_bytes(record))
            os.link(candidate, target)
            os.replace(Path(folder) / 'receipt', record_file)
    record = verify(version, work, target, expected)
    assert record == json.loads(record_file.read_text()), 'the image differs from its record'
    if args.publish:
        destination = IMAGES / version['release']
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, destination / target.name)
        shutil.copy2(record_file, destination / record_file.name)
        # the kernel travels with its image: the next version's packaging and every symbol lookup read it there
        (destination / 'kernel').mkdir(exist_ok=True)
        for name in ('kernel-ipados18-thor.macho', 'mach.development.pong.unstripped'):
            shutil.copy2(work / 'kernel' / name, destination / name)            # the prelinked kernel beside the image, as the next version expects
            shutil.copy2(work / 'kernel' / name, destination / 'kernel' / name)
        assert image.sha_file(destination / target.name) == record['image_sha256']
    print(image.json_bytes(record).decode(), end='')


if __name__ == '__main__':
    main()
