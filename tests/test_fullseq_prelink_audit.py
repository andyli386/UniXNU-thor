#!/usr/bin/env python3
"""Raw ARM64 decoder cases and independently rejected real prelink mutations."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import struct
import unittest

CONTROL=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('fullseq_prelink_audit',
    CONTROL/'tools/thorctl/fullseq_prelink_audit.py')
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

def fixture(blob=b'ABCD',va=0x1000):
    header=struct.pack('<8I',0xfeedfacf,0x100000c,0,2,1,152,1,0)
    command=struct.pack('<II16s4Q4I',0x19,152,b'__TEXT_EXEC',
                         va,4096,4096,len(blob),5,5,1,0)
    section=struct.pack('<16s16sQQ8I',b'__text',b'__TEXT_EXEC',va,len(blob),
                        4096,2,0,0,0,0,0,0)
    return header+command+section+bytes(4096-184)+blob

class ParserTests(unittest.TestCase):
    def test_offset_and_bounds(self):
        image=m.Image(fixture())
        self.assertEqual(image.read(0x1000,4),b'ABCD')
        with self.assertRaises(m.AuditError): image.read(0x1001,4)
    def test_truncated_command(self):
        with self.assertRaises(m.AuditError): m.Image(fixture()[:64])
    def test_va_overflow(self):
        raw=bytearray(fixture())
        struct.pack_into('<Q',raw,32+24,(1<<64)-2048)
        with self.assertRaises(m.AuditError): m.Image(raw)
    def test_bad_section(self):
        raw=bytearray(fixture())
        struct.pack_into('<Q',raw,32+72+32,0x3000)
        with self.assertRaises(m.AuditError): m.Image(raw)
    def test_duplicate_plist_key(self):
        with self.assertRaises(m.AuditError):
            m.plist(b'<dict><key>x</key><integer>1</integer><key>x</key><integer>2</integer></dict>')
    def test_plist_references(self):
        v=m.plist(b'<array><string ID="1">sample</string><string IDREF="1"/></array>')
        self.assertEqual(v,['sample','sample'])
    def test_owner_half_open(self):
        owner=m.owner_lookup([(0x2000,0x3000,'A',{'initprot':5})])
        self.assertIsNotNone(owner(0x2ff8,8))
        self.assertIsNone(owner(0x2ff9,8))
        self.assertIsNone(owner(0x3000))

class Arm64Tests(unittest.TestCase):
    def test_direct_encodings(self):
        for word,target,kind in [
            (0x14000002,0x1008,'b'),(0x17ffffff,0xffc,'b'),
            (0x97fffffe,0xff8,'bl'),(0x10000020,0x1004,'adr'),
            (0x10ffffe0,0xffc,'adr'),(0xb0000000,0x2000,'adrp'),
            (0x54000040,0x1008,'cond'),(0xb4000040,0x1008,'cond'),
            (0x36000040,0x1008,'test'),(0x58000040,0x1008,'literal')]:
            self.assertEqual(m.pc_relative(word,0x1000),(target,kind))
    def test_pcrel_slide(self):
        for word in [0x14000002,0x10000020,0xb0000000,0x58000040]:
            a,k=m.pc_relative(word,0x1000)
            self.assertEqual(m.pc_relative(word,0x601000),(a+0x600000,k))
    def test_load_scaled_offset(self):
        self.assertEqual(m.memory_operand(0xf947fc01),(0,4088,8,True,1,None,0))
    def test_signed_load_is_load(self):
        self.assertTrue(m.memory_operand(0xb9800001)[3]) # LDRSW x1,[x0]
    def test_pair_writeback(self):
        self.assertEqual(m.memory_operand(0xa9bf7bfd),(31,-16,16,False,29,30,-16))
    def inventory(self,rows):
        return m.register_inventory(rows,[(0x2000,0x3000,'A',{'initprot':5})])
    def test_exact_page_tail_load(self):
        self.assertEqual(self.inventory([(0x1000,0xb0000000),
                         (0x1004,0xf947fc01)])['resolved_consumers'],1)
    def test_scaled_load_cross_page_rejected(self):
        with self.assertRaises(m.AuditError):
            self.inventory([(0x1000,0xb0000000),(0x1004,0xf9480001)])
    def test_add_then_load_cross_page_rejected(self):
        with self.assertRaises(m.AuditError):
            self.inventory([(0x1000,0xb0000000),(0x1004,0x913fe001),
                            (0x1008,0xf9400422)])
    def test_cmp_is_not_pointer_construction(self):
        v=self.inventory([(0x1000,0xb0000000),(0x1004,0xf100041f)])
        self.assertEqual(v['resolved_consumers'],0)
    def test_unmodeled_effects_do_not_retain_value(self):
        v=self.inventory([(0x1000,0xb0000000),(0x1004,0x8b020001),
                          (0x1008,0xf9480001)])
        self.assertFalse(v['register_dataflow_qualified'])
        self.assertEqual(v['resolved_consumers'],0)
    def test_indexed_access_unqualified(self):
        v=self.inventory([(0x1000,0xb0000000),(0x1004,0xf8627801)])
        self.assertEqual(v['unresolved_indexed_count'],1)
        self.assertFalse(v['register_dataflow_qualified'])
    def test_basic_block_join_drops_value(self):
        v=self.inventory([(0x1000,0xb0000000),(0x1004,0x14000001),
                          (0x1008,0xf9480001)])
        self.assertFalse(v['register_dataflow_qualified'])
        self.assertEqual(v['resolved_consumers'],0)

class RealPrelinkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        project=Path(os.environ.get('THOR_PROJECT_ROOT',CONTROL.parents[2]))
        old=project/'runs/ios16-apfs-full-closure-host-20261026'
        native=project/'runs/ios16-darwin22-20G81-storage-secondpass/tree/build/xnu/ios-obj/DEVELOPMENT_ARM64_PONG/mach.development.pong'
        original=project/'out/ios16-official-restore-20G81/kernel-unpacked/original-arm64.macho'
        if not all(p.is_file() for p in [native,original,old/'apfs-full-prelink.json']):
            raise unittest.SkipTest('real pinned host fixture absent')
        cls.native,cls.original=native.read_bytes(),original.read_bytes()
        cls.merged=(old/'original-20G81-apfs-fullclosure-hostonly.macho').read_bytes()
        cls.receipt=json.loads((old/'apfs-full-prelink.json').read_text())
        cls.image=m.Image(cls.merged)
    def mutation(self,va,blob,expected):
        mutated=bytearray(self.merged)
        at=self.image.offset(va,len(blob))
        mutated[at:at+len(blob)]=blob
        receipt=copy.deepcopy(self.receipt)
        receipt['candidate_sha256']=m.digest(mutated)
        with self.assertRaisesRegex(m.AuditError,expected):
            m.audit(self.native,self.original,bytes(mutated),receipt)
    def test_real_positive(self):
        proof=m.audit(self.native,self.original,self.merged,self.receipt)
        self.assertEqual(proof['pointer_counts'],{'component':5742,'kernel':7230})
        self.assertEqual(proof['direct_pc_relative_count'],96260)
        self.assertFalse(proof['runtime_qualified'])
    def test_rebound_pointer_mutation(self):
        b=self.receipt['bindings'][0]
        self.mutation(b['new_site'],struct.pack('<Q',b['new_target']^4096),'actual rebound pointer')
    def test_kmod_address_mutation(self):
        k=next(iter(self.receipt['module_entrypoints'].values()))
        self.mutation(k['kmod_info_va']+156,bytes(8),'actual kmod address')
    def test_kmod_start_mutation(self):
        k=next(iter(self.receipt['module_entrypoints'].values()))
        self.mutation(k['kmod_info_va']+180,bytes(8),'actual rebound pointer|start/stop')
    def test_sparse_header_mutation(self):
        self.mutation(self.receipt['sparse_index']['va'],bytes(8),'actual sparse header index')
    def test_original_code_mutation(self):
        name=self.receipt['components'][0]
        module_va=next(e['va'] for e in self.receipt['extents']
                       if e['module']==name and e['segment']=='__TEXT_EXEC')
        word,=struct.unpack('<I',self.image.read(module_va,4))
        self.mutation(module_va,struct.pack('<I',word^1),'coalesced content reconstruction')
    def test_native_code_mutation(self):
        image=m.Image(self.native)
        s=image.segment('__TEXT_EXEC')
        word,=struct.unpack('<I',image.read(s['va'],4))
        self.mutation(s['va'],struct.pack('<I',word^1),'native bytes')
    def test_metadata_identity_mutation(self):
        info=self.image.segment('__PRELINK_INFO')
        blob=self.image.read(info['va'],info['filesize'])
        marker=b'com.apple.filesystems.apfs'
        where=blob.index(marker)
        self.mutation(info['va']+where,b'x'+marker[1:],'all rewritten metadata')
    def test_receipt_pointer_removed(self):
        receipt=copy.deepcopy(self.receipt)
        receipt['bindings'].pop()
        with self.assertRaisesRegex(m.AuditError,'complete pointer list'):
            m.audit(self.native,self.original,self.merged,receipt)
    def test_receipt_count_forged(self):
        receipt=copy.deepcopy(self.receipt)
        receipt['pointer_counts']['kernel']-=1
        with self.assertRaisesRegex(m.AuditError,'pointer kind counts'):
            m.audit(self.native,self.original,self.merged,receipt)
    def test_false_runtime_claim_rejected(self):
        receipt=copy.deepcopy(self.receipt)
        receipt['runtime_qualified']=True
        with self.assertRaisesRegex(m.AuditError,'unqualified emitter flag'):
            m.audit(self.native,self.original,self.merged,receipt)
    def test_sparse_abi_version_mutation(self):
        native=bytearray(self.native)
        ni=m.Image(native)
        va=next(iter(ni.symbols['_kalloc_sparse_prelink_index_version']))
        struct.pack_into('<I',native,ni.offset(va,4),2)
        merged=bytearray(self.merged)
        struct.pack_into('<I',merged,self.image.offset(va,4),2)
        receipt=copy.deepcopy(self.receipt)
        receipt['native_sha256']=m.digest(native)
        receipt['candidate_sha256']=m.digest(merged)
        with self.assertRaisesRegex(m.AuditError,'native sparse ABI version'):
            m.audit(bytes(native),self.original,bytes(merged),receipt)

if __name__=='__main__':
    unittest.main()
