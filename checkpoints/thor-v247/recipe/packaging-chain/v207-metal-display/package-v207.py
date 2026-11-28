"""Assemble the v207 Metal/CPU display image and read it back against both parent controls."""
import argparse, json, os, runpy, shutil, subprocess, tempfile, tomllib
from pathlib import Path
root=Path(__file__).resolve().parent
base=root.parent/'v206-optimized'
hardware=root.parent/'v204-freeze-repair-final'
parent_image=base/'boot-thor-unixnu-v206-optimized.img'
menu_image=hardware/'boot-thor-unixnu-v204-metal-cpu-present.img'
image=root/'boot-thor-unixnu-v207-metal-display.img'
record=root/'BUILD-hardware.json'
kernel=root/'kernel/kernel-ipados18-thor.macho'
util=runpy.run_path(str(root.parent/'v203-display-120hz/package-v203.py'))
decode=runpy.run_path(str(root/'prepare/fat_audit.py'))['decode']
pack=util['pack']; fat=util['fat']; dump=util['json_bytes'];H=util['sha']; file_hash=util['sha_file']
kr='/efi/unixnu/payloads/kernel.macho'; menu='/efi/unixnu/unixnu.toml'; metadata='/efi/unixnu/pong-boot-manifest.json'
parent_sha='30c6855a6ec2b475ab74f18cc4621419c4a1064e4fb3504e299374b9d289e0ae'
menu_sha='314143a982e7ec294b360f783328a1ff70d5d3995db0890ebaef743e787226da'
prior=json.loads((base/'BUILD-private.json').read_bytes())
truth=json.loads((hardware/'BUILD-C.json').read_bytes())
bridge='PongMetalBridge.dylib'
units={'iokit/Kernel/MiniIOMobileFramebuffer.cpp':'iokit/DEVELOPMENT/MiniIOMobileFramebuffer.cpo','osfmk/kern/task_policy.c':'osfmk/DEVELOPMENT/task_policy.o','bsd/kern/kern_exec.c':'bsd/DEVELOPMENT/kern_exec.o'}
def metadata_inputs():
 old,firm,sig,parent_read=decode(parent_image); hw,hfw,hsg,hw_read=decode(menu_image)
 assert parent_read==parent_sha==prior['image_sha256'] and hw_read==menu_sha==truth['image_sha256']
 assert firm==hfw and sig==hsg and old.keys()==hw.keys() and H(old[kr])==prior['kernel_sha256']
 source=json.loads((root/'source.json').read_bytes())
 for unit,pair in source['units'].items():
  assert file_hash(base/'build-tree/.cache/xnu-work'/unit)==pair['baseline']
  assert file_hash(root/'build-tree/.cache/xnu-work'/unit)==pair['candidate']
 assert source['photo_patch']==file_hash(root/'prepare/0001-sample-photos-top-band.patch')=='8fdeedee214b557d50419b75ba13614d9f87a995c97c11eb37c29b6677048b6c'
 assert source['bridge_patch']==file_hash(root/'prepare/0001-break-cross-queue-completion-cycles.patch')
 for n in ['device/drivers/PongPower/PongPlatformResources.cpp','device/drivers/PongPower/ThorStagedBcm.h']:
  assert file_hash(root/n)==file_hash(base/n)
 obj=root/'build-tree/build/xnu/ios-obj/DEVELOPMENT_ARM64_PONG'
 for name,path in units.items():
  compile=json.loads((obj/(path+'.json')).read_text().strip().rstrip(','))
  assert Path(compile['file']).resolve()==root/'build-tree/.cache/xnu-work'/name
 original=json.loads((base/'runtime/final-inputs.json').read_bytes())
 rt=json.loads((root/'runtime/final-inputs.json').read_bytes())
 assert original==prior['runtime'] and original['payloads'].keys()==rt['payloads'].keys()
 assert [n for n in rt['payloads'] if original['payloads'][n]!=rt['payloads'][n]]==[bridge]
 assert rt['payloads'][bridge]==source['bridge'] and rt['pool_path']==source['pool']=='/private/var/thor-metal-pool-v207-06fa2108f2196e9a'
 assert rt['diagnostic_stage_path']==original['diagnostic_stage_path']
 title='Thor v207 - Metal compositor, CPU display copy'
 new_menu,args=pack['new_menu'](hw[menu],dict(title=title,replace_args={}))
 before=json.loads(old[metadata]); config=tomllib.loads(new_menu.decode())
 assert args==truth['boot_args']==json.loads(hw[metadata])['boot_args'] and config['boot']=={'timeout':2,'default':'xnu'}
 assert before['boot_args']==prior['boot_args'] and before['version']==206
 manifest=dict(before,version=207,purpose=title,release_variant='v207-metal-cpu-display',
  candidate_parent=parent_sha,boot_args=args,candidate_change=['Enable v204 hardware compositor parameters and retain v206 cellular observation','Use cycle-safe completed handlers and the v205 diagnostic runtime','Sample Photos top-band comparison only under protected display read ownership'],
  config_change={'menu_reference':menu_sha,'hardware_menu':'v204','software_fallback':parent_sha},
  kernel_sha256=file_hash(kernel),kernel_source=str(kernel),runtime_directory=rt['pool_path'],
  runtime_source='v206 software control signed runtime with v207 Metal pool',diagnostic_delta='v205 app-role and renderer telemetry retained, v207 Photos copy comparison added',
  bridge_uuid=rt['payloads'][bridge]['uuid'],bridge_sha256=rt['payloads'][bridge]['sha256'],
  hardware_validated=False,flash_performed=False,fastboot_boot_performed=False)
 return old,firm,sig,new_menu,dump(manifest),original,rt,source
