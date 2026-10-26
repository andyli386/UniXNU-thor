#!/usr/bin/env python3
"""MEM07 profile and actual owned-layout transition rejection tests; no entropy."""
from pathlib import Path
import importlib.util,json,struct,unittest
HERE=Path(__file__).resolve().parent
sp=importlib.util.spec_from_file_location('mem07_fixture',HERE/'test_mem04_packaging.py')
legacy=importlib.util.module_from_spec(sp);sp.loader.exec_module(legacy)
host=legacy.host

class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.f=legacy.ProfileFixture();self.addCleanup(self.f.close);f=self.f
        f.build['schema']='thor-mem07-build-manifest-v1'
        f.artifact_profile=host.ARTIFACT_PROFILES[f.build['schema']]
        f.lock.update(phone_actions=0,pmap_map_globals_executed=False,native_pmap_map_globals_call_compiled=False,native_arm_init_kprintf_startup_compiled=True,bounded_after_native_KPRINTF_startup=True,normal_machine_init_reached=False,frontier='native-arm-init-kprintf-startup',native_arm_vm_init_return_compiled=True,bounded_after_native_VM_return=True)
        f.build.update(native_pmap_map_globals_call_compiled=False,native_arm_init_kprintf_startup_compiled=True,bounded_after_native_KPRINTF_startup=True,normal_machine_init_reached=False,frontier='native-arm-init-kprintf-startup',native_arm_vm_init_return_compiled=True,bounded_after_native_VM_return=True)
        f.linked.update(native_pmap_map_globals_call_compiled=False,native_arm_init_kprintf_startup_compiled=True,bounded_after_native_KPRINTF_startup=True,normal_machine_init_reached=False,schema='thor-mem07-native-kprintf-machinecode-v1',result='PASS_MEM07_NATIVE_ARM_INIT_KPRINTF_STARTUP',
            native_arm_vm_init_return_compiled=True,bounded_after_native_VM_return=True,
            native_tail={'native_tail_calls':{'_pmap_bootstrap':'0x1234'}},arm_vm_init_returned_on_phone=False,
            full_VM_initialization_proven=False)
        f.linked['checkpoint_contract'].update(compiled_stage_prefix=list(range(16)),reserved_slots=[])
        pins={}
        for relative in ['osfmk/kern/startup.c','pexpert/arm/pe_serial.c','pexpert/arm/pe_kprintf.c']:
            path=f.root/'xnu-source/.cache/xnu-work'/relative
            path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'actual-native-source')
            pins[relative]=host.sha(path)
        guards=f.candidate/'mem07-native-startup-guards.json'
        guards.write_text(json.dumps(dict(schema='thor-mem07-production-C-startup-guards-v1',
            result='PASS_NATIVE_READONLY_GUARDS_WITH_FAULTS',phone_actions=0,source_sha256=pins)))
        f.lock['native_startup_guards_sha256']=f.build['native_startup_guards_sha256']=host.sha(guards)
        f.sync_artifact()
    def validate(self):
        self.f.sync_artifact()
        with legacy.mock.patch.object(host.subprocess, 'check_output',
                side_effect=[self.f.lock['xnu_commit'], '', '\n'.join(self.f.lock['post_build_changes'])]):
            return host.validate_mem03_artifact(self.f.root,self.f.candidate)
    def test_exact_new_native_return_profile(self):self.assertEqual(self.validate()[0]['_validated_packaging_profile'],'MEM07')
    def test_old_proof_schema_cannot_be_relabelled(self):
        self.f.linked['schema']='thor-mem04-grid-pmap-machinecode-v1'
        with self.assertRaises(RuntimeError):self.validate()
    def test_missing_return_gate_rejected(self):
        self.f.linked['bounded_after_native_VM_return']=False
        with self.assertRaises(RuntimeError):self.validate()
    def test_fourteen_stage_downgrade_rejected(self):
        self.f.linked['checkpoint_contract']['compiled_stage_prefix']=list(range(14))
        with self.assertRaises(RuntimeError):self.validate()
    def test_boolean_stage_id_rejected(self):
        self.f.linked['checkpoint_contract']['compiled_stage_prefix'][1]=True
        with self.assertRaises(RuntimeError):self.validate()
    def test_reserved_wait_slots_rejected(self):
        self.f.linked['checkpoint_contract']['reserved_slots']=[14,15]
        with self.assertRaises(RuntimeError):self.validate()
    def test_hardware_claim_rejected(self):
        self.f.linked['arm_vm_init_returned_on_phone']=True
        with self.assertRaises(RuntimeError):self.validate()
    def test_old_frontier_rejected(self):
        self.f.lock['frontier']='pmap-return'
        with self.assertRaises(RuntimeError):self.validate()

    def test_early_native_global_call_rejected(self):
        self.f.linked['native_pmap_map_globals_call_compiled']=True
        with self.assertRaises(RuntimeError):self.validate()
    def test_missing_native_startup_stop_rejected(self):
        self.f.build['bounded_after_native_KPRINTF_startup']=False
        with self.assertRaises(RuntimeError):self.validate()
    def test_runtime_machine_init_claim_rejected(self):
        self.f.linked['normal_machine_init_reached']=True
        with self.assertRaises(RuntimeError):self.validate()

    def test_guard_execution_source_change_rejected(self):
        (self.f.root/'xnu-source/.cache/xnu-work/osfmk/kern/startup.c').write_bytes(b'changed')
        with self.assertRaises(RuntimeError):self.validate()

    def replace_guard_document(self,change):
        p=self.f.candidate/'mem07-native-startup-guards.json'
        d=json.loads(p.read_text());change(d);p.write_text(json.dumps(d))
        self.f.lock['native_startup_guards_sha256']=self.f.build['native_startup_guards_sha256']=host.sha(p)

    def test_empty_guard_execution_source_set_rejected(self):
        self.replace_guard_document(lambda d:d.update(source_sha256={}))
        with self.assertRaises(RuntimeError):self.validate()

    def test_wrong_guard_execution_result_rejected(self):
        self.replace_guard_document(lambda d:d.update(result='FAIL'))
        with self.assertRaises(RuntimeError):self.validate()

    def test_guard_execution_receipt_hash_mismatch_rejected(self):
        self.f.build['native_startup_guards_sha256']='0'*64
        with self.assertRaises(RuntimeError):self.validate()

