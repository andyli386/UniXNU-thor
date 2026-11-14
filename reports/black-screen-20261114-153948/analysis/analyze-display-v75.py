from pathlib import Path
import collections, json, re, sys

root=Path(sys.argv[1]); baseline=Path(sys.argv[2])

def parse(r):
    p=r/'extracted/files/logs/PongConsole.txt'
    raw=p.read_bytes(); physical=raw.splitlines(keepends=True)
    grouped=collections.OrderedDict()
    for n,b in enumerate(physical,1):
        if not b.strip(b'\x00\r\n\t '): continue
        # Exact bytes dedup only. Keep NUL-bearing and unterminated records.
        b=b.rstrip(b'\r\n'); grouped.setdefault(b,[]).append(n)
    rows=[]
    for b,ns in grouped.items():
        s=b.decode('utf-8',errors='replace')
        m=re.match(r'^\[\s*(\d+)\.(\d+)\]:\s*',s)
        us=int(m[1])*1000000+int(m[2]) if m and len(m[2])==6 else None
        rows.append({'line':ns[0], 'physical_lines':ns,'copies':len(ns),
                     'raw':s,'time_literal':m[1]+'.'+m[2] if m else None,'us':us})
    def pick(pat):return [x for x in rows if re.search(pat,x['raw'])]
    can=[x for x in rows if x['us'] is not None]
    transitions=pick(r'MiniIOMFB layers (EMPTIED|RESTORED|CHANGED):')
    for x in transitions:
        m=re.search(r'layers (EMPTIED|RESTORED|CHANGED): submitter=(.*?) pid=(-?\d+) set=(0x[0-9a-fA-F]+) surfaces=(\d+),(\d+),(\d+),(\d+) gpu=(\d+) graphics=(\d+) swap=(\d+)',x['raw'])
        if m:x['fields']={'state':m[1],'submitter':m[2],'pid':int(m[3]),'set':m[4], 'surfaces':list(map(int,m.group(5,6,7,8))), 'gpu':int(m[9]),'graphics':int(m[10]),'swap':int(m[11])}
    exits=pick(r'PONG EXIT ')
    for x in exits:
        m=re.search(r'PONG EXIT (.*?) pid=(\d+) signal=(-?\d+) status=(-?\d+) namespace=(\d+) code=(0x[0-9a-fA-F]+) description=(.*)',x['raw'])
        if m:x['fields']={'process':m[1],'pid':int(m[2]),'signal_field':int(m[3]),'status_field':int(m[4]),'namespace':int(m[5]),'code':m[6],'description':m[7]}
        start=x['line']-1
        x['context']=[{'line':i+1,'raw':physical[i].decode(errors='replace').rstrip('\r\n')} for i in range(max(0,start-1),min(len(physical),start+7))]
    receipts=[]
    for p in sorted((r/'extracted/files').rglob('Pong*.txt')):
        if not p.name.startswith(('PongSB-','PongBBD-','PongDM-')):continue
        text=p.read_text(errors='replace')
        receipts.append({'file':p.relative_to(r).as_posix(),'bytes':p.stat().st_size,'lines':[{'line':i,'text':s} for i,s in enumerate(text.splitlines(),1)]})
    last=max(can,key=lambda x:x['us']) if can else None
    clears=[x for x in transitions if x.get('fields',{}).get('state')=='EMPTIED' and x['us'] is not None]
    post=[]
    for x in clears:
        a=[y for y in can if y['us']>x['us']]
        post.append({'clear_line':x['line'],'clear_us':x['us'],'last_us':last['us'],
            'seconds_to_last':(last['us']-x['us'])/1e6,'canonical_records_after':len(a),
            'restored_after':[y for y in transitions if y.get('fields',{}).get('state')=='RESTORED' and y['us'] is not None and y['us']>x['us']]})
    summary={'root':str(r),'console_bytes':len(raw),'physical_lines':len(physical),'nul_bytes':raw.count(b'\x00'),
        'unique_nonempty':len(rows),'duplicate_nonempty_lines':sum(len(v)-1 for v in grouped.values()),
        'noncanonical_timestamp_types':sum(x['time_literal'] is not None and x['us'] is None for x in rows),
        'wrap_physical_lines':sum(b'WRAP' in b and b'lost' in b.lower() for b in physical),
        'method':'Exact bytes dedup; original indices retained; only six fractional digits converted; NUL bytes retained; no early truncation.',
        'ram':pick(r'Pong RAM:'),'first_visible':pick(r'first visible frame'),'transitions':transitions,
        'maximum_canonical_time':last,'post_clear':post,'exits':exits,
        'mounts':pick(r'PONG APFS:|THOR INJECT: armed'), 'iosurface':pick(r'MiniIOSurface: live'),
        'ui_console':pick(r'Pong UI(?:18)?:|Pong SB stat:|Pong BBD: group_wait'), 'receipts':receipts,
        'last_frame_stats':pick(r'MiniIOMobileFramebuffer: \d+ frames in')[-3:]}
    return summary,rows

new,rows=parse(root);old,_=parse(baseline)
comparison={'baseline':'v74 capture 20261114-103139','new':'capture 20261114-153948','caveat':'Two distinct boots, not a controlled A/B experiment.'}
for label,d in [('current',new),('baseline',old)]:
    comparison[label]={k:d[k] for k in ['console_bytes','ram','first_visible','transitions','maximum_canonical_time','post_clear']}
(root/'analysis/display-runtime-v75.json').write_text(json.dumps(new,ensure_ascii=False,indent=2)+'\n')
(root/'analysis/display-comparison-v74.json').write_text(json.dumps(comparison,ensure_ascii=False,indent=2)+'\n')
(root/'PongConsole-deduplicated.txt').write_text('Exact nonempty original byte-line groups; first physical line and copies shown. NUL stripped only in this readable rendering. All bytes retained in original.\n'+''.join(f"[source L{x['line']}; copies={x['copies']}] {x['raw'].replace(chr(0),'')}\n" for x in rows))
brief={k:new[k] for k in ['console_bytes','physical_lines','unique_nonempty','nul_bytes','noncanonical_timestamp_types','first_visible','transitions','post_clear','maximum_canonical_time']}
brief['exit_counts']=dict(collections.Counter(x.get('fields',{}).get('process','unparsed') for x in new['exits']))
brief['new_receipt_files']=[{'file':x['file'],'bytes':x['bytes'],'line_count':len(x['lines'])} for x in new['receipts']]
brief['iosurface_samples']=len(new['iosurface'])
(root/'analysis/display-summary-v75.json').write_text(json.dumps(brief,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(brief,ensure_ascii=False,indent=2))
