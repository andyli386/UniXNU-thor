"""MEM08-specific source, firmware, public-asset, and preseed bindings."""
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess

from package_host import require, rooted, sha, exact_stage_list, archive_member_bytes

BASE_TRANSITION_SHA = 'de0b5fd081759dde1f579f7741c2a6cab40feea95285e5c9eaee9832a87da195'
ADT = 'Platforms/Xiaomi/thorPkg/Drivers/ThorEntryBdsDxe/'


def validate_transition(root, work, base, receipt, proof, lock):
    from transition_bind import transition_symbols, load_pe, header_expected
    folder = base / 'postlink-transition'
    path = folder / 'mem08-transition-binding.json'
    require(sha(path) == receipt.get('transition_binding_audit_sha256') == proof.get('transition_binding_audit_sha256'),
            'MEM08 immutable transition semantic proof')
    document = json.loads(path.read_text())
    require(document.get('schema') == 'thor-mem08-transition-owned-layout-immediate-proof-v1' and
            document.get('phone_actions') == 0, 'MEM08 transition proof scope')
    old, new = (folder/'old-transition.bin').read_bytes(), (folder/'new-transition.bin').read_bytes()
    require(len(old) == len(new) == 500 and hashlib.sha256(old).hexdigest() == BASE_TRANSITION_SHA,
            'MEM08 actual fixed500-byte predecessor')
    output = work / 'Build/thorEntry09/RELEASE_CLANGPDB/AARCH64/thorPkg/Drivers/ThorEntryBdsDxe/ThorEntryBdsDxe/OUTPUT'
    first, last = transition_symbols(output/'ThorEntryBdsDxe.map')
    require(last-first == 500, 'MEM08 actual transition symbol interval')
    actual, meta = load_pe(output/'ThorEntryBdsDxe.efi', first, last-first)
    require(actual == new and not meta['relocations_in_transition'], 'MEM08 raw proof equals linked reloc-free PE code')
    allowed = {76,80,96,100,116,120,136,140,156,160}
    changed = [i for i in range(0,500,4) if old[i:i+4] != new[i:i+4]]
    require(set(changed) <= allowed, 'MEM08 only five existing MOV-wide offset pairs may change')
    for offset in changed:
        before, after = struct.unpack_from('<I',old,offset)[0], struct.unpack_from('<I',new,offset)[0]
        require(before & ~(0xffff << 5) == after & ~(0xffff << 5), 'MEM08 transition opcode/register/branch invariant')
    candidate = rooted(root, Path(lock['_validated_candidate_directory']))
    layout_path = candidate/'kernel-layout.json'; layout = json.loads(layout_path.read_text())
    previous = dict(transition=20201472+0x4000,args=20201472,entry=0x400488,vector=0x401000,stack_top=20201472+0x3c000)
    expected = dict(transition=layout['loaded_span_bytes']+0x4000,args=layout['loaded_span_bytes'],
                    entry=layout['entry_offset'],vector=layout['vectors_offset'],stack_top=layout['loaded_span_bytes']+0x3c000)
    for name, offset, register in [('transition',76,9),('args',96,8),('entry',116,8),('vector',136,8),('stack_top',156,8)]:
        for label, raw, wanted in [('old',old,previous[name]),('new',new,expected[name])]:
            a,b = struct.unpack_from('<II',raw,offset)
            require(a & 0x7f80001f == (0x52800000|register) and b & 0x7f80001f == (0x72800000|register)
                    and (a>>21)&3 == 0 and (b>>21)&3 == 1 and a>>31 == b>>31,
                    'MEM08 MOVZ/MOVK register/width/shifts')
            decoded = ((a>>5)&0xffff) | (((b>>5)&0xffff)<<16)
            require(type(wanted) is int and 0 < wanted < 0x4000000 and decoded == wanted
                    and document.get(label+'_decoded_'+name+'_offset') == decoded,
                    'MEM08 actual kernel offset immediate: '+name)
    current_sha = hashlib.sha256(new).hexdigest()
    require(document.get('old_code_sha256') == BASE_TRANSITION_SHA and document.get('new_code_sha256') == current_sha
            and document.get('kernel_layout_sha256') == sha(layout_path) and document.get('kernel_sha256') == lock['kernel_sha256']
            and document.get('changed_instruction_word_offsets') == changed and document.get('unchanged_bytes') == 500-4*len(changed),
            'MEM08 independently decoded transition metadata')
    require(sha(work/ADT/'thor_entry06_arch.S') == sha(root/'worktrees/uefi/vm-mem07'/ADT/'thor_entry06_arch.S'),
            'MEM08 retains reviewed handoff assembly source')
    require(header_expected(work/ADT/'ThorEntry06TransitionExpected.h')[0].hex() == current_sha,
            'MEM08 compiled expected transition digest')
    return current_sha


