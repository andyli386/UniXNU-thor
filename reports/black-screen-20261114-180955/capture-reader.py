#!/usr/bin/env python3
from pathlib import Path
import datetime,subprocess,json,struct,shutil,os,time,traceback,re
ADB=['/usr/bin/adb','-s','<device-serial>']
OUT=Path(__file__).with_suffix('')
OUT.mkdir(exist_ok=False)
def run(cmd,timeout=30):
 p=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout)
 if p.returncode: raise RuntimeError('command failed '+repr(cmd)+' '+p.stderr.decode(errors='replace'))
 return p.stdout
def shell(cmd):return run(ADB+['shell',cmd]).decode().strip()
def snapshot():
 nodes=['sda36','dm-7']
 d={'at':datetime.datetime.now().astimezone().isoformat(),'state':run(ADB+['get-state']).decode().strip(),'boot_id':shell('cat /proc/sys/kernel/random/boot_id'),'uptime':shell('cat /proc/uptime'),'root':shell('id -u'),'real':shell('readlink -f /dev/block/by-name/userdata'),'mountinfo':shell('cat /proc/self/mountinfo'),'partition_bytes':int(shell('blockdev --getsize64 /dev/block/sda36')),'blocks':{}}
 for n in nodes:
  x={'dev':shell('cat /sys/class/block/'+n+'/dev'),'stat':[int(x) for x in shell('cat /sys/class/block/'+n+'/stat').split()],'holders':shell('ls -A /sys/class/block/'+n+'/holders').split()}
  if n.startswith('dm-'): x['name']=shell('cat /sys/class/block/'+n+'/dm/name')
  d['blocks'][n]=x
 return d
def validate(d):
 assert d['state']=='recovery' and d['root']=='0' and d['real']=='/dev/block/sda36', 'unexpected device state'
 assert d['blocks']['sda36']['holders']==['dm-7'],'unexpected holders'
 assert d['blocks']['dm-7']['holders']==[] and d['blocks']['dm-7']['name']=='userdata','unexpected mapping'
 mounted={x.split()[2] for x in d['mountinfo'].splitlines() if len(x.split())>2}
 for name,x in d['blocks'].items():
  assert x['dev'] not in mounted,'userdata or holder is mounted'
  st=x['stat'];assert st[8]==0,'block I/O active'
  assert all(st[i]==0 for i in [4,5,6,7,11,12,13,14] if i<len(st)),'write/discard activity'
meta={'started_local':datetime.datetime.now().astimezone().isoformat(),'serial':'<device-serial>','device_writes':False,'method':'read-only physical Data window; unmounted idle dm-7 mapping retained'}
try:
 before=snapshot();validate(before);(OUT/'before.json').write_text(json.dumps(before,indent=2))
 time.sleep(1)
 stable=snapshot();validate(stable)
 assert all(before['blocks'][n]['stat'][4:9]==stable['blocks'][n]['stat'][4:9] for n in before['blocks'])
 raw=run(ADB+['exec-out','dd','if=/dev/block/sda36','bs=4096','count=1','status=none'])
 assert len(raw)==4096 and raw[:8]==b'SHNGROOT','invalid header'
 (OUT/'SHNGROOT.bin').write_bytes(raw)
 version,count,total=struct.unpack_from('<IIQ',raw,8)
 assert version==1 and 1<=count<=31 and 4096<total<=before['partition_bytes']
 records=[]
 for i in range(count):
  name,off,size,block,flags=struct.unpack_from('<32sQQII',raw,24+128*i)
  name=name.split(b'\0')[0].decode('ascii')
  assert size>0 and off>=4096 and off+size<=total and block==512 and flags==1
  records.append(dict(name=name,offset=off,length=size,block=block,flags=flags))
 assert len({r['name'] for r in records})==len(records)
 ordered=sorted(records,key=lambda x:x['offset'])
 assert all(a['offset']+a['length']<=b['offset'] for a,b in zip(ordered,ordered[1:]))
 data=next(x for x in records if x['name']=='Data')
 assert data['offset']%4096==0 and data['length']%4096==0
 assert shutil.disk_usage(OUT).free>data['length']+256*1024*1024,'insufficient host disk'
 meta.update(total=total,records=records,expected_bytes=data['length'])
 (OUT/'extraction.json').write_text(json.dumps(meta,indent=2))
 print('Reading Data',data,flush=True)
 part=OUT/'Data-runtime.img.partial';start=time.monotonic()
 meta['chunk_bytes']=64*1024*1024
 meta['chunks_completed']=0
 with part.open('xb') as dest,(OUT/'dd.stderr').open('wb') as err:
  for rel in range(0,data['length'],meta['chunk_bytes']):
   want=min(meta['chunk_bytes'],data['length']-rel)
   p=subprocess.run(ADB+['exec-out','dd','if=/dev/block/sda36','bs=1048576','iflag=skip_bytes,count_bytes','skip='+str(data['offset']+rel),'count='+str(want),'status=none'],stdout=dest,stderr=err,timeout=90)
   dest.flush()
   assert p.returncode==0 and part.stat().st_size==rel+want,'short/failed chunk at '+str(rel)
   meta['chunks_completed']+=1
   meta['validated_bytes']=rel+want
   if meta['chunks_completed']%8==0:
    print('PROGRESS',meta['validated_bytes'],'/',data['length'],flush=True)
    (OUT/'extraction.json').write_text(json.dumps(meta,indent=2))
  os.fsync(dest.fileno())
 meta.update(dd_returncode=p.returncode,read_bytes=part.stat().st_size)
 (OUT/'extraction.json').write_text(json.dumps(meta,indent=2))
 after=snapshot();validate(after);(OUT/'after.json').write_text(json.dumps(after,indent=2))
 assert before['boot_id']==after['boot_id'],'device rebooted during capture'
 unchanged=all(all(before['blocks'][n]['stat'][i]==after['blocks'][n]['stat'][i] for i in [4,5,6,7,11,12,13,14]) for n in before['blocks'])
 meta.update(dd_returncode=p.returncode,read_bytes=part.stat().st_size,elapsed_seconds=round(time.monotonic()-start,3),no_write_or_discard_counter_change=unchanged)
 assert p.returncode==0 and part.stat().st_size==data['length'] and unchanged,'capture failed validation'
 image=OUT/'Data-runtime.img';part.rename(image)
 meta['capture_status']='complete'
 (OUT/'extraction.json').write_text(json.dumps(meta,indent=2))
 print('Capture complete',meta,flush=True)
 meta['collector_status']='pending_readonly_host_extraction'
except Exception as e:
 meta.update(error=str(e));traceback.print_exc()
finally:
 meta['finished_local']=datetime.datetime.now().astimezone().isoformat()
 (OUT/'extraction.json').write_text(json.dumps(meta,indent=2))
 print('FINISHED',json.dumps(meta),flush=True)
