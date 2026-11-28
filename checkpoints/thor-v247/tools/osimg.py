"""Read-only access to the IPSW's OS volume (import me):  data(path) -> bytes, listing(path) -> [(name, kind, size, owner, mode)], exists(path)."""
import os
import sys
sys.path.insert(0, os.environ['THOR_ROOT'] + '/UniXNU-pong/tools/apfs')
import apfs
_c = apfs.Container(os.environ['THOR_ROOT'] + '/thor-out/ipsw-components/components/094-32804-038.dmg')
_v = apfs.pick_volume(_c, 'CrystalUpdate22H374.J171J172OS')
def _oid(path):
    oid = 2
    for part in [p for p in path.split('/') if p]:
        hit = None
        for name, fid, kind in _v.dir_entries(oid):
            if name == part:
                hit = fid; break
        if hit is None:
            return None
        oid = hit
    return oid
def node(path):
    oid = _oid(path)
    return _v.inode(oid) if oid is not None else None
def exists(path):
    return _oid(path) is not None
def data(path):
    return node(path).contents()
def listing(path):
    oid = _oid(path)
    if oid is None:
        return None
    rows = []
    for name, fid, kind in _v.dir_entries(oid):
        ch = _v.inode(fid)
        rows.append((name, kind, ch.size if ch and ch.is_reg else 0, ch.owner if ch else 0, ch.group if ch else 0, ch.mode if ch else 0, ch.protection_class if ch else 0))
    return sorted(rows)
