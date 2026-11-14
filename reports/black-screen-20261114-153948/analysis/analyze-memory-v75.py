#!/usr/bin/env python3
"""Read-only analysis of exported logs; keep literal timestamps and replay dedup."""
from pathlib import Path
import collections
import json
import re

import sys, plistlib
ROOT = Path(sys.argv[1]).resolve()
BASE = Path(sys.argv[2]).resolve()
OUT = Path(sys.argv[3]).resolve()
OUT.mkdir(parents=True, exist_ok=True)
DEC = json.JSONDecoder()

def parse(root):
    p = root / 'extracted/files/logs/PongConsole.txt'
    raw = p.read_bytes()
    seen = set()
    unique = []
    malformed = []
    lines = raw.decode(errors='replace').splitlines()
    for number, line in enumerate(lines, 1):
        if line in seen or not line.strip('\x00 \t'):
            continue
        seen.add(line)
        m = re.match(r'\[\s*(\d+)\.(\d+)\]:', line)
        item = {'line': number, 'text': line}
        if m:
            item['raw_literal'] = m.group(1) + '.' + m.group(2)
            item['raw_second'] = int(m.group(1))
            item['timestamp_standard'] = len(m.group(2)) == 6
            item['raw'] = float(item['raw_literal']) if item['timestamp_standard'] else None
            if not item['timestamp_standard']:
                malformed.append(item)
        unique.append(item)
    timed = [x for x in unique if x.get('raw') is not None]
    ram = next(x for x in timed if 'Pong RAM:' in x['text'])
    for x in unique:
        if x.get('raw') is not None:
            x['since_ram'] = x['raw'] - ram['raw']
    kills = []
    samples = []
    for x in unique:
        m = re.search(r'memorystatus: (killing\w*) pid (\d+) \[([^\]]+)\] \(([^ )]+)', x['text'])
        if m:
            kills.append({**x, 'function': m.group(1), 'pid': int(m.group(2)),
                          'name': m.group(3), 'reason': m.group(4)})
        m = re.search(r'memorystatus_available_pages:\s*(\d+)\s+compressor_size:\s*(\d+)', x['text'])
        if m:
            samples.append({**x, 'available_pages': int(m.group(1)),
                            'compressor_pages': int(m.group(2)),
                            'available_mib': int(m.group(1)) / 256,
                            'compressor_mib': int(m.group(2)) / 256})
    native = []
    for p in sorted((root / 'extracted/files').rglob('*.ips')):
        text = p.read_text(errors='replace')
        record = {'file': str(p.relative_to(root / 'extracted/files')), 'bytes': p.stat().st_size}
        try:
            record['header'], pos = DEC.raw_decode(text)
            record['header_chars'] = pos
        except ValueError as e:
            record['header_error'] = str(e)
        m = re.search(r'"memoryStatus"\s*:\s*', text)
        if m:
            try:
                mem, _ = DEC.raw_decode(text[m.end():])
                record['memoryStatus'] = mem
                page = mem.get('pageSize', 4096)
                pages = mem.get('memoryPages', {})
                record['mib'] = {k: v * page / 1048576 for k, v in pages.items() if isinstance(v, (int, float))}
                record['compressor_mib'] = mem.get('compressorSize', 0) * page / 1048576
                record['active_plus_inactive_mib'] = (pages.get('active', 0) + pages.get('inactive', 0)) * page / 1048576
            except ValueError as e:
                record['memory_error'] = str(e)
        native.append(record)
    diag = (root / 'extracted/files/logs/PongDiag.txt').read_text(errors='replace').splitlines()
    windows = []
    for i, s in enumerate(diag, 1):
        m = re.search(r'^\[(\d+)\] Pong cache pre-read: .*?read=(\d+) of (\d+) bytes ms=(\d+) (.*)', s)
        if m:
            windows.append({'line': i, 'epoch': int(m.group(1)), 'bytes': int(m.group(2)),
                            'target': int(m.group(3)), 'ms': int(m.group(4)), 'text': s})
    diag_times = [int(m.group(1)) for s in diag if (m := re.match(r'^\[(\d+)\]', s))]
    clears = [x for x in unique if 'layers EMPTIED:' in x['text']]
    if not clears:
        clears = [x for x in unique if 'surfaces in layers 0x0 now' in x['text']]
    return {'root': str(root), 'console_bytes': len(raw), 'console_lines': len(lines),
            'unique_nonempty': len(unique), 'literal_duplicate_lines': sum(c-1 for line,c in collections.Counter(lines).items() if line.strip('\x00 \t')),
            'ram': ram, 'modes': [x for x in unique if 'vm_compressor_mode' in x['text'] or 'VM swap disabled' in x['text']],
            'raw_bounds': [min(x['raw'] for x in timed), max(x['raw'] for x in timed)],
            'raw_duration_since_ram': max(x['raw'] for x in timed) - ram['raw'],
            'last_timed': max(timed, key=lambda x: x['raw']), 'malformed_timestamps': malformed,
            'kill_events': kills, 'kill_counts': dict(collections.Counter(x['reason'] for x in kills)),
            'kernel_memory_samples': samples,
            'first_softlimit': next((x for x in samples if 'enforcing soft memory limits' in x['text']), None),
            'clear_events': clears,
            'native_reports': native, 'diag_epoch_bounds': [min(diag_times), max(diag_times)],
            'prefetch': {'windows': windows, 'count': len(windows), 'mib': sum(x['bytes'] for x in windows) / 1048576,
                         'read_ms_total': sum(x['ms'] for x in windows),
                         'epoch_bounds': [min(x['epoch'] for x in windows), max(x['epoch'] for x in windows)] if windows else [],
                         'deferred_lines': [s for s in diag if 'pre-read' in s and ('defer' in s.lower() or 'gate-fail' in s.lower())],
                         'clamped_complete_lines': [s for s in diag if 'pre-read' in s and 'clamped to EOF complete gate-ok' in s]},
            'audio_memory_kills': [x for x in kills if x['name'] in ['audiomxd', 'mediaremoted', 'SpringBoard', 'backboardd']],
            'audio_console_lines': [x for x in unique if 'audiomxd' in x['text']],
            'apfs_after_clear': [x for x in timed if clears and 'tx_flush:' in x['text'] and x['raw'] > clears[0]['raw']]}


