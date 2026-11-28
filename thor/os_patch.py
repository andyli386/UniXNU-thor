#!/usr/bin/env python3.13
"""OS patch: the changed blocks of the system window of the phone, as one file, verified before anything is written.

The system volume (the "OS" window of SHNGROOT: a raw APFS image of 0x198000000 bytes) is mounted read-only by the
port, so a change to it (the phone resources of the keyboard, for instance) is made in a copy of the window on this
Mac and travels to the phone as the 4 KiB blocks that differ, one record to a block.  Nothing is written on the
phone unless every block of the patch is, right there, either exactly what the patch expects to find (the original)
or already the new content.  The data blocks go first, the newest container superblock of the new image (the
commit) last, the other superblocks after it; every block written is read back.  Because each block is checked on
its own, a patch that was interrupted at any point is simply continued by the next run.

    make    python3.13 -I thor/os_patch.py make --base <window as installed> --modified <window with the change>
                                   --output os-patch.bin --version 249
    show    python3.13 -I thor/os_patch.py show os-patch.bin [--all]
    apply   python3.13 -I thor/os_patch.py apply os-patch.bin [--dry-run]          (the phone in TWRP; adb)
    test    the same apply on a raw image instead of the phone: --image <copy of the window>

In the boot image the file lies in the FAT volume; the kernel streams it from the boot partition and applies it
before any window is published (bsd/kern/thor_os_patch.h).  thor/os_patch.py apply against a phone in TWRP is the
way when an image cannot be booted, and the same checks and the same order are used.

What the commit order does not cover: the old checkpoint may name a block that this patch has already rewritten
when the write is cut off before the commit block.  The boot image cannot mount a half-patched window (it applies
the patch first every time); with TWRP apply again before anything boots.

File format ("THOROSP2"), in whole 4 KiB blocks so that the kernel can stream it:
  block 0  magic[8] u32 header_bytes u32 record_bytes u32 version u32 flags u64 window_bytes u64 record_count
           u8 base_head[32]  SHA-256 of the first 16 blocks of the window as installed (the commit block may
                             rewrite them, so the new image has its own): u8 new_head[32]   then zeros
  table    32 records to a block, each 128 bytes, one for every 4 KiB block that differs, in order:
           u64 offset (bytes from the start of the window) u32 kind u32 zero u8 before[32] u8 after[32]  48 zeros
  data     the new bytes of every record in table order
  kind     0 data  1 commit (the newest superblock of the new image)  2 superblock (any other one: after the commit)
"""
import argparse
import hashlib
import json
import os
import struct
import subprocess
import sys
import time
from pathlib import Path

assert __debug__, "verification needs assertions"
BLOCK = 4096
MAGIC = b'THOROSP2'
HEADER_BYTES, RECORD_BYTES = 4096, 128
RECORDS_PER_BLOCK = HEADER_BYTES // RECORD_BYTES    # 32: no record straddles two blocks
KIND_DATA, KIND_COMMIT, KIND_SUPERBLOCK = 0, 1, 2
HEAD_BLOCKS = 16                         # the first blocks of the container: its superblock slots
MAX_RECORDS = 8192                       # thor_os_patch.h keeps a table of this size


def sha(data):
    return hashlib.sha256(data).hexdigest()


def is_superblock(block):
    """a container superblock: magic NXSB at +32 and the object type of one at +24"""
    return len(block) == BLOCK and block[32:36] == b'NXSB' and struct.unpack_from('<I', block, 24)[0] & 0xffff == 1


# ----------------------------------------------------------------------------------------------------------- targets

class ImageTarget:
    """a raw image file that holds the window (a copy, for the test of a patch)"""

    def __init__(self, path, window_offset=0):
        self.path, self.offset = Path(path), window_offset
        self.window_bytes = self.path.stat().st_size - window_offset
        self.calls = 0

    def check(self, offset, length):
        assert 0 <= offset and length > 0 and offset % BLOCK == 0 and length % BLOCK == 0 and offset + length <= self.window_bytes, (offset, length)

    def read(self, offset, length):
        self.check(offset, length)
        self.calls += 1
        with open(self.path, 'rb') as image:
            image.seek(self.offset + offset)
            data = image.read(length)
        assert len(data) == length, 'short read from the image'
        return data

    def write(self, offset, data):
        self.check(offset, len(data))
        self.calls += 1
        with open(self.path, 'r+b') as image:
            image.seek(self.offset + offset)
            image.write(data)
            image.flush()
            os.fsync(image.fileno())


