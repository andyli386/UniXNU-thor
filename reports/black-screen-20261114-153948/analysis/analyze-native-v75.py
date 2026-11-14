from pathlib import Path
import collections, json, re, sys

root=Path(sys.argv[1]).resolve()
baseline=Path(sys.argv[2]).resolve()
out=root/'analysis'; files=root/'extracted/files'
decoder=json.JSONDecoder()
parts=[]
for p in sorted(files.rglob('*.ips')):
    text=p.read_text(errors='replace')
    item={'file':str(p.relative_to(root)), 'bytes':p.stat().st_size,
          'whole_document_complete':False,'independently_decoded_fields':[]}
    try:
        header,end=decoder.raw_decode(text)
        item['header']=header
        body,end2=decoder.raw_decode(text[end:].lstrip())
        item['whole_document_complete']=True
    except ValueError as e:
        item['parse_error']=str(e)
    for m in re.finditer(r'"(reason|pid|exception|termination|bootSessionUUID|captureTime|date)"\s*:\s*',text):
        try:
            value,n=decoder.raw_decode(text[m.end():])
        except ValueError:
            continue
        item['independently_decoded_fields'].append({
            'key':m[1], 'value':value, 'line':text.count('\n',0,m.start())+1,
            'field_char_offset':m.start(),
            'field_utf8_byte_offset':len(text[:m.start()].encode('utf-8')),
            'value_char_span':[m.end(),m.end()+n]})
    parts.append(item)

def focus(r):
    data=json.loads((r/'analysis/native-pongboot-late.json').read_text())
    selected=[]
    for f in data['files']:
        if not any(k in Path(f['file']).name for k in ['SpringBoard','backboardd','runningboardd','audiomxd','mediaremoted','lsd']):
            continue
        ff={k:v for k,v in f.items() if k!='blocks'}
        ff['blocks']=[]
        for b in f['blocks']:
            copy={k:v for k,v in b.items() if k!='threads'}
            mains=[t for t in b['threads'] if t.get('thread')==0]
            copy['sampled_threads']=len(b['threads'])
            copy['main_thread']=mains[0] if mains else None
            copy['selected_workers']=[]
            for t in b['threads']:
                if t in mains:
                    continue
                ss=' '.join(fr.get('symbol','') for fr in t.get('symbols',[]))
                if any(k in ss for k in ['group_wait','OSAnalytics','AudioServerDriver','libBNNS','libAudioDSP','pong_observed','pong_stat','lstat64','AudioToolbox','CA::Render::Server','IOMFBServer','buttons_thread','CoreServices','UserManagement']):
                    copy['selected_workers'].append(t)
            ff['blocks'].append(copy)
        selected.append(ff)
    return {'cache_uuids':data.get('cache_uuids'),'cache_verified':data.get('cache_verified'),
            'symbolization_basis':data.get('symbolization_basis'), 'files':selected}

probe=[]; receipts=[]
for p in sorted(files.rglob('*.txt')):
    if 'Pong' not in p.name:
        continue
    seen=set()
    for line,s in enumerate(p.read_text(errors='replace').splitlines(),1):
        if s in seen:
            continue
        seen.add(s)
        item={'file':str(p.relative_to(root)),'line':line,'text':s}
        if 'Pong SB stat:' in s or 'Pong BBD:' in s or 'installed' in s and ('stat' in s or 'group_wait' in s):
            probe.append(item)
        if p.name.startswith('PongSB-'):
            receipts.append(item)
console=[]; seen=set()
p=files/'logs/PongConsole.txt'
for line,s in enumerate(p.read_text(errors='replace').splitlines(),1):
    if s in seen:
        continue
    seen.add(s)
    if ('PONG EXIT ' in s and any(k in s for k in ['audiomxd','PosterBoard','backboardd','SpringBoard','runningboardd'])) or 'layers EMPTIED' in s or 'layers RESTORED' in s:
        console.append({'file':str(p.relative_to(root)), 'line':line, 'text':s})
result={'scope':'Read-only retained log analysis. Whole truncated IPS is not repaired. Thread samples do not prove continuous waits.',
        'current_root':str(root),'baseline_root':str(baseline),
        'native_partial_fields':parts, 'v75_probe_receipts':probe,'springboard_receipts':receipts,
        'console_key_events':console, 'current':focus(root),'baseline':focus(baseline)}
(out/'native-key-evidence-v75.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
print(json.dumps({'ips':len(parts),'complete_ips':sum(x['whole_document_complete'] for x in parts),
                  'probe_receipts':len(probe),'sb_receipts':len(receipts),'console_key_events':len(console)},indent=2))
