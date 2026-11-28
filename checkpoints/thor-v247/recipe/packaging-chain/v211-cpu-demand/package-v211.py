#!/opt/homebrew/bin/python3.13
"""Produce a reproducible Thor CPU-demand A/B using the exact v210 hardware release."""
import argparse
import difflib
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile
import tomllib
R=Path(__file__).resolve().parent
B=R.parent/'v210-metal-display'
v=runpy.run_path(str(B/'package-v210.py'))
dec,util,pack,sha,fh,k,m,c=(v[x] for x in ('decoder','util','pack','sha','fh','k','m','c'))
prior='0a2885b5d229fabda7f618cb676f9c84736710c39ff91d288d92c94a4d3af343'
parent=json.loads((B/'verification.json').read_bytes())
image=R/'boot-thor-unixnu-v211-cpu-demand.img'
record=R/'BUILD-hardware.json'
kernel=R/'kernel/kernel-ipados18-thor.macho'
title='Thor v211 - CPU demand, Metal launch diagnostic'
def checkpoint():
    assert parent==json.loads((B/'replayed.json').read_bytes())
    assert parent['image_sha256']==prior and parent['kernel_uuid']=='CE76DD5B-ADBB-3D71-BD16-BB375646AAA3'
    assert (R/'prepare/build.status').read_text().strip()=='0'
    audit=json.loads((R/'prepare/compiled-object-audit.json').read_bytes())
    assert len(audit)==61 and {n:r['changed_sections'] for n,r in audit.items() if r['changed_sections']}=={'armcpu/ArmCpu.o':['__TEXT/__cstring','__TEXT/__text']}
    assert (R/'prepare/test-policy.txt').read_text().strip()=='23 ARM64 ASan/UBSan policy scenarios passed with actual PongCpuPolicy.h'
    rel='device/drivers/ArmCpu/PongCpuPolicy.h'
    orig,mod=[(root/rel).read_text().splitlines(True) for root in (B,R)]
    delta=''.join(difflib.unified_diff(orig,mod,'a/drivers/ArmCpu/PongCpuPolicy.h','b/drivers/ArmCpu/PongCpuPolicy.h'))
    assert delta==(R/'prepare/cpu-policy.patch').read_text()
    assert fh(B/'device/drivers/ArmCpu/ArmCpu.cpp')==fh(R/'device/drivers/ArmCpu/ArmCpu.cpp')
    arm=json.loads((R/'build-tree/build/armcpu/build-report.json').read_bytes())
    assert Path(arm['source']).resolve()==R/'device/drivers/ArmCpu/ArmCpu.cpp'
    assert fh(R/'runtime/final-inputs.json')==fh(B/'runtime/final-inputs.json')
    return dict(policy_patch_sha256=fh(R/'prepare/cpu-policy.patch'),armcpu_source_sha256=fh(R/rel),
                policy_harness_sha256=fh(R/'prepare/test-policy.py'),policy_test_scenarios=23,
                object_audit_sha256=fh(R/'prepare/compiled-object-audit.json'),changed_object='armcpu/ArmCpu.o')
def inputs():
    proof=checkpoint()
    old,fw,sig,whole=dec(B/parent['image'])
    assert whole==prior and sha(old[k])==parent['kernel_sha256']
    menu,args=pack['new_menu'](old[c],dict(title=title,replace_args={}))
    man=json.loads(old[m])
    assert man['boot_args']==args==parent['boot_args'] and not any(x.startswith('pong_cpu_policy=') for x in args.split())
    man.update(version=211,purpose=title,release_variant='v211-cpu-demand',candidate_parent=prior,
        candidate_change=['Default to load-based EPSS only on Thor without an explicit CPU policy; bounded per-domain transition receipts.'],
        config_change={'removed':[],'added':[],'replaced':{},'thor_cpu_default':1},kernel_source=str(kernel),kernel_sha256=fh(kernel),
        hardware_validated=False,flash_performed=False,fastboot_boot_performed=False)
    return old,fw,sig,proof,{k:kernel.read_bytes(),m:util['json_bytes'](man),c:menu}