class PhoneTarget:
    """the phone in TWRP: the OS window inside the userdata partition, read and written through adb (identity first)"""

    def __init__(self, adb, serial):
        self.adb, self.serial, self.remote = adb, serial, '/tmp/thor-os-patch.bin'
        self.calls = 0
        assert self.shell('getprop ro.product.device').strip() == 'thor', 'not thor'
        assert self.shell('id -u').strip() == '0', 'not root in TWRP'
        head = self.read_bytes('/dev/block/sda36', 0, BLOCK)
        assert head[:8] == b'SHNGROOT', 'userdata does not start with a SHNGROOT directory'
        version, count = struct.unpack_from('<II', head, 8)
        assert version == 1 and 1 <= count <= 8, (version, count)
        found = None
        for index in range(count):
            name, offset, size = struct.unpack_from('<8s8x8xQQ', head, 0x18 + 0x80 * index)
            if name.rstrip(b'\0') == b'OS':
                found = (offset, size)
        assert found, 'the SHNGROOT directory names no OS window'
        self.device_offset, self.window_bytes = found
        assert self.device_offset % BLOCK == 0 and self.window_bytes % BLOCK == 0

    def adb_run(self, args, timeout=180):
        command = [self.adb, '-s', self.serial] + args
        error = b''
        for attempt in (1, 2, 3):
            try:
                result = subprocess.run(command, capture_output=True, timeout=timeout)
            except subprocess.TimeoutExpired:
                if attempt == 3:
                    raise
                continue
            if result.returncode == 0:
                return result
            error = result.stderr[-300:]
        raise RuntimeError(f'adb {" ".join(args[:3])} failed ({result.returncode}): {error!r}')

    def shell(self, text, timeout=60):
        result = self.adb_run(['shell', '-n', text + '; echo " rc=$?"'], timeout)
        lines = result.stdout.decode('utf-8', 'replace').replace('\r', '').rstrip('\n').split('\n')
        assert lines and lines[-1].endswith(' rc=0'), f'{text[:70]} -> {lines[-3:]}'
        return '\n'.join(lines[:-1])

    def read_bytes(self, device, offset, length):
        assert offset % BLOCK == 0 and length % BLOCK == 0
        self.calls += 1
        result = self.adb_run(['exec-out', f'dd if={device} bs=4096 skip={offset // BLOCK} count={length // BLOCK} status=none'], timeout=600)
        assert len(result.stdout) == length, f'short read of {device} at {offset:#x}: {len(result.stdout)} of {length} bytes'
        return result.stdout

    def read(self, offset, length):
        assert 0 <= offset and length > 0 and offset % BLOCK == 0 and offset + length <= self.window_bytes, (offset, length)
        return self.read_bytes('/dev/block/sda36', self.device_offset + offset, length)

    def write(self, offset, data):
        assert 0 <= offset and offset % BLOCK == 0 and len(data) % BLOCK == 0 and offset + len(data) <= self.window_bytes, (offset, len(data))
        local = Path(os.environ.get('TMPDIR', '/tmp')) / 'thor-os-piece.bin'
        local.write_bytes(data)
        self.adb_run(['push', str(local), self.remote], timeout=600)
        self.calls += 2
        assert self.shell(f'sha256sum {self.remote}').split()[0] == sha(data), 'the piece on the phone is not the one sent'
        self.shell(f'dd if={self.remote} of=/dev/block/sda36 bs=4096 seek={(self.device_offset + offset) // BLOCK} '
                   f'count={len(data) // BLOCK} conv=fsync status=none', timeout=600)


# -------------------------------------------------------------------------------------------------------------- make

