#!/usr/bin/env python3
"""Derive *non-authoritative* DDR investigation windows from pinned vendor DTS.

DTS availability does not imply bootloader, hypervisor, firmware or DMA
ownership. This module CANNOT release/reserve/mmap/flash physical memory.
"""
import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import re
import subprocess
from pathlib import Path

MIB=1024*1024

def require(ok,message):
    if not ok:raise ValueError(message)

def import_memory(path):
    spec=importlib.util.spec_from_file_location('thor_mem_proof',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

def extract_braced(text,anchor):
    start=text.index(anchor)
    open_brace=text.index('{',start)
    depth=1
    pos=open_brace+1
    while depth:
        require(pos<len(text),'unterminated DTS memory block')
        if text[pos]=='{':depth+=1
        if text[pos]=='}':depth-=1
        pos+=1
    return text[open_brace:pos]

def parse_dts_reserved(text):
    block=extract_braced(text,'reserved_memory: reserved-memory {')
    rows=[]
    dynamic=[]
    level=1
    item=None
    for line in block.splitlines()[1:]:
        t=line.strip()
        if t.endswith('{'):
            if level==1:
                item={'name':t[:-1].strip(),'reg':None,'size':None,'no_map':False}
            level+=t.count('{')
        elif t.startswith('};') or t=='}':
            level-=1
            if level==1 and item is not None:
                if item['reg'] is not None:
                    values=[int(x,0) for x in item['reg'].split()]
                    require(len(values)>0 and len(values)%4==0,'invalid 64bit reserved region cells')
                    for i in range(0,len(values),4):
                        addr=(values[i]<<32)|values[i+1]
                        length=(values[i+2]<<32)|values[i+3]
                        require(length>0,'zero-length DTS reservation '+item['name'])
                        rows.append({'name':item['name'],'start':addr,'end':addr+length,
                                     'bytes':length,'no_map':item['no_map']})
                else:
                    dynamic.append({'name':item['name'],'size_cells':item['size'],
                                    'no_map':item['no_map']})
                item=None
        if level==2 and item is not None:
            if t=='no-map;':item['no_map']=True
            prop=re.fullmatch(r'(reg|size)\s*=\s*<([^>]+)>;',t)
            if prop:item[prop[1]]=prop[2]
    require(level==0,'reserved DTS block had unexpected depth')
    rows.sort(key=lambda x:x['start'])
    return rows,dynamic

def subtract(banks,excluded):
    intervals=[(a['start'],a['end']) for a in banks]
    for block in excluded:
        remaining=[]
        for a,b in intervals:
            if block['end']<=a or b<=block['start']:
                remaining.append((a,b));continue
            if a<block['start']:remaining.append((a,min(b,block['start'])))
            if block['end']<b:remaining.append((max(a,block['end']),b))
        intervals=remaining
    return sorted([{'start':a,'end':b,'bytes':b-a} for a,b in intervals if a<b],
                  key=lambda r:r['bytes'],reverse=True)

def aligned_prefix(block,size,alignment=2*MIB):
    start=(block['start']+alignment-1)//alignment*alignment
    if start+size>block['end']:return None
    return {'start':start,'end':start+size,'bytes':size}

def generate(dts_text,memory_text,memmodule,vendor_revision):
    bank=memmodule.parse_vendor_banks(dts_text)
    reserved,dynamic=parse_dts_reserved(dts_text)
    mm=memmodule.parse_region_map(memory_text)
    require(len(bank)==6,'vendor memory bank topology changed')
    require(len(reserved)==38 and len(dynamic)==16,'vendor reserved area count changed')
    require(len(mm)==13,'F1c memory policy topology changed')
    conventional=memmodule.size_of_contiguous_conventional(mm)
    require(len(conventional)==1 and conventional[0]['bytes']==124*MIB,'F1c default allocatable zone changed')
    gaps=subtract(bank,reserved)
    candidates=[]
    for gap in gaps:
        if gap['bytes']<256*MIB:continue
        candidate=aligned_prefix(gap,256*MIB)
        if candidate is None:continue
        inside_firmware=[]
        for row in mm:
            if row['start']<candidate['end'] and candidate['start']<row['end']:
                inside_firmware.append(row['label'])
        candidates.append({
            'hypothetical_start':hex(candidate['start']),
            'hypothetical_end':hex(candidate['end']),
            'bytes':candidate['bytes'],
            'gap_start':hex(gap['start']),
            'gap_end':hex(gap['end']),
            'whole_public_DDR_gap_MiB':gap['bytes']/MIB,
            'above_4GiB':candidate['start']>=1<<32,
            'overlaps_current_UEFI_policy':inside_firmware,
            'known_device_ownership':False,
            'may_allocate_or_write':False})
    require(any(x['gap_start']=='0xbab00000' and x['whole_public_DDR_gap_MiB']==597.0 for x in candidates),
       'expected low 597MiB DDR investigation interval changed')
    low=next(x for x in candidates if x['gap_start']=='0xbab00000')
    require(low['overlaps_current_UEFI_policy']==['Other low RAM reserved'],
            'unsafe low-DDR candidate unexpectedly unreserved by F1c')
    return {
     'schema':'thor-f1c-public-DDR-hypotheses-not-allocations-v1',
     'created_at':dt.datetime.now(dt.timezone.utc).isoformat(),
     'vendor_source_commit':vendor_revision,
     'result':'RESEARCH_ONLY_ZERO_HARDWARE_MEMORY_OWNERSHIP_PROVEN',
     'physical_vendor_DDR_banks':bank,
     'static_vendor_reserved_regions':len(reserved),
     'dynamic_unknown_placement_reservations':dynamic,
     'current_firmware_allocatable_MiB':124,
     'required_minimum_DarwinBoot_MiB':256,
     'nominal_static_DDR_gaps_over_256M':candidates,
     'first_low4G_investigation_interval':[low['gap_start'],low['gap_end']],
     'first_low4G_investigation_size_MiB':597,
     'none_candidates_UEFI_allocatable':True,
     'live_FDT_and_UEFI_memory_evidence_required':True,
     'runtime_device_tree_validated':False,
     'qualifying_MMIO_or_DMA_owner_proven':False,
     'new_page_tables_created':False,
     'current_UEFI_reserved_region_changed':False,
     'phone_actions':0,
     'next_required_checks':[
       'Read-only dump of the CURRENT device FDT and reserved-memory placement from Android bootloader/UEFI',
       'Resolve all 16 runtime-placed reserved regions and validate no collision with candidate',
       'Verify ownership of a contiguous >=256MiB EFI ConventionalMemory range across SEC, PEI, DXE and EBS',
       'Verify physical access translations and page flags without writing candidate memory',
       'Verify no Android kernel, initrd, IOMMU, secure, display, firmware or DMA uses this space',
       'Only then propose a separate explicitly gated firmware memory-map modification'
     ]
    }

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vendor-repo',required=True,type=Path)
    parser.add_argument('--f1c-map',required=True,type=Path)
    parser.add_argument('--helper',required=True,type=Path)
    parser.add_argument('--out',required=True,type=Path)
    args=parser.parse_args()
    require(not args.out.exists(),'do not overwrite an ownership report')
    repo=args.vendor_repo.resolve()
    revision=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
    require(revision=='3e347601d9e91dd2f1f48e51499823225f7bc0bc','unexpected vendor DTS source revision')
    require(not subprocess.check_output(['git','-C',str(repo),'status','--porcelain'],text=True).strip(),'dirty DTS reference')
    f1c=args.f1c_map.read_bytes()
    require(hashlib.sha256(f1c).hexdigest()=='520acb9e69840ce8dc8bab776d2aa2283034f2dc47f51d4618f5fd8a64868127',
       'unexpected F1c firmware map; do not infer new physical owner')
    out=generate((repo/'thor.dts').read_text(),f1c.decode(),import_memory(args.helper.resolve()),revision)
    out['f1c_memory_map_sha256']=hashlib.sha256(f1c).hexdigest()
    out['vendor_thor_dts_sha256']=hashlib.sha256((repo/'thor.dts').read_bytes()).hexdigest()
    args.out.parent.mkdir(parents=True,exist_ok=True)
    with args.out.open('x') as f:json.dump(out,f,indent=2);f.write('\n')
    print(json.dumps({'result':out['result'],
      'static_reserved':out['static_vendor_reserved_regions'],
      'dynamic_unknown':len(out['dynamic_unknown_placement_reservations']),
      'first_low4G_gap':out['first_low4G_investigation_interval'],
      'hypotheses_over_256MiB':len(out['nominal_static_DDR_gaps_over_256M']),
      'allocated_eligible':False,'phone_actions':0},indent=2))
if __name__=='__main__':main()
