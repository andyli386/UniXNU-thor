#!/usr/bin/env python3
"""MEM05 profile and actual owned-layout transition rejection tests; no entropy."""
from pathlib import Path
import importlib.util,json,struct,unittest
HERE=Path(__file__).resolve().parent
sp=importlib.util.spec_from_file_location('mem05_fixture',HERE/'test_mem04_packaging.py')
legacy=importlib.util.module_from_spec(sp);sp.loader.exec_module(legacy)
host=legacy.host

class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.f=legacy.ProfileFixture();self.addCleanup(self.f.close);f=self.f
        f.build['schema']='thor-mem05-build-manifest-v1'
        f.artifact_profile=host.ARTIFACT_PROFILES[f.build['schema']]
        f.lock.update(frontier='early-vm-return',native_arm_vm_init_return_compiled=True,bounded_after_native_VM_return=True,phone_actions=0)
        f.build.update(frontier='early-vm-return',native_arm_vm_init_return_compiled=True,bounded_after_native_VM_return=True)
        f.linked.update(schema='thor-mem05-early-vm-machinecode-v1',result='PASS_MEM05_BOUNDED_EARLY_VM_RETURN',
            native_arm_vm_init_return_compiled=True,bounded_after_native_VM_return=True,
            native_tail={'native_tail_calls':{'_pmap_bootstrap':'0x1234'}},arm_vm_init_returned_on_phone=False,
            full_VM_initialization_proven=False)
        f.linked['checkpoint_contract'].update(compiled_stage_prefix=list(range(16)),reserved_slots=[])
        f.sync_artifact()
    def validate(self):
        self.f.sync_artifact()
        with legacy.mock.patch.object(host.subprocess, 'check_output',
                side_effect=[self.f.lock['xnu_commit'], '', '\n'.join(self.f.lock['post_build_changes'])]):
            return host.validate_mem03_artifact(self.f.root,self.f.candidate)
    def test_exact_new_native_return_profile(self):self.assertEqual(self.validate()[0]['_validated_packaging_profile'],'MEM05')
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

class TransitionTests(unittest.TestCase):
    def setUp(self):
        self.old=legacy.NEW_TRANSITION;self.new=bytearray(self.old)
        self.layout={'loaded_span_bytes':20185088,'entry_offset':0x3ff488,'vectors_offset':0x400000}
        for offset,value in [(76,20185088+0x4000),(96,20185088),(156,20185088+0x3c000)]:
            for at,immediate in [(offset,value&0xffff),(offset+4,value>>16)]:
                word=struct.unpack_from('<I',self.new,at)[0]
                struct.pack_into('<I',self.new,at,(word&~(0xffff<<5))|(immediate<<5))
        self.new=bytes(self.new)
    def test_actual_reviewed_bytes_and_all_five_pairs(self):
        self.assertEqual(host.sha_bytes(self.new),host.MEM05_TRANSITION_PIN)
        decoded=host.validate_mem05_transition_words(self.old,self.new,self.layout)
        self.assertEqual(decoded['new_decoded_args_offset'],20185088)
        self.assertEqual(decoded['new_decoded_stack_top_offset'],20185088+0x3c000)
    def test_old_span_rejected(self):
        self.layout['loaded_span_bytes']-=0x4000
        with self.assertRaises(RuntimeError):host.validate_mem05_transition_words(self.old,self.new,self.layout)
    def test_wrong_native_entry_rejected(self):
        self.layout['entry_offset']+=4
        with self.assertRaises(RuntimeError):host.validate_mem05_transition_words(self.old,self.new,self.layout)
    def test_wrong_native_vector_rejected(self):
        self.layout['vectors_offset']+=4096
        with self.assertRaises(RuntimeError):host.validate_mem05_transition_words(self.old,self.new,self.layout)
    def test_extra_instruction_change_rejected(self):
        code=bytearray(self.new);code[180]^=1
        with self.assertRaises(RuntimeError):host.validate_mem05_transition_words(self.old,bytes(code),self.layout)
    def test_old_transition_not_reusable(self):
        with self.assertRaises(RuntimeError):host.validate_mem05_transition_words(self.old,self.old,self.layout)
    def test_truncated_code_rejected(self):
        with self.assertRaises(RuntimeError):host.validate_mem05_transition_words(self.old,self.new[:-4],self.layout)

if __name__=='__main__':unittest.main()