class ArchiveMemberTests(unittest.TestCase):
    def member(self,name,data):
        header=f"{name:<16}{0:<12}{0:<6}{0:<6}{0:<8}{len(data):<10}`\n".encode()
        assert len(header)==60
        return header+data+(b'\n' if len(data)%2 else b'')
    def test_plain_member(self):
        raw=b'!<arch>\n'+self.member('p.obj/',b'ABC')
        self.assertEqual(host.archive_member_bytes(raw,'p.obj'),b'ABC')
    def test_actual_coff_null_terminated_absolute_long_name(self):
        names=b'/home/vincent/Projects/p.obj\0'
        raw=b'!<arch>\n'+self.member('//',names)+self.member('/0',b'OBJECT')
        self.assertEqual(host.archive_member_bytes(raw,'p.obj'),b'OBJECT')
    def test_gnu_long_name(self):
        raw=b'!<arch>\n'+self.member('//',b'long/path/p.obj/\n')+self.member('/0',b'OBJECT')
        self.assertEqual(host.archive_member_bytes(raw,'p.obj'),b'OBJECT')
    def test_bsd_inline_name(self):
        name=b'long/path/p.obj'
        raw=b'!<arch>\n'+self.member('#1/'+str(len(name)),name+b'OBJECT')
        self.assertEqual(host.archive_member_bytes(raw,'p.obj'),b'OBJECT')
    def test_thin_archive_rejected(self):
        with self.assertRaises(RuntimeError):host.archive_member_bytes(b'!<thin>\n','p.obj')
    def test_duplicate_basename_rejected(self):
        raw=b'!<arch>\n'+self.member('p.obj/',b'A')+self.member('p.obj/',b'B')
        with self.assertRaises(RuntimeError):host.archive_member_bytes(raw,'p.obj')
    def test_truncated_object_rejected(self):
        raw=b'!<arch>\n'+self.member('p.obj/',b'OBJECT')
        with self.assertRaises(RuntimeError):host.archive_member_bytes(raw[:-1],'p.obj')
    def test_unterminated_long_name_rejected(self):
        raw=b'!<arch>\n'+self.member('//',b'long/path/p.obj')+self.member('/0',b'OBJECT')
        with self.assertRaises(RuntimeError):host.archive_member_bytes(raw,'p.obj')
    def test_missing_native_object_rejected(self):
        raw=b'!<arch>\n'+self.member('q.obj/',b'OBJECT')
        with self.assertRaises(RuntimeError):host.archive_member_bytes(raw,'p.obj')

class UnchangedTransitionTests(unittest.TestCase):
    def setUp(self):
        spec=importlib.util.spec_from_file_location('m6_transition_fixture',HERE/'test_mem06_packaging.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        fixture=module.TransitionTests();fixture.setUp();self.code=fixture.new
        self.layout=dict(loaded_span_bytes=20201472,entry_offset=0x400488,vectors_offset=0x401000)
    def test_actual_unchanged500_bytes_decode_all_five_pairs(self):
        decoded=host.validate_mem07_transition_words(self.code,self.code,self.layout)
        self.assertEqual(decoded['new_decoded_stack_top_offset'],20201472+0x3c000)
        self.assertEqual(host.sha_bytes(self.code),host.MEM07_TRANSITION_PIN)
    def test_changed_span_rejected(self):
        self.layout['loaded_span_bytes']+=4096
        with self.assertRaises(RuntimeError):host.validate_mem07_transition_words(self.code,self.code,self.layout)
    def test_changed_entry_rejected(self):
        self.layout['entry_offset']+=4
        with self.assertRaises(RuntimeError):host.validate_mem07_transition_words(self.code,self.code,self.layout)
    def test_changed_vector_rejected(self):
        self.layout['vectors_offset']+=4096
        with self.assertRaises(RuntimeError):host.validate_mem07_transition_words(self.code,self.code,self.layout)
    def test_extra_instruction_mutation_rejected(self):
        changed=bytearray(self.code);changed[180]^=1
        with self.assertRaises(RuntimeError):host.validate_mem07_transition_words(self.code,bytes(changed),self.layout)
    def test_truncated_transition_rejected(self):
        with self.assertRaises(RuntimeError):host.validate_mem07_transition_words(self.code,self.code[:-4],self.layout)

if __name__=='__main__':unittest.main()
