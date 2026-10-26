#!/usr/bin/env python3
"""Independent static audit of a Thor Darwin22/original-20G81 prelink.
No USB/device operations; runtime and foreign-ABI claims remain unqualified.
"""
import argparse
import base64
from bisect import bisect_right
from collections import Counter
import copy
import hashlib
import json
from pathlib import Path
import struct
import xml.etree.ElementTree as ET

APPLE_SHA = 'd260defcc2eaac258fd3a2234454dda46791dae45e97608c5416f9ba8fd366e9'
ROOTS = (
 'com.apple.AppleFSCompression.AppleFSCompressionTypeZlib',
 'com.apple.driver.AppleEffaceableStorage', 'com.apple.filesystems.apfs',
 'com.apple.iokit.CoreAnalyticsFamily', 'com.apple.iokit.IOCryptoAcceleratorFamily',
 'com.apple.iokit.IOStorageFamily', 'com.apple.kec.Compression',
 'com.apple.kec.corecrypto', 'com.apple.kec.pthread', 'com.apple.kext.CoreTrust',
 'com.apple.security.AppleImage4')
COALESCED = {'__PRELINK_TEXT':'__TEXT', '__PLK_TEXT_EXEC':'__TEXT_EXEC',
 '__PLK_DATA_CONST':'__DATA_CONST', '__PRELINK_DATA':'__DATA'}
LIMIT = 1 << 64

class AuditError(RuntimeError):
    pass

def need(condition, message):
    if not condition:
        raise AuditError(message)

def digest(data):
    return hashlib.sha256(data).hexdigest()

def align(value, alignment=4096):
    return (value + alignment - 1) // alignment * alignment

class Image:
    def __init__(self, data, header=0):
        self.data, self.header = data, header
        need(0 <= header <= len(data)-32, 'Mach-O header bounds')
        magic,cpu,subtype,self.kind,ncmds,cmdbytes,self.flags,_ = struct.unpack_from('<8I',data,header)
        need((magic,cpu,subtype)==(0xfeedfacf,0x100000c,0), 'ordinary arm64 Mach-O')
        need(0 < ncmds < 4096 and header+32+cmdbytes <= len(data), 'command extent')
        self.segments, self.commands, self.symbols = [],[],{}
        pos, end = header+32, header+32+cmdbytes
        for _ in range(ncmds):
            need(pos+8 <= end,'truncated command')
            cmd,size = struct.unpack_from('<II',data,pos)
            need(size>=8 and size%8==0 and pos+size<=end,'command bounds')
            self.commands.append((cmd,pos,size))
            if cmd == 0x19:
                need(size>=72,'segment header')
                name,va,vs,off,fs,maximum,initial,nsect,flags = struct.unpack_from('<16s4Q4I',data,pos+8)
                need(size==72+nsect*80 and fs<=vs and va+vs<=LIMIT,'segment geometry')
                name=name.rstrip(b'\0').decode()
                segment=dict(name=name,va=va,size=vs,fileoff=off,filesize=fs,
                             maxprot=maximum,initprot=initial,flags=flags,at=pos,sections=[])
                for i in range(nsect):
                    at=pos+72+i*80
                    sn,sg,sv,ss,so = struct.unpack_from('<16s16sQQI',data,at)
                    need(not ss or va<=sv<=sv+ss<=va+vs,'section geometry')
                    segment['sections'].append(dict(name=sn.rstrip(b'\0').decode(),
                       va=sv,size=ss,fileoff=so,at=at))
                self.segments.append(segment)
            pos+=size
        need(pos==end,'command bytes consumed')
        self.command_end=end
        tables=[at for cmd,at,size in self.commands if cmd==2 and size==24]
        need(len(tables)<=1,'one symbol table')
        if tables:
            off,count,strings,length=struct.unpack_from('<4I',data,tables[0]+8)
            need(off+count*16<=len(data) and strings+length<=len(data),'symbol table bounds')
            for i in range(count):
                sx,kind,_,_,va=struct.unpack_from('<IBBHQ',data,off+i*16)
                if kind&0xe0 or not kind&1 or not kind&0xe: continue
                stop=data.find(b'\0',strings+sx,strings+length)
                need(sx<length and stop>=0,'symbol string')
                self.symbols.setdefault(data[strings+sx:stop].decode(),set()).add(va)

    def segment(self,name):
        found=[s for s in self.segments if s['name']==name]
        need(len(found)==1,'unique segment '+name)
        return found[0]

    def offset(self,va,size):
        need(0<=va<=va+size<=LIMIT,'address overflow')
        found=[s['fileoff']+va-s['va'] for s in self.segments
               if s['va']<=va and va+size<=s['va']+s['filesize']]
        need(len(found)==1 and found[0]+size<=len(self.data),'file-backed address '+hex(va))
        return found[0]

    def read(self,va,size):
        at=self.offset(va,size)
        return self.data[at:at+size]