def verify(candidate):
 old,fw,sig,menu_bytes,meta,original,rt,source=metadata_inputs()
 new,candidate_fw,candidate_sig,h=decode(candidate)
 assert old.keys()==new.keys() and candidate_fw==fw and candidate_sig==sig
 assert {n for n in old if old[n]!=new[n]}=={kr,menu,metadata} and new[menu]==menu_bytes and new[metadata]==meta
 K=new[kr]; assert K==kernel.read_bytes() and K!=old[kr]
 M=json.loads(meta); pack['MENU']['verify_abi'](); av=pack['MENU']['validate_menu'](tomllib.loads(new[menu].decode()),M)
 assert av[0]['argument_bytes']==1022 and 'pong_ui_software=1' not in M['boot_args'].split()
 assert set('pong_metal=1 pong_gpu_scanout=1 fb_copy=0 fb_hz=120 pong_adreno_start=1'.split()).issubset(M['boot_args'].split())
 expected_pool=rt['pool_path']; byte_strings=[expected_pool+'/'+bridge,expected_pool+'/libvulkan_freedreno.dylib','DYLD_LIBRARY_PATH='+expected_pool,rt['diagnostic_stage_path']]
 for s in byte_strings:assert s.encode()+b'\0' in K,s
 assert ('DYLD_LIBRARY_PATH='+original['pool_path']).encode()+b'\0' not in K and (base/'runtime'/bridge).read_bytes() not in K
 markers=['THOR CONSOLE V205: %s diagnostic armed','THOR APP ROLE V205: at_us=','THOR BCM V206: phase=auth-gate','THOR RPMH V204:', 'THOR PHOTO COPY V207: at_us=']
 for s in markers:assert s.encode() in K,s
 assert markers[-1].encode() not in old[kr]
 abi=pack['PREV']['PREV']['MACHO']; data_map={}
 for name,info in {**rt['payloads'],'thor-apps-install':truth['runtime']['thor-apps-install']}.items():
  src=(root.parent/'v151-balanced-rows/runtime' if name=='thor-apps-install' else root/'runtime')/name
  data=src.read_bytes();assert H(data)==info['sha256'] and len(data)==info['bytes'] and abi(data)['uuid']==info['uuid']
  assert K.count(data)==1 and (name==bridge or old[kr].count(data)==1),name
  subprocess.run(['codesign','--verify','--strict',src],check=True)
  data_map[name]={**info,'offset':K.index(data)}
 uuids={p.name:subprocess.check_output(['xcrun','dwarfdump','--uuid',str(p)],text=True).split()[1] for p in (root/'kernel').glob('*') if p.name in {'kernel-ipados18-thor.macho','mach.development.pong','mach.development.pong.unstripped'}}
 assert len(uuids)==3 and len(set(uuids.values()))==1
 _,off,size,_=util['sections'](candidate)
 with tempfile.NamedTemporaryFile(dir=root,suffix='.fat') as f:
  with candidate.open('rb') as c: c.seek(off);f.write(c.read(size))
  f.flush();subprocess.run(['/sbin/fsck_msdos','-n',f.name],check=True,stdout=subprocess.DEVNULL)
 return dict(image=image.name,version=207,mode='hardware-cpu-display',parent_sha256=parent_sha,menu_reference_sha256=menu_sha,
  image_sha256=h,image_bytes=candidate.stat().st_size,kernel_sha256=H(K),kernel_uuid=next(iter(uuids.values())),
  firmware_sha256=fw,signature_sha256=sig,boot_args=M['boot_args'],argument_bytes=1022,filesystem_check=0,pool_path=rt['pool_path'],diagnostic_path=rt['diagnostic_stage_path'],runtime=data_map,bridge_uuid=M['bridge_uuid'],source=source,changed_fat_files=sorted([kr,metadata,menu]),hardware_validated=False)
if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('action',choices=['package','verify']);opt=a.parse_args()
 if opt.action=='package' and not image.exists():
  _,_,_,m,doc,_,_,_=metadata_inputs();assert file_hash(util['FULL'])==util['PARENT_FULL_SHA']
  with tempfile.TemporaryDirectory(dir=root,prefix='.assembly-') as td:
   d=Path(td);full=d/'full.img';candidate=d/'new.img';shutil.copyfile(util['FULL'],full)
   (d/'menu').write_bytes(m);(d/'meta').write_bytes(doc)
   for src,k in [(kernel,'KERNEL'),(d/'menu','TOML'),(d/'meta','MANIFEST')]:
    subprocess.run(['mcopy','-o','-i',util['volume'](full),str(src),fat[k]],check=True)
   util['compact'](full,candidate)
   info=verify(candidate);(d/'record').write_bytes(dump(info));os.replace(d/'record',record);os.link(candidate,image)
 else:
  info=verify(image);assert info==json.loads(record.read_bytes())
 print(dump(info).decode(),end='')
