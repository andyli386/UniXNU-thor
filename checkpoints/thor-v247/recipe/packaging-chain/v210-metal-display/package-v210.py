#!/opt/homebrew/bin/python3.13
"""Package and independently reopen the v210 launch diagnostic trial against the exact v209 hardware parent."""
import argparse
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile
import tomllib

root=Path(__file__).resolve().parent
before=root.parent/'v209-metal-display'
base=before/'boot-thor-unixnu-v209-metal-display.img'
base_sha='e4a3b492dd163e36ba3199356488e66ac962aa95635a46ed54fb1a33f1bc755a'
image=root/'boot-thor-unixnu-v210-metal-diagnostic.img'
record=root/'BUILD-hardware.json'
kernel=root/'kernel/kernel-ipados18-thor.macho'
oldpkg=runpy.run_path(str(root.parent/'v207-metal-display/package-v207.py'))
decoder=oldpkg['decode'];util=oldpkg['util'];pack=oldpkg['pack'];sha=oldpkg['H'];fh=oldpkg['file_hash']
k,m,c=oldpkg['kr'],oldpkg['metadata'],oldpkg['menu']
adt=k+'.adt'
bridge='PongMetalBridge.dylib'
diag='libpong-console-v205.dylib'
src=Path('build-tree/.cache/xnu-work')
abi=pack['PREV']['PREV']['MACHO']

def inputs():
    assert (root/'prepare/build.status').read_text().strip()=='0'
    old,fw,sig,whole=decoder(base)
    parent=json.loads((before/'BUILD-hardware.json').read_bytes())
    assert parent['image_sha256']==whole==base_sha and sha(old[k])==parent['kernel_sha256']
    source=json.loads((root/'source.json').read_bytes())
    for name,expected in {**source['patch'],**source['evidence']}.items():
        assert fh(root/'prepare'/name)==expected
    assert source['parent_image_sha256']==base_sha and source['evidence']
    rt=json.loads((root/'runtime/final-inputs.json').read_bytes())
    prior=json.loads((before/'runtime/final-inputs.json').read_bytes())
    assert rt['payloads'].keys()==prior['payloads'].keys()
    assert {n for n in rt['payloads'] if rt['payloads'][n]!=prior['payloads'][n]}=={diag}
    assert rt['payloads'][bridge]==source['bridge']==prior['payloads'][bridge]
    assert rt['payloads'][diag]==source['collector']
    assert rt['payloads'][diag]['uuid']=='00F16FF1-5165-3A76-890D-59D3DA338254'
    assert rt['payloads'][diag]['sha256']=='47d0c4d72abd612ba31e9324907492584ea84d8af84835ca3cf3ed92d333a181'
    assert rt['diagnostic_stage_path']==source['collector_path']=='/private/var/thor-console-v210/'+diag
    assert rt['pool_path']==prior['pool_path']==source['pool']
    assert rt['payloads'][bridge]['sha256']=='6c717841ef062d2992dcf5201cd0c8064b231248c11a485136534566830119fe'
    orig=(before/src/'bsd/kern/kern_exec.c').read_text()
    actual=(root/src/'bsd/kern/kern_exec.c').read_text()
    assert orig.count('thor-console-v208')==2 and 'CA_DISABLE_ASYNCHRONOUS=1' not in actual
    with tempfile.TemporaryDirectory(dir=root) as t:
        td=Path(t);f=td/'bsd/kern/kern_exec.c';f.parent.mkdir(parents=True)
        f.write_text(orig.replace('thor-console-v208','thor-console-v210'))
        subprocess.run(['patch','-s','-p1','-i',str(root/'prepare/v210-target-exec.patch')],cwd=td,check=True)
        assert actual==f.read_text()
    for rel in (src/'bsd/kern/thor_metal_pool_payload.h',Path('runtime/thor_metal_pool_payload.h')):
        assert (before/rel).read_text().replace(str(before),str(root))==(root/rel).read_text()
    for rel in (src/'iokit/Kernel/MiniIOMobileFramebuffer.cpp',src/'osfmk/kern/task_policy.c',src/'iokit/Kernel/MiniIOSurface.cpp',
                src/'osfmk/arm/arm_init.c',Path('device/drivers/PongPower/ThorPas4Kernel.inc'),Path('device/drivers/PongPower/PongPlatformResources.cpp')):
        assert fh(before/rel)==fh(root/rel)
    title='Thor v210 - Metal app launch diagnostic'
    menu,args=pack['new_menu'](old[c],{'title':title,'replace_args':{}})
    manifest=json.loads(old[m])
    assert args==manifest['boot_args']==parent['boot_args'] and manifest['version']==209
    manifest.update(version=210,purpose=title,release_variant='v210-metal-launch-diagnostic',
                    candidate_parent=base_sha,candidate_change=source['changes'],kernel_source=str(kernel),kernel_sha256=fh(kernel),
                    runtime_directory=rt['pool_path'],runtime_source='v209 signed Metal and Turnip with v210 signed launch collector',
                    config_change={'added':[],'menu_reference':base_sha},diagnostic_delta='Exact executable path event and bounded target snapshots',
                    bridge_uuid=rt['payloads'][bridge]['uuid'],bridge_sha256=rt['payloads'][bridge]['sha256'],
                    hardware_validated=False,flash_performed=False,fastboot_boot_performed=False)
    return old,fw,sig,source,parent,prior,rt,{k:kernel.read_bytes(),m:util['json_bytes'](manifest),c:menu}