def plist(blob):
    refs={}
    def read(e):
        if 'IDREF' in e.attrib:
            need(e.attrib['IDREF'] in refs,'plist forward/unknown ref')
            return refs[e.attrib['IDREF']]
        if e.tag=='dict':
            children=list(e)
            need(len(children)%2==0,'plist dictionary pairs')
            result={}
            for key,value in zip(children[::2],children[1::2]):
                name=read(key)
                need(name not in result,'duplicate plist key')
                result[name]=read(value)
        elif e.tag=='array': result=[read(child) for child in e]
        elif e.tag in ('string','key'): result=e.text or ''
        elif e.tag=='integer': result=int(e.text or '0',0)
        elif e.tag=='data': result=base64.b64decode(''.join((e.text or '').split()),validate=True)
        elif e.tag in ('true','false'): result=e.tag=='true'
        else: raise AuditError('plist tag '+e.tag)
        if 'ID' in e.attrib:
            need(e.attrib['ID'] not in refs,'duplicate plist ID')
            refs[e.attrib['ID']]=result
        return result
    return read(ET.fromstring(blob.split(b'\0',1)[0]))

def metadata(image):
    s=image.segment('__PRELINK_INFO')
    value=plist(image.read(s['va'],s['filesize']))
    entries={}
    for entry in value['_PrelinkInfoDictionary']:
        name=entry['CFBundleIdentifier']
        need(name not in entries,'duplicate module '+name)
        entries[name]=entry
    return value,entries

def closure(entries,roots):
    selected,interfaces=set(),set()
    pending=list(roots)
    while pending:
        name=pending.pop()
        if name in selected|interfaces: continue
        need(name in entries,'missing dependency '+name)
        entry=entries[name]
        if entry.get('OSKernelResource') is True: interfaces.add(name)
        else:
            selected.add(name)
            pending.extend(entry.get('OSBundleLibraries') or {})
    return selected,interfaces

def modules(image,entries):
    result,extents={},[]
    for name,e in entries.items():
        if e.get('OSKernelResource') or not e.get('_PrelinkExecutableSize'): continue
        va=e.get('_PrelinkExecutableLoadAddr')
        need(isinstance(va,int),'module header address')
        module=Image(image.data,image.offset(va,32))
        need(module.kind==11,'MH_KEXT_BUNDLE')
        result[name]=module
        for s in module.segments:
            if s['size'] and s['name']!='__LINKEDIT':
                extents.append((s['va'],s['va']+s['size'],name,s))
    extents.sort(key=lambda e:e[:3])
    need(all(a[1]<=b[0] for a,b in zip(extents,extents[1:])),'component overlap')
    return result,extents

def owner_lookup(extents):
    starts=[e[0] for e in extents]
    def owner(va,size=1):
        index=bisect_right(starts,va)-1
        return extents[index] if index>=0 and va+size<=extents[index][1] else None
    return owner