def validate_release(root, work, receipt_path, audit_path, lock, receipt, audit):
    from full_loader_asset import validate as validate_asset
    require(lock.get('_validated_packaging_profile') == 'MEM08', 'MEM08 artifact/release profile match')
    require(receipt.get('board') == 'thor' and receipt.get('kernel_sha256') == lock['kernel_sha256']
            and receipt.get('kernel_bytes') == lock['kernel_bytes'] and receipt.get('kernel_source_commit') == lock['xnu_commit']
            and receipt.get('loaded_span_bytes') == lock['loaded_span_bytes'], 'MEM08 release native kernel identity')
    require(receipt.get('firmware_audit_sha256') == sha(audit_path) and audit.get('schema') == 1 and
            audit.get('status') == receipt.get('firmware_audit_result') == 'PASS' and audit.get('failures') == [] and
            receipt.get('uefi_fd_sha256') == audit['fd']['sha256'] and
            receipt.get('uefi_fd_bytes') == audit['fd']['bytes'] == 3145728, 'MEM08 complete FD composition audit')
    for field, key, count in [('firmware_volume_count','volumes',2),('executable_modules','actual_modules',20)]:
        require(receipt.get(field) == len(audit.get(key,[])) == count, 'MEM08 firmware structure: '+field)
    require(receipt.get('linker_inputs') == len(audit.get('unique_link_inputs',[])) == 97,
            'MEM08 complete native archive set including the standard decoder')
    base = receipt_path.parent
    detail_path, ui_path = base/'mem08-uefi-proof.json', base/'ui-stage-grid-audit.json'
    require(receipt.get('mem08_uefi_proof_sha256') == sha(detail_path) and
            receipt.get('new_ui_layout_audit_sha256') == sha(ui_path), 'MEM08 detail and UI proof identities')
    detail, ui = json.loads(detail_path.read_text()), json.loads(ui_path.read_text())
    require(detail.get('schema') == 'thor-mem08-native-log-full-loader-host-proof-v1' and
            detail.get('result') == 'PASS_MEM08_NATIVE_LOG_AND_FULL_LOADER_BINDING' and
            ui.get('schema') == 'thor-mem08-UEFI-HUD-grid-linked-proof-v1' and
            ui.get('result') == 'PASS_LINKED_MEM08_AND_RESERVED_GOP_Y0_383', 'MEM08 explicit release/UI scopes')
    require(detail['firmware']['sha256'] == ui.get('fd_sha256') == receipt['uefi_fd_sha256'] and
            detail.get('kernel_sha256') == receipt['kernel_sha256'] and detail.get('pe_sha256') == ui.get('pe_sha256') and
            ui.get('grid_machinecode_sha256') == lock['machinecheck_sha256'], 'MEM08 FD/PE/native proof agreement')
    transition = validate_transition(root,work,base,receipt,detail,lock)
    require(receipt.get('linked_transition_sha256') == detail['transition']['sha256'] == transition and
            detail['transition']['bytes'] == 500 and detail['transition']['relocations'] == 0 and
            receipt.get('transition_pe_relocations_in_interval') == 0 and
            receipt.get('transition_sha_matches_expected_header') is True, 'MEM08 actual handoff binding')
    pins = detail.get('source_files_sha256',{})
    require(isinstance(pins,dict) and len(pins) >= 15, 'MEM08 complete production source inventory')
    for name,digest in pins.items():
        require(sha(rooted(work,Path(name))) == digest, 'MEM08 source identity: '+name)
    for field,relative in [('actual_kernel_binding_sha256',ADT+'VM_POST_TTBR1_PROVENANCE.json'),
            ('new_ui_source_sha256',ADT+'ThorReportHud.inc.c'),('new_bds_source_sha256',ADT+'ThorEntryBdsDxe.c'),
            ('unchanged_fdt_validator_sha256',ADT+'thor_fdt_gate.c'),
            ('unchanged_memorymap_source_sha256','Platforms/Xiaomi/thorPkg/Library/MemoryMapLib/MemoryMapLib.c')]:
        require(receipt.get(field) == pins.get(relative), 'MEM08 release source linkage: '+field)
    require(ui.get('hud_sha256') == receipt.get('new_ui_source_sha256') and
            ui.get('bds_source_sha256') == receipt.get('new_bds_source_sha256'), 'MEM08 linked HUD provenance')
    require(all(detail.get('policy_unchanged',{}).get(k) is True for k in
            ('fdt_gate','fdt_gate_header','memory_map','payload_read_permit')), 'MEM08 original RAM and source permit policies')
    require(ui.get('gop_y_reserved') == [0,383] and ui.get('uefi_first_postclear_draw_y',0) >= 384 and
            ui.get('never_calls_GOP_after_EBS') is True and
            ui.get('last_fullscreen_clear_followed_by_five_FDT_rows') is True, 'MEM08 reserved UI and late FDT overlay')
    require(all(receipt.get(k) is False for k in ('hardware_tested','xnu_runtime_qualified')) and
            all(x.get('phone_actions') == 0 for x in (receipt,detail,ui)), 'MEM08 unexecuted hardware scope')
    candidate = rooted(root,Path(lock['_validated_candidate_directory']))
    machinecode = json.loads((candidate/'mem08-native-log-machinecode-final.json').read_text())
    require(ui.get('checkpoint_contract') == machinecode.get('checkpoint_contract') and
            ui.get('native_arm_vm_init_return_compiled') is True, 'MEM08 exact native checkpoint contract')
    render_path = base/'stage-render-audit.json'
    require(receipt.get('stage_render_audit_sha256') == sha(render_path), 'MEM08 production renderer proof digest')
    render = json.loads(render_path.read_text())
    require(render.get('schema') == 'thor-mem08-executed-stage-render-event-audit-v1' and
            render.get('result') == 'PASS_ALL_STAGE_FRAMES_NO_OVERLAP' and
            render.get('unexpected_overlap_count') == 0 and render.get('text_bounds_valid') is True and
            render.get('xnu_reserved_preserved') is True and
            render.get('renderer_source_sha256') == receipt['new_bds_source_sha256'] and
            render.get('hud_source_sha256') == receipt['new_ui_source_sha256'], 'MEM08 complete production UI executions')
    validate_render_trace(work,render_path,render)
    tests = detail.get('tests',{})
    require(all(type(n) is int and n > 0 for n in tests.values()) and
            all(tests.get(k) == n for k,n in [('mem01',12),('mem02',10),('mem03',12)]) and
            receipt.get('source_level_regressions_passed') == sum(tests.values()) and
            detail.get('test_results_sha256') == sha(base/'mem08-source-test-results.json'), 'MEM08 tested source set and counts')
    full = receipt.get('full_loader')
    require(isinstance(full,dict) and full == detail.get('full_loader') and
            full == validate_asset(root,rooted(root,Path(full['compressed_path'])),rooted(root,Path(full['manifest_path']))),
            'MEM08 compiled firmware binds this exact public FULL08 asset')
    loader_path = base/'mem08-full-loader-proof.json'
    require(receipt.get('full_loader_audit_sha256') == detail.get('full_loader_audit_sha256') == sha(loader_path),
            'MEM08 real full-loader production proof')
    loader = json.loads(loader_path.read_text())
    require(loader.get('phone_actions') == 0 and loader.get('full_kernel_entry_executed') is False,
            'MEM08 full kernel is staged, hashed and released before diagnostic entry')
    for relative,digest in loader.get('source_files_sha256',{}).items():
        require(sha(rooted(work,Path(relative))) == digest, 'MEM08 actual full-loader source: '+relative)
    validate_full_loader_native(work,base,receipt,loader,full)
    return receipt,audit


