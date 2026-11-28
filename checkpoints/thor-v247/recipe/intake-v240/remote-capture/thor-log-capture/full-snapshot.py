import os
from pathlib import Path
import hashlib, json, os, re, selectors, struct, subprocess, sys, time
sys.path.insert(0, sys.argv[1])
import apfs
from dmg import read_gpt, APFS_PART_TYPE

adb = [os.environ.get("ADB") or "adb", "-s", os.environ['THOR_SERIAL']]
nodes = ('sda36', 'dm-7')
system_apps = {'Home.app', 'MobileSMS.app', 'MobileMail.app', 'FindMy.app', 'MobileStore.app', 'VoiceMemos.app', 'SequoiaTranslator.app', 'Files.app', 'Maps.app', 'News.app', 'Shortcuts.app', 'Weather.app', 'Reminders.app', 'Camera.app', 'MobileTimer.app', 'Health.app', 'MobileNotes.app', 'MobileSafari.app', 'Calculator.app', 'Stocks.app', 'AppleTV.app', 'Contacts.app', 'Magnifier.app', 'Tips.app', 'Photos.app', 'Music.app', 'Books.app', 'Photo Booth.app', 'MobileCal.app', 'Freeform.app', 'Passwords.app', 'FaceTime.app', 'Fitness.app', 'AppStore.app', 'Podcasts.app', 'Measure.app'}
write_fields = (4, 5, 6, 7, 11, 12, 13, 14)
def shell(cmd):
    return subprocess.check_output(adb+['shell','-n',cmd],stdin=subprocess.DEVNULL,timeout=10).decode().strip()
def save_json(name, value):
    Path(name).write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
def snap():
    assert subprocess.check_output(adb+['get-state'],timeout=5).strip()==b'recovery'
    assert shell('id -u')=='0'
    assert shell('readlink -f /dev/block/by-name/userdata')=='/dev/block/sda36'
    assert shell('ls -A /sys/class/block/sda36/holders')=='dm-7'
    assert shell('cat /sys/class/block/dm-7/dm/name')=='userdata'
    assert not shell('ls -A /sys/class/block/dm-7/holders')
    mounted={line.split()[2] for line in shell('cat /proc/self/mountinfo').splitlines()}
    stats={}
    for node in nodes:
        assert shell(f'cat /sys/class/block/{node}/dev') not in mounted
        row=list(map(int,shell(f'cat /sys/class/block/{node}/stat').split()))
        assert row[8]==0 and all(row[i]==0 for i in write_fields)
        stats[node]=row
    return {'boot_id':shell('cat /proc/sys/kernel/random/boot_id'),'stats':stats}

class Physical:
    def __init__(self, size):
        self.size=size
        self.reopens = 0
        self._open()
        self.calls=0
        self.bytes=0
    def _open(self):
        self.child=subprocess.Popen(adb+['shell','-T',
          'while read -r skip count; do dd if=/dev/block/sda36 bs=4096 skip="$skip" count="$count" status=none || exit 1; done'],
          stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=None)
        self.poll=selectors.DefaultSelector()
        self.poll.register(self.child.stdout,selectors.EVENT_READ)

    def _abort(self):
        self.poll.close()
        self.child.kill()
        self.child.stdin.close()
        self.child.stdout.close()
        self.child.wait(timeout=5)
    def read(self, start, length):
        for attempt in range(8):
            try:
                return self._read_once(start, length)
            except (TimeoutError, EOFError, BrokenPipeError, ConnectionError, OSError) as exc:
                self._abort()
                if attempt == 7: raise
                self.reopens += 1
                print(f"ADB range retry offset={start:#x} attempt={attempt+1} failure={exc}", flush=True)
                for _ in range(40):
                    try:
                        ident=shell('cat /proc/sys/kernel/random/boot_id')
                        break
                    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                        time.sleep(0.45)
                else: raise RuntimeError("Recovery ADB unavailable")
                if ident != before["boot_id"]: raise RuntimeError("Recovery changed on reconnect")
                self._open()
    def _read_once(self, start, length):
        assert 0 <= start <= start+length <= self.size and 0 < length <= 16*1024**2
        first=start//4096
        end=(start+length+4095)//4096
        amount=(end-first)*4096
        self.child.stdin.write(f'{first} {end-first}\n'.encode())
        self.child.stdin.flush()
        data=bytearray()
        deadline=time.monotonic()+20
        while len(data)<amount:
            wait=deadline-time.monotonic()
            if wait<=0 or not self.poll.select(wait):
                raise TimeoutError(f'ADB range {start:#x}+{length:#x} stalled')
            b=os.read(self.child.stdout.fileno(),min(65536,amount-len(data)))
            if not b:
                raise EOFError(f'ADB read truncated at {start:#x}')
            data.extend(b)
        self.calls+=1
        self.bytes+=amount
        offset=start%4096
        return bytes(data[offset:offset+length])
    def close(self):
        self.poll.close()
        self.child.stdin.close()
        try:
            assert self.child.wait(timeout=5)==0
        except subprocess.TimeoutExpired:
            self.child.kill()
            self.child.wait()
            raise