def bindings(native,original,meta,extents,selected,slide):
    packed=meta['_PrelinkLinkKASLROffsets']
    need(len(packed)>=4,'relocation count')
    count,=struct.unpack_from('<I',packed)
    need(len(packed)==4+count*4,'relocation extent')
    offsets=struct.unpack_from('<'+str(count)+'I',packed,4)
    need(len(set(offsets))==count,'duplicate pointer site')
    by_va={}
    for name,values in original.symbols.items():
        for va in values: by_va.setdefault(va,[]).append(name)
    owner=owner_lookup(extents)
    base=original.segment('__PRELINK_TEXT')['va']
    answer=[]
    for offset in offsets:
        site=base+offset
        source=owner(site,8)
        if source is None or source[2] not in selected: continue
        target,=struct.unpack('<Q',original.read(site,8))
        destination=owner(target)
        if destination:
            need(destination[2] in selected,'undeclared pointer dependency')
            replacement,kind=target+slide,'component'
        else:
            choices={va for name in by_va.get(target,[]) for va in native.symbols.get(name,set())}
            need(len(choices)==1,'ambiguous native symbol binding '+hex(target))
            replacement,kind=next(iter(choices)),'kernel'
            need(any(s['va']<=replacement<s['va']+s['size'] for s in native.segments),
                 'kernel export outside loaded image')
        need(0<=site+slide<LIMIT and 0<=replacement<LIMIT,'pointer slide overflow')
        answer.append(dict(site=site,target=target,new_site=site+slide,
                           new_target=replacement,kind=kind,module=source[2]))
    return answer

def sxt(v,bits):
    return v-(1<<bits) if v&(1<<(bits-1)) else v

def pc_relative(word,pc):
    if word&0x9f000000 in (0x10000000,0x90000000):
        immediate=sxt(((word>>5)&0x7ffff)*4+((word>>29)&3),21)
        page=bool(word&0x80000000)
        return ((pc&~4095)+(immediate<<12), 'adrp') if page else (pc+immediate,'adr')
    if word&0x7c000000==0x14000000:
        return pc+(sxt(word&0x3ffffff,26)<<2),'bl' if word&(1<<31) else 'b'
    if word&0xff000010==0x54000000 or word&0x7e000000==0x34000000:
        return pc+(sxt((word>>5)&0x7ffff,19)<<2),'cond'
    if word&0x3b000000==0x18000000:
        return pc+(sxt((word>>5)&0x7ffff,19)<<2),'literal'
    if word&0x7e000000==0x36000000:
        return pc+(sxt((word>>5)&0x3fff,14)<<2),'test'
    return None

# These five pools are byte-exact data inside TEXT_EXEC of the pinned Apple KC.
LITERALS = (
 ('com.apple.AppleFSCompression.AppleFSCompressionTypeZlib',18446744005098791168,528,'f7bb30778915b574b63a17573babfdeb97251ae92fb57e7302e69783f454f92a'),
 ('com.apple.AppleFSCompression.AppleFSCompressionTypeZlib',18446744005098827744,40,'48bdf8e57f8647f2ef3cfda5b500209c4be9b5742632db0ff3614aca8d7ee63d'),
 ('com.apple.kec.Compression',18446744005091935680,528,'f7bb30778915b574b63a17573babfdeb97251ae92fb57e7302e69783f454f92a'),
 ('com.apple.kec.corecrypto',18446744005082207760,256,'74ef7306e7452d6859b6463ce496b8df30925f69e1b2969e1f3f34bbc9c6af04'),
 ('com.apple.kec.corecrypto',18446744005082226784,256,'74ef7306e7452d6859b6463ce496b8df30925f69e1b2969e1f3f34bbc9c6af04'))

