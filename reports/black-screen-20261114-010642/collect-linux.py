#!/usr/bin/env python3
"""Read selected boot evidence from an already read-only mounted host APFS image."""
from pathlib import Path
import datetime, json, os, plistlib, shutil, stat, struct, sys, uuid

root, mount, source = (Path(x).resolve() for x in sys.argv[1:4])
capture = json.loads((root / 'extraction.json').read_text())
image = root / 'Data-runtime.img'
assert capture['capture_status'] == 'complete'
assert capture['read_bytes'] == image.stat().st_size
assert capture['no_write_or_discard_counter_change'] is True
assert source.is_relative_to(mount)
mountlines = [x for x in Path('/proc/self/mountinfo').read_text().splitlines()
              if x.split()[4] == str(mount)]
assert len(mountlines) == 1, mountlines
mountfields = mountlines[0].split()
sep = mountfields.index('-')
assert 'fuse' in mountfields[sep + 1], mountlines
assert 'ro' in set(mountfields[5].split(',')) | set(mountfields[sep + 3].split(',')), mountlines
out = root / 'extracted'
out.mkdir(exist_ok=False)
manifest = {
    'started_local': datetime.datetime.now().astimezone().isoformat(),
    'files': [], 'inventory': [], 'errors': [], 'skipped': [], 'state': {},
    'mountinfo': mountlines, 'mountpoint': str(mount), 'volume_root': str(source),
    'host_readonly': True,
    'ownership_note': 'APFS-FUSE mapped host stat; do not infer original owner if uid/gid mount mapping is enabled',
    'detached': False,
}
seen = set()

def meta(path, relative):
    s = path.stat()
    return dict(path=str(relative), bytes=s.st_size, uid=s.st_uid, gid=s.st_gid,
                mode=oct(stat.S_IMODE(s.st_mode)), mtime=s.st_mtime)

def error(path, exc):
    manifest['errors'].append({'path': str(path), 'error': repr(exc)})

def copy(path):
    rel = path.relative_to(source)
    if str(rel) in seen:
        return
    try:
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(source):
            return
        record = meta(path, rel)
        if record['bytes'] > 64 * 1024 * 1024:
            manifest['skipped'].append({**record, 'reason': 'over64MiB'})
            return
        dst = out / 'files' / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dst)
        dst.chmod(0o600)
        if dst.stat().st_size != record['bytes']:
            raise IOError('copy length differs from APFS stat')
        record['saved_as'] = str(dst.relative_to(out))
        manifest['files'].append(record)
        seen.add(str(rel))
    except Exception as exc:
        error(rel, exc)

try:
    for rel in ('logs', 'mobile/Library/Logs', 'root/Library/Logs', 'cores', 'log/com.apple.xpc.launchd'):
        top = source / rel
        if not top.is_dir() or top.is_symlink():
            continue
        for dirname, dirs, files in os.walk(top, followlinks=False, onerror=lambda e: error(rel, e)):
            parent = Path(dirname)
            dirs[:] = [d for d in dirs if not (parent / d).is_symlink()
                       and len((parent / d).relative_to(top).parts) <= 7]
            for name in sorted(files):
                path = parent / name
                if path.is_symlink():
                    continue
                try:
                    if len(manifest['inventory']) < 4000:
                        manifest['inventory'].append(meta(path, path.relative_to(source)))
                    lower = name.lower()
                    if (lower.endswith(('.ips', '.crash')) or name.startswith('Pong')
                        or any(k in lower for k in ('usermanager', 'migrat', 'lockdown', 'keybagd',
                           'fairplay', 'locationd', 'springboard', 'runningboard', 'backboard',
                           'mdmd', 'filecoordination', 'mediaremote', 'audiomx'))):
                        copy(path)
                except Exception as exc:
                    error(path.relative_to(source), exc)

    for rel in ('pong/libpong-ui18.dylib', 'pong/libpong-ui.dylib'):
        copy(source / rel)
        path = out / 'files' / rel
        if path.is_file():
            data = path.read_bytes()
            if data[:4] == b'\xcf\xfa\xed\xfe':
                ncmds = struct.unpack_from('<I', data, 16)[0]
                offset = 32
                for _ in range(ncmds):
                    cmd, size = struct.unpack_from('<II', data, offset)
                    assert size >= 8 and offset + size <= len(data)
                    if cmd == 0x1b:
                        manifest.setdefault('macho_uuids', {})[rel] = str(uuid.UUID(bytes=data[offset+8:offset+24])).upper()
                    offset += size

    for rel in ('Library/Preferences/com.apple.migration.plist',
                'mobile/Library/Preferences/com.apple.migration.plist',
                'Library/Preferences/com.apple.datamigrator.plist',
                'mobile/Library/Preferences/com.apple.datamigrator.plist', 'db/.AppleSetupDone'):
        path = source / rel
        record = {'exists': path.exists()}
        if path.is_file() and not path.is_symlink():
            record.update(meta(path, rel))
            copy(path)
            if path.stat().st_size < 1024 * 1024:
                try:
                    record['plist'] = plistlib.loads(path.read_bytes())
                except Exception as exc:
                    record['plist_error'] = str(exc)
        manifest['state'][rel] = record

    for rel in ('keybags', 'mobile/Library/Pong/LaunchServices', 'Library/Preferences', 'mobile/Library/Preferences'):
        path = source / rel
        items = []
        if path.is_dir() and not path.is_symlink():
            for item in sorted(path.iterdir()):
                if rel.endswith('Preferences') and not any(k in item.name.lower() for k in ('migrat', 'lockdown', 'setup', 'usermanager')):
                    continue
                s = item.lstat()
                items.append(dict(name=item.name, bytes=s.st_size, directory=stat.S_ISDIR(s.st_mode),
                                  symlink=stat.S_ISLNK(s.st_mode), uid=s.st_uid, gid=s.st_gid,
                                  mode=oct(stat.S_IMODE(s.st_mode))))
                if rel.endswith('Preferences') and stat.S_ISREG(s.st_mode) and s.st_size < 1024 * 1024:
                    copy(item)
                if len(items) > 350:
                    break
        manifest['state'][rel] = dict(exists=path.exists(), entries=items)
finally:
    manifest['finished_local'] = datetime.datetime.now().astimezone().isoformat()
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False, default=str) + '\n')

print(json.dumps(dict(files=len(manifest['files']), bytes=sum(x['bytes'] for x in manifest['files']),
                     errors=manifest['errors'], skipped=manifest['skipped'],
                     macho_uuids=manifest.get('macho_uuids'),
                     logs=[(x['path'], x['bytes']) for x in manifest['files'] if Path(x['path']).name.startswith('Pong')]), indent=2))
