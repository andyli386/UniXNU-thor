#!/usr/bin/env python3
from pathlib import Path
import collections, json, re, sys

root, base = map(Path, sys.argv[1:3])
out = root / 'analysis'
DEC = json.JSONDecoder()

def records(r):
    f = r / 'extracted/files/logs/PongConsole.txt'
    raw = f.read_bytes()
    groups = collections.OrderedDict()
    for n, line in enumerate(raw.splitlines(), 1):
        if not line.strip(b'\x00 \t'):
            continue
        groups.setdefault(line, []).append(n)
    rows = []
    for line, locations in groups.items():
        text = line.decode(errors='replace')
        row = {'physical_lines': locations, 'text': text}
        m = re.match(r'^\[\s*(\d+)\.(\d+)\]:', text)
        if m:
            row['raw_literal'] = m[1] + '.' + m[2]
            row['time_us'] = int(m[1]) * 1000000 + int(m[2]) if len(m[2]) == 6 else None
        rows.append(row)
    return rows

def analyze(r):
    rows = records(r)
    surface = []
    for row in rows:
        if 'MiniIOSurface:' not in row['text']:
            continue
        item = dict(row)
        m = re.search(r'MiniIOSurface: live (\d+) MB in (\d+) surfaces \((release )?crossed (\d+) x 64MB\)', row['text'])
        if m:
            item['counter'] = {'live_mib_floor': int(m[1]), 'surface_count': int(m[2]),
                               'side': 'release' if m[3] else 'create', 'bucket': int(m[4])}
        surface.append(item)
    pressure = [x for x in rows if 'Pressure level has' in x['text'] or
                'compressor_is_thrashing' in x['text'] or 'enforcing soft memory limits' in x['text']]
    last_samples = [x for x in rows if 'memorystatus_available_pages:' in x['text']]
    native = []
    for f in sorted((r / 'extracted/files').rglob('*.ips')):
        text = f.read_text(errors='replace')
        record = {'file':str(f.relative_to(r / 'extracted/files')), 'bytes':f.stat().st_size}
        complete_body = None
        try:
            h,pos = DEC.raw_decode(text)
            record['header'] = h
            pos = len(text) - len(text[pos:].lstrip())
            try:
                complete_body,end = DEC.raw_decode(text, pos)
                record['complete_body'] = True
            except ValueError as e:
                record['complete_body'] = False
                record['body_error'] = str(e)
        except ValueError as e:
            record['header_error'] = str(e)
        record['fields'] = {}
        for field in ['reason','termination','pid','captureTime','date','exception','memoryStatus','bootSessionUUID']:
            if isinstance(complete_body, dict) and field not in complete_body:
                continue
            m = re.search(r'"' + field + r'"\s*:\s*',text)
            if not m:
                continue
            try:
                value,end = DEC.raw_decode(text,m.end())
                record['fields'][field] = {'value':value,'line':text.count('\n',0,m.start())+1,
                    'start_char':m.start(),'value_end_char':end,'complete_value':True}
                record['fields'][field]['scope'] = 'top-level body key' if isinstance(complete_body,dict) else 'first occurrence in retained body prefix; whole body incomplete'
            except ValueError as e:
                record['fields'][field] = {'error':str(e),'complete_value':False}
        native.append(record)
    return {'surface_receipts': surface, 'pressure_receipts': pressure,
            'last_memory_samples':last_samples[-10:], 'native_reports':native}

d = {'current':analyze(root), 'baseline_v74':analyze(base),
     'surface_source_semantics':{
        'unit':'MB in literal log is integer MiB: gSurfaceBytes >> 20',
        'scope':'Sum of alloc sizes for surface table entries, not full system wired and not allocation owner accounting.',
        'emission':'create/release sites maintain separate static lastMark counters for gSurfaceBytes >> 26.',
        'exclusions':['per-surface state pages','object and mapping overhead','retained backing pool after table removal'],
        'retained_pool_bound':'4 entries, each 4–32 MiB, at most 128 MiB backing capacity; observed occupancy is not recorded. No pressure-triggered reclamation path identified in current source.',
        'ambiguity':'Wrapped client memory is included in alloc total; overlapping client backing can be counted more than once.',
        'absence':'No retained counter receipt is not evidence of zero IOSurface use; a missing or sub-threshold receipt is possible.'},
     'parsing':'Exact byte-line dedup with every physical line retained. Six-digit timestamps only converted. Complete individual JSON field values decoded from truncated IPS without repairing source.'}
for report in d['current']['native_reports']:
    cf=root/'extracted/files'/report['file']; bf=base/'extracted/files'/report['file']
    report['same_path_in_baseline']=bf.is_file()
    report['bytes_identical_to_baseline']=bf.is_file() and cf.read_bytes()==bf.read_bytes()
(out/'memory-supplement-v75.json').write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'surface_receipts':d['current']['surface_receipts'],
                  'pressure_receipts':len(d['current']['pressure_receipts']),
                  'native_reports':[{k:v for k,v in x.items() if k!='fields'} for x in d['current']['native_reports']]},indent=2))
