#!/usr/bin/env python3
"""Positive final-Mach-O ENTRY06 instruction/CFG and bounded host semantics audit.
The host model does not emulate a CPU, establish live permissions, or claim a boot.
"""
from pathlib import Path
import collections, hashlib, json, re, struct, subprocess, sys

TREE=Path(__file__).resolve().parents[1]
ROOT=TREE.parent.parent
RUN=ROOT/'analysis/thor-entry06-kernel-build-20261024'
TARGET=TREE/'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837'
KERNEL=TARGET/'mach.development.bcm2837.unstripped.noctf'
MASK64=(1<<64)-1
SHA=lambda b:hashlib.sha256(b).hexdigest()
checks=[]
check_index={}
def need(ok,name):
    if name not in check_index:
        check_index[name]={'name':name,'passed':bool(ok),'observations':1};checks.append(check_index[name])
    else:
        check_index[name]['observations']+=1;check_index[name]['passed'] &= bool(ok)
    if not ok:raise RuntimeError(name)
def signed(n,bits):
    return n-(1<<bits) if n&(1<<(bits-1)) else n

def parse(data,wanted_kind):
    need(len(data)>=32,'complete Mach-O header')
    magic,cpu,subtype,kind,ncmd,size=struct.unpack_from('<6I',data)
    need((magic,cpu,subtype,kind)==(0xfeedfacf,0x100000c,0,wanted_kind),'actual ARM64 Mach-O kind')
    need(32+size<=len(data),'load commands bounded')
    off=32;segments=[];sections=[];symtab=None;entry=None;thread_count=0
    for _ in range(ncmd):
        need(off+8<=32+size,'command header bounded')
        cmd,clen=struct.unpack_from('<II',data,off)
        need(clen>=8 and off+clen<=32+size,'command length bounded')
        if cmd==0x19:
            need(clen>=72,'segment header bounded')
            name,va,vms,fo,fs,maxp,initp,ns,flags=struct.unpack_from('<16s4Q4I',data,off+8)
            need(fo<=len(data) and fs<=len(data)-fo and fs<=vms,'segment file/VM bounds')
            need(ns<=(clen-72)//80,'section table bounded')
            seg={'name':name.rstrip(b'\0').decode(),'va':va,'vmsize':vms,'fileoff':fo,'filesize':fs,'initial_protection':initp}
            segments.append(seg)
            for i in range(ns):
                p=off+72+80*i
                sn,sg,addr,sz,pos,align,ro,nr,fl=struct.unpack_from('<16s16sQQ5I',data,p)
                sections.append({'name':sn.rstrip(b'\0').decode(),'segment':sg.rstrip(b'\0').decode(),'va':addr,'size':sz,'fileoff':pos,'reloff':ro,'nreloc':nr,'flags':fl,'protection':initp})
        elif cmd==2:
            need(symtab is None and clen==24,'one actual symbol table')
            symtab=struct.unpack_from('<4I',data,off+8)
        elif cmd==5:
            need(clen==288,'ARM64 LC_UNIXTHREAD size')
            flavor,count=struct.unpack_from('<II',data,off+8)
            need((flavor,count)==(6,68),'ARM_THREAD_STATE64 flavor/count')
            entry=struct.unpack_from('<Q',data,off+272)[0];thread_count+=1
        off+=clen
    need(off==32+size and symtab is not None,'complete commands and symbol table')
    if wanted_kind==2:need(thread_count==1,'one final LC_UNIXTHREAD')
    so,ns,sto,ss=symtab
    need(so<=len(data) and ns<=(len(data)-so)//16 and sto<=len(data) and ss<=len(data)-sto,'symbol and string bounds')
    symbols=collections.defaultdict(list)
    for i in range(ns):
        ni,nt,nsec,nd,val=struct.unpack_from('<IBBHQ',data,so+16*i)
        if nt&0xe0:continue
        need(ni<ss,'symbol name bounded')
        end=data.find(b'\0',sto+ni,sto+ss);need(end>=0,'symbol name terminated')
        symbols[data[sto+ni:end].decode()].append({'va':val,'type':nt,'section':nsec,'desc':nd})
    return {'segments':segments,'sections':sections,'symbols':dict(symbols),'entry':entry,'data':data}

def symbol(info,name):
    defs=info['symbols'].get(name,[])
    need(len(defs)==1,'unique '+name)
    x=defs[0]
    need(x['type']&0x0f==0x0f and not x['desc']&0xc0,'strong external N_SECT '+name)
    need(1<=x['section']<=len(info['sections']),'valid section '+name)
    s=info['sections'][x['section']-1]
    need(s['name']=='__text' and s['protection']&4 and s['va']<=x['va']<s['va']+s['size'],'real executable text '+name)
    return x['va']

def read_va(info,va,n):
    matches=[]
    for s in info['segments']:
        if s['va']<=va and va-s['va']<=s['filesize'] and n<=s['filesize']-(va-s['va']):
            p=s['fileoff']+va-s['va'];matches.append(info['data'][p:p+n])
    need(len(matches)==1 and len(matches[0])==n,'unique file-backed VA range')
    return matches[0]

data=KERNEL.read_bytes();info=parse(data,2)
names=['__start','_thor_entry_stop_begin','_thor_entry_stop_reset','_thor_entry_stop_park','_thor_entry_stop_end','_LowExceptionVectorBase','_start_first_cpu']
syms={n:symbol(info,n) for n in names}
entry=syms['__start'];begin=syms['_thor_entry_stop_begin'];end=syms['_thor_entry_stop_end'];reset=syms['_thor_entry_stop_reset'];park=syms['_thor_entry_stop_park'];vectors=syms['_LowExceptionVectorBase']
need(info['entry']==entry,'LC_UNIXTHREAD is real __start')
need(begin==entry+4 and begin<reset<park<end and end-park==4,'entry/body/terminal layout')
need(end-begin==217*4,'all 217 reviewed body instructions present')
body=read_va(info,begin,end-begin);word_entry=struct.unpack('<I',read_va(info,entry,4))[0]
need(word_entry&0xfc000000==0x14000000 and entry+signed(word_entry&0x3ffffff,26)*4==begin,'real entry direct branch enters body')
need(vectors%4096==0,'original XNU vector table aligned 4 KiB')
vector_bytes=read_va(info,vectors,0x800)
need(all(struct.unpack_from('<I',vector_bytes,i*128)[0]==0x14000000 for i in range(16)),'all 16 existing vector slots enter pure B-self')
need(not(begin<=syms['_start_first_cpu']<end),'normal primary entry is outside stop body')
objpath=TARGET/'osfmk/DEVELOPMENT/start.o';obj=parse(objpath.read_bytes(),1)
ob=symbol(obj,'_thor_entry_stop_begin');oe=symbol(obj,'_thor_entry_stop_end')
need(oe-ob==len(body) and read_va(obj,ob,oe-ob)==body,'final body equals actual freshly assembled start.o')
record_path=objpath.with_suffix('.o.json')
record=json.loads(record_path.read_text().strip().rstrip(','))
need(Path(record['file']).resolve()==(TREE/'.cache/xnu-work/osfmk/arm64/start.s').resolve(),'actual start compile input is candidate source')
need(Path(record['directory']).resolve().is_relative_to(TREE),'actual start compile cwd is candidate')
need(any(x in record['arguments'] for x in ['XNU_NATIVE_ARCH_TIMER=1','-DXNU_NATIVE_ARCH_TIMER=1']),'native timer flag retained')
need(any(x in record['arguments'] for x in ['XNU_THOR_ENTRY_STOP=1','-DXNU_THOR_ENTRY_STOP=1']),'entry-stop flag actually compiled')
need(not any(x in record['arguments'] for x in ['-UXNU_THOR_ENTRY_STOP','XNU_THOR_ENTRY_STOP=0','-DXNU_THOR_ENTRY_STOP=0']),'entry-stop flag not overridden')
normal=json.loads((RUN/'normal-flag-off-verification.json').read_text())
need(normal['status']=='PASS' and normal['text_byte_identical_to_baseline'] and normal['text_relocations_identical_to_baseline'],'fresh flag-off preserves normal entry text and relocation')
need(normal['candidate_object']['sha256']==SHA(Path(normal['candidate_object']['path']).read_bytes()),'flag-off proof still binds actual object')

# All decoder classes are positive allow-lists of the actual AArch64 instructions.
# This deliberately has no generic unknown-instruction/fallthrough fallback.
MRS={0xd5384249:('CurrentEL',9),0xd5381009:('SCTLR_EL1',9),0xd53be016:('CNTFRQ_EL0',22),0xd53be057:('CNTVCT_EL0',23),0xd53be049:('CNTVCT_EL0',9)}
def decode(pc,w):
    sf=w>>31;bits=64 if sf else 32;rd=w&31;rn=(w>>5)&31;rm=(w>>16)&31
    d={'va':pc,'word':hex(w),'bits':bits}
    if w&0xfc000000==0x14000000:return {**d,'op':'b','target':pc+signed(w&0x3ffffff,26)*4}
    if w&0xff000010==0x54000000:
        cond=w&15;need(cond in (0,1,2,3,8),'reviewed conditional branch condition')
        return {**d,'op':'bcond','cond':cond,'target':pc+signed((w>>5)&0x7ffff,19)*4}
    if w&0x7f000000==0x35000000:
        need(rd==9,'CBNZ consumes checked field register')
        return {**d,'op':'cbnz','rn':rd,'target':pc+signed((w>>5)&0x7ffff,19)*4}
    if w&0x7f000000==0x37000000:
        bit=((w>>26)&32)|((w>>19)&31);need(rd==9 and bit==25,'TBNZ checks SCTLR EE only')
        return {**d,'op':'tbnz','rn':rd,'bit':bit,'target':pc+signed((w>>5)&0x3fff,14)*4}
    if w&0x9f000000==0x10000000:
        imm=((w>>5)&0x7ffff)<<2|((w>>29)&3);target=pc+signed(imm,21)
        need(rd==9 and target in (entry,vectors),'ADR names actual entry or vectors')
        return {**d,'op':'adr','rd':rd,'target':target}
    if w&0x7f800000 in (0x52800000,0x72800000):
        need(rd!=31 and ((w>>21)&3)<=(3 if sf else 1),'bounded MOVZ/MOVK destination/shift')
        return {**d,'op':'movk' if w&0x7f800000==0x72800000 else 'movz','rd':rd,'imm':(w>>5)&0xffff,'shift':((w>>21)&3)*16}
    if w&0x1f000000==0x11000000:
        flags=(w>>29)&1;need(rn!=31 and (rd!=31 or flags),'immediate arithmetic never uses SP')
        return {**d,'op':'arith_imm','rd':rd,'rn':rn,'sub':(w>>30)&1,'flags':flags,'imm':((w>>10)&0xfff)<<(12 if (w>>22)&1 else 0)}
    if w&0x1f200000==0x0b000000:
        shift=(w>>22)&3;amount=(w>>10)&63
        need(shift==0 and amount<bits,'reviewed shifted ADD/SUB uses bounded LSL')
        return {**d,'op':'arith_reg','rd':rd,'rn':rn,'rm':rm,'sub':(w>>30)&1,'flags':(w>>29)&1,'shift':amount}
    if w&0x1f800000==0x12000000:
        opc=(w>>29)&3;N=(w>>22)&1;imms=(w>>10)&63;immr=(w>>16)&63
        need(opc in (0,3) and (rd!=31 or opc==3),'AND/TST logical immediate only')
        length=((N<<6)|((~imms)&63)).bit_length()-1
        need(length>=1 and not (bits==32 and N),'valid logical immediate element')
        levels=(1<<length)-1;S=imms&levels;RR=immr&levels;esize=1<<length
        need(S!=levels and esize<=bits,'nonreserved logical immediate')
        elem=(1<<(S+1))-1;mask=(1<<esize)-1;elem=((elem>>RR)|(elem<<(esize-RR)))&mask
        immediate=sum(elem<<k for k in range(0,bits,esize))
        return {**d,'op':'and_imm','rd':rd,'rn':rn,'flags':opc==3,'imm':immediate}
    if w&0x1f200000==0x0a000000:
        opc=(w>>29)&3
        need((w>>22)&3==0 and (w>>10)&63==0 and not w&(1<<21),'unshifted noninverted logical register')
        need((opc==1 and rn==31 and rd!=31) or (opc==3 and rd==31),'MOV-register or TST-register only')
        return {**d,'op':'logical_reg','rd':rd,'rn':rn,'rm':rm,'opc':opc}
    if w==0xd37ae77b:return {**d,'op':'lsl','rd':27,'rn':27,'shift':6}
    if w==0x9b0a7ed8:return {**d,'op':'mul','rd':24,'rn':22,'rm':10}
    if w==0x9ad6096a:return {**d,'op':'udiv','rd':10,'rn':11,'rm':22}
    bases={0xf9400000:8,0xb9400000:4,0x39400000:1}
    if w&0xffc00000 in bases:
        size=bases[w&0xffc00000];offset=((w>>10)&0xfff)*size
        need(rn==0 and rd in (9,19,20),'loads use only x0 checked boot_args')
        return {**d,'op':'load','rd':rd,'rn':rn,'size':size,'offset':offset}
    if w in (0xb80045b0,0xb80045bf):return {**d,'op':'store','rn':13,'rt':rd,'size':4,'post':4}
    if w in MRS:
        reg,rd=MRS[w];return {**d,'op':'mrs','sysreg':reg,'rd':rd}
    if w==0xd518c009:return {**d,'op':'vbar','rn':9}
    if w==0xd5034fdf:return {**d,'op':'daifset'}
    if w==0xd5033fdf:return {**d,'op':'isb'}
    if w==0xd5033f9f:return {**d,'op':'dsb'}
    if w==0xd4000003:return {**d,'op':'smc'}
    raise RuntimeError('Unapproved instruction at %x: %08x'%(pc,w))
insns={entry:decode(entry,word_entry)}
for off in range(0,len(body),4):insns[begin+off]=decode(begin+off,struct.unpack_from('<I',body,off)[0])
need(len(insns)==218,'entry plus every body word decoded exactly once')
edges={}
for pc,d in insns.items():
    if d['op']=='b':edges[pc]=[d['target']]
    elif d['op'] in ('bcond','cbnz','tbnz'):edges[pc]=[pc+4,d['target']]
    else:edges[pc]=[pc+4]
    need(all(x in insns for x in edges[pc]),'all reachable branch/fallthrough destinations are audited code')
reachable=set();todo=[entry]
while todo:
    pc=todo.pop()
    if pc in reachable:continue
    reachable.add(pc);todo.extend(edges[pc])
need(reachable==set(insns),'complete CFG covers every body instruction')
need(syms['_start_first_cpu'] not in reachable,'normal primary entry unreachable')
need(insns[park]['op']=='b' and edges[park]==[park],'terminal park is permanent direct self-loop')
need([d['va'] for d in insns.values() if d['op']=='adr']==[begin+17*4,begin+25*4],'only two reviewed PC-relative address constructions')
reset_words=struct.unpack('<10I',read_va(info,reset,40))
need(reset_words==(0xd5034fdf,0xd5033f9f,0x52800120,0x72b08000,0xaa1f03e1,0xaa1f03e2,0xaa1f03e3,0xd4000003,0x14000001,0x14000000),'exact reset register setup, one SMC, then pure park')
need(sum(d['op']=='smc' for d in insns.values())==1,'only one SMC instruction')
need([d['word'] for d in insns.values() if d['op']=='store']==['0xb80045b0','0xb80045b0','0xb80045bf'],'only three bounded framebuffer store sites')
expected_loads=[(0,4,9),(8,8,9),(0x10,8,9),(0x18,8,9),(0x478,8,9),(0x20,8,9),(0x58,4,9),(0x60,8,9),(0x68,4,9),(0x6c,1,9),(0x470,8,9),(0x28,8,19),(0x30,8,9),(0x38,8,20),(0x40,8,9),(0x48,8,9),(0x50,8,9)]
need([(d['offset'],d['size'],d['rd']) for d in insns.values() if d['op']=='load']==expected_loads,'exact complete boot_args field load footprint')
segments=sorted(info['segments'],key=lambda x:x['va'])
need(all(a['va']+a['vmsize']<=b['va'] for a,b in zip(segments,segments[1:])),'final Mach-O VM segments do not overlap')
minimum=segments[0]['va'];last=max(s['va']+s['vmsize'] for s in segments);span=last-minimum
need(minimum==0xfffffff007004000,'actual minimum VM address equals reviewed contract')
need(span>0 and span<64*1024*1024-2*1024*1024-0x4000,'new complete loaded span leaves Args and guard')
args_offset=(span+0x3fff)&~0x3fff
used=(args_offset+0x480+0x3fff)&~0x3fff
loaded=bytearray(span)
for s in segments:
    off=s['va']-minimum;loaded[off:off+s['filesize']]=data[s['fileoff']:s['fileoff']+s['filesize']]

# A deliberately small raw-instruction semantics model exercises the real final
# words at relocated PAs. All unknown instructions already fail the decoder.
# No firmware, kernel or provider code is executed on the host.
def model(name,base=0xf8004000,mutate=None,systems=None,timer='normal'):
    regs=[0]*31;flags={'N':False,'Z':False,'C':False,'V':False}
    args=bytearray(0x480);argpa=base+args_offset
    struct.pack_into('<HH',args,0,2,2)
    for off,v in [(8,minimum),(0x10,base),(0x18,0x4000000),(0x20,base+used),(0x28,0xb8000000),(0x30,1),(0x38,5760),(0x40,1440),(0x48,3200),(0x50,32),(0x478,0x4000000)]:struct.pack_into('<Q',args,off,v)
    regs[:4]=[argpa,base+entry-minimum,base,0x4000000]
    sysvals={'CurrentEL':4,'SCTLR_EL1':0,'CNTFRQ_EL0':19200000}
    if systems:sysvals.update(systems)
    if mutate:mutate(regs,args)
    pc=entry;steps=0;stores=[];loads=[];vbar=None;smc=[];timer_reads=0;counter=0x10000000;painted=False
    def readreg(n,bits=64):return (regs[n] if n!=31 else 0)&((1<<bits)-1)
    def write(n,v,bits=64):
        if n!=31:regs[n]=v&((1<<bits)-1)
    def setflags(a,b,result,bits,sub):
        mask=(1<<bits)-1;sign=1<<(bits-1);rr=result&mask
        flags.update(N=bool(rr&sign),Z=rr==0,C=(a>=b if sub else result>mask),V=bool(((a^b)&(a^rr) if sub else (~(a^b)&(a^rr)))&sign))
    while True:
        steps+=1
        if steps>1000000:raise RuntimeError(name+': host-model instruction budget')
        if pc==park:return {'name':name,'terminal':'reset-return-park' if smc else 'early-park','steps':steps,'stores':len(stores),'loads':len(loads),'vbar':vbar,'smc':smc,'store_min':min((x[0] for x in stores),default=None),'store_end':max((x[0]+4 for x in stores),default=None),'colors':dict(collections.Counter(hex(x[1]) for x in stores))}
        d=insns[pc];op=d['op'];nextpc=pc+4;bits=d['bits']
        if op=='b':nextpc=d['target']
        elif op=='bcond':
            cond={0:flags['Z'],1:not flags['Z'],2:flags['C'],3:not flags['C'],8:flags['C'] and not flags['Z']}[d['cond']]
            if cond:nextpc=d['target']
        elif op=='cbnz':
            if readreg(d['rn'],bits):nextpc=d['target']
        elif op=='tbnz':
            if readreg(d['rn'])&(1<<d['bit']):nextpc=d['target']
        elif op=='adr':write(d['rd'],base+d['target']-minimum)
        elif op=='movz':write(d['rd'],d['imm']<<d['shift'],bits)
        elif op=='movk':write(d['rd'],(readreg(d['rd'],bits)&~(0xffff<<d['shift']))|(d['imm']<<d['shift']),bits)
        elif op in ('arith_imm','arith_reg'):
            aa=readreg(d['rn'],bits);bb=d['imm'] if op=='arith_imm' else (readreg(d['rm'],bits)<<d['shift'])&((1<<bits)-1)
            rr=aa-bb if d['sub'] else aa+bb
            if d['flags']:setflags(aa,bb,rr,bits,d['sub'])
            write(d['rd'],rr,bits)
        elif op=='and_imm':
            rr=readreg(d['rn'],bits)&d['imm'];write(d['rd'],rr,bits)
            if d['flags']:flags.update(N=bool(rr&(1<<(bits-1))),Z=rr==0,C=False,V=False)
        elif op=='logical_reg':
            aa=readreg(d['rn'],bits);bb=readreg(d['rm'],bits);rr=(aa|bb) if d['opc']==1 else (aa&bb);write(d['rd'],rr,bits)
            if d['opc']==3:flags.update(N=bool(rr&(1<<(bits-1))),Z=rr==0,C=False,V=False)
        elif op=='lsl':write(d['rd'],readreg(d['rn'])<<d['shift'])
        elif op=='mul':write(d['rd'],readreg(d['rn'])*readreg(d['rm']))
        elif op=='udiv':
            divisor=readreg(d['rm']);write(d['rd'],readreg(d['rn'])//divisor if divisor else 0)
        elif op=='load':
            pa=readreg(d['rn'])+d['offset'];off=pa-argpa
            if not (0<=off and off+d['size']<=len(args)):raise RuntimeError(name+': load escaped actual boot_args buffer')
            if not(base<=pa and pa+d['size']<=base+0x4000000):raise RuntimeError(name+': load escaped modeled allocation')
            write(d['rd'],int.from_bytes(args[off:off+d['size']],'little'));loads.append((pa,d['size']))
        elif op=='store':
            pa=readreg(d['rn']);val=readreg(d['rt'],32);off=pa-0xb8000000;row,colbyte=divmod(off,5760)
            if not(pa%4==0 and 0<=off<18432000):raise RuntimeError(name+': store outside actual framebuffer')
            col=colbyte//4
            if 64<=row<=79 and col<1440:expected_color=0xff00ff;ok=val==expected_color
            elif 80<=row<=95 and col<1440:expected_color=0xffff;ok=val==expected_color
            elif 112<=row<=127 and col<1280:ok=val in (0,0xffffff)
            else:ok=False
            if not ok:raise RuntimeError(name+': wrong marker/gauge address or color')
            stores.append((pa,val));painted=True;write(d['rn'],pa+d['post'])
        elif op=='mrs':
            if d['sysreg']!='CNTVCT_EL0':value=sysvals[d['sysreg']]
            else:
                timer_reads+=1
                if timer=='stalled':pass
                elif timer=='stall_after_marker' and painted:pass
                elif timer=='backward_after_marker' and painted:counter-=1
                else:counter+=19200000*(19 if timer=='jump_19s' else 1)
                value=counter&MASK64
            write(d['rd'],value)
        elif op=='vbar':
            vbar=readreg(d['rn'])
            if vbar!=base+vectors-minimum or vbar%2048:raise RuntimeError(name+': wrong VBAR target')
        elif op=='smc':
            actual=[readreg(i) for i in range(4)]
            if actual!=[0x84000009,0,0,0]:raise RuntimeError(name+': wrong PSCI request')
            smc.append(actual)
            if len(smc)>1:raise RuntimeError(name+': repeated SMC')
        elif op in ('daifset','isb','dsb'):pass
        else:raise RuntimeError(name+': unknown model instruction')
        pc=nextpc

results=[]
for name,base,timer in [('normal_min_base',0xf8004000,'normal'),('normal_max_base',0xfba04000,'normal'),('counter_jump_19s',0xf8004000,'jump_19s')]:
    result=model(name,base=base,timer=timer);need(result['terminal']=='reset-return-park' and result['stores']==86016 and len(result['smc'])==1,'full marker/gauge/reset model '+name);results.append(result)
for name,timer,stores_expected in [('stalled_counter','stalled',0),('counter_stalls_after_marker','stall_after_marker',66560),('counter_reverses_after_marker','backward_after_marker',66560)]:
    result=model(name,timer=timer);need(result['terminal']=='reset-return-park' and result['stores']==stores_expected,'bounded timer failure '+name);results.append(result)
for name,system in [('wrong_EL',{'CurrentEL':8}),('MMU_enabled',{'SCTLR_EL1':1}),('Dcache_enabled',{'SCTLR_EL1':4}),('Icache_enabled',{'SCTLR_EL1':0x1000}),('big_endian',{'SCTLR_EL1':1<<25}),('wrong_frequency',{'CNTFRQ_EL0':1})]:
    result=model(name,systems=system);need(result['stores']==0 and (result['terminal']=='early-park' if name=='wrong_EL' else result['terminal']=='reset-return-park'),'reject system contract '+name);results.append(result)
register_mutations=[
('wrong_grant_size',lambda rr,aa:rr.__setitem__(3,0x4001000)),
('base_below_heap',lambda rr,aa:rr.__setitem__(2,0xf7e04000)),
('base_above_limit',lambda rr,aa:rr.__setitem__(2,0xfbc04000)),
('wrong_base_congruence',lambda rr,aa:rr.__setitem__(2,0xf8000000)),
('entry_mismatch',lambda rr,aa:rr.__setitem__(1,rr[1]+4)),
('unaligned_args',lambda rr,aa:rr.__setitem__(0,rr[0]+1)),
('args_below_grant',lambda rr,aa:rr.__setitem__(0,rr[2]-0x4000)),
('args_after_grant',lambda rr,aa:rr.__setitem__(0,rr[2]+rr[3])),
]
for name,mutation in register_mutations:
    result=model(name,mutate=mutation);need(result['stores']==0 and result['loads']==0,'reject register/bounds before load '+name);results.append(result)
field_mutations=[('revision',0,2,1),('version',2,2,1),('virtBase',8,8,0),('physBase',0x10,8,0),('memSize',0x18,8,0),('memSizeActual',0x478,8,0),('top_unaligned',0x20,8,0xf9400001),('top_below_args',0x20,8,0xf8004000),('top_in_tail_guard',0x20,8,0xfbf04000),('machineType',0x58,4,1),('deviceTreeP',0x60,8,1),('deviceTreeLength',0x68,4,1),('commandLine',0x6c,1,1),('bootFlags',0x470,8,1),('fbBase',0x28,8,0xb8000004),('display',0x30,8,0),('rowBytes',0x38,8,5764),('width',0x40,8,1441),('height',0x48,8,3201),('depth',0x50,8,24)]
for name,off,n,v in field_mutations:
    result=model(name,mutate=lambda rr,aa,off=off,n=n,v=v:aa.__setitem__(slice(off,off+n),v.to_bytes(n,'little')))
    need(result['stores']==0 and result['terminal']=='reset-return-park','reject boot_args field '+name);results.append(result)
need(len(results)==40,'all 40 bounded final-machine-code scenarios evaluated')

command=['xcrun','llvm-objdump','-d','--start-address='+hex(entry),'--stop-address='+hex(end),str(KERNEL)]
p=subprocess.run(command,capture_output=True,text=True,timeout=60);need(p.returncode==0,'independent llvm final disassembly available')
(RUN/'entry06-final-disassembly.txt').write_text(p.stdout)
listing={}
for line in p.stdout.splitlines():
    m=re.match(r'^\s*([0-9a-fA-F]+):\s+([0-9a-fA-F]{8})\s+(.+)$',line)
    if m:listing[int(m[1],16)]=(int(m[2],16),m[3].strip())
need(set(listing)==set(insns),'LLVM disassembly covers every same final instruction')
need(all(listing[pc][0]==int(d['word'],16) for pc,d in insns.items()),'raw words match independent disassembly')
need(not any(re.search(r'\b(sp|bl|blr|br|ret|eret|wfi|wfe)\b',s.lower()) for _,s in listing.values()),'no stack operand, runtime call, indirect branch, return or WFI/WFE')
for pc,d in insns.items():d['llvm']=listing[pc][1];d['successors']=edges[pc]
report={'status':'PASS','scope':'Positive static final-Mach-O machine-code CFG and bounded instruction semantics; no hardware execution','kernel':{'path':str(KERNEL),'bytes':len(data),'sha256':SHA(data)},'source_entry_body':{'path':str(TREE/'.cache/xnu-work/osfmk/arm64/thor_entry_stop.inc.S'),'sha256':SHA((TREE/'.cache/xnu-work/osfmk/arm64/thor_entry_stop.inc.S').read_bytes())},'actual_start_object':{'path':str(objpath),'sha256':SHA(objpath.read_bytes()),'compile_record_sha256':SHA(record_path.read_bytes())},'symbols':{k:hex(v) for k,v in syms.items()},'entry_is_LC_UNIXTHREAD':True,'body_sha256':SHA(body),'body_bytes':len(body),'all_instructions_positive_decoded':len(insns),'all_CFG_nodes_reachable':len(reachable),'normal_start_unreachable':True,'vector_bytes_sha256':SHA(vector_bytes),'all_16_vector_slots_self_loop':True,'instructions':list(insns.values()),'bounded_host_scenarios':results,'layout':{'minimum_vm_address':hex(minimum),'span_bytes':span,'args_offset':args_offset,'boot_args_bytes':0x480,'used_with_args_alignment':used,'guard_bytes':0x200000,'window_bytes':0x4000000,'entry_offset':entry-minimum,'vector_offset':vectors-minimum,'loaded_span_sha256':SHA(loaded),'segments':segments},'checks_passed':len(checks),'checks':checks,'limitations':['No host execution of the ARM64 code; the bounded model interprets an explicitly permitted subset only','No current EL/MMU/PTE/GOP observation or allocation ownership proof','No ExitBootServices, actual entry, PSCI reset, normal primary startup, ADT or runtime provider selftest demonstrated']}
(RUN/'entry06-cfg-verification.json').write_text(json.dumps(report,indent=2)+'\n')
(RUN/'entry06-layout.json').write_text(json.dumps({'kernel':report['kernel'],**report['layout'],'symbols':report['symbols']},indent=2)+'\n')
print(json.dumps({'status':'PASS','kernel':report['kernel'],'instructions':len(insns),'model_scenarios':len(results),'layout':report['layout'],'report_sha256':SHA((RUN/'entry06-cfg-verification.json').read_bytes())},indent=2))
