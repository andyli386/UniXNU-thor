from pathlib import Path
import sys,json,re,struct,subprocess,bisect
root=Path(sys.argv[1]);base=root/'analysis';lib=root/'extracted/files/pong/libpong-ui18.dylib'
d=json.loads((base/'native-pongboot-late.json').read_text())
b=lib.read_bytes()
if struct.unpack_from('<I',b)[0]!=0xfeedfacf:raise ValueError('expected thin Mach-O 64')
pos=32;ranges=[]
for _ in range(struct.unpack_from('<I',b,16)[0]):
 cmd,sz=struct.unpack_from('<II',b,pos)
 if cmd==0x19:
  nsec=struct.unpack_from('<I',b,pos+64)[0]
  for j in range(nsec):
   s=pos+72+80*j
   sn=b[s:s+16].split(b'\0')[0].decode()
   if sn=='__text':
    adr,length=struct.unpack_from('<QQ',b,s+32);ranges.append((adr,adr+length))
 pos+=sz
p=subprocess.run(['/usr/bin/xcrun','nm','-n',str(lib)],capture_output=True,text=True,check=True)
Path('/Users/<user>/projects/thor-unixnu1030/UniXNU-pong/reports/black-screen-20261114-153948/analysis/native-ui18-v75-nm.txt').write_text(p.stdout)
symbols=[]
for l in p.stdout.splitlines():
 m=re.match(r'^([0-9a-fA-F]+)\s+([tT])\s+(.+)$',l)
 if m:symbols.append((int(m[1],16),m[3]))
symbols.sort();addrs=[x[0] for x in symbols];hits=[]
for f in d['files']:
 for block in f['blocks']:
  h=block.get('header','')
  if 'libpong-ui18.dylib ' not in h:continue
  m=re.search(r'image_slide=([0-9a-fA-F]+)',h)
  if not m:continue
  slide=int(m[1],16)
  for t in block['threads']:
   for fr in t['symbols']:
    addr=int(fr['runtime'],16)-slide
    if not any(lo<=addr<hi for lo,hi in ranges):continue
    ix=bisect.bisect_right(addrs,addr)-1
    if ix<0:continue
    start,name=symbols[ix]
    if not any(lo<=start<=addr<hi for lo,hi in ranges):continue
    label=f'libpong-ui18!{name}+{addr-start:#x} [actual extracted library, image0 slide]'
    fr['symbol']=label;fr['ui18_vmaddr']=hex(addr);fr['ui18_image_slide']=hex(slide)
    hits.append({'file':f['file'],'time':block.get('time'),'pid':block.get('pid'),'tid':t['tid'],'line':t['line'],'kind':fr['kind'],'runtime':fr['runtime'],'symbol':label})
(base/'native-pongboot-late.json').write_text(json.dumps(d,indent=2)+'\n')
(base/'native-ui18-frames.json').write_text(json.dumps({'text_ranges':ranges,'matching_image0_required':True,'symbol_source':str(lib),'frames':hits},indent=2)+'\n')
print(json.dumps({'ui18_frames':len(hits),'examples':hits[:12]},indent=2))
