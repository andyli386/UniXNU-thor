from pathlib import Path
import collections, datetime, json, re, sys

root=Path(sys.argv[1])
baseline=Path(sys.argv[2])
console=root/'extracted/files/logs/PongConsole.txt'
raw=console.read_bytes()
physical=raw.splitlines(keepends=True)
groups=collections.OrderedDict()
for n,line in enumerate(physical,1):
    if not line.endswith(b'\n'): continue
    line=line.rstrip(b'\r\n')
    if not line or b'\x00' in line: continue
    groups.setdefault(line,[]).append(n)

def entry(b):
    s=b.decode('utf-8',errors='replace')
    m=re.match(r'^\[\s*(\d+\.\d+)\]:\s*',s)
    ts=m[1] if m else None
    us=None
    if ts and re.fullmatch(r'\d+\.\d{6}',ts):
        sec,frac=ts.split('.')
        us=int(sec)*1000000+int(frac)
    return {'console_time_raw':ts,'console_time_us':us,'physical_lines':groups[b],
            'copies':len(groups[b]),'raw_line':s}

entries=[entry(b) for b in groups]
def select(pattern):
    p=re.compile(pattern)
    return [e for e in entries if p.search(e['raw_line'])]

canonical=[e for e in entries if e['console_time_us'] is not None]
transitions=select(r'MiniIOMFB layers (?:EMPTIED|RESTORED|CHANGED):')
for e in transitions:
    m=re.search(r'layers (EMPTIED|RESTORED|CHANGED): submitter=(.*?) pid=(-?\d+) set=(0x[0-9a-fA-F]+) surfaces=(\d+),(\d+),(\d+),(\d+) gpu=(\d+) graphics=(\d+) swap=(\d+)',e['raw_line'])
    if m:e['parsed']={'kind':m[1],'submitter':m[2],'pid':int(m[3]),'set_hex':m[4],
                      'surface_ids':list(map(int,m.group(5,6,7,8))),
                      'gpu':int(m[9]),'graphics':int(m[10]),'swap':int(m[11])}

exit_entries=select(r'PONG EXIT ')
for e in exit_entries:
    m=re.search(r'PONG EXIT (.*?) pid=(\d+) signal=(-?\d+) status=(-?\d+) namespace=(\d+) code=(0x[0-9a-fA-F]+) description=(.*)',e['raw_line'])
    if m:e['parsed']={'process':m[1],'pid':int(m[2]),'signal_field':int(m[3]),
                     'status_field':int(m[4]),'namespace':int(m[5]),'code_hex':m[6],
                     'description_first_line':m[7]}
    start=e['physical_lines'][0]
    e['first_occurrence_context']=[{'physical_line':i+1,'text':physical[i].decode('utf-8',errors='replace').rstrip('\r\n')}
                                   for i in range(max(0,start-2),min(len(physical),start+8))]

sbfiles=[]
for p in sorted((root/'extracted/files').rglob('PongSB-*.txt')):
    b=p.read_bytes();s=b.decode('utf-8',errors='replace')
    sbfiles.append({'path':p.relative_to(root).as_posix(),'bytes':len(b),'content':s,
                    'ctor_recorded':'SpringBoard adapter ctor' in s,
                    'scheduled_recorded':'wake scheduled' in s,
                    'enqueue_returned_recorded':'wake enqueue returned' in s,
                    'wake_begin_recorded':'wake_home begin' in s,
                    'wake_end_recorded':'wake_home end' in s,
                    'clock':'time(NULL), separate from Console raw clock'})

ram=select(r'Pong RAM:')
firstframes=select(r'MiniIOMobileFramebuffer: first visible frame')
clear=[e for e in transitions if e.get('parsed',{}).get('kind')=='EMPTIED']
restored=[e for e in transitions if e.get('parsed',{}).get('kind')=='RESTORED']
lastphysical=max(canonical,key=lambda e:e['physical_lines'][-1]) if canonical else None
lastmax=max(canonical,key=lambda e:e['console_time_us']) if canonical else None
laststats=select(r'MiniIOMobileFramebuffer: \d+ frames in')
post=[]
for e in clear:
    ts=e['console_time_us']
    if ts is None: continue
    activity=[x for x in canonical if x['console_time_us']>ts]
    post.append({'emptied':e,'canonical_activity_count_after':len(activity),
                 'last_activity':max(activity,key=lambda x:x['console_time_us']) if activity else None,
                 'seconds_to_last_activity':(max(x['console_time_us'] for x in activity)-ts)/1e6 if activity else 0,
                 'restored_after':[x for x in restored if x['console_time_us'] is not None and x['console_time_us']>ts]})

