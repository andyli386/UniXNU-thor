#!/usr/bin/env python3
"""Read-only fail-closed DarwinBoot/Thor memory & FAT preflight.

Source-of-truth: compiled Thor MemoryMapLib.c, original DarwinBoot22 bounds,
genuine Apple 20G81 prelink candidate length, static vendor DTS (advisory).
NO DRAM is made allocatable, no device actions, no memory mapping changes.
"""
import argparse
import datetime as dt
import hashlib
import json
import re
from pathlib import Path

MIB=1024*1024
PHYSICAL_LIMIT=1<<64

def fail_if_untrue(predicate,description):
    if not predicate:raise ValueError(description)

def parse_region_map(source):
    rows=[]
    pattern=r'\{\s*"([^"]+)"\s*,\s*(0x[0-9a-fA-F]+)ULL\s*,\s*(0x[0-9a-fA-F]+)ULL\s*,\s*(AddDev|AddMem|NoHob)\s*,\s*([^}]+)\}'
    for match in re.finditer(pattern,source):
        label,address,length,mode,rest=match.groups()
        start=int(address,16);size=int(length,16)
        fail_if_untrue(size>0 and 0<=start<PHYSICAL_LIMIT and size<=PHYSICAL_LIMIT-start,'invalid UEFI range '+label)
        values=[v.strip() for v in rest.split(',')]
        category=values[2] if len(values)>2 else ''
        rows.append({'label':label,'start':start,'end':start+size,'bytes':size,
                     'mode':mode,'type':category,'can_allocate':category=='Conv' and mode=='AddMem'})
    fail_if_untrue(len(rows)>=1,'no firmware regions could be parsed')
    physical=sorted(rows,key=lambda o:o['start'])
    for a,b in zip(physical,physical[1:]):
        fail_if_untrue(a['end']<=b['start'],'firmware regions overlap: '+a['label']+' '+b['label'])
    return physical

def parse_vendor_banks(source):
    match=re.search(r'\bmemory\s*\{[\s\S]{0,500}?\breg\s*=\s*<([^;]+)>',source)
    fail_if_untrue(match is not None,'no memory/reg in pinned source DTS')
    cells=[int(token,0) for token in match.group(1).split()]
    fail_if_untrue(len(cells)%4==0 and len(cells)>=4,'unexpected FDT bank cell arity')
    banks=[]
    for i in range(0,len(cells),4):
        addr=(cells[i]<<32)|cells[i+1]
        size=(cells[i+2]<<32)|cells[i+3]
        fail_if_untrue(size>0 and addr+size<=PHYSICAL_LIMIT,'invalid FDT bank')
        banks.append({'start':addr,'end':addr+size,'bytes':size})
    banks.sort(key=lambda x:x['start'])
    for a,b in zip(banks,banks[1:]):
        fail_if_untrue(a['end']<=b['start'],'vendor bank overlap')
    return banks

def size_of_contiguous_conventional(regions):
    """UEFI GetMemoryMap RunEnd may join adjacent Conv descriptors."""
    rows=[r for r in regions if r['can_allocate']]
    runs=[]
    for r in rows:
        if runs and runs[-1]['end']==r['start']:
            runs[-1]['end']=r['end']
        else:
            runs.append({'start':r['start'],'end':r['end']})
    for run in runs:run['bytes']=run['end']-run['start']
    return runs

def largest_aligned_window(runs,minimum,align=2*MIB,base_offset=0):
    """Replicate DarwinBoot's aligned AllocateAddress candidate rule."""
    best=None
    for run in runs:
        start=(run['start']+align-1)//align*align+base_offset
        if start<run['start']:continue
        available=max(0,run['end']-start)
        row={'candidate_start':start,'available':available,
             'meets_minimum':available>=minimum}
        if best is None or row['available']>best['available']:
            best=row
    return best