def process_state(root):
    files=root/'extracted/files'
    snapshots={}
    for f in sorted(files.rglob('PongBoot-*.txt')):
        records=[]; ticks=[]; image=[]
        for n,line in enumerate(f.read_text(errors='replace').splitlines(),1):
            m=re.match(r'Pong boot snapshot BEGIN: (.+?) pid=(\d+) seconds=(\d+) time=(\d+) threads=(\d+)',line)
            if m: records.append({'line':n,'name':m[1],'pid':int(m[2]),'seconds':int(m[3]),'epoch':int(m[4]),'threads':int(m[5]),'text':line})
            m=re.match(r'Pong tick: (.+?) pid=(\d+) seconds=(\d+) threads=(\d+)',line)
            if m: ticks.append({'line':n,'name':m[1],'pid':int(m[2]),'seconds':int(m[3]),'threads':int(m[4]),'text':line})
            if 'image0=' in line and line not in image:image.append(line)
        snapshots[str(f.relative_to(files))]={'snapshots':records,'ticks':ticks,'image_lines':image,'observed_pids':sorted(set(x['pid'] for x in records+ticks))}
    diag=(files/'logs/PongDiag.txt').read_text(errors='replace').splitlines()
    watches={}; exits=[]
    for n,line in enumerate(diag,1):
        m=re.match(r'\[(\d+)\] Pong watch: (.*?) (\(none\)|\[[^\]]*\]) -> (\[[^\]]*\])',line)
        if m:
            watches.setdefault(m[2],[]).append({'line':n,'epoch':int(m[1]),'before':m[3],'pids':[int(x) for x in re.findall(r'\d+',m[4])],'text':line})
        m=re.match(r'\[(\d+)\] Pong watch: pid (\d+) exited data=(0x[0-9a-f]+)',line)
        if m: exits.append({'line':n,'epoch':int(m[1]),'pid':int(m[2]),'data_literal':m[3],'text':line})
    migration=[]
    for f in sorted(files.rglob('*.plist')):
        if f.name not in ['com.apple.migration.plist','com.apple.springboard.datamigrator.plist','com.apple.datamigrator.plist','com.apple.mobile.lockdown.plist']:continue
        try: data=plistlib.loads(f.read_bytes())
        except Exception as e:data={'parse_error':str(e)}
        migration.append({'file':str(f.relative_to(files)),'data':data})
    new_receipts=[]
    for f in sorted(files.rglob('PongDM-*.txt'))+sorted(files.rglob('PongSB-*.txt')):
        new_receipts.append({'file':str(f.relative_to(files)),'lines':[{'line':n,'text':s} for n,s in enumerate(f.read_text(errors='replace').splitlines(),1)]})
    return {'snapshots':snapshots,'watches':watches,'watch_exits':exits,'migration_plists':migration,'dm_sb_receipts':new_receipts}