class Window:
    def __init__(self, phys, base, size):
        self.phys, self.base, self.size=phys, base, size
        self.path=f'Recovery:{base:#x}+{size:#x}'
    def read(self, off, size):
        assert 0<=off<=off+size<=self.size
        return self.phys.read(self.base+off,size)
    def close(self):
        pass

def data_container(phys, region):
    back=Window(phys,*region)
    first=back.read(512,512)
    if first.startswith(b'EFI PART'):
        parts=[part for part in read_gpt(back) if part.type_guid==APFS_PART_TYPE]
        assert len(parts)==1
        part=parts[0]
        assert 0<=part.offset<part.offset+part.length<=back.size
        back=Window(phys,back.base+part.offset,part.length)
    apfs.open_image=lambda _: back
    return apfs.Container(back.path)

def recovery_ranges(body, prev_fsize_field):
    """Classify physically extracted bytes using the APFS crash-replay EOF."""
    raw = memoryview(body)
    size = len(raw)
    def interval(start, end):
        return dict(offset=start, length=end - start, sha256=hashlib.sha256(raw[start:end]).hexdigest())

    previous = None
    boundary = size
    state = 'dstream-size'
    if prev_fsize_field is not None:
        if len(prev_fsize_field) != 8:
            return dict(state='invalid-prev-fsize-width', prev_fsize_hex=prev_fsize_field.hex(),
                        recovered=None, salvage=interval(0, size), raw_size=size)
        previous, = struct.unpack('<Q', prev_fsize_field)
        if previous > size:
            return dict(state='prev-fsize-exceeds-extracted-stream', prev_fsize=previous,
                        recovered=None, salvage=interval(0, size), raw_size=size)
        boundary = previous
        state = 'prev-fsize'
    return dict(state=state, prev_fsize=previous, recovered=interval(0, boundary),
                salvage=interval(boundary, size), raw_size=size)


# ---- v228 full read-only snapshot (inventory / dump) --------------------
# argv[1] = APFS tool directory, argv[2] = inventory|dump, argv[3] = output dir,
# argv[4:] = roots to dump (volume-relative). Transport is unchanged: raw reads of sda36.
import sys as _sys
MODE = _sys.argv[2]
OUT = Path(_sys.argv[3]); OUT.mkdir(parents=True, exist_ok=True)
ROOTS = _sys.argv[4:] or ['logs', 'mobile/Library/Logs', 'Library/Logs', 'db/diagnostics',
                          'root/Library/Logs', 'DiagnosticLogs', 'MobileSoftwareUpdate',
                          'mobile/Library/Preferences', 'Library/Preferences', 'db/uuidtext', 'tmp']
PER_FILE_CAP = 512 * 1024 ** 2
_orig_read = Physical.read


def _read(self, start, length):
    # The transport bounds one request at 16 MiB; split larger logical reads.
    out = bytearray()
    pos = start
    while pos < start + length:
        n = min(8 * 1024 ** 2, start + length - pos)
        out += _orig_read(self, pos, n)
        pos += n
    return bytes(out)