def make(args):
    size = Path(args.base).stat().st_size
    assert size == Path(args.modified).stat().st_size and size % BLOCK == 0, 'the two windows differ in size'
    records, data = [], bytearray()
    newest_xid, newest = 0, None
    with open(args.base, 'rb') as before, open(args.modified, 'rb') as after:
        head, new_head = before.read(HEAD_BLOCKS * BLOCK), after.read(HEAD_BLOCKS * BLOCK)
        before.seek(0)
        after.seek(0)
        for block in range(size // BLOCK):
            old, new = before.read(BLOCK), after.read(BLOCK)
            assert len(old) == BLOCK and len(new) == BLOCK, 'short read'
            if old == new:
                continue
            kind = KIND_DATA
            if is_superblock(new):
                xid = struct.unpack_from('<Q', new, 16)[0]
                kind = KIND_SUPERBLOCK
                if xid > newest_xid:
                    newest_xid, newest = xid, len(records)
            records.append([block * BLOCK, kind, sha(old), sha(new)])
            data += new
    assert records, 'the two windows are the same'
    assert newest is not None and newest_xid > 0, 'the new image holds no container superblock among the changed blocks'
    records[newest][1] = KIND_COMMIT
    assert len(records) <= MAX_RECORDS, f'{len(records)} changed blocks is more than thor_os_patch.h keeps ({MAX_RECORDS})'
    table = b''.join(struct.pack('<QII', r[0], r[1], 0) + bytes.fromhex(r[2]) + bytes.fromhex(r[3]) + bytes(48) for r in records)
    table += bytes(-len(table) % HEADER_BYTES)
    header = struct.pack('<8sIIIIQQ', MAGIC, HEADER_BYTES, RECORD_BYTES, args.version, 1, size, len(records))
    header += bytes.fromhex(sha(head)) + bytes.fromhex(sha(new_head))
    header += bytes(HEADER_BYTES - len(header))
    assert len(header) == HEADER_BYTES and len(table) % HEADER_BYTES == 0
    args.output.write_bytes(header + table + data)
    kinds = {KIND_DATA: 0, KIND_COMMIT: 0, KIND_SUPERBLOCK: 0}
    for record in records:
        kinds[record[1]] += 1
    runs = 1 + sum(1 for a, b in zip(records, records[1:]) if b[0] != a[0] + BLOCK)
    report = dict(format=MAGIC.decode(), output=str(args.output), version=args.version, window_bytes=size, records=len(records),
                  runs=runs, kinds={str(k): v for k, v in kinds.items()}, changed_bytes=len(records) * BLOCK,
                  file_bytes=args.output.stat().st_size,
                  newest_superblock=dict(xid=newest_xid, record=newest, offset=records[newest][0]),
                  sha256=sha(args.output.read_bytes()))
    args.output.with_suffix('.json').write_text(json.dumps(report, indent=1) + '\n')
    print(json.dumps(report, indent=1))


# -------------------------------------------------------------------------------------------------------- load, show

def load(path):
    blob = Path(path).read_bytes()
    assert blob[:8] == MAGIC, 'not an OS patch of this format'
    header_bytes, record_bytes, version, flags, window_bytes, count = struct.unpack_from('<IIIIQQ', blob, 8)
    assert header_bytes == HEADER_BYTES and record_bytes == RECORD_BYTES and flags == 1, 'unknown layout'
    assert 0 < count <= MAX_RECORDS and blob[104:HEADER_BYTES] == bytes(HEADER_BYTES - 104), 'the header block is not as expected'
    records, at = [], HEADER_BYTES
    for index in range(count):
        offset, kind, zero = struct.unpack_from('<QII', blob, at)
        assert offset % BLOCK == 0 and kind <= KIND_SUPERBLOCK and zero == 0, (index, offset, kind, zero)
        records.append(dict(offset=offset, kind=kind, before=blob[at + 16:at + 48].hex(), after=blob[at + 48:at + 80].hex()))
        at += RECORD_BYTES
        if index and records[index - 1]['offset'] >= offset:
            raise AssertionError('records are not in strict order of offset')
    assert records[-1]['offset'] + BLOCK <= window_bytes, 'a record lies outside the window'
    at = HEADER_BYTES + len(records) * RECORD_BYTES
    at += -at % HEADER_BYTES                                         # the data starts at the next block
    assert len(blob) == at + count * BLOCK, 'the patch file is not complete'
    assert sum(r['kind'] == KIND_COMMIT for r in records) == 1, 'a patch names exactly one commit block'
    return dict(version=version, window_bytes=window_bytes, base_head=blob[40:72].hex(), new_head=blob[72:104].hex(),
                records=records, blob=blob, data_at=at)


def groups(records, want, size=32):
    """(first index, number of records) of every group of at most `size` records that `want` accepts and that are
    neighbours in the window as well as in the table: one read, one write or one load of the patch for each"""
    first, count = None, 0
    for index, record in enumerate(records):
        if want(index, record):
            if first is not None and count < size and records[index - 1]['offset'] + BLOCK == record['offset']:
                count += 1
                continue
            if first is not None:
                yield first, count
            first, count = index, 1
        elif first is not None:
            yield first, count
            first, count = None, 0
    if first is not None:
        yield first, count


def show(args):
    patch = load(args.patch)
    records = patch['records']
    names = {KIND_DATA: 'data', KIND_COMMIT: 'commit', KIND_SUPERBLOCK: 'sb'}
    runs = 1 + sum(1 for a, b in zip(records, records[1:]) if b['offset'] != a['offset'] + BLOCK)
    print(f'OS patch for a window of {patch["window_bytes"]:#x} bytes, made for v{patch["version"]}: {len(records)} blocks in {runs} runs, '
          f'{len(records) * BLOCK} changed bytes, file {len(patch["blob"])} bytes, first blocks {patch["base_head"][:16]}…')
    if args.all:
        for index, record in enumerate(records):
            print(f'  {index:5d} {names[record["kind"]]:6s} +{record["offset"]:#012x}   {record["before"][:12]} -> {record["after"][:12]}')


# ------------------------------------------------------------------------------------------------------------ apply

def apply(patch_path, target, dry_run=False, say=None):
    patch = load(patch_path)
    records, blob, data_at = patch['records'], patch['blob'], patch['data_at']
    say = say or (lambda text: print(time.strftime('%H:%M:%S ') + text, flush=True))
    assert target.window_bytes == patch['window_bytes'], f'the target holds a window of {target.window_bytes:#x} bytes, the patch was made for {patch["window_bytes"]:#x}'
    say(f'OS patch v{patch["version"]}: {len(records)} blocks, window of {target.window_bytes:#x} bytes')
    head = sha(target.read(0, HEAD_BLOCKS * BLOCK))
    assert head in (patch['base_head'], patch['new_head']), \
        'the first 16 blocks are neither those of the installed image nor those of the new one (a newer checkpoint?): nothing is written'
    say('the first blocks are those of the installed image' if head == patch['base_head'] else 'the first blocks are already those of the new image')

    def data(index, count):
        return blob[data_at + index * BLOCK: data_at + (index + count) * BLOCK]

    def check(index, count, current):
        """what the window holds for a group: for every record 0 (to write) or 1 (already new)"""
        states = []
        for inside in range(count):
            record, digest = records[index + inside], sha(current[inside * BLOCK:(inside + 1) * BLOCK])
            if digest == record['after']:
                states.append(1)
            elif digest == record['before']:
                states.append(0)
            else:
                raise SystemExit(f'record {index + inside} at +{record["offset"]:#x} is neither the original nor the new content; '
                                 f'nothing is written\n  found {digest}\n  want {record["before"]} (original) or {record["after"]} (new)')
        return states

    todo = [0] * len(records)
    for first, count in groups(records, lambda i, r: True):
        got = check(first, count, target.read(records[first]['offset'], count * BLOCK))
        for inside, state in enumerate(got):
            todo[first + inside] = 1 - state
    to_write = sum(todo)
    say(f'verified every block: {len(records) - to_write} already new, {to_write} to write ({to_write * BLOCK} bytes)')
    if dry_run or not to_write:
        say('dry run: nothing written' if dry_run else 'nothing to write: the window already holds the new content')
        return dict(written=0, already=len(records), records=len(records))
    for kind in (KIND_DATA, KIND_COMMIT, KIND_SUPERBLOCK):
        for first, count in groups(records, lambda i, r, k=kind: r['kind'] == k and todo[i]):
            piece = data(first, count)
            assert all(sha(piece[inside * BLOCK:(inside + 1) * BLOCK]) == records[first + inside]['after'] for inside in range(count)), \
                f'the patch file is damaged at record {first}'
            target.write(records[first]['offset'], piece)
            got = check(first, count, target.read(records[first]['offset'], count * BLOCK))
            assert all(got), f'record {first} at +{records[first]["offset"]:#x} did not read back as written; stop here'
    say(f'APPLIED: {to_write} blocks written now, {len(records) - to_write} were already new, {len(records)} in all')
    return dict(written=to_write, already=len(records) - to_write, records=len(records))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('make')
    p.add_argument('--base', required=True)
    p.add_argument('--modified', required=True)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--version', type=int, required=True)
    p = commands.add_parser('show')
    p.add_argument('patch')
    p.add_argument('--all', action='store_true')
    p = commands.add_parser('apply')
    p.add_argument('patch')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--image', help='a raw image that holds the window, instead of the phone')
    p.add_argument('--window-offset', type=lambda v: int(v, 0), default=0)
    p.add_argument('--adb', default='/opt/homebrew/bin/adb')
    p.add_argument('--serial', default=os.environ.get('THOR_SERIAL', ''))
    args = parser.parse_args()
    if args.command == 'make':
        make(args)
    elif args.command == 'show':
        show(args)
    elif args.command == 'apply':
        target = ImageTarget(args.image, args.window_offset) if args.image else PhoneTarget(args.adb, args.serial)
        apply(args.patch, target, args.dry_run)


if __name__ == '__main__':
    main()