def extra_console(root):
    f=root/'extracted/files/logs/PongConsole.txt'
    raw=f.read_bytes(); seen=set(); unique=[]; duplicate_count=0; empty_count=0
    for n,s in enumerate(raw.decode(errors='replace').splitlines(),1):
        if not s.strip('\x00 \t'):empty_count+=1;continue
        if s in seen:duplicate_count+=1;continue
        seen.add(s);unique.append({'line':n,'text':s})
    return {'nul_bytes':raw.count(b'\x00'),'first_nul_byte':raw.find(b'\x00'),'exact_duplicate_lines':duplicate_count,'empty_nul_lines':empty_count,
        'wrap_lines':[x for x in unique if 'WRAP' in x['text']],
        'exit_receipts':[x for x in unique if 'PONG EXIT ' in x['text']],
        'crash_receipts':[x for x in unique if any(k in x['text'] for k in ['Corpse failure','Corpse allowed','crashed','panic('])],
        'migration_receipts':[x for x in unique if any(k in x['text'] for k in ['migrationDidFinish','Completed =','Completed=','DMLastMigrationResults','migration complete'])]}

def equal_counts(data,duration):
    cutoff=data['ram']['raw']+duration
    selected=[x for x in data['kill_events'] if x.get('raw') is not None and data['ram']['raw']<=x['raw']<=cutoff]
    ambiguous=[x for x in data['kill_events'] if x.get('raw') is None]
    inside=[x for x in ambiguous if data['ram']['raw']+2<=x.get('raw_second',-1)<=cutoff-2]
    outside=[x for x in ambiguous if x.get('raw_second',-1)>cutoff+2 or x.get('raw_second',-1)<data['ram']['raw']-2]
    boundary=[x for x in ambiguous if x not in inside and x not in outside]
    selected+=inside
    return {'ram_anchor_raw':data['ram']['raw'],'cutoff_raw':cutoff,'kills':len(selected),'reason_counts':dict(collections.Counter(x['reason'] for x in selected)),
       'nonstandard_inside':len(inside),'nonstandard_outside':len(outside),'boundary_ambiguous':boundary,'key_process_kills':[x for x in selected if x['name'] in ['audiomxd','mediaremoted','SpringBoard','backboardd','runningboardd','PosterBoard','datamigrator','com.apple.datamig']]}

