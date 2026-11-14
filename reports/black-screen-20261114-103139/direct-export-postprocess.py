from pathlib import Path
import json,struct,uuid,datetime,os
root=Path('/home/vincent/Projects/thor-unixnu/PongBoot-pull-20261114-103139')
out=root/'extracted'
p=out/'manifest.json'
m=json.loads(p.read_text())
inventory=[json.loads(x) for x in (out/'inventory.jsonl').read_text().splitlines() if x.strip()]
by={x['path']:x for x in inventory}
m['inventory']=inventory
m['state']={}
targets=['Library/Preferences/com.apple.migration.plist','mobile/Library/Preferences/com.apple.migration.plist','Library/Preferences/com.apple.datamigrator.plist','mobile/Library/Preferences/com.apple.datamigrator.plist','db/.AppleSetupDone','preferences/FeatureFlags/Domain/AirPlay.plist','pong/etc/AirPlay-local-audio.plist','pong/libpong-ui18.dylib','pong/libpong-ui.dylib']
for rel in targets:
    item=by.get(rel)
    m['state'][rel]={'exists':item is not None}
    if item:m['state'][rel].update(item)
for rel in ['keybags','mobile/Library/Pong/LaunchServices','Library/Preferences','mobile/Library/Preferences','pong/audio-hal18','pong/staged-audio18','Library/Audio/Plug-Ins/HAL','pong']:
    items=[]
    for path,item in by.items():
        if str(Path(path).parent)!=rel:continue
        if rel.endswith('Preferences') and not any(k in Path(path).name.lower() for k in ('migrat','lockdown','setup','usermanager')):continue
        if rel=='pong' and not any(k in Path(path).name.lower() for k in ('audio','hal','airplay','manifest','init','libpong')):continue
        item=dict(item);item['name']=Path(path).name;items.append(item)
    m['state'][rel]={'exists':rel in by,'entries':items,'metadata_only':True}
m['macho_uuids']={}
for rel in ['pong/libpong-ui18.dylib','pong/libpong-ui.dylib']:
    fp=out/'files'/rel
    if not fp.is_file():continue
    data=fp.read_bytes()
    if data[:4]!=b'\xcf\xfa\xed\xfe':continue
    ncmds=struct.unpack_from('<I',data,16)[0]
    off=32
    for _ in range(ncmds):
        if off+8>len(data):raise RuntimeError('truncated Mach-O load commands')
        cmd,size=struct.unpack_from('<II',data,off)
        if size<8 or off+size>len(data):raise RuntimeError('invalid Mach-O load command')
        if cmd==0x1b:
            if size<24:raise RuntimeError('invalid LC_UUID')
            m['macho_uuids'][rel]=str(uuid.UUID(bytes=data[off+8:off+24])).upper()
        off+=size
warnings=[x for x in (root/'direct-export.log').read_text(errors='replace').splitlines() if 'Warning:' in x]
m['parser_warnings']=warnings
m['parser_warning_inode_paths']={}  # retained warnings are reviewed separately
m['complete']=not m['errors'] and m['source_stat_unchanged']
m['completion_basis']='exporter finalized manifest and summary; all selected files have completed byte counts; source size/mtime/ctime/inode unchanged'
m['tool_source']='../direct-export-apfs-direct-export.cpp'
m['tool_command']='../direct-export-command.txt'
m['postprocess_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat()
for f in m['files']:
    fp=out/f['saved_as']
    if not fp.is_file() or fp.stat().st_size!=f['bytes']:raise RuntimeError('export file size changed: '+f['path'])
    f['mtime']=f['mtime_ns']/1e9
tmp=out/'manifest.postprocess.tmp'
tmp.write_text(json.dumps(m,indent=2,ensure_ascii=False)+'\n')
os.replace(tmp,p)
print(json.dumps({'files':len(m['files']),'bytes':m['exported_bytes'],'inventory':len(inventory),'errors':m['errors'],'skipped':m['skipped'],'macho_uuids':m['macho_uuids'],'parser_warnings':m['parser_warnings'],'warning_paths':m['parser_warning_inode_paths'],'policy_state':{k:m['state'][k] for k in ['preferences/FeatureFlags/Domain/AirPlay.plist','pong/etc/AirPlay-local-audio.plist','pong/audio-hal18','pong/staged-audio18','Library/Audio/Plug-Ins/HAL']}},indent=2))