def coff_symbols(raw):
    require(len(raw) >= 20 and struct.unpack_from('<H',raw)[0] == 0xaa64, 'MEM08 real ARM64 COFF')
    sections = struct.unpack_from('<H',raw,2)[0]
    offset,count = struct.unpack_from('<II',raw,8)
    strings = offset+count*18
    require(20+sections*40 <= len(raw) and strings+4 <= len(raw), 'MEM08 COFF table bounds')
    size = struct.unpack_from('<I',raw,strings)[0]
    require(size >= 4 and strings+size <= len(raw), 'MEM08 COFF string bounds')
    found=[]; index=0
    while index<count:
        pos=offset+index*18; name=raw[pos:pos+8]
        if name[:4] == bytes(4):
            start=struct.unpack_from('<I',name,4)[0]
            require(4 <= start < size, 'MEM08 COFF symbol name offset')
            end=raw.find(b'\0',strings+start,strings+size)
            require(end >= 0, 'MEM08 COFF terminated symbol name')
            name=raw[strings+start:end]
        else:
            name=name.rstrip(b'\0')
        value,section=struct.unpack_from('<Ih',raw,pos+8)
        found.append((name.decode(),value,section))
        index+=1+raw[pos+17]
    require(index == count, 'MEM08 complete COFF auxiliary records')
    return found,sections