current=parse(ROOT); baseline=parse(BASE)
current['process_state']=process_state(ROOT)
current['extra_console']=extra_console(ROOT)
baseline['process_state']=process_state(BASE)
duration=min(current['raw_duration_since_ram'],baseline['raw_duration_since_ram'])
current['equal_window']={'duration_since_ram':duration,'current':equal_counts(current,duration),'v74_baseline':equal_counts(baseline,duration),'causal_ab_test':False}
current['baseline_summary']={k:baseline[k] for k in ['root','ram','raw_bounds','raw_duration_since_ram','kill_counts','prefetch','clear_events','process_state']}
current['method']='Exact whole Console line dedup; literal nonstandard fractions retained. Kernel raw, userland epoch, and native report clocks remain distinct. Equal windows anchored at each RAM marker; nonstandard fractions included only safely inside. Individually decode memoryStatus subobjects without repairing truncated IPS. PID stability is only sampled liveness, not functional health. No new benchmark or device writes.'
(OUT/'memory-current.json').write_text(json.dumps(current,indent=2,ensure_ascii=False,default=str)+'\n')
brief={k:current[k] for k in ['console_bytes','console_lines','unique_nonempty','ram','raw_bounds','raw_duration_since_ram','kill_counts','equal_window','audio_memory_kills','first_softlimit']}
brief['malformed_timestamp_count']=len(current['malformed_timestamps'])
brief['prefetch']={k:v for k,v in current['prefetch'].items() if k!='windows'}
brief['console_quality']={k:current['extra_console'][k] for k in ['nul_bytes','first_nul_byte','exact_duplicate_lines','empty_nul_lines']}
brief['console_quality']['wrap_distinct_count']=len(current['extra_console']['wrap_lines'])
brief['native_memory']=[{k:v for k,v in x.items() if k in ['file','bytes','header','header_error','memoryStatus','memory_error','mib','compressor_mib','active_plus_inactive_mib']} for x in current['native_reports']]
brief['process_snapshots']={k:{'observed_pids':v['observed_pids'],'snapshots':v['snapshots']} for k,v in current['process_state']['snapshots'].items()}
brief['watch_summary']={k:{'count':len(v),'observed_pids':sorted(set(pid for x in v for pid in x['pids'])),'first':v[0],'last':v[-1]} for k,v in current['process_state']['watches'].items()}
brief['migration_plists']=current['process_state']['migration_plists']
brief['migration_receipts']=current['extra_console']['migration_receipts']
brief['pong_exit_receipts']=current['extra_console']['exit_receipts']
(OUT/'memory-summary-current.json').write_text(json.dumps(brief,indent=2,ensure_ascii=False,default=str)+'\n')
print(json.dumps({k:brief[k] for k in ['console_bytes','raw_bounds','kill_counts','equal_window','console_quality','watch_summary','migration_plists']},ensure_ascii=False,indent=2,default=str))

# Additional source-preserving receipts and completeness checks.
files=ROOT/'extracted/files'
raw_lines=(files/'logs/PongConsole.txt').read_text(errors='replace').splitlines()
current['extra_console']['wrap_physical_lines']=[{'line':i,'text':s} for i,s in enumerate(raw_lines,1) if 'WRAP' in s]
receipts=[]
lockdown=files/'logs/lockdownd.log'
if lockdown.is_file():
    for i,s in enumerate(lockdown.read_text(errors='replace').splitlines(),1):
        if 'migrationDidFinish' in s or 'Data Migration Completed:' in s:receipts.append({'file':'logs/lockdownd.log','line':i,'text':s})
current['process_state']['lockdown_migration_receipts']=receipts
manifest=json.loads((ROOT/'extracted/manifest.json').read_text())
current['process_state']['setup_sentinel']=manifest.get('state',{}).get('db/.AppleSetupDone')
validity=[]
for f in sorted(files.rglob('*.ips')):
    t=f.read_text(errors='replace');rec={'file':str(f.relative_to(files))}
    try:
        h,pos=DEC.raw_decode(t);rec['header_complete']=True
        pos=len(t)-len(t[pos:].lstrip())
        try:body,end=DEC.raw_decode(t,pos);rec.update(body_complete=True,body_end=end)
        except ValueError as e:rec.update(body_complete=False,body_error=str(e))
    except ValueError as e:rec.update(header_complete=False,header_error=str(e))
    validity.append(rec)
current['native_body_validity']=validity
counts=collections.Counter()
for x in current['extra_console']['exit_receipts']:
    m=re.search(r'PONG EXIT (\S+) pid=(\d+) signal=(\d+) status=(\d+) namespace=(\d+)',x['text'])
    if m:counts[(m[1],m[3],m[4],m[5])]+=1
current['exit_receipt_counts']=[{'name':x[0],'signal_literal':x[1],'status_literal':x[2],'namespace_literal':x[3],'count':c} for x,c in counts.items()]
brief['console_quality']['wrap_physical_count']=len(current['extra_console']['wrap_physical_lines'])
brief['lockdown_migration_receipts']=receipts
brief['setup_sentinel']=current['process_state']['setup_sentinel']
brief['exit_receipt_counts']=current['exit_receipt_counts']
brief['native_body_validity']=validity
(OUT/'native-memory-body-validity.json').write_text(json.dumps(validity,ensure_ascii=False,indent=2)+'\n')
(OUT/'memory-current.json').write_text(json.dumps(current,indent=2,ensure_ascii=False,default=str)+'\n')
(OUT/'memory-summary-current.json').write_text(json.dumps(brief,indent=2,ensure_ascii=False,default=str)+'\n')
