#!/usr/bin/env python3
"""Package a full-only Darwin22 image once, after public-input validation."""
from pathlib import Path
import argparse
import datetime
import gzip
import hashlib
import json
import os
import re
import subprocess
import tomllib
from fullseq_asset import validate as asset_validate, encode_v3, decode_v3
from package_host import load_tools, package_commands, private_output, private_seed, rooted, require, sha


def now():
    return datetime.datetime.now().astimezone().isoformat()


def write(path, data):
    with path.open('xb') as stream:
        stream.write(data)


def validate_inputs(root, source, asset_manifest, release_path, audit_path, preflight_path, preflight_sha):
    asset = asset_validate(root, asset_manifest)
    release, audit, preflight = [json.loads(path.read_text()) for path in (release_path, audit_path, preflight_path)]
    require(release.get('schema') == 'thor-fullseq01-uefi-release-v1' and
            str(release.get('result', '')).startswith('PASS'), 'full-only firmware release')
    require(release.get('phone_actions') == 0 and release.get('full_kernel_entry_executed') is False and
            release.get('no_unknown_FDT_RAM') is True, 'host-only release and unchanged RAM authorization')
    require(release.get('execution_kernel_sha256') == asset['raw_sha256'] and
            release.get('execution_kernel_raw_bytes') == asset['raw_bytes'] and
            release.get('span_bytes') == asset['loaded_span_bytes'] and
            release.get('loaded_span_sha256') == asset['loaded_span_sha256'], 'firmware binds this actual full kernel')
    require(release.get('boot_args_abi') == 'darwin22-8796' and release.get('boot_args_bytes') == 736 and
            release.get('command_line_bytes') == 608, 'actual Darwin22 startup ABI')
    require(audit.get('status') == 'PASS' and audit.get('failures') == [] and
            sha(audit_path) == release.get('firmware_audit_sha256'), 'complete fresh firmware audit')
    fd = source / 'Build/thorEntry09/RELEASE_CLANGPDB/FV/SILICIUM_UEFI.fd'
    require(fd.stat().st_size == release.get('uefi_fd_bytes') == audit['fd']['bytes'] == 3145728 and
            sha(fd) == release.get('uefi_fd_sha256') == audit['fd']['sha256'], 'actual FD identity')
    head = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    require(head == release.get('uefi_commit') and not subprocess.check_output(
            ['git', '-C', str(source), 'status', '--porcelain'], text=True).strip(), 'clean frozen firmware source')
    require(isinstance(preflight_sha, str) and re.fullmatch('[0-9a-f]{64}', preflight_sha) and
            sha(preflight_path) == preflight_sha, 'exact completed public preflight')
    require(preflight.get('schema') == 'thor-fullseq02-public-preflight-v1' and
            preflight.get('result') == 'PASS_PUBLIC_FULLSEQ02' and preflight.get('ready_for_packaging') is True and
            preflight.get('phone_actions') == 0 and preflight.get('seed_generated') is False,
            'public preflight precedes one-use package generation')
    require(preflight.get('full_kernel') == asset and preflight.get('fd_sha256') == sha(fd), 'preflight selected input roles')
    checks = preflight.get('checks', [])
    require(checks and all(row.get('passed') is True for row in checks), 'all independent preflight conditions')
    pins, sizes = preflight.get('frozen_input_sha256', {}), preflight.get('frozen_input_bytes', {})
    require(pins and set(pins) == set(sizes), 'complete frozen public input inventory')
    required = {str(path.resolve()) for path in (asset_manifest, release_path, audit_path, fd,
        Path(__file__), Path(__file__).with_name('fullseq_asset.py'), Path(__file__).with_name('package_host.py'),
        Path(__file__).with_name('full_loader_asset.py'))}
    require(required <= set(pins), 'public preflight covers actual packager helpers and chosen artifacts')
    for name, digest in pins.items():
        path = rooted(root, Path(name))
        require(path.is_file() and path.stat().st_size == sizes[name] and sha(path) == digest,
                'frozen public input unchanged: ' + name)
    require(sha(rooted(root, Path(preflight['verifier_path']))) == preflight.get('verifier_sha256'),
            'exact independent public verifier')
    return asset, release, fd


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('root', 'source', 'asset-manifest', 'release', 'firmware-audit', 'preflight'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--preflight-sha256', required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(); os.umask(0o077)
    root = args.root.resolve()
    require(os.sys.platform == 'darwin', 'FULLSEQ02 image generation host is Mac mini')
    require(re.fullmatch('thor-[a-z0-9-]{8,90}', args.run_id), 'new immutable run identity')
    source, manifest_path, release_path, audit_path, preflight_path = [rooted(root, getattr(args, name))
        for name in ('source', 'asset_manifest', 'release', 'firmware_audit', 'preflight')]
    out = root / 'runs' / args.run_id / 'l2/package'
    require(not out.exists() and not (root / 'runs' / args.run_id / 'l3').exists(), 'never regenerate an existing attempt')
    asset, release, fd_path = validate_inputs(root, source, manifest_path, release_path,
        audit_path, preflight_path, args.preflight_sha256)
    python, scripts, tools = load_tools(root, Path('toolchains/android-boot-tools/package-tools.json'))
    for name in ('fullseq02_pack.py', 'fullseq_asset.py', 'package_host.py', 'full_loader_asset.py'):
        tools[name] = sha(Path(__file__).with_name(name))
    stock = tomllib.loads((root / 'thor-build.toml').read_text())['stock']
    shim_path, dtb_path = [root / 'artifacts/thor-smoke-01' / name
        for name in ('BootShim.bin', 'thor-stock-vendor-dtb.bin')]
    require(sha(shim_path) == stock['bootshim_sha256'] and sha(dtb_path) == stock['dtb_sha256'], 'unchanged original Thor boot components')
    require(shim_path.stat().st_size == 112 and dtb_path.stat().st_size == 6443651, 'actual stock component lengths')
    payload = (root / asset['header_path']).read_bytes() + (root / asset['compressed_path']).read_bytes()
    require(len(payload) == 128 + asset['compressed_bytes'], 'exact full-only public payload')
    metadata = dict(schema='thor-fullseq02-one-use-boot-package-v1', run_id=args.run_id,
        created_at=now(), entry_mode='darwin22-full', sole_boot_host='macmini',
        kernel_sha256=asset['raw_sha256'], kernel_bytes=asset['raw_bytes'],
        kernel_source_revision=release['execution_kernel_source_commit'],
        full_kernel=asset, uefi_commit=release['uefi_commit'], fd_sha256=sha(fd_path),
        full_kernel_entry_planned=True, full_kernel_entry_executed=False, hardware_tested=False,
        phone_attempts=0, phone_actions=0, seed_reuse_allowed=False, seed_raw_logged=False,
        pre_generation_preflight_sha256=args.preflight_sha256, package_tools=tools,
        packet_version=3, packet_header_bytes=128, payload_sha256=hashlib.sha256(payload).hexdigest(),
        payload_bytes=len(payload), boot_args_abi='darwin22-8796')
    if args.dry_run:
        print(json.dumps(dict(result='PASS_NO_ARTIFACT_CREATED', **metadata), indent=2)); return
    with private_output(out, now()):
        seed, origin = private_seed(); bundle = encode_v3(payload, seed)
        require(decode_v3(bundle)['raw_sha256'] == asset['raw_sha256'], 'fresh v3 packet roundtrip')
        shim, fd, dtb = shim_path.read_bytes(), fd_path.read_bytes(), dtb_path.read_bytes()
        for name, data in [('kernel-bundle.bin', bundle), ('BootShim.bin', shim),
                ('SILICIUM_UEFI.fd', fd), ('thor-stock-vendor-dtb.bin', dtb),
                ('kernel.gz-dtb', gzip.compress(shim + fd, compresslevel=9, mtime=0) + dtb)]:
            write(out / name, data)
        records = []
        for label, command in zip(('mkbootimg', 'verify-image', 'unpack-crosscheck'), package_commands(python, scripts, out)):
            process = subprocess.run(command, capture_output=True, text=True, timeout=180)
            write(out / (label + '.stdout'), process.stdout.encode()); write(out / (label + '.stderr'), process.stderr.encode())
            records.append(dict(name=label, argv=command, returncode=process.returncode))
            require(process.returncode == 0, 'actual pinned boot tool: ' + label)
        require((out / 'unpacked/kernel').read_bytes() == (out / 'kernel.gz-dtb').read_bytes() and
                (out / 'unpacked/ramdisk').read_bytes() == bundle, 'actual Android boot v4 unpack identity')
        image = out / 'thor-boot.img'; digest = sha(image)
        require(0 < image.stat().st_size < 64 * 1024 * 1024 and
                not (root / 'state/consumed-images' / (digest + '.json')).exists(), 'fresh bounded unused image')
        metadata.update(image_sha256=digest, image_bytes=image.stat().st_size,
            bundle_sha256=hashlib.sha256(bundle).hexdigest(), bundle_bytes=len(bundle),
            seed_sha256=hashlib.sha256(seed).hexdigest(), seed_source=origin, seed_bytes=64,
            package_commands=records, state='HOST_PACKAGED_AWAITING_INDEPENDENT_L2')
        write(out / 'manifest.json', (json.dumps(metadata, indent=2) + '\n').encode())
        print(json.dumps(dict(state=metadata['state'], image_sha256=digest,
            image_bytes=image.stat().st_size, phone_actions=0), indent=2))


if __name__ == '__main__':
    main()