def memory_operand(word):
    """Return base, signed offset, width, load, destination, second, writeback.
    Indexed-register operands are reported separately as unbounded.
    """
    size=(word>>30)&3
    vector=bool(word&(1<<26))
    opc=(word>>22)&3
    rn,rt=(word>>5)&31,word&31
    width=16 if vector and opc&2 else 1<<size
    load=bool(opc&1) if vector else bool(opc)
    if word&0x3b000000==0x39000000:
        if not vector and size==3 and opc==2: return None # PRFM
        return rn,((word>>10)&4095)*width,width,load,rt,None,0
    if word&0x3a000000==0x28000000:
        width=(4<<((word>>30)&3)) if vector else (8 if size==2 else 4)
        mode=(word>>23)&3
        offset=sxt((word>>15)&127,7)*width
        return rn,0 if mode==1 else offset,width*2,bool(word&(1<<22)),rt,(word>>10)&31,offset if mode in (1,3) else 0
    if word&0x3b200000==0x38000000:
        mode=(word>>10)&3
        offset=sxt((word>>12)&511,9)
        return rn,0 if mode==1 else offset,width,load,rt,None,offset if mode in (1,3) else 0
    return None

def register_inventory(rows,extents):
    owner=owner_lookup(extents)
    leaders={rows[0][0]} if rows else set()
    for pc,word in rows:
        decoded=pc_relative(word,pc)
        if decoded and decoded[1] in ('b','bl','cond','test'):
            leaders.add(decoded[0])
            leaders.add(pc+4)
    known,origins,consumed={},set(),set()
    resolved,unresolved,hazards=[],[],[]
    incomplete_semantics=0
    ends={e[1] for e in extents}
    for pc,word in rows:
        if pc in leaders: known={}
        rd,rn=word&31,(word>>5)&31
        decoded=pc_relative(word,pc)
        if decoded and decoded[1] in ('adrp','adr'):
            known[rd]=(decoded[0],pc)
            origins.add(pc)
            continue
        if word&0x1f000000==0x11000000:
            if rd==31: continue # CMP/CMN or SP, no derived GPR address
            if rn in known and word&(1<<31):
                value,origin=known[rn]
                immediate=((word>>10)&4095)<<(12 if word&(1<<22) else 0)
                value=value-immediate if word&(1<<30) else value+immediate
                known[rd]=(value,origin)
                consumed.add(origin)
                resolved.append((pc,origin,value,0))
                if owner(value) is None and value not in ends:
                    hazards.append(dict(pc=hex(pc),origin=hex(origin),address=hex(value),kind='immediate-address'))
            else: known.pop(rd,None)
            continue
        operand=memory_operand(word)
        if operand:
            base,offset,width,load,dest,second,writeback=operand
            if base in known:
                value,origin=known[base]
                address=value+offset
                consumed.add(origin)
                resolved.append((pc,origin,address,width))
                if owner(address,width) is None:
                    hazards.append(dict(pc=hex(pc),origin=hex(origin),address=hex(address),width=width,kind='memory'))
                if writeback: known[base]=(value+writeback,origin)
            if load and not word&(1<<26):
                known.pop(dest,None)
                if second is not None: known.pop(second,None)
            continue
        if word&0x3b200c00==0x38200800:
            if rn in known:
                unresolved.append(dict(pc=hex(pc),origin=hex(known[rn][1]),kind='indexed-register-memory'))
            if word&(1<<22): known.pop(rd,None)
            continue
        if word&0x7fe0ffe0==0x2a0003e0: # MOV Xd,Xm alias
            source=(word>>16)&31
            if word&(1<<31) and source in known: known[rd]=known[source]
            else: known.pop(rd,None)
            continue
        if decoded and decoded[1]=='bl' or word&0xfffffc1f==0xd63f0000:
            for reg in range(19): known.pop(reg,None)
        elif decoded and decoded[1] in ('b','cond','test') or word&0xfffffc1f in (0xd61f0000,0xd65f0000):
            known={}
        else:
            incomplete_semantics += bool(known)
            known={} # unsupported register effects cannot retain a symbolic value
    missing=sorted(origins-consumed)
    need(not hazards,'derived PC-relative address escapes selected components: '+str(hazards[:4]))
    return dict(adr_origins=len(origins),resolved_consumers=len(resolved),
       resolved_inventory_sha256=digest(json.dumps(resolved,separators=(',',':')).encode()),
       unresolved_indexed_count=len(unresolved),unresolved_indexed_examples=unresolved[:12],
       origins_without_proven_consumer=len(missing),unconsumed_examples=[hex(p) for p in missing[:12]],
       unsupported_effects_with_live_origin=incomplete_semantics,
       analysis_scope='proven constant-base immediate consumers within one basic block',
       register_dataflow_qualified=not unresolved and not missing and not incomplete_semantics)

