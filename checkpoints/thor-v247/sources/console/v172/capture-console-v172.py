import os
from pathlib import Path
import hashlib, json, os, re, selectors, struct, subprocess, sys, time
sys.path.insert(0, sys.argv[1])
import apfs
from dmg import read_gpt, APFS_PART_TYPE

adb = ['/opt/homebrew/bin/adb', '-s', os.environ['THOR_SERIAL']]
nodes = ('sda36', 'dm-7')
system_apps = {'Home.app', 'MobileSMS.app', 'MobileMail.app', 'FindMy.app', 'MobileStore.app', 'VoiceMemos.app', 'SequoiaTranslator.app', 'Files.app', 'Maps.app', 'News.app', 'Shortcuts.app', 'Weather.app', 'Reminders.app', 'Camera.app', 'MobileTimer.app', 'Health.app', 'MobileNotes.app', 'MobileSafari.app', 'Calculator.app', 'Stocks.app', 'AppleTV.app', 'Contacts.app', 'Magnifier.app', 'Tips.app', 'Photos.app', 'Music.app', 'Books.app', 'Photo Booth.app', 'MobileCal.app', 'Freeform.app', 'Passwords.app', 'FaceTime.app', 'Fitness.app', 'AppStore.app', 'Podcasts.app', 'Measure.app'}
write_fields = (4, 5, 6, 7, 11, 12, 13, 14)
def shell(cmd):
    return subprocess.check_output(adb+['shell',cmd],timeout=10).decode().strip()
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
        self.child=subprocess.Popen(adb+['shell','-T',
          'while read -r skip count; do dd if=/dev/block/sda36 bs=4096 skip="$skip" count="$count" status=none || exit 1; done'],
          stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=None)
        self.poll=selectors.DefaultSelector()
        self.poll.register(self.child.stdout,selectors.EVENT_READ)
        self.calls=0
        self.bytes=0
    def read(self, start, length):
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

def probe(cont, vol, names, dest, collect):
    result=[]
    for name in names:
        hit=vol.lookup(name)
        row={'path':name,'exists':bool(hit)}
        if hit:
            ino=vol.inode(hit[0])
            row.update(inode=hit[0],mode=oct(ino.mode),uid=ino.owner,bytes=ino.size,mtime_ns=ino.mod_time)
            if collect(name,ino):
                body=ino.contents()
                assert len(body)==ino.size
                out=Path(dest)/name
                out.parent.mkdir(parents=True,exist_ok=True)
                out.write_bytes(body)
                row['sha256']=hashlib.sha256(body).hexdigest()
        result.append(row)
    return result

before=snap()
save_json('before.json',before)
size=int(shell('blockdev --getsize64 /dev/block/sda36'))
phys=Physical(size)
manifest={'boot_id':before['boot_id'],'regions':{},'volumes':{},'device_writes':False}
try:
    raw=phys.read(0,4096)
    assert raw[:8]==b'SHNGROOT'
    Path('SHNGROOT.bin').write_bytes(raw)
    version,count,total=struct.unpack_from('<IIQ',raw,8)
    assert version==1 and count in range(1,32) and total<=size
    regions={}
    for i in range(count):
        name,off,length,block,flags=struct.unpack_from('<32sQQII',raw,24+128*i)
        name=name.split(b'\x00')[0].decode('ascii')
        assert name not in regions and 4096<=off<off+length<=total and block==512 and flags==1
        regions[name]=(off,length)
    ending=4096
    for off,length in sorted(regions.values()):
        assert ending<=off
        ending=off+length
    assert regions['Data']==(10809769984,8591003648)
    manifest['regions']=regions
    print('Verified SHNGROOT geometry and read-only transport',flush=True)
    for region in ('Data',):
        if region not in regions:
            continue
        container=data_container(phys,regions[region])
        volumes=container.volumes()
        manifest['volumes'][region]={ 'xid':container.xid,'names':[x.volname for x in volumes] }
        print(region,container.xid,[v.volname for v in volumes],flush=True)
        volume=apfs.pick_volume(container,'Data')
        picks=['logs/PongConsole-v172.txt','logs/PongConsole-v172.previous.txt',
               'logs/PongConsole.txt','logs/PongConsole.previous.txt',
               'logs/PongDiag.txt',
               'mobile/Library/Logs/PongMotion-v147.txt',
               'mobile/Library/Logs/PongBoot-Preferences.txt',
               'mobile/Library/Logs/PongBoot-SpringBoard.txt',
               'mobile/Library/Logs/PongBoot-backboardd.txt']
        manifest['volumes'][region]['files']=probe(container,volume,picks,'Data',
          lambda name,ino:ino.is_reg and ino.size<=24*1024**2)
        container.close()
        save_json('manifest.json',manifest)
finally:
    phys.close()
after=snap()
save_json('after.json',after)
assert before['boot_id']==after['boot_id']
assert all(before['stats'][node][i]==after['stats'][node][i] for node in nodes for i in write_fields)
manifest.update(read_requests=phys.calls,read_bytes=phys.bytes,verified_zero_writes=True)
save_json('manifest.json',manifest)
print('Recovery verification complete',phys.calls,phys.bytes,'bytes; no writes or discards',flush=True)