old=json.loads((baseline/'analysis/v73-display-runtime.json').read_text())
comparison={'baseline':'v73 capture 20261114-025335','baseline_first_visible':old['first_visible_frame'],
            'baseline_emptied':old['emptied_unique'],'baseline_postclear_activity_seconds':old['post_clear_console_activity_seconds'],
            'baseline_sb_receipts':old['springboard_receipts']}
if len(ram)==len(firstframes)==len(clear)==1 and all(x['console_time_us'] is not None for x in [ram[0],firstframes[0],clear[0]]):
    rt=ram[0]['console_time_us'];ft=firstframes[0]['console_time_us'];ct=clear[0]['console_time_us']
    comparison.update(current_ram_to_first_visible_seconds=(ft-rt)/1e6,
                      current_ram_to_clear_seconds=(ct-rt)/1e6,
                      first_visible_delta_raw_seconds=(ft-old['first_visible_frame']['console_time_us'])/1e6,
                      clear_delta_raw_seconds=(ct-old['emptied_unique'][0]['console_time_us'])/1e6,
                      first_visible_delta_ram_relative_seconds=(ft-rt)/1e6-old['ram_to_first_visible_seconds'],
                      clear_delta_ram_relative_seconds=(ct-rt)/1e6-old['ram_to_clear_seconds'])

report={'created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'scope':'Read-only analysis of extracted saved logs; no phone operations or source modifications.',
        'console':{'path':str(console),'bytes':len(raw),'physical_lines':len(physical),'nul_bytes':raw.count(b'\x00'),
                   'ends_in_nul':raw.endswith(b'\x00'),'unique_complete_lines':len(groups),
                   'canonical_unique_timestamp_lines':len(canonical),
                   'duplicate_full_nonempty_line_types':sum(len(v)>1 for v in groups.values()),
                   'additional_duplicate_full_nonempty_lines':sum(len(v)-1 for v in groups.values()),
                   'wrap_markers_physical':sum(b'WRAP' in x and b'lost' in x.lower() for x in physical),
                   'noncanonical_timestamp_line_types':sum(e['console_time_raw'] is not None and e['console_time_us'] is None for e in entries)},
        'deduplication':'Exact complete original byte lines grouped, every physical line position retained. Only exactly six fractional digits converted to integer microseconds. Missing lines are not interpreted as non-execution.',
        'ram_markers':ram,'first_visible_frames':firstframes,'layer_transitions':transitions,
        'legacy_layer_changes':select(r'MiniIOMobileFramebuffer: swap #.*surfaces in layers'),
        'last_frame_stats':laststats[-1] if laststats else None,
        'last_physical_valid_timestamp':lastphysical,'maximum_valid_timestamp':lastmax,
        'post_clear_coverage':post,'springboard_receipts':sbfiles,
        'exit_entries':exit_entries,
        'boot_mount_injection':select(r'PONG APFS:|Data mount|THOR INJECT:|SystemOS mounted|mount.*PASS|PONG.*mount.*(?:PASS|FAIL)'),
        'ui_key_entries':select(r'Pong UI(?:18)?:.*(?:entered|neutralized|screen |wake|SpringBoard adapter|main snapshot|SKIP|FAIL)'),
        'crash_context_entries':select(r'Corpse failure|proc [^\n]*crashed|panic\('),
        'comparison_v73':comparison,
        'limits':['Saved logs can wrap or truncate; absence means not present in retained evidence.',
                  'Console raw times and PongSB time(NULL) are distinct clocks.',
                  'Exit signal/status are alternative wait-status interpretations, not two simultaneous valid exit outcomes.',
                  'A clear-frame submitter identifies immediate request provenance, not necessarily the upstream root cause.']}
out=root/'analysis/v74-display-runtime.json'
out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:report[k] for k in ['console','ram_markers','first_visible_frames','layer_transitions','springboard_receipts','comparison_v73']},ensure_ascii=False,indent=2))
