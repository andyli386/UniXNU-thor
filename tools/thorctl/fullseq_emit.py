#!/usr/bin/env python3
"""Emit a full execution kernel from a frozen native build and original Apple components."""
from pathlib import Path
import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from fullseq_asset import execution_identity
from full_loader_asset import macho_layout


def require(value, message):
    if not value:
        raise RuntimeError('FULLSEQ01 emit: ' + message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('root', 'source', 'native', 'native-proof', 'original', 'reference-plan', 'out'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args(); root = args.root.resolve(); os.umask(0o022)
    def path(value):
        result = (root / value).resolve()
        require(result.is_relative_to(root), 'input stays inside project')
        return result
    source, native_path, native_proof, original_path, reference, out = [path(getattr(args, key))
        for key in ('source', 'native', 'native_proof', 'original', 'reference_plan', 'out')]
    require(not out.exists(), 'never replace an existing execution kernel candidate')
    native, original = native_path.read_bytes(), original_path.read_bytes()
    proof = json.loads(native_proof.read_text()); old = json.loads(reference.read_text())
    require(proof.get('schema') == 'thor-full-seq01-native-build-v1' and
            str(proof.get('result', '')).startswith('PASS'), 'completed native production build proof')
    require(proof.get('kernel_sha256') == sha(native) and proof.get('kernel_bytes') == len(native),
            'actual frozen self-built native identity')
    require(proof.get('phone_actions') == 0 and proof.get('hardware_tested') is False,
            'native proof does not claim hardware execution')
    require(sha(original) == old['original_sha256'] ==
            'd260defcc2eaac258fd3a2234454dda46791dae45e97608c5416f9ba8fd366e9',
            'unchanged original ordinary ARM64 20G81 kernel cache')
    head = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    require(head == proof['source_commit'] and not subprocess.check_output(
        ['git', '-C', str(source), 'status', '--porcelain'], text=True).strip(), 'clean frozen native build source')
    kit = source / 'kits/NativePrelink'; sys.path.insert(0, str(kit))
    spec = importlib.util.spec_from_file_location('fullseq_frozen_emitter', kit / 'emit_prelink.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    roots, slide = old['components'], old['slide']
    merged, emission = module.emit(native, original, roots, slide)
    require(emission['native_sha256'] == sha(native) and emission['original_sha256'] == sha(original) and
            emission['candidate_sha256'] == sha(merged), 'emitter input and execution-kernel digests')
    geometry = macho_layout(merged); identity = execution_identity(merged, geometry)
    native_geometry = macho_layout(native); native_identity = execution_identity(native, native_geometry)
    require(identity == native_identity and geometry['virtual_base'] == native_geometry['virtual_base'],
            'prelink preserves the actual native cold entry, both vectors and page-table identities')
    require(len(roots) == len(emission['original_executable_sha256']) == 11,
            'complete original APFS and storage dependency closure')
    out.mkdir(parents=True, exist_ok=False)
    (out / 'kernel.macho').write_bytes(merged); (out / 'native.macho').write_bytes(native)
    (out / 'native-selfbuild.json').write_bytes(native_proof.read_bytes())
    (out / 'prelink-emission.json').write_text(json.dumps(emission, indent=2) + '\n')
    auditor = Path(__file__).with_name('fullseq_prelink_audit.py')
    command = [sys.executable, '-B', str(auditor), '--native', str(out / 'native.macho'),
        '--original', str(original_path), '--merged', str(out / 'kernel.macho'),
        '--emission', str(out / 'prelink-emission.json'), '--output', str(out / 'prelink-integrity.json')]
    with (out / 'prelink-integrity.stdout.log').open('x') as stdout, (out / 'prelink-integrity.stderr.log').open('x') as stderr:
        check = subprocess.run(command, stdout=stdout, stderr=stderr, timeout=300)
    require(check.returncode == 0, 'independent full-content and instruction-integrity execution')
    integrity = json.loads((out / 'prelink-integrity.json').read_text())
    require(integrity['result'] == 'PASS_STATIC_PRELINK_CONTENT_AND_DIRECT_PCREL' and
            integrity['merged_sha256'] == sha(merged) and integrity['native_sha256'] == sha(native) and
            integrity['original_sha256'] == sha(original) and integrity['phone_actions'] == 0,
            'actual independent execution-kernel reconstruction proof')
    execution = dict(schema='thor-fullseq01-execution-kernel-build-v1',
        result='PASS_FULLSEQ01_EXECUTION_KERNEL_STATIC_CONTENT',
        kernel_path=str((out / 'kernel.macho').relative_to(root)), kernel_bytes=len(merged), kernel_sha256=sha(merged),
        native_kernel_path=str((out / 'native.macho').relative_to(root)), native_kernel_bytes=len(native),
        native_kernel_sha256=sha(native), original_kernel_sha256=sha(original),
        source_commit=head, build_source_commit=proof['build_source_commit'],
        native_selfbuild_proof_path=str((out / 'native-selfbuild.json').relative_to(root)),
        native_selfbuild_proof_sha256=sha(native_proof.read_bytes()),
        prelink_emission_path=str((out / 'prelink-emission.json').relative_to(root)),
        prelink_emission_sha256=sha((out / 'prelink-emission.json').read_bytes()),
        prelink_integrity_path=str((out / 'prelink-integrity.json').relative_to(root)),
        prelink_integrity_sha256=sha((out / 'prelink-integrity.json').read_bytes()),
        geometry=dict(**{key: value for key, value in geometry.items()
            if key not in ('segments', 'sorted_nonzero_segments')}, **identity),
        stage_contract=proof['stage_contract'], boot_args_abi='darwin22-8796', boot_args_bytes=736,
        prepared_source_sha256=proof.get('prepared_source_sha256', {}),
        source_files_sha256={str(p.relative_to(root)): sha(p.read_bytes()) for p in
            [Path(__file__), auditor, kit / 'emit_prelink.py', kit / 'native_prelink.py', reference, native_proof]},
        foreign_kernel_abi_qualified=False, runtime_qualified=False, hardware_tested=False,
        full_kernel_entry_executed=False, phone_actions=0)
    (out / 'native-build.json').write_text(json.dumps(execution, indent=2) + '\n')
    (out / 'kernel-layout.json').write_text(json.dumps(dict(**geometry, **identity), indent=2) + '\n')
    manifest = dict(schema='thor-fullseq01-execution-kernel-candidate-v1',
        result='PASS_EXECUTION_KERNEL_HOST_STATIC_CHECKS',
        kernel_sha256=sha(merged), kernel_bytes=len(merged), source_commit=head,
        execution_build_sha256=sha((out / 'native-build.json').read_bytes()),
        files={p.name: dict(sha256=sha(p.read_bytes()), bytes=p.stat().st_size)
               for p in out.iterdir() if p.is_file()}, phone_actions=0,
        hardware_tested=False, foreign_kernel_abi_qualified=False, runtime_qualified=False)
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    require(sha(native_path.read_bytes()) == sha(native) and sha(original_path.read_bytes()) == sha(original),
            'source images remained unchanged through the complete operation')
    print(json.dumps(dict(result=manifest['result'], kernel_sha256=sha(merged), kernel_bytes=len(merged),
        loaded_span_bytes=geometry['loaded_span_bytes'], pointer_counts=integrity['pointer_counts'],
        full_kernel_entry_executed=False, phone_actions=0), indent=2))


if __name__ == '__main__':
    main()