def instruction_inventory(original,merged,module_images,selected_extents,slide):
    pc_count=0
    code_hashes={}
    reg_reports={}
    literals=[]
    for name,module in module_images.items():
        rows=[]
        for segment in module.segments:
            if segment['name']!='__TEXT_EXEC' or not segment['filesize']: continue
            va,size=segment['va'],segment['filesize']
            old=original.read(va,size)
            new=merged.read(va+slide,size)
            need(old==new,'original TEXT_EXEC changed '+name)
            code_hashes[name]=digest(old)
            pools=[(v,v+n,h) for m,v,n,h in LITERALS if m==name and va<=v<v+n<=va+size]
            for begin,end,sha in pools:
                need(digest(original.read(begin,end-begin))==sha,'inline data digest')
                literals.append(dict(module=name,va=begin,size=end-begin,sha256=sha))
            for offset in range(0,len(old)-3,4):
                pc=va+offset
                if any(a<=pc<b for a,b,h in pools): continue
                word,=struct.unpack_from('<I',old,offset)
                rows.append((pc,word))
                decoded=pc_relative(word,pc)
                if decoded is None: continue
                target,kind=decoded
                if kind=='adrp':
                    found=any((a&~4095)<=target<align(b) for a,b,_,_ in selected_extents)
                else:
                    width=1
                    if kind=='literal':
                        opc=(word>>30)&3
                        width=(4<<opc) if word&(1<<26) else (8 if opc==1 else 4)
                        if not word&(1<<26) and opc==3: width=1 # PRFM, no data read
                    extent=owner_lookup(selected_extents)(target,width)
                    found=extent is not None
                    if kind in ('b','bl','cond','test'):
                        found=found and bool(extent[3]['initprot']&4)
                need(found,'direct PC-relative target escapes closure at '+hex(pc))
                rebased=pc_relative(word,pc+slide)
                need(rebased==(target+slide,kind),'PC-relative slide changed semantics')
                pc_count+=1
        reg_reports[name]=register_inventory(rows,selected_extents)
    return dict(direct_pc_relative_count=pc_count,original_TEXT_EXEC_sha256=code_hashes,
       known_inline_data=literals,register_dataflow=reg_reports,
       register_dataflow_qualified=all(v['register_dataflow_qualified'] for v in reg_reports.values()))