def validate_full_loader_native(work,base,receipt,document,asset):
    require(document.get('schema') == 'thor-mem08-production-full-loader-audit-v1' and
            document.get('result') == 'PASS_ACTUAL_CODEC_INPLACE_FULL_SPAN_AND_RELEASE_GUARDS' and
            document.get('full_kernel_VM_budget_proven') is False and document.get('new_full_entry_jump_count') == 0,
            'MEM08 staging proof does not qualify complete VM execution')
    path=base/'native-stack-usage.json'
    require(sha(path) == document['stack_usage_artifact_sha256'], 'MEM08 native stack receipt identity')
    stack=json.loads(path.read_text())
    require(stack.get('schema') == 'thor-mem08-native-compiler-stack-usage-v1' and
            stack.get('result') == 'PASS_NEW_NATIVE_FRAMES_WITHIN_EXISTING_STACK_GUARD' and
            stack.get('source_build_commit') == receipt['uefi_build_source_commit'] and stack.get('phone_actions') == 0,
            'MEM08 actual compiled stack output revision')
    objects=stack.get('actual_native_objects',{})
    require(objects == document.get('actual_native_objects') and set(objects) ==
            {'thor_mem08_full.obj','BaseUefiDecompressLib.obj','ThorEntryBdsDxe.obj',
             'thor_entry06_arch.obj','thor_mem07_loader.obj'}, 'MEM08 all five actual native COFF objects')
    raw_objects={}
    for name,item in objects.items():
        library=rooted(work,Path(item['library_relative_path']))
        require(sha(library) == item['library_sha256'] and
                sha(rooted(work,Path(item['source_relative_path']))) == item['source_sha256'],
                'MEM08 linked native library/source '+name)
        raw=archive_member_bytes(library.read_bytes(),name); raw_objects[name]=raw
        require(len(raw) == item['object_bytes'] and hashlib.sha256(raw).hexdigest() == item['object_sha256'] ==
                sha(base/name), 'MEM08 exact extracted linked COFF '+name)
        symbols,_=coff_symbols(raw)
        require(item['machine'] == '0xaa64' and item['symbols'] and all(
                any(n == wanted and section > 0 for n,_,section in symbols) for wanted in item['symbols']),
                'MEM08 actual defined production symbols '+name)
    raw=raw_objects['ThorEntryBdsDxe.obj']; symbols,sections=coff_symbols(raw)
    workspace=[(value,section) for name,value,section in symbols if name == 'mMem08Work']
    require(len(workspace) == 1, 'MEM08 single actual native static workspace')
    value,section=workspace[0]
    require(1 <= section <= sections and not any(s == section and v > value for _,v,s in symbols),
            'MEM08 static workspace last symbol in COFF section')
    size=struct.unpack_from('<I',raw,20+(section-1)*40+16)[0]
    require(size-value == stack['workspace_static_bytes'] == document['actual_workspace_static_bytes'] == 1288 and
            (value,section,size) == (stack['workspace_coff_symbol_value'],stack['workspace_coff_section_index'],
                                    stack['workspace_coff_section_bytes']), 'MEM08 independently derived native BSS workspace')
    reports=stack.get('reports',{})
    require(set(reports) == {'ThorEntryBdsDxe.su','thor_mem07_loader.su',
                            'thor_mem08_full.su','BaseUefiDecompressLib.su'}, 'MEM08 four native compiler reports')
    rows={}
    for name,item in reports.items():
        actual=rooted(work,Path(item['compiler_output_relative_path']))
        require(actual.name == name and actual.stat().st_size == item['bytes'] and
                sha(actual) == item['sha256'] == sha(base/'native-stack-usage'/name), 'MEM08 actual compiler stack file '+name)
        found={m.group(1):int(m.group(2)) for m in re.finditer(r'(?m)^.*:([^:\t]+)\t([0-9]+)\tstatic\s*$',actual.read_text())}
        require(found == item['functions'], 'MEM08 independent native stack function sizes '+name); rows[name]=found
    selected=dict(rows['thor_mem08_full.su'],**rows['BaseUefiDecompressLib.su'])
    for name in ('RunMem08Probe','Mem08FullCanary','Mem08Writable','Mem08Hash','Mem08Allow','Mem08Info','Mem08Decode','Mem08SetOps'):
        selected[name]=rows['ThorEntryBdsDxe.su'][name]
    require(selected == stack['new_function_stack_bytes'] and max(selected.values()) ==
            stack['maximum_new_function_stack_bytes'] == 432 and all(0 <= value < 1024 for value in selected.values()) and
            stack['existing_entry_function_stack_bytes'] == rows['ThorEntryBdsDxe.su']['ThorEntryBdsEntry'] and
            stack['existing_shared_stack_bytes'] == 0x40000 and stack['shared_stack_guard_bytes'] == 0x4000,
            'MEM08 measured new native frames and unchanged shared stack bounds')
    pe=work/'Build/thorEntry09/RELEASE_CLANGPDB/AARCH64/thorPkg/Drivers/ThorEntryBdsDxe/ThorEntryBdsDxe/OUTPUT/ThorEntryBdsDxe.efi'
    raw=pe.read_bytes(); peoff=struct.unpack_from('<I',raw,0x3c)[0]
    retained=struct.unpack_from('<I',raw,peoff+24+56)[0]
    require(0 < retained == document['retained_image_bytes'] < document['retained_image_limit_bytes'] == 0x200000,
            'MEM08 actual retained PE fits original live interval')
    executions={}
    for field,name,count in [('production_full_loader_host_execution_sha256','mem08-full-loader-host-execution.json',22),
            ('whole_window_writable_audit_sha256','mem08-whole-window-writable.json',20),
            ('shared_stack_audit_sha256','mem08-shared-stack.json',15)]:
        path=base/name; require(sha(path) == document[field], 'MEM08 native adapter execution identity '+name)
        execution=json.loads(path.read_text()); executions[name]=execution
        require(execution.get('phone_actions') == 0 and len(execution.get('results',[])) == count,
                'MEM08 exact native adapter execution case set '+name)
    success=[x for x in executions['mem08-full-loader-host-execution.json']['results'] if x['mode'] == 'success']
    walk=[x for x in executions['mem08-whole-window-writable.json']['results'] if x['mode'] == 0]
    require(len(success) == len(walk) == 1 and success[0]['stage_status'] == 0 and success[0]['phase'] == 5 and
            success[0]['raw_sha256'] == asset['raw_sha256'] and success[0]['loaded_span_sha256'] == asset['loaded_span_sha256'] and
            walk[0]['pass'] == 1 and walk[0]['at_calls'] == 57344, 'MEM08 real decode/layout/hash and complete R/W page walk')
    require(document['writable_page_count'] == 28672 and document['writable_AT_callback_count'] == 57344 and
            document['decoder_scratch_bytes'] == asset['scratch_bytes'] == 13376 and
            document['scratch_reserved_bytes'] == 16384 and document['scratch_offset'] == asset['scratch_offset'] and
            document['temporary_window_bytes'] == asset['window_bytes'] == 117440512 and document['one_deadline_seconds'] == 30,
            'MEM08 original owned window, codec scratch and one work deadline')
    require(all(document.get(key) is True for key in ('writable_before_any_decode_or_clear','architecture_snapshot_preserved',
            'ArchAt_preserves_PAR_EL1_and_DAIF','deadline_starts_before_locator','deadline_not_reset_before_probe',
            'cleanup_after_deadline','free_and_fresh_map_before_64m','free_failure_stops','source_retained_and_rehashed_before_after')),
            'MEM08 execution, release and architecture observation guards')