def verify(path):
    old,fw,sig,proof,modified=inputs()
    data,fw2,sig2,digest=dec(path)
    assert old.keys()==data.keys() and (fw,sig)==(fw2,sig2)
    assert {n for n in old if data[n]!=old[n]}==set(modified) and all(data[n]==blob for n,blob in modified.items())
    dt=data[k+'.adt']
    assert sha(dt)==parent['adt_sha256'] and parent['adt_pas4_trial'] is False
    cfg=tomllib.loads(data[c].decode());man=json.loads(data[m])
    pack['MENU']['verify_abi']();argv=pack['MENU']['validate_menu'](cfg,man)
    assert cfg['boot']=={'default':'xnu','timeout':2} and argv[0]['argument_bytes']==1022
    K=data[k]
    for needle in [b'THOR CPU V211: governor=load source=thor-default',b'THOR CPU V211: cpu=%u event=%s',b'first-recovery\0']:
        assert needle in K and needle not in old[k]
    for needle in [b'THOR TARGET EXEC V210:',b'com.pong.thor.target-v210.%u.%s',b'pong_cpu_policy\0',b'IOAcceleratorES\0']:
        assert needle in K and needle in old[k]
    runtime={}
    for name,spec in parent['runtime'].items():
        p=(R/'runtime' if name!='thor-apps-install' else R.parent/'v151-balanced-rows/runtime')/name
        blob=p.read_bytes()
        assert sha(blob)==spec['sha256'] and K.count(blob)==old[k].count(blob)==1
        assert v['abi'](blob)['uuid']==spec['uuid']
        subprocess.run(['codesign','--verify','--strict',str(p)],check=True)
        runtime[name]=dict(spec,offset=K.index(blob))
    ids={subprocess.check_output(['xcrun','dwarfdump','--uuid',str(R/'kernel'/name)],text=True).split()[1] for name in ('kernel-ipados18-thor.macho','mach.development.pong','mach.development.pong.unstripped')}
    assert ids=={'101B8044-1923-31B0-AA19-DEEBAFDDBEC9'}
    _,off,size,_=util['sections'](path)
    with tempfile.NamedTemporaryFile(dir=R,suffix='.fat') as tmp,path.open('rb') as full:
        full.seek(off);tmp.write(full.read(size));tmp.flush()
        subprocess.run(['/sbin/fsck_msdos','-n',tmp.name],stdout=subprocess.DEVNULL,check=True)
    return dict(image=image.name,image_sha256=digest,image_bytes=path.stat().st_size,kernel_sha256=sha(K),kernel_uuid=ids.pop(),
        parent_sha256=prior,version=211,mode='hardware-cpu-demand-diagnostic',runtime=runtime,
        pool_path=parent['pool_path'],diagnostic_path=parent['diagnostic_path'],bridge_uuid=parent['bridge_uuid'],
        firmware_sha256=fw,signature_sha256=sig,adt_sha256=sha(dt),adt_pas4_trial=False,
        argument_bytes=1022,boot_args=man['boot_args'],filesystem_check=0,changed_fat_files=sorted(modified),
        cpu_policy={'implicit_thor':1,'explicit_precedence':True,'other_default':2,'observed_on_device':False},
        hardware_validated=False,source=proof)
def build(d):
    changes=inputs()[-1]
    assert fh(util['FULL'])==util['PARENT_FULL_SHA']
    epoch=1795428802
    full=d/'full.img';result=d/'candidate.img'
    shutil.copyfile(util['FULL'],full);env=dict(os.environ,TZ='UTC',SOURCE_DATE_EPOCH=str(epoch))
    for i,(name,payload) in enumerate(changes.items()):
        p=d/str(i);p.write_bytes(payload);os.utime(p,(epoch,epoch))
        subprocess.run(['mcopy','-m','-o','-i',util['volume'](full),str(p),'::'+name],env=env,check=True)
    util['compact'](full,result)
    return result
def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['package','verify','replay'])
    act=parser.parse_args().action
    if act=='verify' or (act=='package' and image.exists()):
        r=verify(image);assert r==json.loads(record.read_bytes())
    else:
        with tempfile.TemporaryDirectory(dir=R,prefix='.pack-') as tmp:
            tmp=Path(tmp);p=build(tmp);r=verify(p)
            if act=='replay':
                assert r==json.loads(record.read_bytes()) and fh(p)==fh(image)
            else:
                (tmp/'report').write_bytes(util['json_bytes'](r));os.replace(tmp/'report',record);os.link(p,image)
    print(util['json_bytes'](r).decode(),end='')
if __name__=='__main__':main()
