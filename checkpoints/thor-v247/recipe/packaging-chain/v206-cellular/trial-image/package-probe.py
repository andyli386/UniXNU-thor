#!/opt/homebrew/bin/python3.13
"""Pack the separately selected signed-PAS4 experiment and verify its full extracted contents."""
import argparse,hashlib,json,os,runpy,shutil,subprocess,tempfile,tomllib
from pathlib import Path

out=Path(__file__).resolve().parent
cell=out.parent
thor=cell.parent
tree=thor/'v206-modem-trial-build'
main=thor/'v206-optimized'
lib=runpy.run_path(str(main/'package-v206.py'))
p=lib['p'];decode=lib['decode'];sha=lib['sha'];fh=lib['file_hash']
k,m,c=lib['kname'],lib['mname'],lib['cname']
a=k+'.adt'
base=main/'boot-thor-unixnu-v206-optimized.img'
parent='30c6855a6ec2b475ab74f18cc4621419c4a1064e4fb3504e299374b9d289e0ae'
image=out/'boot-thor-unixnu-v206-pas4-trial.img'
record=out/'BUILD-probe.json'
api=runpy.run_path(str(thor/'v152-amp-clusters/prepare-adt-v152.py'))
args=lambda blob:next(row['args'] for row in tomllib.loads(blob.decode())['entry'] if row['type']=='xnu')
prop='thor-pas4-probe'
on=b'\x01\0\0\0'
parts={'0002-cape-optional-provider-owned-pas-probe.patch':'eaaf274b4de8ce307a975648265e24b842a0e584ba9490232396734233504c9d','0003-gate-graceful-stop-on-observed-ready.patch':'07154579a82f9f2023813e735f811206fdaee649efa76bfda5fe13dbf1558d00'}
kernel=tree/'kernel/kernel-ipados18-thor.macho'
def inputs():
    old,fw,sig,orig=decode(base);assert orig==parent and args(old[c])==json.loads(old[m])['boot_args']
    assert not any(s.startswith('pas4trial') for s in args(old[c]).split())
    nodes=api['parse_adt'](old[a]);assert bytes(api['emit'](nodes,'/'))==old[a] and prop not in nodes['/chosen']
    nodes['/chosen'][prop]=on
    adt=bytes(api['emit'](nodes,'/'))
    assert api['parse_adt'](adt)==nodes
    menu,bootargs=p['pack']['new_menu'](old[c],{'title':'Thor v206 - signed PAS4 boot and stop trial','replace_args':{},'append_args':[]})
    assert bootargs==args(old[c])
    assert all(fh(cell/n)==h for n,h in parts.items())
    man=dict(json.loads(old[m]),purpose='Thor v206 signed PAS4 modem boot and stop trial',release_variant='v206-pas4-probe',candidate_parent=parent,
             candidate_change='One-shot signed PAS4 authentication, readiness measurement and secure shutdown on the stock Cape consumer path. Trial retains shared votes and carveouts.',
             config_change={'added':[],'adt':{'/chosen/thor-pas4-probe':'01000000'}},kernel_source=str(kernel),kernel_sha256=fh(kernel),
             adt_sha256=sha(adt),modem_trial={'permanent_service':False,'retry':False,'selection':'/chosen/thor-pas4-probe=LE32(1)'},source_patches=parts)
    return {k:kernel.read_bytes(),a:adt,m:p['json_bytes'](man),c:menu},old,fw,sig

