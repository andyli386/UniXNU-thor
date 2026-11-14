#!/usr/bin/env python3
from pathlib import Path
import json,re,collections,plistlib,sys

ROOT,BASE=map(Path,sys.argv[1:3])
OUT=ROOT/'analysis'; OUT.mkdir(exist_ok=True)
DEC=json.JSONDecoder()

def rows(root):
    raw=(root/'extracted/files/logs/PongConsole.txt').read_bytes()
    groups=collections.OrderedDict()
    for n,s in enumerate(raw.splitlines(),1):
        if s.strip(b'\x00 \t'):groups.setdefault(s,[]).append(n)
    result=[]
    for s,locs in groups.items():
        row={'physical_lines':locs,'text':s.decode(errors='replace')}
        m=re.match(r'^\[\s*(\d+)\.(\d+)\]:',row['text'])
        if m:
            row['raw_literal']=m[1]+'.'+m[2]
            row['time_us']=int(m[1])*1000000+int(m[2]) if len(m[2])==6 else None
        result.append(row)
    return result

def native(root,baseline):
    result=[]
    for p in sorted((root/'extracted/files').rglob('*.ips')):
        s=p.read_text(errors='replace'); body=None
        rec={'file':str(p.relative_to(root/'extracted/files')),'bytes':p.stat().st_size}
        bp=baseline/'extracted/files'/rec['file']
        rec['same_path_in_baseline']=bp.is_file()
        rec['bytes_identical_to_baseline']=bp.is_file() and p.read_bytes()==bp.read_bytes()
        try:
            h,pos=DEC.raw_decode(s);rec['header']=h
            pos=len(s)-len(s[pos:].lstrip())
            try:
                body,end=DEC.raw_decode(s,pos);rec['body_complete']=True
            except ValueError as e:rec.update(body_complete=False,body_error=str(e))
        except ValueError as e:rec['header_error']=str(e)
        fields={}
        for field in ['reason','termination','pid','captureTime','date','exception','memoryStatus','bootSessionUUID','largestProcess','pageSize','zoneMapSize','zoneMapCap','largestZone','largestZoneSize']:
            if isinstance(body,dict) and field not in body:continue
            m=re.search(r'"'+field+r'"\s*:\s*',s)
            if not m:continue
            try:
                value,end=DEC.raw_decode(s,m.end())
                fields[field]={'value':value,'line':s.count('\n',0,m.start())+1,'complete_value':True,'scope':'body top-level' if isinstance(body,dict) else 'first occurrence in retained prefix, whole body incomplete'}
            except ValueError as e:fields[field]={'error':str(e),'complete_value':False}
        rec['fields']=fields
        if isinstance(body,dict) and isinstance(body.get('processes'),list):
            page=body.get('pageSize',body.get('memoryStatus',{}).get('pageSize',4096))
            ps=[]
            for proc in body['processes']:
                if proc.get('name') in ['SpringBoard','backboardd','runningboardd','lsd','audiomxd','PosterBoard','AccessibilityUIServer','usermanagerd'] or proc.get('reason'):
                    item=dict(proc)
                    if 'rpages' in item:item['resident_mib']=item['rpages']*page/1048576
                    if 'lifetimeMax' in item:item['lifetimeMax_mib']=item['lifetimeMax']*page/1048576
                    ps.append(item)
            rec['selected_processes']=ps
        result.append(rec)
    return result

def analyze(root,baseline):
    rr=rows(root)
    ram=[r for r in rr if 'Pong RAM:' in r['text'] or 'maxmem' in r['text'] or 'hw.memsize' in r['text']]
    surface=[]
    for r in rr:
        if 'MiniIOSurface:' not in r['text']:continue
        item=dict(r)
        m=re.search(r'live (\d+) MB in (\d+) surfaces(?:, kept pool (\d+) MB)? \((release )?crossed (\d+) x 64MB\)',r['text'])
        if m:item['counter']={'live_mib_floor':int(m[1]),'surfaces':int(m[2]),'pool_mib_floor':int(m[3]) if m[3] else None,'side':'release' if m[4] else 'create','bucket':int(m[5])}
        m=re.search(r'released (\d+) kept pieces \((\d+) MiB pool now\) under pressure',r['text'])
        if m:item['release_counter']={'pieces':int(m[1]),'pool_after_mib_floor':int(m[2])}
        surface.append(item)
    ls=[r for r in rr if 'lsd' in r['text'] or any(k in r['text'] for k in ['LS_DATABASE_DIR','LaunchServices','memory limit','memlimit'])]
    pressure=[r for r in rr if any(k in r['text'] for k in ['Pressure level has','compressor_is_thrashing','enforcing soft memory limits'])]
    memory=[r for r in rr if 'memorystatus_available_pages:' in r['text']]
    migration=[]
    files=root/'extracted/files'
    for p in sorted(files.rglob('*.plist')):
        if not any(k in p.name.lower() for k in ('migration','datamigrator','setup','lockdown')):continue
        try:value=plistlib.loads(p.read_bytes())
        except Exception as e:value={'parse_error':str(e)}
        bp=baseline/'extracted/files'/p.relative_to(files)
        migration.append({'file':str(p.relative_to(files)),'data':value,'bytes_identical_to_baseline':bp.is_file() and p.read_bytes()==bp.read_bytes()})
    lock=[]
    for p in sorted((files/'logs').glob('*lockdown*')):
        for n,s in enumerate(p.read_text(errors='replace').splitlines(),1):
            if any(k in s for k in ['migrationDidFinish','Data Migration Completed','migration fail']):lock.append({'file':str(p.relative_to(files)),'line':n,'text':s})
    dm=[]
    for p in sorted(files.rglob('PongDM-*.txt')):
        lines=p.read_text(errors='replace').splitlines()
        dm.append({'file':str(p.relative_to(files)),'lines':[{'line':n,'text':s} for n,s in enumerate(lines,1)],'diagnostics_count':sum('diagnostics incident=' in s for s in lines)})
    return {'ram_receipts':ram,'surface_receipts':surface,'ls_receipts':ls,'pressure_receipts':pressure,'last_memory_samples':memory[-10:],'native_reports':native(root,baseline),'migration_plists':migration,'migration_notifications':lock,'dm_receipts':dm}

d={'current':analyze(ROOT,BASE),'baseline_v75':analyze(BASE,ROOT),'pool_counter_source_caveat':'v76 full-pool replacement subtracts evicted capacity without adding new capacity; later subtraction may underflow uint64. Literal kept pool values are not reliable occupancy until this accounting defect is fixed; actual release iterates slots independently.', 'method':'Exact byte-line groups with all physical positions retained. Six-digit raw timestamps converted only. IPS bodies never repaired; complete subobjects individually decoded with explicit scope. Source code pool release trigger is allocation failure, despite literal under pressure log.'}
(OUT/'memory-supplement-v76.json').write_text(json.dumps(d,ensure_ascii=False,indent=2,default=str)+'\n')
print(json.dumps({'ram':d['current']['ram_receipts'],'surface':d['current']['surface_receipts'],'native_report_count':len(d['current']['native_reports']),'migration_notifications':d['current']['migration_notifications']},ensure_ascii=False,indent=2))