def verify(path):
    old,fw,sig,source,parent,prior,rt,changes=inputs()
    data,f2,s2,digest=decoder(path)
    assert (fw,sig,old.keys())==(f2,s2,data.keys()) and all(data[n]==v for n,v in changes.items())
    assert {n for n in old if old[n]!=data[n]}==set(changes)
    assert old[adt]==data[adt]
    adt_lib=runpy.run_path(str(root.parent/'v152-amp-clusters/prepare-adt-v152.py'))
    assert 'thor-pas4-probe' not in adt_lib['parse_adt'](data[adt])['/chosen']
    cfg=tomllib.loads(data[c].decode());man=json.loads(data[m]);pack['MENU']['verify_abi']()
    argv=pack['MENU']['validate_menu'](cfg,man)
    assert cfg['boot']=={'default':'xnu','timeout':2} and argv[0]['argument_bytes']==1022
    opts=set(man['boot_args'].split())
    assert {'pong_metal=1','pong_gpu_scanout=1','pong_adreno_start=1','fb_hz=120','fb_copy=0'}<=opts and 'pong_ui_software=1' not in opts
    K=data[k];oldk=old[k]
    for token in [b'THOR BCM V206: phase=auth-gate',b'THOR PHOTO COPY V207: at_us=',b'THOR RPMH V204:']:
        assert token in K and token in oldk
    for token in [b'THOR PAS4 PROBE:',b'THOR SW V208: backboardd CA_DISABLE_ASYNCHRONOUS=1 at exec',b'CA_DISABLE_ASYNCHRONOUS=1\0']:
        assert token not in K and token not in oldk
    for token in [b'com.pong.thor.focus-v208.%s']:
        assert token in K and token in oldk
    for token in [b'THOR TARGET EXEC V210: at_us=%llu pid=%d name=%s\n\0',b'com.pong.thor.target-v210.%u.%s',b'%s app_pid=%u sample_us=%llu',b'thor-target-v210 role=runningboardd']:
        assert token in K and token not in oldk
    for q in [rt['pool_path']+'/'+bridge,rt['pool_path']+'/libvulkan_freedreno.dylib','DYLD_LIBRARY_PATH='+rt['pool_path']]:
        assert q.encode()+b'\0' in K and q.encode()+b'\0' in oldk
    assert rt['diagnostic_stage_path'].encode()+b'\0' in K and rt['diagnostic_stage_path'].encode()+b'\0' not in oldk
    assert prior['diagnostic_stage_path'].encode()+b'\0' not in K
    payloads={}
    for name,parent_spec in parent['runtime'].items():
        d=root.parent/'v151-balanced-rows/runtime' if name=='thor-apps-install' else root/'runtime'
        payload=d/name;blob=payload.read_bytes();info=rt['payloads'].get(name,parent_spec)
        assert sha(blob)==info['sha256'] and len(blob)==info['bytes'] and abi(blob)['uuid']==info['uuid']
        assert K.count(blob)==1 and oldk.count(blob)==(0 if name==diag else 1),name
        if name==diag:
            assert (before/'runtime'/name).read_bytes() not in K
        subprocess.run(['codesign','--verify','--strict',str(payload)],check=True)
        payloads[name]={**info,'offset':K.index(blob)}
    obj=root/'build-tree/build/xnu/ios-obj/DEVELOPMENT_ARM64_PONG/bsd/DEVELOPMENT/kern_exec.o'
    cmd=json.loads(Path(str(obj)+'.json').read_text().strip().rstrip(','))
    assert Path(cmd['file']).resolve()==root/src/'bsd/kern/kern_exec.c'
    uuids={subprocess.check_output(['xcrun','dwarfdump','--uuid',str(root/'kernel'/f)],text=True).split()[1]
           for f in ('kernel-ipados18-thor.macho','mach.development.pong','mach.development.pong.unstripped')}
    assert len(uuids)==1 and parent['kernel_uuid'] not in uuids
    _,off,size,_=util['sections'](path)
    with tempfile.NamedTemporaryFile(dir=root,suffix='.fat') as tmp,path.open('rb') as full:
        full.seek(off);tmp.write(full.read(size));tmp.flush();subprocess.run(['/sbin/fsck_msdos','-n',tmp.name],stdout=subprocess.DEVNULL,check=True)
    return dict(image=image.name,image_sha256=digest,image_bytes=path.stat().st_size,kernel_sha256=sha(K),kernel_uuid=uuids.pop(),
                parent_sha256=base_sha,source=source,version=210,mode='hardware-cpu-display-diagnostic',runtime=payloads,
                bridge_uuid=rt['payloads'][bridge]['uuid'],pool_path=rt['pool_path'],diagnostic_path=rt['diagnostic_stage_path'],
                firmware_sha256=fw,signature_sha256=sig,adt_sha256=sha(data[adt]),adt_pas4_trial=False,
                linked_kern_exec_sha256=fh(obj),argument_bytes=argv[0]['argument_bytes'],boot_args=man['boot_args'],
                filesystem_check=0,hardware_validated=False,changed_fat_files=sorted(changes))