def validate_native_log(candidate, tree, lock, build, linked):
    for key in ('native_first_kprintf_call_compiled', 'bounded_before_PE_create_console'):
        require(all(item.get(key) is True for item in (lock, build, linked)), 'MEM08 natural log boundary: ' + key)
    require(linked.get('native_pmap_map_globals_call_compiled') is False and
            linked.get('normal_machine_init_reached') is False,
            'MEM08 natural continuation retains the bounded pre-console frontier')
    guards_path = candidate / 'mem08-native-log-guards.json'
    guards = json.loads(guards_path.read_text())
    require(sha(guards_path) == lock.get('native_log_guards_sha256') == build.get('native_log_guards_sha256'),
            'MEM08 production C log guard artifact identity')
    require(guards.get('schema') == 'thor-mem08-production-C-log-guards-v1' and
            guards.get('result') == 'PASS_NATIVE_LOG_GUARDS_WITH_FAULTS' and guards.get('phone_actions') == 0,
            'MEM08 actual production log guard executions')
    sources = guards.get('source_sha256', {})
    require(isinstance(sources, dict) and {'libkern/os/log.c', 'bsd/kern/subr_log.c'} <= set(sources),
            'MEM08 executed native log sources present')
    prepared = linked.get('prepared_source_sha256', {})
    for relative, digest in sources.items():
        require(prepared.get(relative) == digest and sha(rooted(tree / '.cache/xnu-work', Path(relative))) == digest,
                'MEM08 executed guard belongs to the actual prepared TU: ' + relative)
    validate_native_stack(candidate, tree, lock, build)