Physical.read = _read


def walk(volume, oid, rel, rows, depth=0):
    for name, fid, kind in volume.dir_entries(oid):
        if name in ('.', '..'):
            continue
        child = volume.inode(fid)
        if child is None:
            continue
        path = rel + '/' + name if rel else name
        size = child.size if child.is_reg else 0
        rows.append(dict(path=path, kind=kind, bytes=size, mtime_ns=child.mod_time,
                         mode=oct(child.mode)))
        if child.is_dir and depth < 14:
            walk(volume, fid, path, rows, depth + 1)


before = snap()
save_json(str(OUT / 'before.json'), before)
size = int(shell('blockdev --getsize64 /dev/block/sda36'))
phys = Physical(size)
summary = {'boot_id': before['boot_id'], 'mode': MODE, 'roots': {}, 'device_writes': False}
try:
    raw = phys.read(0, 4096)
    assert raw[:8] == b'SHNGROOT'
    (OUT / 'SHNGROOT.bin').write_bytes(raw)
    version, count, total = struct.unpack_from('<IIQ', raw, 8)
    assert version == 1 and count in range(1, 32) and total <= size
    regions = {}
    for i in range(count):
        name, off, length, block, flags = struct.unpack_from('<32sQQII', raw, 24 + 128 * i)
        name = name.split(b'\x00')[0].decode('ascii')
        assert name not in regions and 4096 <= off < off + length <= total and block == 512 and flags == 1
        regions[name] = (off, length)
    assert regions['Data'] == (10809769984, 8591003648)
    container = data_container(phys, regions['Data'])
    volume = apfs.pick_volume(container, 'Data')
    for root in ROOTS:
        hit = volume.lookup(root)
        info = {'exists': bool(hit)}
        if hit:
            rows = []
            ino = volume.inode(hit[0])
            if ino.is_dir:
                walk(volume, hit[0], '', rows)
            else:
                rows.append(dict(path=root, kind=hit[1], bytes=ino.size, mtime_ns=ino.mod_time, mode=oct(ino.mode)))
            files = [r for r in rows if r['kind'] == 8]
            info.update(files=len(files), bytes=sum(r['bytes'] for r in files), dirs=len(rows) - len(files))
            (OUT / (root.replace('/', '__') + '.inventory.json')).write_text(json.dumps(rows, indent=1) + '\n')
            if MODE == 'dump' and ino.is_dir:
                done = 0
                for r in sorted(files, key=lambda r: r['path']):
                    if not r['bytes'] or r['bytes'] > PER_FILE_CAP:
                        r['skipped'] = 'empty-or-over-cap'
                        continue
                    try:
                        body = volume.inode(volume.lookup(root + '/' + r['path'])[0]).contents()
                    except Exception as exc:  # keep going; record the failure
                        r['error'] = repr(exc)[:200]
                        continue
                    assert len(body) == r['bytes']
                    target = OUT / 'Data' / r['path']
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(body)
                    r['sha256'] = hashlib.sha256(body).hexdigest()
                    done += len(body)
                info['dumped_bytes'] = done
            (OUT / (root.replace('/', '__') + '.inventory.json')).write_text(json.dumps(rows, indent=1) + '\n')
        summary['roots'][root] = info
        print(root, json.dumps(info), flush=True)
    container.close()
finally:
    phys.close()
after = snap()
save_json(str(OUT / 'after.json'), after)
assert before['boot_id'] == after['boot_id']
assert all(before['stats'][node][i] == after['stats'][node][i] for node in nodes for i in write_fields)
summary.update(read_requests=phys.calls, read_bytes=phys.bytes, verified_zero_writes=True,
               session_reconnects=phys.reopens)
save_json(str(OUT / 'manifest.json'), summary)
print('SNAPSHOT', MODE, 'complete', phys.calls, phys.bytes, 'bytes; no writes or discards', flush=True)