def assemble(directory):
    data=inputs();source=data[3];changes=data[-1]
    assert fh(util['FULL'])==util['PARENT_FULL_SHA']
    epoch=int(source['source_date_epoch'])
    env=dict(os.environ,TZ='UTC',SOURCE_DATE_EPOCH=str(epoch))
    full=directory/'full.img';candidate=directory/'compact.img'
    shutil.copyfile(util['FULL'],full)
    for i,(n,v) in enumerate(changes.items()):
        f=directory/str(i);f.write_bytes(v);os.utime(f,(epoch,epoch))
        subprocess.run(['mcopy','-m','-o','-i',util['volume'](full),str(f),'::'+n],env=env,check=True)
    util['compact'](full,candidate)
    return candidate

def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=('package','verify','replay'))
    act=parser.parse_args().action
    if act=='verify' or (act=='package' and image.exists()):
        info=verify(image);assert json.loads(record.read_bytes())==info
    else:
        with tempfile.TemporaryDirectory(dir=root,prefix='.image-') as tmp:
            d=Path(tmp);candidate=assemble(d);info=verify(candidate)
            if act=='replay':
                assert fh(candidate)==fh(image) and info==json.loads(record.read_bytes())
            else:
                (d/'report').write_bytes(util['json_bytes'](info))
                os.replace(d/'report',record);os.link(candidate,image)
    print(util['json_bytes'](info).decode(),end='')
if __name__=='__main__':main()