def validate_native_stack(candidate, tree, lock, build):
    path = candidate/'mem08-native-log-stack.json'
    require(sha(path) == lock.get('native_log_stack_sha256') == build.get('native_log_stack_sha256'),
            'MEM08 selected native stack proof identity')
    document = json.loads(path.read_text())
    require(document.get('schema') == 'thor-mem08-native-log-stack-v1' and
            document.get('result') == 'PASS_SELECTED_NATIVE_LOG_STACK_BOUND' and
            document.get('phone_actions') == 0 and document.get('hardware_tested') is False and
            document.get('kernel_sha256') == lock['kernel_sha256'] and
            document.get('source_commit') == lock['build_source_commit'], 'MEM08 static stack proof scope/source')
    require(document.get('conservative_bound_bytes') == 2048 and document.get('runtime_guard_bytes') == 12288,
            'MEM08 selected returning path stays within the native runtime stack guard')
    paths = document.get('paths',[])
    require(paths and all(type(p.get('sum_bytes')) is int and
            p['sum_bytes'] == sum(f['bytes'] for f in p['frames']) and
            all(type(f['bytes']) is int and f['bytes'] >= 0 for f in p['frames']) and
            p['sum_bytes'] <= 2048 for p in paths), 'MEM08 independent stack chain addition')
    require(document.get('largest_listed_conservative_chain_bytes') == max(p['sum_bytes'] for p in paths),
            'MEM08 largest actual listed stack chain')
    records = document.get('actual_stack_usage',{})
    require(isinstance(records,dict) and len(records) == 15, 'MEM08 all fifteen actual compiled stack reports')
    for name,item in records.items():
        for field,hashfield in [('path','sha256'),('object_path','object_sha256'),
                ('compile_record','compile_record_sha256'),('source','source_sha256')]:
            require(sha(rooted(tree,Path(item[field]))) == item[hashfield], 'MEM08 compiled stack input '+name+': '+field)
        text = rooted(tree,Path(item['path'])).read_text()
        found = {m.group(1):int(m.group(2)) for m in re.finditer(r'(?m)^.*:([^:\t]+)\t([0-9]+)\tstatic\s*$',text)}
        require(found == item['functions'], 'MEM08 actual compiler function stack sizes: '+name)
    for relative,digest in document.get('source_sha256',{}).items():
        require(sha(rooted(tree,Path(relative))) == digest, 'MEM08 stack closure source: '+relative)
    require(sha(rooted(tree,Path(document['libdispatch_archive_path']))) == document['libdispatch_archive_sha256'],
            'MEM08 actual private firehose linked archive')
    require(sha(rooted(tree.parents[2],Path(document['producer_path']))) == document['producer_sha256'],
            'MEM08 exact stack evidence producer')


