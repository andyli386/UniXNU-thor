#!/usr/bin/env python3
"""Compare genuine vendor DTS UFS nodes before reusing Pong SM8475 driver.

WARNING: publicly extracted downstream DTS may not equal the user's live
bootloader DTB; do NOT use this to authorize MMIO/DMA or flash memory.
"""
import argparse,datetime,hashlib,json,re,subprocess
from pathlib import Path

def extract(text,label):
    pat=re.compile(r'(?m)^[\t ]*'+re.escape(label)+r': [^{}\n]+\{')
    matches=list(pat.finditer(text))
    if len(matches)!=1:raise ValueError('Expected one labeled node: '+label)
    m=matches[0]
    at=text.index('{',m.start())
    depth=1;end=at+1
    while depth:
        if end>=len(text):raise ValueError('Unclosed DTS node')
        depth+=(text[end]=='{')-(text[end]=='}')
        end+=1
    return text[m.start():end+1]

def properties(node):
    # Exclude child-node properties which may repeat key names such as mask.
    result={}
    depth=0
    for line in node.splitlines():
        stripped=line.strip()
        depth+=stripped.count('{')
        if depth==1 and '=' in stripped and stripped.endswith(';'):
            name,value=stripped[:-1].split('=',1)
            name=name.strip();value=' '.join(value.split())
            if name in result:raise ValueError('Duplicate root DTS property: '+name)
            result[name]=value
        depth-=stripped.count('}')
    assert depth==0
    return result

def parse_cells(value, count):
    m=re.fullmatch(r'<([^<>]+)>',value)
    if not m:raise ValueError('Expected simple DTS cells')
    tokens=m[1].split()
    if len(tokens)!=count:raise ValueError('DTS cell arity changed')
    return [int(x,0) for x in tokens]

def main():
    a=argparse.ArgumentParser()
    a.add_argument('--vendor-repo',required=True,type=Path)
    a.add_argument('--out',required=True,type=Path)
    args=a.parse_args()
    repo=args.vendor_repo.resolve();out=args.out.resolve()
    if out.exists():raise ValueError('refuse to overwrite immutable report')
    rev=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
    if rev!='3e347601d9e91dd2f1f48e51499823225f7bc0bc':
        raise ValueError('Vendor DTS reference revision is not pinned')
    assert not subprocess.check_output(['git','-C',str(repo),'status','--porcelain'],text=True).strip()
    names=['ufshc_mem','ufsphy_mem']
    input={}
    source_digest={}
    for kind in ('thor','pong'):
        path=repo/(kind+'.dts')
        data=path.read_bytes()
        source_digest[kind+'.dts']=hashlib.sha256(data).hexdigest()
        input[kind]={}
        for name in names:
            raw=extract(data.decode(),name)
            input[kind][name]={'sha256':hashlib.sha256(raw.encode()).hexdigest(),
                               'properties':properties(raw)}
    thor=input['thor'];pong=input['pong']
    ctrl=thor['ufshc_mem']['properties']
    other=pong['ufshc_mem']['properties']
    phy=thor['ufsphy_mem']['properties']
    otherphy=pong['ufsphy_mem']['properties']
    comparable=['compatible','reg','reg-names','interrupts','phys','phy-names',
                'lanes-per-direction','clock-names','clocks','freq-table-hz',
                'iommus','qcom,iommu-dma','status']
    for field in comparable:
        if ctrl.get(field)!=other.get(field):
            raise AssertionError('UFS SAME-SoC controller property changed: '+field)
    for field in ['reg','reg-names','lanes-per-direction','clock-names','clocks',
                  'compatible','status']:
        if phy.get(field)!=otherphy.get(field):
            raise AssertionError('UFS PHY SM8475 property changed: '+field)
    mmio=parse_cells(ctrl['reg'],6)
    assert mmio==[0x1d84000,0x3000,0x1d88000,0x8000,0x1d90000,0x9000]
    interrupts=parse_cells(ctrl['interrupts'],3)
    assert interrupts==[0,0x109,4]
    iommu=parse_cells(ctrl['iommus'],3)
    assert iommu==[91,224,0]
    assert parse_cells(phy['reg'],2)==[0x1d80000,0x2000]
    differences={}
    for name in names:
        d1=input['thor'][name]['properties']
        d2=input['pong'][name]['properties']
        differences[name]={k:{'thor':d1.get(k),'pong':d2.get(k)}
            for k in sorted(set(d1)|set(d2)) if d1.get(k)!=d2.get(k)}
    assert 'interconnects' in differences['ufshc_mem']
    assert 'resets' in differences['ufsphy_mem']
    report={'schema':'thor-pong-sm8475-real-vendor-fdt-ufs-comparison-v1',
        'checked_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'result':'PASS_PINNED_THOR_PONG_UFS_MMIO_PHY_IRQ_AND_STREAMID_AGREE',
        'vendor_fdt_git':rev,'vendor_dts_sha256':source_digest,
        'ufs_controller_mmio':[{'base':hex(mmio[x]),'bytes':mmio[x+1]}
                                for x in range(0,6,2)],
        'ufs_interrupt_raw_cells':interrupts,
        'ufs_phy_mmio':{'base':hex(0x1d80000),'bytes':0x2000},
        'ufs_iommu_phandle':iommu[0],
        'ufs_stream_id':hex(iommu[1]),
        'ufs_iommu_flags':iommu[2],
        'same_ufs_controller_selected_properties':comparable,
        'board_specific_differences_raw_phandles':differences,
        'phandle_references_resolved_to_provider':False,
        'source_dts_is_live_phone_dt':False,
        'hardware_MMIO_or_DMA_authorized':False,
        'production_storage_write_safe':False,
        'phone_connected_during_test':False,
        'phone_actions':0}
    out.parent.mkdir(parents=True,exist_ok=True)
    with out.open('x') as f:json.dump(report,f,indent=2);f.write('\n')
    print(json.dumps({'result':report['result'],'MMIO':report['ufs_controller_mmio'],
      'PHY':report['ufs_phy_mmio'],'IRQ':interrupts,'IOMMU_SID':report['ufs_stream_id'],
      'differences':differences,'phone_actions':0},indent=2))
if __name__=='__main__':main()