def verify(path):
    exp,old,fw,sig=inputs();got,observed_fw,observed_sig,complete=decode(path)
    assert fw==observed_fw and sig==observed_sig and old.keys()==got.keys() and {x for x in old if old[x]!=got[x]}==set(exp)
    for n in exp:assert exp[n]==got[n],n
    nodes=api['parse_adt'](got[a]);assert nodes['/chosen'].pop(prop)==on and bytes(api['emit'](nodes,'/'))==old[a]
    cfg=tomllib.loads(got[c].decode());assert cfg['boot']=={'default':'xnu','timeout':2} and args(got[c])==args(old[c]) and len(args(got[c]).encode())==1020
    p['pack']['MENU']['verify_abi']();p['pack']['MENU']['validate_menu'](cfg,json.loads(got[m]))
    markers=('thor-pas4-probe','THOR PAS4 PROBE: methods=','THOR PAS4 PROBE: stop=','probe-stop','THOR PAS4 PROBE: aoss-off=','retry=none','thor-pas4-probe','THOR PRESENT V206:','THOR BCM V206:','probe-pre-auth','ready-wait','auth-not-confirmed','shutdown-not-confirmed')
    assert all(x.encode() in got[k] for x in markers)
    assert 'THOR PAS4 PROBE:'.encode() not in old[k]
    assert (tree/'final-build.status').read_text().strip()=='0'
    runtime=json.loads((main/'runtime/final-inputs.json').read_bytes())
    for n,s in runtime['payloads'].items():
        payload=main/'runtime'/n;binary=payload.read_bytes()
        assert got[k].count(binary)==1 and sha(binary)==s['sha256'];subprocess.run(['codesign','--verify','--strict',str(payload)],check=True)
    arch=tree/'build-tree/build/pong-power/libPongPower.a'
    objects={}
    for member in ('QcomSmem.o','PongPlatformResources.o'):
        pth=arch.parent/member;data=subprocess.check_output(['xcrun','ar','p',str(arch),member])
        assert data==pth.read_bytes();objects[member]=sha(data)
    f,begin,length,sign=p['sections'](path)
    with tempfile.NamedTemporaryFile(dir=out,suffix='.fat') as q:
        with path.open('rb') as src:src.seek(begin);q.write(src.read(length))
        q.flush();subprocess.run(['/sbin/fsck_msdos','-n',q.name],check=True,stdout=subprocess.DEVNULL)
    targets=(b'__ZN12_GLOBAL__N_116thorModemIccHeldEv',b'_qcom_smem_mss_owned_stop')
    symbol_dump=subprocess.check_output(['xcrun','nm',str(tree/'kernel/mach.development.pong.unstripped')],text=True)
    assert '_qcom_smem_mss_owned_stop' in symbol_dump and 'pasRunProbe' in symbol_dump
    return dict(image=image.name,image_sha256=complete,image_bytes=path.stat().st_size,kernel_sha256=sha(got[k]),kernel_uuid=subprocess.check_output(['xcrun','dwarfdump','--uuid',str(kernel)],text=True).split()[1],parent_sha256=parent,adt_sha256=sha(got[a]),adt_chosen={prop:on.hex()},boot_args=args(got[c]),argument_bytes=len(args(got[c]).encode()),runtime=runtime,source_patches=parts,archive_objects=objects,
                filesystem_check=0,mode='signed-pas4-trial',temporary_only=True,hardware_verification='pending',members={n:sha(data) for n,data in got.items()})
def main_action():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=('package','verify'));op=parser.parse_args().action
    if op=='verify' or image.exists():
        info=verify(image);assert info==json.loads(record.read_bytes())
    else:
        changes,*_=inputs()
        assert fh(p['FULL'])==p['PARENT_FULL_SHA']
        with tempfile.TemporaryDirectory(dir=out,prefix='.pack-') as d:
            d=Path(d);full=d/'full.img';candidate=d/'compact.img'
            shutil.copyfile(p['FULL'],full)
            for i,(key,content) in enumerate(changes.items()):
                dest=d/str(i);dest.write_bytes(content)
                subprocess.run(['mcopy','-o','-i',p['volume'](full),str(dest),'::'+key],check=True)
            p['compact'](full,candidate);info=verify(candidate)
            (d/'record').write_bytes(p['json_bytes'](info));os.replace(d/'record',record);os.link(candidate,image)
    print(json.dumps(info,indent=2))
if __name__=='__main__':main_action()