def validate_asset_precheck(root, path, expected_sha256, asset):
    require(isinstance(expected_sha256, str) and re.fullmatch('[0-9a-f]{64}', expected_sha256)
            and sha(path) == expected_sha256, 'MEM08 full-loader precheck digest')
    proof = json.loads(path.read_text())
    require(proof.get('schema') == 'thor-mem08-public-full-loader-precheck-v1' and
            proof.get('result') == 'PASS_PUBLIC_FULL_LOADER_PRECHECK' and
            proof.get('phone_actions') == 0 and proof.get('seed_generated') is False and
            proof.get('full_L2_ready') is False, 'MEM08 public preseed replay scope')
    validator = Path(__file__).with_name('verify_mem08_candidate.py')
    require(proof.get('verifier_sha256') == sha(validator), 'MEM08 exact independent preseed validator')
    pins, sizes = proof.get('frozen_input_sha256'), proof.get('frozen_input_bytes')
    require(isinstance(pins, dict) and isinstance(sizes, dict) and set(pins) == set(sizes),
            'MEM08 complete preseed replay input inventory')
    expected_files = {str(rooted(root, asset[name])) for name in ('compressed_path', 'header_path', 'manifest_path')}
    expected_files.add(str(validator.resolve()))
    expected_files.add(str(Path(__file__).with_name('full_loader_asset.py').resolve()))
    require(expected_files <= set(pins), 'MEM08 replay covers selected asset/header/manifest/validator/helper')
    for name, digest in pins.items():
        current = rooted(root, Path(name))
        require(current.is_file() and current.stat().st_size == sizes[name] and sha(current) == digest,
                'MEM08 public replay input changed: ' + name)
    codec, layout = proof.get('production_codec_replay', {}), proof.get('production_inplace_replay', {})
    require(codec.get('decoded_raw_sha256') == asset['raw_sha256'] and
            codec.get('decoded_raw_bytes') == asset['raw_bytes'] and
            codec.get('scratch_bytes') == asset['scratch_bytes'], 'MEM08 real production codec roundtrip')
    require(layout.get('loaded_span_sha256') == asset['loaded_span_sha256'] and
            layout.get('loaded_span_bytes') == asset['loaded_span_bytes'] and
            layout.get('raw_source_sha256') == asset['raw_sha256'], 'MEM08 real production inplace layout roundtrip')
    production = proof.get('production_sha_replay',{})
    require(production.get('schema') == 'thor-mem08-independent-production-sha-host-v1' and
            production.get('result') == 'PASS_REAL_PRODUCTION_SHA_FULL_LOADED_SPAN' and production.get('tests_run') == 8 and
            all(production.get(key) is True for key in ('actual_production_Mem08Hash','actual_production_ThorPayloadSha256',
                                                       'actual_project_OpenSSL_SHA_C')),
            'MEM08 actual production SHA callback and full algorithm replay')
    require(production.get('raw_sha256') == asset['raw_sha256'] and
            production.get('loaded_span_sha256') == asset['loaded_span_sha256'] and
            production.get('loaded_span_bytes') == asset['loaded_span_bytes'] and
            production.get('phone_actions') == 0, 'MEM08 actual production SHA full113MB identity')
    checks = proof.get('checks')
    require(isinstance(checks, list) and checks and all(item.get('passed') is True for item in checks),
            'MEM08 complete public preseed replay conditions')
    return proof