def audit(source_map,window_header,ram_header,prelink,source_dts,expected_sha):
    actual=hashlib.sha256(prelink).hexdigest()
    fail_if_untrue(actual==expected_sha,'prelink changed since audited result')
    rows=parse_region_map(source_map)
    # This exact experiment pins 13 actual F1c descriptors, including two
    # MMIO device regions. Synthetic unit maps are validated independently.
    fail_if_untrue(len(rows)==13,'F1c source memory descriptor count changed')
    banks=parse_vendor_banks(source_dts)
    result=re.search(r'return size >= (0x[0-9a-fA-F]+)ULL\s*&&\s*size <= (0x[0-9a-fA-F]+)ULL',window_header)
    fail_if_untrue(result is not None,'DarwinBoot min/max parser semantics changed')
    min_window=int(result[1],16)
    max_window=int(result[2],16)
    fail_if_untrue(min_window==256*MIB and max_window==8*1024*MIB,'unexpected DarwinBoot bounds')
    limit=re.search(r'#define\s+THOR_FAT_MAX_PAYLOAD\s+\(48ULL \* 1024ULL \* 1024ULL\)',ram_header)
    fail_if_untrue(limit is not None,'F1C THORFAT max payload bound changed')
    initrd=re.search(r'#define\s+THOR_FAT_MAX_INITRD\s+SIZE_64MB',ram_header)
    fail_if_untrue(initrd is not None,'F1C THORFAT initrd bound changed')
    fat_limit=48*MIB
    runs=size_of_contiguous_conventional(rows)
    best=largest_aligned_window(runs,min_window)
    fail_if_untrue(best is not None,'no mapped conventional descriptor')
    bank_contains=lambda start,end:any(x['start']<=start and end<=x['end'] for x in banks)
    region_dram=[{'label':row['label'],'within_static_vendor_memory_bank':bank_contains(row['start'],row['end'])}
                  for row in rows]
    fail_if_untrue(sum(r['can_allocate'] for r in rows)==1,'review non-singleton UEFI allocatable ranges')
    fail_if_untrue(best['available']==124*MIB,'unexpected DXE-conventional max allocation')
    fail_if_untrue(len(prelink)>fat_limit,'artifact no longer exceeds FAT bound; update acceptance')
    return {
      'schema':'thor-darwin22-first-full-boot-memory-capacity-v1',
      'evaluated_at':dt.datetime.now(dt.timezone.utc).isoformat(),
      'result':'BLOCKED_EXPECTED_F1C_CANNOT_BOOT_F2_FULL_KERNEL',
      'darwinboot_min_window_bytes':min_window,
      'darwinboot_max_window_bytes':max_window,
      'current_UEFI_allocatable_runs':runs,
      'current_UEFI_largest_valid_aligned_window':best,
      'available_MiB':best['available']/MIB,
      'minimum_window_MiB':min_window/MIB,
      'minimum_window_shortfall_bytes':min_window-best['available'],
      'kernel_input_sha256':actual,'kernel_input_bytes':len(prelink),
      'F1C_FAT_image_payload_max_bytes':fat_limit,
      'F1C_initrd_max_bytes':64*MIB,
      'kernel_over_FAT_limit_bytes':len(prelink)-fat_limit,
      'FAT_full_boot_eligible':False,
      'DarwinBoot_allocate_window_eligible':False,
      'current_firmware_regions':rows,
      'original_vendor_DDR_banks':banks,
      'regions_crosschecked_against_public_DTS':region_dram,
      'new_allocatable_memory_authorized_by_vendor_DTS':False,
      'required_runtime_evidence':[
        'Live Thor bootloader actual FDT memory and reserved-memory ownership',
        'Complete trusted UEFI GetMemoryMap descriptors and real MMU translation',
        'Contiguous >=256MiB *owned* EfiConventionalMemory, at matching alignment and Kernel.Base offset',
        'Nonoverlap with source Android kernel/FDT/initrd/UEFI/firmware/secure/display/DMA',
        'Updated FAT transport and heap test for full kernel+ADT+trustcache payload',
        'Explicit newly reviewed firmware release, with full L2 and single-use L3 gates'
      ],
      'existing_hw11_test_image_modified':False,
      'device_actions':0,'phone_boot_allowed':False}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('memory-map','window-header','ram-header','prelink','vendor-thor-dts','out'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--prelink-sha256',required=True)
    x=p.parse_args()
    fail_if_untrue(not x.out.exists(),'immutable output report already exists')
    result=audit(x.memory_map.read_text(),x.window_header.read_text(),
        x.ram_header.read_text(),x.prelink.read_bytes(),x.vendor_thor_dts.read_text(),
        x.prelink_sha256)
    x.out.parent.mkdir(parents=True,exist_ok=True)
    with x.out.open('x') as f:json.dump(result,f,indent=2);f.write('\n')
    print(json.dumps({k:result[k] for k in ('result','available_MiB',
      'minimum_window_MiB','minimum_window_shortfall_bytes',
      'kernel_input_bytes','F1C_FAT_image_payload_max_bytes',
      'kernel_over_FAT_limit_bytes','phone_boot_allowed')},indent=2))
if __name__=='__main__':main()