def audit(native_data,original_data,merged_data,receipt):
    need(digest(original_data)==APPLE_SHA,'pinned original Apple KC digest')
    native,original,merged=Image(native_data),Image(original_data),Image(merged_data)
    need(native.kind==original.kind==merged.kind==2 and native.flags&1,'MH_EXECUTE and native NOUNDEFS')
    need(receipt.get('native_sha256')==digest(native_data),'native receipt hash')
    need(receipt.get('original_sha256')==digest(original_data),'original receipt hash')
    need(receipt.get('candidate_sha256')==digest(merged_data),'merged receipt hash')
    need(receipt.get('candidate_bytes')==len(merged_data),'merged receipt byte count')
    for flag in ('instruction_relocation_complete','abi_qualified','loadable',
                 'register_dataflow_qualified','runtime_qualified'):
        need(receipt.get(flag) is False,'preserve unqualified emitter flag '+flag)
    slide=receipt['slide']
    need(isinstance(slide,int) and slide%4096==0,'4K component slide')
    meta,entries=metadata(original)
    selected,interfaces=closure(entries,ROOTS)
    need(selected==set(ROOTS),'exact eleven executable dependency closure')
    need(receipt['components']==sorted(selected) and
         receipt['kernel_interfaces']==sorted(interfaces) and
         receipt['codeless_components']==[],'receipt dependency closure')
    all_modules,all_extents=modules(original,entries)
    selected_modules={name:all_modules[name] for name in sorted(selected)}
    extents=[e for e in all_extents if e[2] in selected]
    derived=bindings(native,original,meta,all_extents,selected,slide)
    need(derived==receipt['bindings'],'independently reconstructed complete pointer list')
    counts=dict(Counter(b['kind'] for b in derived))
    need(counts==receipt['pointer_counts'],'pointer kind counts')
    for binding in derived:
        actual,=struct.unpack('<Q',merged.read(binding['new_site'],8))
        need(actual==binding['new_target'],'actual rebound pointer '+hex(binding['new_site']))
    expected={}
    for outer,inner in COALESCED.items():
        old,new=original.segment(outer),merged.segment(outer)
        need((new['va'],new['size'],new['filesize'],new['maxprot'],new['initprot'])==
             (old['va']+slide,old['size'],old['size'],old['initprot'],old['initprot']),
             'coalesced geometry '+outer)
        blob=bytearray(old['size'])
        for module in selected_modules.values():
            for s in module.segments:
                if s['name']==inner and s['filesize']:
                    at=s['va']-old['va']
                    need(0<=at<=at+s['size']<=len(blob),'component coalesced range')
                    blob[at:at+s['filesize']]=original.read(s['va'],s['filesize'])
        if outer=='__PRELINK_TEXT':
            for name in interfaces:
                va,length=entries[name]['_PrelinkExecutableLoadAddr'],entries[name]['_PrelinkExecutableSize']
                at=va-old['va']
                need(0<=at<=at+length<=len(blob),'interface record range')
                blob[at:at+length]=original.read(va,length)
        expected[outer]=blob

    def write(va,blob):
        found=[(name,s) for name in expected for s in [merged.segment(name)]
               if s['va']<=va<=va+len(blob)<=s['va']+s['size']]
        need(len(found)==1,'expected copied span '+hex(va))
        name,s=found[0]
        at=va-s['va']
        expected[name][at:at+len(blob)]=blob

    for b in derived: write(b['new_site'],struct.pack('<Q',b['new_target']))
    for name,module in selected_modules.items():
        old_header=entries[name]['_PrelinkExecutableLoadAddr']
        for s in module.segments:
            if s['va']: write(old_header+slide+s['at']-module.header+24,struct.pack('<Q',s['va']+slide))
            for section in s['sections']:
                if section['va']:
                    write(old_header+slide+section['at']-module.header+32,
                          struct.pack('<Q',section['va']+slide))
    kmods={}
    for name in sorted(selected):
        kmod=entries[name]['_PrelinkKmodInfo']
        record=original.read(kmod,196)
        address,=struct.unpack_from('<Q',record,156)
        start,stop=struct.unpack_from('<QQ',record,180)
        need(not address or address==entries[name]['_PrelinkExecutableLoadAddr'],'kmod header identity')
        if address: write(kmod+slide+156,struct.pack('<Q',address+slide))
        actual_address,=struct.unpack('<Q',merged.read(kmod+slide+156,8))
        actual_start,actual_stop=struct.unpack('<QQ',merged.read(kmod+slide+180,16))
        need(actual_address==(address+slide if address else 0),'actual kmod address')
        need((actual_start,actual_stop)==(start+slide if start else 0,stop+slide if stop else 0),
             'actual kmod start/stop '+name)
        for entrypoint in (actual_start,actual_stop):
            need(not entrypoint or entrypoint%4==0 and any(
                s['initprot']&4 and s['va']+slide<=entrypoint<s['va']+slide+s['filesize']
                for s in selected_modules[name].segments),'kmod executable entrypoint '+name)
        kmods[name]=dict(kmod_info_va=kmod+slide,start_va=actual_start,stop_va=actual_stop)
    need(kmods==receipt['module_entrypoints'],'receipt kmod start/stop identities')
    headers=sorted(entries[n]['_PrelinkExecutableLoadAddr']+slide for n in selected)
    text=merged.segment('__PRELINK_TEXT')
    index_size=len(headers)*8
    index_va=(text['va']+text['size']-index_size)&~7
    occupied=max([s['va']+slide+s['size'] for m in selected_modules.values()
                  for s in m.segments if s['name']=='__TEXT']+
                 [entries[n]['_PrelinkExecutableLoadAddr']+slide+entries[n]['_PrelinkExecutableSize']
                  for n in interfaces])
    need(index_va>=occupied,'sparse index in unused readonly padding')
    index=struct.pack('<'+str(len(headers))+'Q',*headers)
    write(index_va,index)
    need(merged.read(index_va,index_size)==index,'actual sparse header index')
    sparse=dict(va=index_va,size=index_size,fileoff=merged.offset(index_va,index_size),
                headers=headers,abi=1)
    need(sparse==receipt['sparse_index'],'receipt sparse index')
    marker=native.symbols.get('_kalloc_sparse_prelink_index_version',set())
    need(len(marker)==1 and native.read(next(iter(marker)),4)==b'\x01\0\0\0','native sparse ABI version')
    for outer,blob in expected.items():
        segment=merged.segment(outer)
        need(merged.read(segment['va'],segment['filesize'])==blob,
             'entire coalesced content reconstruction '+outer)
    newmeta,newentries=metadata(merged)
    transformed={}
    initializers=receipt.get('early_security_initializers',{})
    libraries=receipt.get('library_only_components',{})
    for name in sorted(selected|interfaces):
        e=copy.deepcopy(entries[name])
        need('_NativePrelinkEarlySecurityInit' not in e,'input initializer override')
        if name in initializers:
            need(e.get('AppleSecurityExtension') is True,'security initializer component')
            va=initializers[name]
            need(any(s['initprot']==5 and not s['maxprot']&2 and
                     s['va']+slide<=va< s['va']+slide+s['filesize']
                     for s in selected_modules[name].segments),'early initializer executable range')
            e['_NativePrelinkEarlySecurityInit']=va
        if name in libraries:
            personalities=e.pop('IOKitPersonalities',{})
            security=e.pop('AppleSecurityExtension',None)
            need(libraries[name]==dict(removed_personalities=sorted(personalities),
                   removed_security_autoload=security is not None),'library-only policy exact')
        for key in ('_PrelinkExecutableLoadAddr','_PrelinkExecutableSourceAddr','_PrelinkKmodInfo'):
            if e.get(key): e[key]+=slide
        transformed[name]=e
    need(set(initializers)<=selected and set(libraries)<=selected and
         not set(initializers)&set(libraries),'explicit initializer/library policy')
    need(newentries==transformed,'all rewritten metadata and dependencies')
    packed=struct.pack('<'+str(len(derived)+1)+'I',len(derived),
                       *[b['new_site']-text['va'] for b in derived])
    need(newmeta['_PrelinkLinkKASLROffsets']==packed,'new relocation table complete')
    need(newmeta['_PrelinkKCID']==hashlib.sha256(native_data+original_data).digest()[:16],'prelink KCID')
    need(set(newmeta)=={'_PrelinkInfoDictionary','_PrelinkLinkKASLROffsets','_PrelinkKCID'},'metadata top-level keys')
    info=merged.segment('__PRELINK_INFO')
    xml=merged.read(info['va'],info['filesize'])
    end_xml=xml.find(b'\0')
    need(end_xml>=0 and not any(xml[end_xml:]),'metadata nul and zero padding')
    info_va=align(max(s['va']+s['size'] for s in
                    native.segments+[merged.segment(n) for n in COALESCED] if s['size']))
    need(info['va']==info_va and info['size']==align(end_xml+1),'Darwin22 metadata static top')
    need(receipt['darwin22_slid_top']==info_va+info['size'],'receipt static top')
    need(all(s['va']+slide+s['size']<=info_va for m in selected_modules.values()
             for s in m.segments if s['size'] and s['name']!='__LINKEDIT'),'module static slide extent')
    first_payload=min(sec['fileoff'] for s in native.segments for sec in s['sections']
                      if sec['size'] and sec['fileoff'])
    need(merged.command_end<=first_payload and
         merged_data[first_payload:len(native_data)]==native_data[first_payload:],
         'all native bytes after verified header padding are unchanged')
    old_header=bytearray(native_data[:32])
    struct.pack_into('<I',old_header,20,merged.command_end-32)
    need(merged_data[:32]==old_header and len(native.commands)==len(merged.commands),'native Mach-O header changes')
    for (oc,oa,ol),(nc,na,nl) in zip(native.commands,merged.commands):
        need(oc==nc,'native command order')
        raw=bytearray(native_data[oa:oa+ol])
        segment=next((s for s in native.segments if s['at']==oa),None)
        if segment and segment['name'] in (*COALESCED,'__PRELINK_INFO'):
            name=segment['name']
            new=merged.segment(name)
            need(segment['size']==0 and len(segment['sections'])==1,'empty native prelink placeholder')
            struct.pack_into('<4Q2I',raw,24,new['va'],new['size'],new['fileoff'],new['filesize'],
                             new['maxprot'],new['initprot'])
            extent=index_va-new['va'] if name=='__PRELINK_TEXT' else new['size']
            struct.pack_into('<QQI',raw,72+32,new['va'],extent,new['fileoff'])
            if name=='__PRELINK_TEXT':
                struct.pack_into('<I',raw,4,ol+80)
                struct.pack_into('<I',raw,64,2)
                raw.extend(struct.pack('<16s16sQQ8I',b'__macho_list',b'__PRELINK_TEXT',
                           index_va,index_size,merged.offset(index_va,index_size),3,0,0,0,0,0,0))
        need(bytes(raw)==merged_data[na:na+nl],'only authorized native load-command changes')
    instructions=instruction_inventory(original,merged,selected_modules,extents,slide)
    need(instructions['direct_pc_relative_count']==receipt['pc_relative_encodings_preserved'],
         'independent direct instruction count')
    need(instructions['original_TEXT_EXEC_sha256']==receipt['original_executable_sha256'],
         'independent original code digests')
    native_code={s['name']:digest(native.read(s['va'],s['filesize'])) for s in native.segments
                 if s['filesize'] and s['initprot']&4 and s['name']!='__TEXT'}
    return dict(schema='thor-fullseq-independent-prelink-v1',
       result='PASS_STATIC_PRELINK_CONTENT_AND_DIRECT_PCREL',
       native_sha256=digest(native_data),original_sha256=digest(original_data),
       merged_sha256=digest(merged_data),component_count=len(selected),
       component_names=sorted(selected),pointer_counts=counts,kmod_entrypoints=kmods,
       sparse_index=sparse,native_executable_sha256=native_code,
       all_pointer_sites_reconstructed=True,all_coalesced_bytes_reconstructed=True,
       native_payload_unchanged=True,metadata_and_static_top_verified=True,**instructions,
       foreign_kernel_abi_qualified=False,runtime_qualified=False,boot_eligible=False,
       preserved_emitter_unqualified_flags={k:receipt[k] for k in
         ('instruction_relocation_complete','abi_qualified','loadable','register_dataflow_qualified','runtime_qualified')},
       phone_actions=0)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('native','original','merged','emission','output'):
        parser.add_argument('--'+name,required=True,type=Path)
    args=parser.parse_args()
    report=audit(args.native.read_bytes(),args.original.read_bytes(),
                 args.merged.read_bytes(),json.loads(args.emission.read_text()))
    report['auditor_sha256']=digest(Path(__file__).read_bytes())
    report['emission_sha256']=digest(args.emission.read_bytes())
    report['inputs']={name:str(getattr(args,name).resolve())
                     for name in ('native','original','merged','emission')}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    print(json.dumps({k:report[k] for k in ('result','component_count','pointer_counts',
                       'register_dataflow_qualified','runtime_qualified','phone_actions')},indent=2))

if __name__=='__main__':
    main()