def validate_render_trace(work, render_path, render, prefix="mem08"):
    """Bind frame summaries to the executed host trace and recheck its geometry."""
    trace_path = render_path.parent / "stage-render-events.jsonl"
    harness_path = render_path.parent / "stage-render-harness.c"
    require(render.get("host_trace_sha256") == sha(trace_path) and
            render.get("host_harness_sha256") == sha(harness_path),
            "MEM08 executed renderer trace/harness identity")
    test_path = work / ("Platforms/Xiaomi/thorPkg/Tests/test_" + prefix + "_stage_render.py")
    require(render.get("test_source_sha256") == sha(test_path),
            "MEM08 executed renderer test source identity")
    pins = render.get("source_files_sha256")
    require(isinstance(pins, dict) and len(pins) >= 13,
            "MEM08 executed renderer source coverage")
    for relative, expected in pins.items():
        require(sha(rooted(work, Path(relative))) == expected,
                "MEM08 executed renderer pinned source: " + relative)
    require(render.get("render_functions_from_production_C") is True and
            render.get("font_pixels_executed") is True and
            render.get("host_service_and_loader_state_stubs") is True and
            render.get("xnu_y_reserved") == [0, 383],
            "MEM08 honest executed renderer scope")
    events = [json.loads(line) for line in trace_path.read_text().splitlines()
              if line.strip()]
    require(events and type(render.get("event_count")) is int and
            render["event_count"] == len(events),
            "MEM08 nonempty executed renderer event trace")
    groups, active = {}, None
    for event in events:
        number, kind = event.get("frame"), event.get("kind")
        require(type(number) is int and number > 0,
                "MEM08 renderer frame ID")
        if kind == "begin":
            require(active is None and number not in groups,
                    "MEM08 renderer frame ordering")
            active = number
            groups[number] = []
        require(active == number, "MEM08 renderer event outside active frame")
        groups[number].append(event)
        if kind == "pixels":
            active = None
    require(active is None, "MEM08 incomplete renderer frame")
    frames = render["frames"]
    require(type(render.get("frame_count")) is int and
            render["frame_count"] == len(frames) == len(groups) and
            exact_stage_list(list(groups), list(range(1, len(groups) + 1))),
            "MEM08 renderer frame-count identity")
    essential = {"initial", "read_permit", "kernel_hash_pass",
                 "kernel_hash_refused", "loader_handoff_ready", "loader_error_max_values"}
    require(essential <= {row.get("name") for row in frames},
            "MEM08 required renderer stages")
    def rectangle(event):
        value = event.get("rect")
        require(isinstance(value, list) and len(value) == 4 and
                all(type(item) is int for item in value),
                "MEM08 renderer rectangle shape")
        x, y, width, height = value
        require(x >= 0 and y >= 384 and width >= 0 and height >= 0 and
                x + width <= 1440 and y + height <= 3200,
                "MEM08 renderer text/fill bounds")
        return value
    def overlap(left, right):
        x, y, w, h = left
        xx, yy, ww, hh = right
        return min(x + w, xx + ww) > max(x, xx) and min(y + h, yy + hh) > max(y, yy)
    for index, frame in enumerate(frames, 1):
        require(type(frame.get("id")) is int and frame["id"] == index,
                "MEM08 renderer frame summary ID")
        rows = groups[index]
        require(len(rows) >= 4 and rows[0].get("kind") == "begin" and
                rows[0].get("name") == frame.get("name") and
                rows[1].get("kind") == "clear" and
                rows[1].get("rect") == [0, 0, 1440, 3200] and
                sum(row.get("kind") == "clear" for row in rows) == 1 and
                rows[-1].get("kind") == "pixels",
                "MEM08 renderer full-frame clear and completion")
        pixels = rows[-1]
        require(type(frame.get("event_count")) is int and
                frame["event_count"] == len(rows) and
                type(frame.get("texts")) is int and frame["texts"] > 0 and
                frame["texts"] == sum(row.get("kind") == "text" for row in rows) and
                type(frame.get("glyph_pixels")) is int and frame["glyph_pixels"] > 0 and
                frame["glyph_pixels"] == pixels.get("glyph_pixels") and
                frame.get("forbidden_glyphs") == pixels.get("forbidden_glyphs") == 0,
                "MEM08 renderer summary/pixel identity")
        live_text = []
        for row in rows[2:-1]:
            kind = row.get("kind")
            require(kind in ("text", "fill"), "MEM08 renderer event kind")
            rect = rectangle(row)
            if kind == "text":
                require(isinstance(row.get("text"), str), "MEM08 renderer text value")
                require(not any(overlap(rect, old) for old in live_text),
                        "MEM08 executed text overlap")
                if row["text"]:
                    live_text.append(rect)
            else:
                require(not any(overlap(rect, old) for old in live_text),
                        "MEM08 renderer fill erases live text")
